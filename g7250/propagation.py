"""Solar indices (public N0NBH feed at hamqsl.com) and the sub-solar point for the greyline."""
import asyncio
import datetime as dt
import math
import time
import urllib.request
import xml.etree.ElementTree as ET

FEED = "https://www.hamqsl.com/solarxml.php"


def subsolar_point(when=None):
    """(lat, lon) where the sun is overhead, accurate to a fraction of a degree."""
    t = when or dt.datetime.now(dt.timezone.utc)
    day = t.timetuple().tm_yday
    hours = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (day - 1 + (hours - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))     # minutes
    lon = -15 * (hours - 12 + eot / 60)
    lon = (lon + 180) % 360 - 180
    return math.degrees(decl), lon


class Propagation:
    def __init__(self):
        self.data = {}
        self.updated = 0.0
        self.error = None

    async def refresh(self):
        def fetch():
            req = urllib.request.Request(FEED, headers={"User-Agent": "G7250-OpenControl"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        try:
            raw = await asyncio.get_running_loop().run_in_executor(None, fetch)
            sd = ET.fromstring(raw).find("solardata")
            pick = lambda tag: (sd.findtext(tag) or "").strip()
            self.data = {"sfi": pick("solarflux"), "k": pick("kindex"), "a": pick("aindex"),
                         "ssn": pick("sunspots"), "xray": pick("xray"), "source_updated": pick("updated"),
                         "bands": {f"{b.get('name')} {b.get('time')}": b.text
                                   for b in sd.iter("band") if b.get("name")}}
            self.updated, self.error = time.time(), None
        except Exception as exc:
            self.error = f"{type(exc).__name__}"

    async def run(self, every_s=1800):
        while True:
            await self.refresh()
            await asyncio.sleep(every_s if not self.error else 300)

    def snapshot(self):
        lat, lon = subsolar_point()
        return {**self.data, "subsolar": [round(lon, 2), round(lat, 2)], "error": self.error,
                "updated": self.updated}
