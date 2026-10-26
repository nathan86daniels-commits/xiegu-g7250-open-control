"""MUF(3000 km) map data: KC2G contour lines + current ionosonde readings, and the MUF
everywhere on a 1-degree grid rebuilt from those lines (KC2G publishes lines, not a grid).

How it works:
  * KC2G's lines keep the higher MUF on their LEFT; a vote over the data confirms it on
    every build, so a source that flips its lines still comes out the right way up.
  * Each grid point takes, per level, the nearest line's side and distance; the value is the
    split that disagrees least with the sides (nearer lines count more), interpolated by
    distance between the levels either side, easing past the outermost line.
  * Conditional GET with If-Modified-Since only: KC2G's server sends a "-gzip" ETag that
    never matches, which also makes it ignore If-Modified-Since when an ETag is sent.
Data: KC2G (prop.kc2g.com), GIRO ionosondes (CC BY-NC-SA 4.0).
"""
import asyncio
import base64
import datetime as dt
import email.utils
import json
import time
import urllib.error
import urllib.request

import numpy as np

CONTOURS_URL = "https://prop.kc2g.com/renders/current/mufd-normal-now.geojson"
STATIONS_URL = "https://prop.kc2g.com/api/stations.json"
BAND_LEVELS = [(5.3, "60m"), (7.0, "40m"), (10.1, "30m"), (14.0, "20m"), (18.0, "17m"),
               (21.0, "15m"), (24.8, "12m"), (28.0, "10m")]
STATION_MAX_AGE_S = 3600
MIN_CONFIDENCE = 25
MAP_STALE_S = 45 * 60
COLUMNS, ROWS = 360, 181
FIELD_SCALE = 6.0                 # field bytes = MHz * 6 (0..42.5 MHz)


def band_for(mhz):
    for level, band in BAND_LEVELS:
        if abs(level - mhz) < 0.35:
            return band
    return f"{mhz:.1f} MHz"


def unit(lat, lon):
    f, l = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(f) * np.sin(l), np.sin(f), np.cos(f) * np.cos(l)], axis=-1)


def parse_contours(text):
    doc = json.loads(text)
    levels = {}
    for feat in doc.get("features", []):
        try:
            mhz = float(feat["properties"]["level-value"])
            geom = feat["geometry"]
        except (KeyError, TypeError, ValueError):
            continue
        lines = levels.setdefault(mhz, [])
        coords = geom.get("coordinates", [])
        runs = [coords] if geom.get("type") == "LineString" else coords if geom.get("type") == "MultiLineString" else []
        for run in runs:
            pts = [(float(p[0]), float(p[1])) for p in run if len(p) >= 2]
            if len(pts) > 1:
                lines.append(pts)
    return [(mhz, lines) for mhz, lines in sorted(levels.items()) if lines]


def _num(v):
    return float(v) if not isinstance(v, str) else float(v.strip())


def parse_stations(text):
    out = []
    for row in json.loads(text):
        try:
            if not isinstance(row.get("mufd"), (int, float)):
                continue
            st = row["station"]
            t = dt.datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            cs = row.get("cs")
            out.append({"name": st.get("name", ""), "lat": _num(st["latitude"]), "lon": ((_num(st["longitude"]) + 180) % 360) - 180,
                        "muf": float(row["mufd"]), "time": t.timestamp(), "cs": float(cs) if isinstance(cs, (int, float)) else None})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def current_stations(stations, now):
    return [s for s in stations if now - s["time"] <= STATION_MAX_AGE_S and s["time"] <= now + 600
            and 0 < s["muf"] < 80 and (s["cs"] is None or s["cs"] < 0 or s["cs"] >= MIN_CONFIDENCE)]


# ---------------------------------------------------------------- the field

class _Level:
    def __init__(self, mhz, lines):
        self.mhz = mhz
        pts, ids = [], []
        for k, line in enumerate(lines):
            for lon, lat in line:
                pts.append((lat, lon))
                ids.append(k)
        ll = np.array(pts)
        self.points = unit(ll[:, 0], ll[:, 1])
        self.line = np.array(ids)
        n = len(self.points)
        idx = np.arange(n)
        self.has_prev = (idx > 0) & (np.roll(self.line, 1) == self.line)
        self.has_next = (idx < n - 1) & (np.roll(self.line, -1) == self.line)

    def nearest(self, c):
        """(radians, left) for each row of c (M x 3) to this level's nearest line."""
        P = self.points
        dots = c @ P.T                                  # M x N
        i = np.argmax(dots, axis=1)
        best_dot = dots[np.arange(len(c)), i]
        corner_angle = np.arccos(np.clip(best_dot, -1, 1))
        best_angle = np.full(len(c), np.inf)
        left = np.zeros(len(c), bool)
        corner = np.zeros_like(c)
        for valid, j in ((self.has_prev[i], i - 1), (self.has_next[i], i + 1)):
            j = np.clip(j, 0, len(P) - 1)
            a = np.where((j < i)[:, None], P[j], P[i])
            b = np.where((j < i)[:, None], P[i], P[j])
            n = np.cross(a, b)
            ln = np.linalg.norm(n, axis=1)
            ok = valid & (ln > 1e-9)
            n = n / np.where(ln > 1e-9, ln, 1)[:, None]
            corner += np.where(ok[:, None], n, 0)
            along = ok & (np.einsum("ij,ij->i", np.cross(a, c), n) >= 0) & (np.einsum("ij,ij->i", np.cross(c, b), n) >= 0)
            side = np.einsum("ij,ij->i", c, n)
            ang = np.arcsin(np.minimum(1, np.abs(side)))
            better = along & (ang < best_angle)
            best_angle = np.where(better, ang, best_angle)
            left = np.where(better, side > 0, left)
        none = ~np.isfinite(best_angle)
        best_angle = np.where(none, corner_angle, best_angle)
        left = np.where(none, np.einsum("ij,ij->i", c, corner) > 0, left)
        return best_angle, left

    def left_normals(self, idx):
        P = self.points
        before = np.where(self.has_prev[idx][:, None], P[np.clip(idx - 1, 0, None)], P[idx])
        after = np.where(self.has_next[idx][:, None], P[np.clip(idx + 1, None, len(P) - 1)], P[idx])
        n = np.cross(P[idx], after - before)
        ln = np.linalg.norm(n, axis=1)
        return n / np.where(ln > 0, ln, 1)[:, None]


def _higher_on_left(levels):
    if len(levels) < 2:
        return True
    votes = 0
    for i, lev in enumerate(levels):
        idx = np.arange(0, len(lev.points), 5)
        v, normal = lev.points[idx], lev.left_normals(idx)
        for side in (-1, 1):
            q = v + normal * (0.03 * side)
            q /= np.linalg.norm(q, axis=1)[:, None]
            nearest = np.full(len(q), np.inf)
            other = np.zeros(len(q))
            for k, lk in enumerate(levels):
                if k == i:
                    continue
                d, _ = lk.nearest(q)
                better = d < nearest
                nearest = np.where(better, d, nearest)
                other = np.where(better, lk.mhz, other)
            votes += int(np.sum(np.where(other > lev.mhz, 1, -1) * side))
    return votes >= 0


def _next_band(mhz, direction):
    lv = [m for m, _ in BAND_LEVELS]
    if direction < 0:
        below = [l for l in lv if l < mhz - 0.35]
        return below[-1] if below else None
    above = [l for l in lv if l > mhz + 0.35]
    return above[0] if above else None


def build_field(contours):
    """MUF (MHz) on a ROWS x COLUMNS grid: lat 90..-90, lon -180..179. None without data."""
    levels = [_Level(m, lines) for m, lines in contours if lines]
    if not levels:
        return None
    higher_left = _higher_on_left(levels)
    floor = _next_band(levels[0].mhz, -1) or max(1.5, levels[0].mhz - 3)
    ceiling = _next_band(levels[-1].mhz, +1) or levels[-1].mhz + 4
    lat = 90 - np.arange(ROWS)
    lon = np.arange(COLUMNS) - 180
    grid = unit(np.repeat(lat, COLUMNS).astype(float), np.tile(lon, ROWS).astype(float))
    n = len(levels)
    dist = np.zeros((len(grid), n))
    above = np.zeros((len(grid), n), bool)
    for k, lev in enumerate(levels):
        for s in range(0, len(grid), 4096):
            d, left = lev.nearest(grid[s:s + 4096])
            dist[s:s + 4096, k] = d
            above[s:s + 4096, k] = left == higher_left
    w = 1 / (dist + 0.01)
    # cost(k) = sum_{i<k} w*(not above) + sum_{i>=k} w*above ; choose the least
    below_cost = np.concatenate([np.zeros((len(grid), 1)), np.cumsum(w * ~above, axis=1)], axis=1)
    above_cost = np.concatenate([np.cumsum((w * above)[:, ::-1], axis=1)[:, ::-1], np.zeros((len(grid), 1))], axis=1)
    split = np.argmin(below_cost + above_cost, axis=1)
    mhz_levels = np.array([l.mhz for l in levels])
    ease = lambda r: 1 - np.exp(-r / 0.12)
    out = np.empty(len(grid))
    lo_mask, hi_mask = split == 0, split == n
    out[lo_mask] = mhz_levels[0] - 0.9 * (mhz_levels[0] - floor) * ease(dist[lo_mask, 0])
    out[hi_mask] = mhz_levels[-1] + 0.9 * (ceiling - mhz_levels[-1]) * ease(dist[hi_mask, -1])
    mid = ~(lo_mask | hi_mask)
    k = split[mid]
    rows = np.nonzero(mid)[0]
    lo, hi = mhz_levels[k - 1], mhz_levels[k]
    d_lo, d_hi = dist[rows, k - 1], dist[rows, k]
    out[mid] = lo + (hi - lo) * d_lo / np.maximum(1e-9, d_lo + d_hi)
    return out.reshape(ROWS, COLUMNS)


# ---------------------------------------------------------------- the feed

class MufService:
    def __init__(self):
        self.contours = []
        self.stations = []
        self.field = None
        self.map_time = None           # KC2G Last-Modified (epoch)
        self.last_check = None
        self.last_ok = None
        self.offline = False
        self.error = None
        self._modified = None
        self._payload = None

    def _get(self, url, conditional):
        headers = {"User-Agent": "G7250-OpenControl", "Accept-Encoding": "identity"}
        if conditional and self._modified:
            headers["If-Modified-Since"] = self._modified
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read(8 * 1024 * 1024).decode("utf-8", "replace"), resp.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, None, None
            raise

    async def refresh(self):
        loop = asyncio.get_running_loop()
        self.last_check = time.time()
        try:
            status, body, modified = await loop.run_in_executor(None, self._get, CONTOURS_URL, True)
            if status == 200:
                contours = parse_contours(body)          # only a feed that parses is kept
                field = await loop.run_in_executor(None, build_field, contours)
                self.contours, self.field = contours, field
                self._modified = modified
                self.map_time = email.utils.parsedate_to_datetime(modified).timestamp() if modified else None
            _, sbody, _ = await loop.run_in_executor(None, self._get, STATIONS_URL, False)
            self.stations = parse_stations(sbody)
            self.last_ok, self.offline, self.error = time.time(), False, None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self.offline, self.error = True, type(exc).__name__
        except Exception as exc:                            # bad data / HTTP error
            self.offline, self.error = False, f"KC2G ERROR ({type(exc).__name__})"
        self._payload = None

    async def run(self):
        while True:
            await self.refresh()
            await asyncio.sleep(900 if not (self.error or self.offline) else 300)

    def state_text(self):
        now = time.time()
        if not self.contours:
            return "OFFLINE, NO MAP" if self.offline else ("LOADING" if self.error is None else self.error)
        if self.offline:
            return "OFFLINE, SAVED MAP"
        if self.error:
            return "KC2G ERROR, SAVED MAP"
        if self.map_time is None:
            return "MAP TIME UNKNOWN"
        if self.last_ok and now - self.last_ok < MAP_STALE_S and now - self.map_time > MAP_STALE_S:
            return "KC2G NOT UPDATING"
        if now - self.map_time > MAP_STALE_S:
            return "OUT OF DATE"
        return "CURRENT"

    def payload(self):
        if self._payload is None:
            self._payload = {
                "contours": [{"mhz": m, "band": band_for(m), "lines": [[[round(x, 2), round(y, 2)] for x, y in line] for line in lines]}
                             for m, lines in self.contours],
                "field": base64.b64encode(np.clip(np.round(self.field * FIELD_SCALE), 0, 255).astype(np.uint8).tobytes()).decode()
                if self.field is not None else None,
                "field_scale": FIELD_SCALE, "columns": COLUMNS, "rows": ROWS,
                "bands": [{"mhz": m, "band": b} for m, b in BAND_LEVELS],
            }
        now = time.time()
        return {**self._payload, "stations": current_stations(self.stations, now), "map_time": self.map_time,
                "state": self.state_text(), "attribution": "Data: KC2G, prop.kc2g.com · GIRO ionosondes"}
