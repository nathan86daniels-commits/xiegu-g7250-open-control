"""Maidenhead locators, great-circle distance and bearing, and callsign -> country (cty.dat)."""
import math
from functools import lru_cache
from pathlib import Path

EARTH_KM = 6371.0
CTY_PATH = Path(__file__).resolve().parent.parent / "data" / "cty.dat"


def grid_to_latlon(grid: str):
    """Centre of a 2/4/6/8-character Maidenhead square, or None."""
    g = (grid or "").strip().upper()
    if len(g) < 2 or len(g) % 2 or not ("A" <= g[0] <= "R" and "A" <= g[1] <= "R"):
        return None
    lon, lat = (ord(g[0]) - 65) * 20 - 180, (ord(g[1]) - 65) * 10 - 90
    w, h = 20.0, 10.0
    try:
        if len(g) >= 4:
            w, h = w / 10, h / 10
            lon, lat = lon + int(g[2]) * w, lat + int(g[3]) * h
        if len(g) >= 6:
            w, h = w / 24, h / 24
            lon, lat = lon + (ord(g[4]) - 65) * w, lat + (ord(g[5]) - 65) * h
        if len(g) >= 8:
            w, h = w / 10, h / 10
            lon, lat = lon + int(g[6]) * w, lat + int(g[7]) * h
    except ValueError:
        return None
    return lat + h / 2, lon + w / 2


def latlon_to_grid(lat, lon, chars=6):
    lon, lat = lon + 180, lat + 90
    g = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    g += str(int(lon % 20 // 2)) + str(int(lat % 10))
    if chars >= 6:
        g += chr(97 + int(lon % 2 * 12)) + chr(97 + int(lat % 1 * 24))
    return g


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(deg):
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][int((deg + 11.25) % 360 // 22.5)]


# ---------------------------------------------------------------- cty.dat

_SUFFIXES = {"P", "M", "MM", "AM", "A", "QRP", "QRPP", "R", "LH", "LGT", "BCN"}


@lru_cache(maxsize=1)
def _cty():
    """AD1C cty.dat: header 'Name: CQ: ITU: Cont: Lat: Lon(W+): GMT(W+): Prefix:' then
    indented alias prefixes; '=' marks an exact callsign."""
    prefixes, exact = {}, {}
    current = None
    try:
        lines = CTY_PATH.read_text(encoding="latin-1").splitlines()
    except OSError:
        return prefixes, exact
    for line in lines:
        if not line.strip():
            continue
        if not line[0].isspace():
            f = line.split(":")
            try:
                current = {"country": f[0].strip(), "cont": f[3].strip(), "lat": float(f[4]),
                           "lon": -float(f[5]), "prefix": f[7].strip().lstrip("*"),
                           "gmt": -float(f[6]) if f[6].strip() else None}
            except (IndexError, ValueError):
                current = None
            continue
        if current is None:
            continue
        for token in line.strip().rstrip(";").split(","):
            token = token.strip()
            if not token:
                continue
            is_call = token.startswith("=")
            key = token[1 if is_call else 0:]
            for ch in "([<{~;":
                key = key.split(ch)[0]
            key = key.strip().upper()
            if key:
                (exact if is_call else prefixes).setdefault(key, current)
    return prefixes, exact


def locate_call(call: str):
    """Country entity for a callsign: dict(country, prefix, cont, lat, lon) or None.
    The position is the entity's reference point, so it is approximate."""
    call = (call or "").strip().upper()
    if not call:
        return None
    prefixes, exact = _cty()
    if call in exact:
        return exact[call]
    parts = [p for p in call.split("/") if p]
    meaningful = [p for p in parts if not (p in _SUFFIXES or (len(p) == 1 and p.isdigit()))] or parts
    for cand in sorted(meaningful, key=len):
        if cand in exact:
            return exact[cand]
        for n in range(len(cand), 0, -1):
            if cand[:n] in prefixes:
                return prefixes[cand[:n]]
    return None
