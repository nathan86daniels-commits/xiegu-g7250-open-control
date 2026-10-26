"""Callbook lookups (QRZ.com XML, HamQTH), QRZ Logbook upload and Cabrillo export.

* QRZ: the official XML data service (xmldata.qrz.com), no page scraping. The session key is
  renewed when QRZ expires it; good lookups are cached on disk for 30 days. QRZ only gives grid
  and coordinates to XML subscribers, so a station's position comes, in order, from QRZ, HamQTH
  (the station's own published position, any registered user), then a town/state estimate
  (OpenStreetMap Nominatim, marked approximate).
* QRZ Logbook: ACTION=INSERT only. OPTION=REPLACE is never sent - QRZ warns it overwrites
  confirmed QSOs with the unconfirmed copy - so a duplicate is reported and left alone.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from . import geo

AGENT = "G7250-OpenControl/0.2"
QRZ_XML = "https://xmldata.qrz.com/xml/current/"
HAMQTH = "https://www.hamqth.com/xml.php"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
QRZ_LOGBOOK = "https://logbook.qrz.com/api"
CACHE_DAYS = 30
UK_NATIONS = {"England", "Scotland", "Wales", "Northern Ireland"}


def plausible_call(call):
    c = (call or "").strip()
    return 3 <= len(c) <= 20 and all(ch.isalnum() or ch == "/" for ch in c) and any(ch.isalpha() for ch in c) and any(ch.isdigit() for ch in c)


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _xml_text(root, name):
    """Namespaced replies: match on local names anywhere."""
    if root is None:
        return None
    for e in root.iter():
        if e.tag.split("}")[-1] == name and e.text and e.text.strip():
            return e.text.strip()
    return None


def _child(root, name):
    for e in list(root):
        if e.tag.split("}")[-1] == name:
            return e
    return None


class Callbook:
    def __init__(self, data_dir: Path, settings):
        self.path = Path(data_dir) / "callbook-cache.json"
        self.settings = settings           # callable -> station settings dict
        try:
            self.cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self._qrz_key = self._qrz_user = None
        self._ham_session = self._ham_user = None
        self.ham_problem = None
        self._last_nominatim = 0.0

    def _store(self, rec):
        self.cache[rec["call"].upper()] = rec
        try:
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")
        except OSError:
            pass

    # ---------------------------------------------------------------- QRZ
    def _qrz_login(self, user, password):
        root = ET.fromstring(_get(f"{QRZ_XML}?username={urllib.parse.quote(user)};password={urllib.parse.quote(password)};agent={urllib.parse.quote(AGENT)}"))
        session = _child(root, "Session")
        key = _xml_text(session, "Key")
        if not key:
            err = _xml_text(session, "Error") or "no session returned"
            raise PermissionError("QRZ rejected the username or password." if "password" in err.lower() or "username" in err.lower() else f"QRZ login failed: {err}")
        self._qrz_key, self._qrz_user = key, user

    def lookup(self, call, use_cache=True):
        """{"outcome": found|not_found|no_account|auth_failed|offline|invalid|error, "record": {...}|None, "message": str}"""
        call = (call or "").strip().upper()
        if not plausible_call(call):
            return {"outcome": "invalid", "record": None, "message": "That doesn't look like a callsign."}
        hit = self.cache.get(call)
        if use_cache and hit and time.time() - hit.get("fetched", 0) < CACHE_DAYS * 86400:
            return {"outcome": "found", "record": hit, "message": "From the local cache."}
        q = self.settings().get("qrz", {})
        if not q.get("username") or not q.get("password"):
            # no QRZ account: HamQTH alone still gives name and position
            ham = self._hamqth(call)
            if ham:
                rec = {"call": call, "name": ham.get("name"), "qth": ham.get("qth"), "region": None, "country": ham.get("country"),
                       "grid": ham.get("grid"), "lat": ham.get("lat"), "lon": ham.get("lon"), "source": "HamQTH",
                       "approx": False, "partial": False, "fetched": time.time()}
                self._store(rec)
                return {"outcome": "found", "record": rec, "message": "Found on HamQTH."}
            return {"outcome": "no_account", "record": None, "message": "Add your QRZ (or HamQTH) login in Settings to look callsigns up."}
        try:
            for _ in range(2):
                if self._qrz_key is None or self._qrz_user != q["username"]:
                    self._qrz_login(q["username"], q["password"])
                root = ET.fromstring(_get(f"{QRZ_XML}?s={urllib.parse.quote(self._qrz_key)};callsign={urllib.parse.quote(call)}"))
                session, cs = _child(root, "Session"), _child(root, "Callsign")
                err = _xml_text(session, "Error")
                if err and ("Session Timeout" in err or "Invalid session key" in err):
                    self._qrz_key = None
                    continue
                if err and err.lower().startswith("not found"):
                    return {"outcome": "not_found", "record": None, "message": f"{call} isn't on QRZ."}
                if cs is None:
                    return {"outcome": "error", "record": None, "message": err or "QRZ returned no record."}
                rec = self._parse_qrz(cs, _xml_text(session, "Message"))
                rec = self._with_location(rec)
                self._store(rec)
                msg = ("Found on QRZ; location from HamQTH." if rec.get("source") == "HamQTH"
                       else "Location estimated from the " + ("state/province" if rec.get("approx_from") == "region" else "town") + " - QRZ gave no grid."
                       if rec.get("approx") else "QRZ returned a limited record - an XML subscription gives location details."
                       if rec.get("partial") and rec.get("lat") is None else "Found on QRZ.")
                return {"outcome": "found", "record": rec, "message": msg}
            return {"outcome": "error", "record": None, "message": "QRZ kept rejecting the session. Try again shortly."}
        except PermissionError as e:
            self._qrz_key = None
            return {"outcome": "auth_failed", "record": None, "message": str(e)}
        except (urllib.error.URLError, OSError, TimeoutError):
            return {"outcome": "offline", "record": None, "message": "Couldn't reach QRZ - check the internet connection."}
        except ET.ParseError as e:
            return {"outcome": "error", "record": None, "message": f"QRZ lookup failed: {e}"}

    @staticmethod
    def _parse_qrz(c, message):
        t = lambda n: _xml_text(c, n)
        num = lambda n: float(t(n)) if t(n) not in (None, "") else None
        name = " ".join(x for x in (t("fname"), t("name")) if x) or None
        region = ", ".join(x for x in (t("county"), t("state")) if x) or None
        try:
            lat, lon = num("lat"), num("lon")
        except ValueError:
            lat = lon = None
        grid = t("grid")
        if (lat is None or lon is None) and grid:
            ll = geo.grid_to_latlon(grid)
            lat, lon = ll if ll else (None, None)
        return {"call": (t("call") or "").upper(), "name": name, "qth": t("addr2"), "region": region, "state": t("state"),
                "country": t("country") or t("land"), "dxcc": t("dxcc"), "grid": grid, "lat": lat, "lon": lon,
                "class": t("class"), "image": t("image"), "email": t("email"),
                "partial": bool(message and "subscription" in message.lower()), "approx": False, "source": "QRZ",
                "fetched": time.time()}

    def _with_location(self, rec):
        if rec.get("lat") is not None and rec.get("lon") is not None:
            return rec
        ham = self._hamqth(rec["call"])
        if ham and (ham.get("lat") is not None or ham.get("grid")):
            lat, lon = ham.get("lat"), ham.get("lon")
            if lat is None or (lat == 0 and lon == 0):
                ll = geo.grid_to_latlon(ham.get("grid"))
                lat, lon = ll if ll else (None, None)
            if lat is not None:
                return {**rec, "lat": lat, "lon": lon, "grid": rec.get("grid") or ham.get("grid"), "source": "HamQTH", "approx": False}
        if not rec.get("country") or not (rec.get("qth") or rec.get("region")):
            return rec
        country, region = rec["country"].strip(), rec.get("region")
        if country in UK_NATIONS:
            country, region = "United Kingdom", region or rec["country"].strip()
        place = self._geocode(rec.get("qth"), region, country)
        if not place:
            return rec
        lat, lon, precision = place
        return {**rec, "lat": lat, "lon": lon, "approx": True, "approx_from": precision,
                "grid": rec.get("grid") or geo.latlon_to_grid(lat, lon)[:4]}

    def _geocode(self, town, region, country):
        """Town (or state) centre from OpenStreetMap Nominatim; 1 request/second as its policy asks."""
        for query, precision in (((town, region, country), "town"), ((region, country), "region")):
            parts = [p for p in query if p]
            if len(parts) < 2:
                continue
            wait = 1.1 - (time.time() - self._last_nominatim)
            if wait > 0:
                time.sleep(wait)
            self._last_nominatim = time.time()
            try:
                rows = json.loads(_get(f"{NOMINATIM}?format=json&limit=1&q={urllib.parse.quote(', '.join(parts))}"))
            except Exception:
                return None
            if rows:
                return float(rows[0]["lat"]), float(rows[0]["lon"]), precision
        return None

    # ---------------------------------------------------------------- HamQTH
    def _hamqth(self, call):
        h = self.settings().get("hamqth", {})
        if not h.get("username") or not h.get("password"):
            return None
        try:
            for _ in range(2):
                if self._ham_session is None or self._ham_user != h["username"]:
                    root = ET.fromstring(_get(f"{HAMQTH}?u={urllib.parse.quote(h['username'])}&p={urllib.parse.quote(h['password'])}", 10))
                    self._ham_session = _xml_text(root, "session_id")
                    if not self._ham_session:
                        err = _xml_text(root, "error") or ""
                        self.ham_problem = "HamQTH rejected the username or password." if "password" in err.lower() else None
                        return None
                    self._ham_user, self.ham_problem = h["username"], None
                root = ET.fromstring(_get(f"{HAMQTH}?id={urllib.parse.quote(self._ham_session)}&callsign={urllib.parse.quote(call)}&prg=G7250OpenControl", 10))
                err = _xml_text(root, "error")
                if err and "session" in err.lower():
                    self._ham_session = None
                    continue
                if err:
                    return None
                num = lambda n: float(_xml_text(root, n)) if _xml_text(root, n) else None
                return {"call": _xml_text(root, "callsign") or call, "name": _xml_text(root, "adr_name") or _xml_text(root, "nick"),
                        "qth": _xml_text(root, "qth"), "country": _xml_text(root, "country"), "grid": _xml_text(root, "grid"),
                        "lat": num("latitude"), "lon": num("longitude")}
        except Exception:
            return None
        return None

    # ---------------------------------------------------------------- QRZ Logbook
    def qrz_upload(self, adif_record, my_call=""):
        key = self.settings().get("qrz", {}).get("logbook_key", "")
        if not key:
            return {"outcome": "rejected", "message": "No QRZ Logbook API key set in Settings."}
        reply = self._qrz_post({"KEY": key, "ACTION": "INSERT", "ADIF": adif_record}, my_call)
        if reply is None:
            return {"outcome": "offline", "message": "Couldn't reach QRZ."}
        result, reason = reply.get("RESULT", "").upper(), self._clean(reply.get("REASON", ""))
        if result in ("OK", "REPLACE"):
            return {"outcome": "sent", "logid": reply.get("LOGID"), "message": "Sent to your QRZ logbook."}
        if "duplicate" in reason.lower():
            return {"outcome": "already", "message": "Already in your QRZ logbook - left as it is."}
        if "subscription" in reason.lower():
            return {"outcome": "needs_subscription", "message": "QRZ Logbook upload needs a QRZ XML subscription (or higher)."}
        if result == "AUTH":
            return {"outcome": "rejected", "message": f"QRZ didn't accept the Logbook API key ({reason})."}
        return {"outcome": "rejected", "message": "QRZ refused the QSO: " + (reason or "no reason given.")}

    def _qrz_post(self, fields, my_call):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(QRZ_LOGBOOK, data=data, headers={"User-Agent": f"{AGENT}{f' ({my_call})' if my_call else ''}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
        return {k.upper(): v for k, v in urllib.parse.parse_qsl(body, keep_blank_values=True)}

    @staticmethod
    def _clean(reason):
        at = reason.upper().rfind("REASON:")
        if at >= 0:
            reason = reason[at + 7:]
        end = reason.upper().find("EXTENDED:")
        return (reason[:end] if end >= 0 else reason).strip()


# ---------------------------------------------------------------- Cabrillo 3.0

def cabrillo_mode(mode):
    m = (mode or "").upper()
    if m in ("FM", "FM-N", "C4FM"):
        return "FM"
    if m.startswith("RTTY"):
        return "RY"
    if m in ("CW", "CW-R"):
        return "CW"
    if m in ("USB", "LSB", "SSB", "AM"):
        return "PH"
    return "DG"


def cabrillo_freq(hz):
    if hz < 30_000_000:
        return str(hz // 1000)
    mhz = hz / 1e6
    return "50" if mhz < 54 else "70" if mhz < 72 else "144" if mhz < 148 else "432" if 420 <= mhz < 450 else str(int(mhz))


def cabrillo(details, qsos):
    """details: contest_id, callsign, operators, name, email, club, grid, location, operator, band, mode, power, assisted, soapbox."""
    d = {"operator": "SINGLE-OP", "band": "ALL", "mode": "MIXED", "power": "HIGH", "assisted": "NON-ASSISTED", **details}
    lines = []
    def tag(name, value):
        value = "".join(c if " " <= c <= "~" else " " for c in str(value or "")).strip()
        if value:
            lines.append(f"{name}: {value}")
    call = d.get("callsign", "").strip().upper()
    tag("START-OF-LOG", "3.0"); tag("CREATED-BY", "G7250 Open Control")
    tag("CONTEST", d.get("contest_id", "").upper()); tag("CALLSIGN", call)
    tag("CATEGORY-OPERATOR", d["operator"]); tag("CATEGORY-ASSISTED", d["assisted"]); tag("CATEGORY-BAND", d["band"])
    tag("CATEGORY-MODE", d["mode"]); tag("CATEGORY-POWER", d["power"])
    tag("CATEGORY-TRANSMITTER", "" if d["operator"] == "MULTI-OP" else "ONE")
    tag("LOCATION", d.get("location", "").upper()); tag("GRID-LOCATOR", d.get("grid", "").upper()); tag("CLUB", d.get("club", ""))
    tag("OPERATORS", (d.get("operators") or call).upper()); tag("NAME", d.get("name", "")); tag("EMAIL", d.get("email", ""))
    for s in (d.get("soapbox") or "").split("\n"):
        tag("SOAPBOX", s)
    words = lambda *parts: " ".join(w for p in parts for w in (p or "").upper().split())
    for q in sorted(qsos, key=lambda q: q["utc"]):
        when = q["utc"].replace("T", " ").replace("Z", "")
        date, hhmm = when[:10], when[11:13] + when[14:16]
        sent, rcvd = words(q.get("rst_s"), q.get("stx")), words(q.get("rst_r"), q.get("srx"))
        lines.append(f"QSO: {cabrillo_freq(q.get('freq_hz') or 0):>5} {cabrillo_mode(q.get('mode'))} {date} {hhmm} "
                     f"{call:<13} {sent:<10} {q['call'].upper():<13} {rcvd}".rstrip())
    lines.append("END-OF-LOG:")
    return "\r\n".join(lines) + "\r\n"
