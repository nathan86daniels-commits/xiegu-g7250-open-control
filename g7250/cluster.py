"""Read-only DX cluster client (telnet). Logs in with the operator's callsign and reads
"DX de" spot lines; never posts anything. Reconnects with back-off if the node drops."""
import asyncio
import random
import re
import time
from collections import deque

from . import geo

SPOT_RE = re.compile(r"^DX de\s+([A-Z0-9/\-#_]+?)[-#]*:?\s+(\d+(?:\.\d+)?)\s+([A-Z0-9/]+)\s+(.*?)\s*(\d{4})Z", re.I)


def parse_spot(line):
    m = SPOT_RE.match(line.strip())
    if not m:
        return None
    try:
        khz = float(m.group(2))
    except ValueError:
        return None
    dx = m.group(3).upper()
    entity = geo.locate_call(dx) or {}
    return {"time": time.time(), "utc": f"{m.group(5)[:2]}:{m.group(5)[2:]}", "freq_hz": int(round(khz * 1000)),
            "dx": dx, "spotter": m.group(1).upper().rstrip("-#"), "note": m.group(4).strip()[:40],
            "country": entity.get("country", ""), "prefix": entity.get("prefix", ""),
            "lat": entity.get("lat"), "lon": entity.get("lon")}


class DxCluster:
    def __init__(self, max_age_s=1800):
        self.spots = deque(maxlen=400)
        self.status = "Off"
        self.max_age_s = max_age_s
        self._task = None

    def recent(self):
        cutoff = time.time() - self.max_age_s
        return [s for s in reversed(self.spots) if s["time"] >= cutoff]

    def _add(self, spot):
        # one entry per DX per ~1 kHz: newest wins
        for old in list(self.spots):
            if old["dx"] == spot["dx"] and abs(old["freq_hz"] - spot["freq_hz"]) < 1000:
                self.spots.remove(old)
        self.spots.append(spot)

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self.status = "Off"

    def start(self, host, port, callsign):
        self.stop()
        self._task = asyncio.create_task(self._run(host, int(port), callsign.strip().upper()))

    def start_simulated(self, band):
        """Spots for the simulator's own signals, so the cluster panel can be tried offline."""
        self.stop()
        self._task = asyncio.create_task(self._simulate(band))

    async def _run(self, host, port, call):
        backoff = 5
        while True:
            try:
                self.status = f"Connecting to {host}:{port}"
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 12)
                await self._session(reader, writer, call, host)
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = f"Lost ({type(exc).__name__}); retrying in {backoff} s"
            await asyncio.sleep(backoff)
            backoff = min(120, backoff * 2)

    async def _session(self, reader, writer, call, host):
        logged_in = False
        buf = b""
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(4096), 240)
                except asyncio.TimeoutError:
                    writer.write(b"\r\n")                 # keep-alive
                    await writer.drain()
                    continue
                if not data:
                    raise ConnectionError("node closed the connection")
                buf += data.replace(b"\xff", b"")
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    spot = parse_spot(raw.decode("ascii", "replace"))
                    if spot:
                        self._add(spot)
                pending = buf.decode("ascii", "replace").lower()
                if not logged_in and ("login" in pending or "call" in pending or pending.rstrip().endswith(":")):
                    writer.write(call.encode() + b"\r\n")
                    await writer.drain()
                    logged_in = True
                    buf = b""
                    self.status = f"Connected to {host} as {call}"
                    await asyncio.sleep(1.5)
                    writer.write(b"sh/dx 50\r\n")
                    await writer.drain()
        finally:
            writer.close()

    async def _simulate(self, band):
        self.status = "Simulated spots"
        calls = ["EA8SIM", "VK2SIM", "K3SIM", "9H1SIM", "JA1SIM", "ZS6SIM", "DL1SIM", "PY2SIM",
                 "OH2SIM", "W1SIM", "LU1SIM", "ZL2SIM", "VE3SIM", "I2SIM", "SP5SIM"]
        spotters = ["G4SIM", "EA8SIM", "DL8SIM", "IK2SIM", "N1SIM", "OH6SIM", "F5SIM"]
        rng = random.Random(7250)
        candidates = [s for s in band.signals if type(s).__name__ in ("_Ft8", "_Cw")]
        while True:
            sig = rng.choice(candidates)
            cw = type(sig).__name__ == "_Cw"
            t = time.gmtime()
            khz = sig.freq / 1000
            line = (f"DX de {rng.choice(spotters)}:   {khz:8.1f}  {rng.choice(calls):<12} "
                    f"{'CW ' + str(rng.randint(12, 32)) + ' dB 24 WPM' if cw else 'FT8 ' + str(rng.randint(-20, 5)) + ' dB'}"
                    f"      {t.tm_hour:02d}{t.tm_min:02d}Z")
            spot = parse_spot(line)
            if spot:
                self._add(spot)
            await asyncio.sleep(rng.uniform(1.5, 6.0) if len(self.spots) < 20 else rng.uniform(8, 25))
