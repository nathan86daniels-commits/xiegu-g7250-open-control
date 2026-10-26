"""WSPR on the station: receive (decode every 2-minute slot from the IQ stream) and transmit
(a beacon at the even minute through the radio's USB audio).

Transmit safety:
  * PTT is held through the station's PttGuard ("wspr" owner) and released FIRST in every
    exit path, before anything is restored; a hard limit unkeys 5 s after the message
    should have ended even if this loop stalls.
  * Early stop: cancel, CAT lost, transmit released elsewhere, mode changed on the radio,
    no power output for 3 s, or SWR above 3:1 (optional).
  * Frequency, mode and power are put back afterwards.
The G7250's audio input source for DIG/USB cannot be set over CI-V (no published command):
the radio must already take its transmit audio from USB.
"""
import asyncio
import datetime as dt
import json
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from . import geo, wspr_codec
from .cat import po_watts, swr_value
from .demod import Demodulator

DIALS = {"160m": 1_836_600, "80m": 3_568_600, "60m": 5_287_200, "40m": 7_038_600, "30m": 10_138_700,
         "20m": 14_095_600, "17m": 18_104_600, "15m": 21_094_600, "12m": 24_924_600, "10m": 28_124_600,
         "6m": 50_293_000}
SLOT_SAMPLES = 114 * 12000
STOP_SWR_ABOVE = 3.0
POWER_GRACE_S = 3.0
UPLOAD_URL = "http://wsprnet.org/post/"
VERSION = "G7250-OpenControl_0.2"


def next_slot(now=None, setup=2.0):
    t = (now or time.time()) + setup
    slot = (int(t) // 120 + (1 if t % 120 else 0)) * 120
    return float(slot)


class WsprService:
    def __init__(self, station, data_dir: Path):
        self.st = station
        self.data_dir = Path(data_dir)
        self.hashed_path = self.data_dir / "wspr-hashed-calls.json"
        try:
            self.hashed = {int(k): v for k, v in json.loads(self.hashed_path.read_text()).items()}
        except (OSError, ValueError):
            self.hashed = {}
        self.demod = Demodulator(agc=False, as_float=True)
        self.demod.configure(mode="USB", low=0, high=3000)
        self.receiving = False
        self.transmitting = False
        self.running = False
        self.state = "Idle"
        self.decodes = []              # newest first
        self.tx_log = []
        self.last_tx_message = ""
        self.slot_start = None
        self._buf = []
        self._buf_n = 0
        self._task = None
        self._slot_count = 0
        self._stop = asyncio.Event()
        self.progress = None

    # ---------------------------------------------------------------- settings
    @property
    def cfg(self):
        return self.st.settings["wspr"]

    def dial_hz(self):
        return DIALS.get(self.cfg["band"], DIALS["20m"])

    # ---------------------------------------------------------------- receive
    def feed(self, samples):
        """Called with every IQ block while WSPR is running."""
        if not self.receiving or self.transmitting:
            return
        now = time.time()
        if self.slot_start is None:
            # start collecting at the top of the next even minute
            if now % 120 > 0.5:
                return
            self.slot_start = now - now % 120
            self._buf, self._buf_n = [], 0
            self._slot_freq = self.st.frequency
        audio = self.demod.push(samples)
        if audio.size:
            self._buf.append(audio)
            self._buf_n += audio.size
        if self._buf_n >= SLOT_SAMPLES:
            slot = np.concatenate(self._buf)[:SLOT_SAMPLES]
            start = self.slot_start
            self.slot_start, self._buf, self._buf_n = None, [], 0
            if self.st.frequency != self._slot_freq:
                self.state = "Slot skipped: the radio was retuned during it"
                return
            asyncio.get_running_loop().create_task(self._decode(slot, start, self._slot_freq))

    async def _decode(self, audio, slot_start, dial):
        self.state = "Decoding…"
        try:
            found = await asyncio.get_running_loop().run_in_executor(None, wspr_codec.decode, audio, self.hashed)
        except Exception as exc:
            self.state = f"Decode error: {type(exc).__name__}"
            return
        when = dt.datetime.fromtimestamp(slot_start, dt.timezone.utc)
        home = self.st.home()
        for d in found:
            d["time"] = when.strftime("%Y-%m-%d %H:%M")
            d["freq_hz"] = int(dial + d["audio_hz"])
            pos = geo.grid_to_latlon(d["grid"]) if d["grid"] else None
            if not pos:
                ent = geo.locate_call(d["call"])
                pos = (ent["lat"], ent["lon"]) if ent else None
            d["lat"], d["lon"] = (pos if pos else (None, None))
            d["km"] = round(geo.distance_km(home, pos)) if home and pos else None
        self.decodes = found + self.decodes[:500]
        self.state = f"{when:%H:%M} slot: {len(found)} decoded"
        try:
            self.hashed_path.write_text(json.dumps(self.hashed))
        except OSError:
            pass
        if self.cfg.get("upload") and self.st.settings["callsign"] and self.st.settings["grid"]:
            await asyncio.get_running_loop().run_in_executor(None, self._upload, found, when, dial)

    def _upload(self, decodes, when, dial):
        """WSJT-X style WSPRnet post: one GET per spot, or a status report for an empty slot. Opt-in only."""
        s = self.st.settings
        mhz = lambda hz: f"{hz / 1e6:.6f}"
        common = f"rcall={urllib.parse.quote(s['callsign'])}&rgrid={urllib.parse.quote(s['grid'][:6])}&rqrg={mhz(dial)}"
        urls = []
        if not decodes:
            urls.append(f"{UPLOAD_URL}?function=wsprstat&{common}&tpct={self.cfg['tx_percent']}&tqrg={mhz(dial + 1500)}"
                        f"&dbm={self.cfg['dbm']}&version={VERSION}&mode=2")
        for d in decodes:
            urls.append(f"{UPLOAD_URL}?function=wspr&{common}&date={when:%y%m%d}&time={when:%H%M}&sig={d['snr']}"
                        f"&dt={d['dt']:.1f}&drift={d['drift']}&tqrg={mhz(d['freq_hz'])}&tcall={urllib.parse.quote(d['call'])}"
                        f"&tgrid={urllib.parse.quote(d['grid'])}&dbm={d['dbm']}&version={VERSION}&mode=2")
        for u in urls:
            try:
                urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": VERSION}), timeout=20).close()
            except Exception:
                pass

    # ---------------------------------------------------------------- transmit
    def _output_device(self):
        import sounddevice as sd
        name = (self.cfg.get("audio_out") or "").lower()
        devices = sd.query_devices()
        if name:
            for i, d in enumerate(devices):
                if d["max_output_channels"] > 0 and name in d["name"].lower():
                    return i, d
        raise RuntimeError("Choose the radio's USB audio output in WSPR settings")

    async def transmit_slot(self, slot_start):
        cat = self.st.cat
        c, s = self.cfg, self.st.settings
        err = wspr_codec.validate(s["callsign"], s["grid"], int(c["dbm"]))
        if err:
            return False, err
        if not self.st.cat_connected:
            return False, "Connect the radio's CAT first"
        if cat.state["ptt"]:
            return False, "The radio is already transmitting"
        watts = int(c["watts"])
        if not 1 <= watts <= 50:
            return False, "WSPR power must be 1-50 W"
        import sounddevice as sd
        dev_index, dev = self._output_device()
        rate = int(dev["default_samplerate"]) or 48000
        audio = wspr_codec.tones(wspr_codec.encode(s["callsign"], s["grid"], int(c["dbm"])), float(c["audio_hz"]), rate, 0.5, 1.0)
        orig = {"freq": cat.state["freq"], "mode": self.st.mode, "power": cat.state["power"]}
        keyed = False
        message = "WSPR stopped."
        limit = len(audio) / rate + 5
        try:
            await self.st.set_mode("DIG")
            await cat.set_freq(self.dial_hz())
            await cat.set_level("power", round(watts / 100 * 255))
            wait = slot_start - time.time()
            if wait > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), wait)
                except asyncio.TimeoutError:
                    pass
            if self._stop.is_set():
                raise asyncio.CancelledError
            self.transmitting = True
            if not await self.st.guard.key("wspr", heartbeat=False):
                raise RuntimeError("could not key the radio")
            keyed = True
            keyed_at = last_power = time.monotonic()
            sd.play(audio, rate, device=dev_index, blocking=False)
            while True:
                await asyncio.sleep(0.25)
                elapsed = time.monotonic() - keyed_at
                stream = sd.get_stream()
                if not stream.active and elapsed > 2:
                    message = "WSPR transmission sent."
                    self.tx_log.insert(0, {"time": dt.datetime.fromtimestamp(slot_start, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                                           "band": c["band"], "dbm": c["dbm"], "watts": watts})
                    return True, message
                if self._stop.is_set():
                    message = "WSPR stopped."; break
                if elapsed > limit:
                    message = "WSPR stopped: transmission ran over time."; break
                if not self.st.cat_connected:
                    message = "WSPR stopped: the radio disconnected."; break
                if elapsed > 1.0 and not cat.state["ptt"]:
                    message = "WSPR stopped: transmit was released."; break
                if self.st.mode != "DIG":
                    message = "WSPR stopped: the mode was changed."; break
                po = po_watts(cat.state["po_raw"])
                swr = swr_value(cat.state["swr_raw"])
                self.progress = {"seconds": round(elapsed, 1), "watts": po, "swr": swr}
                if po > 0.3 or elapsed < 1.0:
                    last_power = time.monotonic()
                elif time.monotonic() - last_power >= POWER_GRACE_S:
                    message = "WSPR stopped: no power output. Check the radio takes USB audio in DIG mode and its USB audio level."; break
                if c.get("stop_on_high_swr", True) and po > 0.3 and swr > STOP_SWR_ABOVE:
                    message = f"WSPR stopped: SWR {swr:.1f}:1 is above {STOP_SWR_ABOVE:.0f}:1."; break
            return False, message
        except asyncio.CancelledError:
            return False, "WSPR stopped."
        except Exception as exc:
            return False, f"WSPR stopped: {exc}"
        finally:
            # unkey first, then restore; each guarded so one failure can never leave the radio keyed
            try:
                sd.stop()
            except Exception:
                pass
            if keyed:
                await self.st.guard.release("wspr", "WSPR slot ended")
            self.transmitting = False
            self.progress = None
            for step in (lambda: cat.set_level("power", orig["power"]) if orig["power"] is not None else None,
                         lambda: self.st.set_mode(orig["mode"]) if orig["mode"] else None,
                         lambda: cat.set_freq(orig["freq"]) if orig["freq"] else None):
                try:
                    coro = step()
                    if coro:
                        await coro
                except Exception:
                    pass

    # ---------------------------------------------------------------- run loop
    async def start(self, transmit):
        if self.running:
            await self.stop()
        self.running = True
        self._stop.clear()
        self._slot_count = 0
        self.receiving = True
        self.transmit_enabled = bool(transmit)
        await self.st.set_mode("DIG")
        await self.st.set_frequency(self.dial_hz())
        self.demod = Demodulator(agc=False, as_float=True)
        self.demod.configure(mode="USB", low=0, high=3000)
        self._task = asyncio.create_task(self._run(transmit))

    async def stop(self):
        self._stop.set()
        self.running = False
        self.receiving = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, 6)
            except Exception:
                self._task.cancel()
        self._task = None
        if self.transmitting or self.st.guard.owner == "wspr":
            await self.st.guard.release("wspr", "WSPR stopped")
        self.state = "Idle"

    async def _run(self, transmit):
        every = max(1, int(self.cfg.get("every", 5)))
        while not self._stop.is_set():
            slot = next_slot(setup=4.0)
            tx_now = transmit and (self._slot_count == 0 or random.randrange(every) == 0)
            self._slot_count += 1
            if tx_now:
                self.state = f"Transmitting at {dt.datetime.fromtimestamp(slot, dt.timezone.utc):%H:%M} UTC"
                self.receiving = False
                ok, self.last_tx_message = await self.transmit_slot(slot)
                self.receiving = not self._stop.is_set()
                self.slot_start = None
                if not ok and self.last_tx_message != "WSPR stopped.":
                    self.state = self.last_tx_message
                    if "Choose" in self.last_tx_message or "Connect" in self.last_tx_message or "Callsign" in self.last_tx_message:
                        self.running = False
                        return
            else:
                self.state = f"Listening · next slot {dt.datetime.fromtimestamp(slot, dt.timezone.utc):%H:%M} UTC"
                try:
                    await asyncio.wait_for(self._stop.wait(), max(1, slot + 118 - time.time()))
                except asyncio.TimeoutError:
                    pass

    def status(self):
        return {"running": self.running, "receiving": self.receiving, "transmitting": self.transmitting,
                "transmit_enabled": self.running and getattr(self, "transmit_enabled", False),
                "state": self.state, "last_tx": self.last_tx_message, "progress": self.progress,
                "dial_hz": self.dial_hz(), "capturing_s": round(self._buf_n / 12000, 1) if self.slot_start else 0,
                "next_slot": next_slot(setup=0)}


def list_output_devices():
    try:
        import sounddevice as sd
    except ImportError:
        return []
    seen, out = set(), []
    for d in sd.query_devices():
        if d["max_output_channels"] > 0 and d["name"] not in seen:
            seen.add(d["name"])
            out.append(d["name"])
    return out
