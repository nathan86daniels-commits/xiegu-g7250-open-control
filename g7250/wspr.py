"""WSPR reports from wspr.live, the public ClickHouse mirror of WSPRnet (free, no key).

"Heard me": every report of our callsign as a transmitter. "I heard": every report we
uploaded as a receiver. Read-only HTTP GET. The callsign is checked to be plain letters,
digits and '/' before it goes into the query.
"""
import asyncio
import email.utils
import json
import re
import time
import urllib.parse
import urllib.request

ENDPOINT = "https://db1.wspr.live/"
DIALS = {"160m": 1_836_600, "80m": 3_568_600, "60m": 5_287_200, "40m": 7_038_600, "30m": 10_138_700,
         "20m": 14_095_600, "17m": 18_104_600, "15m": 21_094_600, "12m": 24_924_600, "10m": 28_124_600,
         "6m": 50_293_000}
_SAFE = re.compile(r"^[A-Z0-9/]{3,12}$")


def band_name(hz):
    for name, dial in DIALS.items():
        if abs(hz - dial) < 20_000:
            return name
    return f"{hz / 1e6:.3f}"


def _query(column, call, hours):
    other = "rx" if column == "tx_sign" else "tx"
    return (f"SELECT time, {other}_sign AS sign, {other}_loc AS loc, {other}_lat AS lat, {other}_lon AS lon, "
            f"frequency, snr, distance, power FROM wspr.rx WHERE {column} = '{call}' "
            f"AND time > subtractHours(now(), {hours}) ORDER BY time DESC LIMIT 2000 FORMAT JSONEachRow")


class WsprReports:
    def __init__(self):
        self.cache = {}            # (call, hours) -> (fetched_at, result)
        self.min_interval = 120    # wspr.live updates every couple of minutes; don't hammer it

    def _fetch(self, column, call, hours):
        url = ENDPOINT + "?query=" + urllib.parse.quote(_query(column, call, hours))
        req = urllib.request.Request(url, headers={"User-Agent": "G7250-OpenControl"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            date = resp.headers.get("Date")
        spots = []
        for line in body.splitlines():
            try:
                r = json.loads(line)
                spots.append({"time": r["time"], "call": r["sign"], "loc": r["loc"], "lat": float(r["lat"]),
                              "lon": float(r["lon"]), "freq_hz": int(r["frequency"]), "band": band_name(int(r["frequency"])),
                              "snr": int(r["snr"]), "km": int(r["distance"]), "dbm": int(r["power"])})
            except (ValueError, KeyError, TypeError):
                continue
        server = email.utils.parsedate_to_datetime(date).timestamp() if date else None
        return spots, server

    async def get(self, callsign, hours=24, force=False):
        call = (callsign or "").strip().upper()
        if not _SAFE.match(call):
            raise ValueError("Set your callsign in Settings first")
        hours = max(1, min(72, int(hours)))
        key = (call, hours)
        cached = self.cache.get(key)
        if cached and not force and time.time() - cached[0] < self.min_interval:
            return cached[1]
        loop = asyncio.get_running_loop()
        heard_me, server = await loop.run_in_executor(None, self._fetch, "tx_sign", call, hours)
        i_heard, _ = await loop.run_in_executor(None, self._fetch, "rx_sign", call, hours)
        result = {"call": call, "hours": hours, "heard_me": heard_me, "i_heard": i_heard,
                  "fetched": time.time(), "clock_offset_s": round(time.time() - server, 1) if server else None}
        self.cache[key] = (time.time(), result)
        return result
