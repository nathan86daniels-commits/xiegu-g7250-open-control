"""SQLite logbook with ADIF 3 import/export and worked-before lookups."""
import datetime as dt
import re
import sqlite3
from pathlib import Path

from . import geo
from .timezone import station_time

BANDS = [(1.8, 2.0, "160m"), (3.5, 4.0, "80m"), (5.25, 5.45, "60m"), (7.0, 7.3, "40m"),
         (10.1, 10.15, "30m"), (14.0, 14.35, "20m"), (18.068, 18.168, "17m"), (21.0, 21.45, "15m"),
         (24.89, 24.99, "12m"), (28.0, 29.7, "10m"), (50.0, 54.0, "6m")]

FIELDS = ("utc", "call", "freq_hz", "band", "mode", "rst_s", "rst_r", "name", "qth", "grid", "notes",
          "stx", "srx", "contest", "region")
EXTRA_COLUMNS = {"stx": "TEXT", "srx": "TEXT", "contest": "TEXT", "region": "TEXT", "qrz_logid": "TEXT", "qrz_status": "TEXT"}


def band_of(freq_hz):
    if not freq_hz:
        return ""
    mhz = freq_hz / 1e6
    return next((b for lo, hi, b in BANDS if lo <= mhz <= hi), "")


def _adif_mode(mode):
    m = (mode or "").upper()
    if m in ("USB", "LSB"):
        return "SSB", m
    if m == "CW-R":
        return "CW", ""
    if m in ("FT4", "JS8", "Q65"):
        return "MFSK", m
    if m == "DIG":
        return "DATA", ""
    return m, ""


class Logbook:
    def __init__(self, path: Path, home=lambda: None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.home = home                     # callable -> (lat, lon) of the station, or None
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS qso (
            id INTEGER PRIMARY KEY, utc TEXT NOT NULL, call TEXT NOT NULL, freq_hz INTEGER,
            band TEXT, mode TEXT, rst_s TEXT, rst_r TEXT, name TEXT, qth TEXT, grid TEXT,
            notes TEXT, country TEXT, prefix TEXT, lat REAL, lon REAL, approx INTEGER)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS qso_call ON qso(call)")
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(qso)")}
        for col, kind in EXTRA_COLUMNS.items():          # upgrade older logbooks in place
            if col not in have:
                self.db.execute(f"ALTER TABLE qso ADD COLUMN {col} {kind}")
        self.db.commit()

    # ---- helpers -------------------------------------------------------------

    def _place(self, call, grid):
        entity = geo.locate_call(call) or {}
        pos = geo.grid_to_latlon(grid) if grid else None
        approx = pos is None
        if pos is None and entity:
            pos = (entity["lat"], entity["lon"])
        return entity, pos, approx

    def _path(self, pos):
        home = self.home()
        if not home or not pos:
            return {}
        km = geo.distance_km(home, pos)
        brg = geo.bearing_deg(home, pos)
        return {"dist_km": round(km), "bearing": round(brg), "compass": geo.compass(brg),
                "lp_km": round(2 * 3.14159265 * geo.EARTH_KM - km)}

    def _row(self, r):
        d = dict(r)
        d.update(self._path((d["lat"], d["lon"]) if d["lat"] is not None else None))
        return d

    # ---- operations ------------------------------------------------------------

    def add(self, qso: dict) -> dict:
        call = (qso.get("call") or "").strip().upper()
        if not call:
            raise ValueError("Callsign required")
        utc = qso.get("utc") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        grid = (qso.get("grid") or "").strip()
        entity, pos, approx = self._place(call, grid)
        freq = int(qso.get("freq_hz") or 0)
        row = {k: qso.get(k) for k in FIELDS}
        row.update(call=call, utc=utc, grid=grid.upper()[:2] + grid.lower()[2:] if grid else "",
                   freq_hz=freq, band=qso.get("band") or band_of(freq),
                   country=entity.get("country", ""), prefix=entity.get("prefix", ""),
                   lat=pos[0] if pos else None, lon=pos[1] if pos else None, approx=int(approx))
        cols = ", ".join(row)
        cur = self.db.execute(f"INSERT INTO qso ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, qso_id):
        r = self.db.execute("SELECT * FROM qso WHERE id = ?", (qso_id,)).fetchone()
        return self._row(r) if r else None

    def update(self, qso_id, changes: dict):
        allowed = {k: v for k, v in changes.items() if k in FIELDS}
        if not allowed:
            return self.get(qso_id)
        if "call" in allowed or "grid" in allowed:
            cur = self.get(qso_id) or {}
            call = (allowed.get("call") or cur.get("call") or "").upper()
            grid = allowed.get("grid", cur.get("grid") or "")
            entity, pos, approx = self._place(call, grid)
            allowed.update(call=call, country=entity.get("country", ""), prefix=entity.get("prefix", ""),
                           lat=pos[0] if pos else None, lon=pos[1] if pos else None, approx=int(approx))
        if "freq_hz" in allowed and "band" not in allowed:
            allowed["band"] = band_of(int(allowed["freq_hz"] or 0))
        self.db.execute(f"UPDATE qso SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?",
                        [*allowed.values(), qso_id])
        self.db.commit()
        return self.get(qso_id)

    def set_upload(self, qso_id, status, logid=None):
        self.db.execute("UPDATE qso SET qrz_status = ?, qrz_logid = COALESCE(?, qrz_logid) WHERE id = ?", (status, logid, qso_id))
        self.db.commit()

    def all_rows(self, contest=None):
        q, a = "SELECT * FROM qso", []
        if contest:
            q, a = q + " WHERE contest = ?", [contest]
        return [dict(r) for r in self.db.execute(q + " ORDER BY utc", a)]

    def contests(self):
        return [r["contest"] for r in self.db.execute("SELECT DISTINCT contest FROM qso WHERE contest IS NOT NULL AND contest != '' ORDER BY contest")]

    def next_serial(self, contest):
        r = self.db.execute("SELECT count(*) AS n FROM qso WHERE contest = ?", (contest,)).fetchone()
        return f"{r['n'] + 1:03d}"

    def delete(self, qso_id):
        self.db.execute("DELETE FROM qso WHERE id = ?", (qso_id,))
        self.db.commit()

    def list(self, search="", limit=200):
        q = "SELECT * FROM qso"
        args = []
        if search:
            q += " WHERE call LIKE ? OR country LIKE ? OR name LIKE ? OR notes LIKE ? OR grid LIKE ?"
            args = [f"%{search}%"] * 5
        q += " ORDER BY utc DESC, id DESC LIMIT ?"
        return [self._row(r) for r in self.db.execute(q, [*args, int(limit)])]

    def points(self):
        """Every distinct worked location, for the globe."""
        rows = self.db.execute("SELECT DISTINCT round(lat, 1) AS lat, round(lon, 1) AS lon FROM qso WHERE lat IS NOT NULL")
        return [[r["lon"], r["lat"]] for r in rows]

    def globe(self, since_utc=None, band=None):
        """One entry per callsign (its latest QSO and a QSO count), for the logbook globe."""
        q = "SELECT * FROM qso WHERE lat IS NOT NULL"
        args = []
        if since_utc:
            q += " AND utc >= ?"
            args.append(since_utc)
        if band:
            q += " AND band = ?"
            args.append(band)
        q += " ORDER BY utc"
        latest, counts = {}, {}
        for r in self.db.execute(q, args):
            latest[r["call"]] = r
            counts[r["call"]] = counts.get(r["call"], 0) + 1
        out = []
        for call, r in latest.items():
            d = self._row(r)
            d["count"] = counts[call]
            out.append(d)
        return out

    def stats(self):
        r = self.db.execute("""SELECT count(*) AS qsos, count(DISTINCT NULLIF(country, '')) AS dxcc,
                               count(DISTINCT NULLIF(substr(grid, 1, 4), '')) AS grids FROM qso""").fetchone()
        return dict(r)

    def lookup(self, call, grid="", lat=None, lon=None, region=None):
        """grid / lat+lon / region come from a callbook record when there is one."""
        call = (call or "").strip().upper()
        entity, pos, approx = self._place(call, grid or "")
        last = self.db.execute("SELECT grid, lat, lon, approx FROM qso WHERE call = ? AND grid != '' ORDER BY utc DESC LIMIT 1", (call,)).fetchone()
        if grid and geo.grid_to_latlon(grid):
            pass
        elif lat is not None and lon is not None:
            pos, approx = (lat, lon), False
        elif last:
            pos, approx = (last["lat"], last["lon"]), bool(last["approx"])
        worked = self.db.execute("SELECT count(*) AS n, max(utc) AS last FROM qso WHERE call = ?", (call,)).fetchone()
        bands = [r["band"] for r in self.db.execute("SELECT DISTINCT band FROM qso WHERE call = ? AND band != ''", (call,))]
        entity_worked = self.db.execute("SELECT count(*) AS n FROM qso WHERE country = ? AND country != ''",
                                        (entity.get("country", ""),)).fetchone()["n"]
        out = {"call": call, "country": entity.get("country", ""), "prefix": entity.get("prefix", ""),
               "cont": entity.get("cont", ""), "lat": pos[0] if pos else None, "lon": pos[1] if pos else None,
               "approx": approx, "grid": grid or (last["grid"] if last else ""), "worked": worked["n"],
               "last": worked["last"], "bands": bands, "new_country": bool(entity) and entity_worked == 0}
        out.update(self._path(pos))
        out["local_time"] = station_time(call, region, lon=pos[1] if pos else None)
        return out

    # ---- ADIF ------------------------------------------------------------------

    def export_adif(self, station_call="", my_grid=""):
        def f(name, value):
            value = "" if value is None else str(value)
            return f"<{name}:{len(value)}>{value} " if value else ""
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d %H%M%S")
        out = [f"G7250 Open Control ADIF export\n<ADIF_VER:5>3.1.4 <PROGRAMID:17>G7250-OpenControl <CREATED_TIMESTAMP:15>{now} <EOH>\n"]
        for r in self.db.execute("SELECT * FROM qso ORDER BY utc"):
            t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ")
            mode, sub = _adif_mode(r["mode"])
            rec = (f("CALL", r["call"]) + f("QSO_DATE", t.strftime("%Y%m%d")) + f("TIME_ON", t.strftime("%H%M%S"))
                   + f("BAND", r["band"]) + f("FREQ", f"{r['freq_hz'] / 1e6:.6f}" if r["freq_hz"] else "")
                   + f("MODE", mode) + f("SUBMODE", sub) + f("RST_SENT", r["rst_s"]) + f("RST_RCVD", r["rst_r"])
                   + f("NAME", r["name"]) + f("QTH", r["qth"]) + f("GRIDSQUARE", r["grid"]) + f("COMMENT", r["notes"])
                   + f("COUNTRY", r["country"]) + f("STATION_CALLSIGN", station_call) + f("MY_GRIDSQUARE", my_grid)
                   + f("STX_STRING", r["stx"]) + f("SRX_STRING", r["srx"]) + f("CONTEST_ID", r["contest"]))
            out.append(rec + "<EOR>\n")
        return "".join(out)

    def adif_record(self, qso_id, station_call="", my_grid=""):
        """One QSO as an ADIF record (for the QRZ Logbook upload)."""
        r = self.db.execute("SELECT * FROM qso WHERE id = ?", (qso_id,)).fetchone()
        if not r:
            return ""
        f = lambda n, v: f"<{n}:{len(str(v))}>{v} " if v not in (None, "") else ""
        t = dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ")
        mode, sub = _adif_mode(r["mode"])
        return (f("CALL", r["call"]) + f("QSO_DATE", t.strftime("%Y%m%d")) + f("TIME_ON", t.strftime("%H%M%S")) + f("BAND", r["band"])
                + f("FREQ", f"{r['freq_hz'] / 1e6:.6f}" if r["freq_hz"] else "") + f("MODE", mode) + f("SUBMODE", sub)
                + f("RST_SENT", r["rst_s"]) + f("RST_RCVD", r["rst_r"]) + f("NAME", r["name"]) + f("QTH", r["qth"])
                + f("GRIDSQUARE", r["grid"]) + f("COMMENT", r["notes"]) + f("STATION_CALLSIGN", station_call)
                + f("MY_GRIDSQUARE", my_grid) + "<EOR>")

    def import_adif(self, text):
        body = re.split(r"<eoh>", text, flags=re.I)[-1]
        count = 0
        for record in re.split(r"<eor>", body, flags=re.I):
            fields = {}
            for m in re.finditer(r"<([A-Za-z0-9_]+):(\d+)(?::[A-Za-z])?>", record):
                fields[m.group(1).upper()] = record[m.end():m.end() + int(m.group(2))]
            if not fields.get("CALL"):
                continue
            date, time = fields.get("QSO_DATE", ""), (fields.get("TIME_ON", "") + "000000")[:6]
            try:
                utc = dt.datetime.strptime(date + time, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
            mode = fields.get("SUBMODE") if fields.get("MODE", "").upper() in ("SSB", "MFSK") and fields.get("SUBMODE") else fields.get("MODE", "")
            try:
                freq = int(float(fields.get("FREQ", "0")) * 1e6)
            except ValueError:
                freq = 0
            if self.db.execute("SELECT 1 FROM qso WHERE call = ? AND utc = ?", (fields["CALL"].upper(), utc)).fetchone():
                continue                                      # already in the log
            self.add({"call": fields["CALL"], "utc": utc, "freq_hz": freq, "band": fields.get("BAND", "").lower(),
                      "mode": mode.upper(), "rst_s": fields.get("RST_SENT", ""), "rst_r": fields.get("RST_RCVD", ""),
                      "name": fields.get("NAME", ""), "qth": fields.get("QTH", ""), "grid": fields.get("GRIDSQUARE", ""),
                      "notes": fields.get("COMMENT", ""), "stx": fields.get("STX_STRING", fields.get("STX", "")),
                      "srx": fields.get("SRX_STRING", fields.get("SRX", "")), "contest": fields.get("CONTEST_ID", "")})
            count += 1
        return count
