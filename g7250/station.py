"""Ties the parts together: LAN IQ link, USB CAT, demodulator, PTT guard, logbook,
DX cluster, propagation and the Hamlib server.

Without CAT the station still works as a receiver: tuning goes over the LAN and the mode
only changes the local demodulator. With CAT, the radio's VFO is the truth and the LAN
LO follows it, so the waterfall is always centred on what the radio is tuned to.
"""
import asyncio
import copy
import json
import time
from pathlib import Path

import numpy as np

from . import geo, protocol
from .cat import CatLink, PttGuard, SerialTransport, SimTransport, s_meter
from .cluster import DxCluster
from .demod import Demodulator
from .logbook import Logbook
from .muf import MufService
from .wspr import WsprReports
from .wspr_service import WsprService
from .cw_service import CwService
from .callbook import Callbook
from .propagation import Propagation
from .radio import RadioLink
from .rigctl import RigctlServer
from .sim_civ import SimCivRadio
from .simulator import Simulator, run_simulator

DEFAULT_SETTINGS = {
    "callsign": "",
    "grid": "",
    "cat": {"port": "", "baud": 115200, "address": "auto", "autoconnect": False},
    "cluster": {"enabled": False, "host": "dxc.nc7j.com", "port": 7373},
    "rigctl": {"enabled": True, "port": 4532},
    "tx": {"max_seconds": 180},
    "scope": {"centred": False},
    "cq": ["CQ CQ DE {CALL} {CALL} K", "CQ TEST {CALL} {CALL}", "{CALL} TU 5NN"],
    "propagation": {"enabled": True},
    "wspr": {"band": "20m", "dbm": 37, "watts": 5, "audio_hz": 1500, "every": 5, "audio_out": "",
             "upload": False, "tx_percent": 20, "stop_on_high_swr": True},
    "cw_decoder": {"decoder": "neural", "delay": 1.0},
    "cw_memories": [{"label": "CQ", "text": "CQ CQ DE {CALL} {CALL} K"}, {"label": "TEST", "text": "CQ TEST {CALL} {CALL}"},
                    {"label": "5NN", "text": "TU 5NN"}, {"label": "QRZ?", "text": "QRZ?"},
                    {"label": "AGN", "text": "AGN?"}, {"label": "73", "text": "TU 73 DE {CALL} SK"}],
    "qrz": {"username": "", "password": "", "logbook_key": "", "auto_upload": False},
    "hamqth": {"username": "", "password": ""},
}

MODES = ("LSB", "USB", "CW", "CW-R", "AM", "FM", "RTTY", "DIG")


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


class Station:
    def __init__(self, data_dir: Path, simulator=False, radio_ip=protocol.DEFAULT_RADIO_IP,
                 bind_ip="0.0.0.0", swap_iq=False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.settings = _merge(DEFAULT_SETTINGS, self._read_json(self.settings_path, {}))
        self.memories_path = self.data_dir / "memories.json"
        self.memories = self._read_json(self.memories_path, [None] * 6)

        self.simulator = simulator
        self.link = RadioLink("127.0.0.1" if simulator else radio_ip, bind_ip, swap_iq=swap_iq)
        self.demod = Demodulator()
        self.cat = None
        self.cat_error = None
        self.rigctl_error = None
        self.guard = PttGuard(lambda: self.cat, max_tx_seconds=self.settings["tx"]["max_seconds"])
        self.logbook = Logbook(self.data_dir / "logbook.db", home=self.home)
        self.cluster = DxCluster()
        self.prop = Propagation()
        self.wspr = WsprReports()
        self.muf = MufService()
        self.wspr_service = WsprService(self, self.data_dir)
        self.cw = CwService(self)
        self.callbook = Callbook(self.data_dir, lambda: self.settings)
        self.rigctl = RigctlServer(self, self.settings["rigctl"]["port"])
        self.band_sim = Simulator("127.0.0.1", protocol.LOCAL_PORT) if simulator else None
        self.sim_radio = SimCivRadio(band=self.band_sim) if simulator else None

        self._lan_freq = 14_074_000         # used when there is no CAT
        self._lan_mode = "USB"
        self._audio = set()                 # asyncio.Queue per listening browser
        self._tasks = []
        self.state_seq = 0                  # bumps on any change the UI shows
        self.link.on_iq = self._on_iq

    # ---- persistence ----------------------------------------------------------

    @staticmethod
    def _read_json(path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save_settings(self, changes: dict):
        self.settings = _merge(self.settings, changes)
        self.settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        self.guard.max_tx_seconds = int(self.settings["tx"]["max_seconds"])
        self._apply_cluster()
        self.state_seq += 1

    def save_memory(self, slot, value):
        self.memories[slot] = value
        self.memories_path.write_text(json.dumps(self.memories), encoding="utf-8")
        self.state_seq += 1

    def home(self):
        return geo.grid_to_latlon(self.settings.get("grid", ""))

    # ---- lifecycle ------------------------------------------------------------

    async def start(self):
        await self.link.start()
        spawn = lambda c: self._tasks.append(asyncio.create_task(c))
        if self.simulator:
            spawn(run_simulator(sim=self.band_sim))
            await asyncio.sleep(0.2)
        self.link.tune(self._lan_freq)
        spawn(self.guard.run())
        if self.settings["propagation"]["enabled"]:
            spawn(self.prop.run())
            spawn(self.muf.run())
        if self.settings["rigctl"]["enabled"]:
            try:
                await self.rigctl.start()
            except OSError as exc:
                self.rigctl_error = f"Port {self.rigctl.port} busy ({exc.strerror})"
        if self.simulator:
            await self.connect_cat(simulated=True)
        elif self.settings["cat"]["autoconnect"] and self.settings["cat"]["port"]:
            await self.connect_cat()
        self._apply_cluster()

    async def stop(self):
        await self.wspr_service.stop()
        await self.guard.release(reason="shutdown")
        for t in self._tasks:
            t.cancel()
        self.cluster.stop()
        await self.rigctl.stop()
        if self.cat:
            self.cat.close()
        self.link.close()

    def _apply_cluster(self):
        c = self.settings["cluster"]
        if self.simulator and not c["enabled"]:
            if self.cluster.status == "Off":
                self.cluster.start_simulated(self.band_sim)
        elif c["enabled"] and self.settings["callsign"]:
            self.cluster.start(c["host"], c["port"], self.settings["callsign"])
        else:
            self.cluster.stop()

    # ---- CAT ------------------------------------------------------------------

    async def connect_cat(self, port=None, baud=None, address=None, simulated=False):
        await self.disconnect_cat()
        cfg = self.settings["cat"]
        if simulated:
            transport = SimTransport(self.sim_radio)
        else:
            transport = SerialTransport(port or cfg["port"], int(baud or cfg["baud"]))
        addr = address if address is not None else cfg["address"]
        addr = None if addr in (None, "", "auto") else int(str(addr), 16)
        cat = CatLink(transport, addr, on_change=self._on_cat_change)
        try:
            await cat.connect()
        except Exception as exc:
            self.cat_error = f"{type(exc).__name__}: {exc}"
            self.state_seq += 1
            return False
        self.cat, self.cat_error = cat, None
        if not simulated:
            self.save_settings({"cat": {"port": transport.port, "baud": transport.baud,
                                        "address": "auto" if addr is None else f"{addr:02X}"}})
        self._on_cat_change({"freq": cat.state["freq"], "mode": cat.state["mode"]})
        return True

    async def disconnect_cat(self):
        if self.cat:
            await self.guard.release(reason="CAT disconnect")
            self.cat.close()
            self.cat = None
            self.state_seq += 1

    def _on_cat_change(self, changes):
        self.state_seq += 1
        if changes.get("freq"):
            self._follow_lo(changes["freq"])
        if "mode" in changes or "data" in changes:
            self.demod.configure(mode=self.mode)

    @property
    def cat_connected(self):
        return bool(self.cat and self.cat.state["connected"])

    # ---- unified rig view (used by the UI and Hamlib) ---------------------------

    @property
    def frequency(self):
        return self.cat.state["freq"] if self.cat_connected else self._lan_freq

    @property
    def freq_other(self):
        return self.cat.state["freq_other"] if self.cat_connected else None

    @property
    def mode(self):
        if self.cat_connected and self.cat.state["mode"]:
            m = self.cat.state["mode"]
            return "DIG" if m == "USB" and self.cat.state["data"] else m
        return self._lan_mode

    @property
    def split(self):
        return bool(self.cat_connected and self.cat.state["split"])

    @property
    def ptt(self):
        return bool(self.cat_connected and self.cat.state["ptt"])

    @property
    def passband_width(self):
        return self.demod.high - self.demod.low

    @property
    def s_over_s9(self):
        if self.cat_connected:
            return s_meter(self.cat.state["s_raw"])[1]
        return self.iq_level_dbfs + 40               # rough: no calibration without CAT

    @property
    def iq_level_dbfs(self):
        """Signal power inside the demodulator passband, from the latest spectrum."""
        db = self.link.spectrum.latest_db
        if db is None:
            return -150.0
        lo, hi = self.demod.passband()
        off = self.frequency - (self.link.frequency_hz or self.frequency)
        lo, hi = lo + off, hi + off
        n = db.size
        a = int(n / 2 + lo / protocol.NOMINAL_SAMPLE_RATE * n)
        b = int(n / 2 + hi / protocol.NOMINAL_SAMPLE_RATE * n) + 1
        return float(10 * np.log10(np.sum(10 ** (db[max(0, a):min(n, b)] / 10)) + 1e-20))

    LO_HALF_HZ = 24_000            # the IQ is +/-24 kHz around the LAN centre
    LO_EDGE_HZ = 4_000             # keep the passband this far inside the edge when tuning scrolls the window

    def _follow_lo(self, hz):
        """Scope behaviour. The IQ centre stays put while the VFO moves inside the window, so the
        passband moves across the scope. Tuning past an edge scrolls the window just enough (no snap back to
        the centre); a jump of more than a window (band change, memory) centres it. Centred tuning is optional."""
        lo = self.link.frequency_hz
        inner = self.LO_HALF_HZ - self.LO_EDGE_HZ
        if not lo or self.settings.get("scope", {}).get("centred") or abs(hz - lo) > 2 * self.LO_HALF_HZ:
            self.link.tune(hz)
        elif hz > lo + inner:
            self.link.tune(hz - inner)
        elif hz < lo - inner:
            self.link.tune(hz + inner)

    def pan_scope(self, centre_hz):
        """Background drag on the scope: move the IQ window only, the VFO stays where it is."""
        self.link.tune(protocol.clamp_frequency(int(centre_hz)))
        self.state_seq += 1

    async def set_frequency(self, hz):
        hz = protocol.clamp_frequency(hz)
        if self.cat_connected:
            await self.cat.set_freq(hz)
        else:
            self._lan_freq = hz
            self._follow_lo(hz)
            self.state_seq += 1

    async def set_other_frequency(self, hz):
        if self.cat_connected:
            await self.cat.set_other_freq(hz)

    async def set_mode(self, mode):
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode}")
        if self.cat_connected:
            await self.cat.set_mode(mode)
        else:
            self._lan_mode = mode
            self.state_seq += 1
        self.demod.configure(mode=mode)

    async def set_split(self, on):
        if self.cat_connected:
            await self.cat.set_split(on)

    async def rig_set(self, name, value):
        """Generic control from the UI. Raises if it needs CAT and there is none."""
        if name == "freq":
            return await self.set_frequency(int(value))
        if name == "mode":
            return await self.set_mode(value)
        if name == "passband":
            self.demod.configure(low=value[0], high=value[1])
            self.state_seq += 1
            return
        if name == "squelch":                         # local squelch on the LAN audio
            self.demod.squelch_dbfs = None if value is None else float(value)
            self.state_seq += 1
            return
        if not self.cat_connected:
            raise RuntimeError("Needs USB CAT")
        cat = self.cat
        if name == "freq_other":
            return await cat.set_other_freq(int(value))
        if name == "filter":                          # radio filter FIL1-3 for the current mode
            return await cat.set_mode(self.mode, int(value))
        if name == "vfo":
            return await cat.select_vfo(value)
        if name == "swap":
            return await cat.swap_vfo()
        if name == "equal":
            return await cat.equalize_vfo()
        if name == "split":
            return await cat.set_split(bool(value))
        if name in ("af", "rf", "sql", "power", "mic", "comp_level", "nr_level", "nb_level", "keyer_speed"):
            return await cat.set_level(name, int(value))
        if name in ("preamp", "agc", "nb", "nr", "anf", "comp", "vox", "lock"):
            return await cat.set_func(name, value)
        if name == "att":
            return await cat.set_att(bool(value))
        if name == "antenna":
            return await cat.set_antenna(int(value))
        if name in ("rit", "xit"):
            return await cat.set_rit(name, bool(value))
        if name == "rit_hz":
            return await cat.set_rit_offset(int(value))
        if name == "atu":
            return await cat.set_atu(bool(value))
        if name == "tune":
            return await cat.atu_tune()
        if name == "cw":
            return await self.cw.send(str(value))
        raise ValueError(f"Unknown control {name}")

    # ---- audio --------------------------------------------------------------

    def add_audio_listener(self):
        q = asyncio.Queue(maxsize=50)
        self._audio.add(q)
        return q

    def remove_audio_listener(self, q):
        self._audio.discard(q)

    def _on_iq(self, samples):
        off = float(self.frequency - (self.link.frequency_hz or self.frequency))
        for d in (self.demod, self.cw.demod, getattr(self.wspr_service, "demod", None)):
            if d is not None:
                d.offset_hz = off
        if self.wspr_service.running:
            self.wspr_service.feed(samples)
        self.cw.feed(samples)
        if not self._audio:
            return
        pcm = self.demod.push(samples)
        if pcm:
            for q in list(self._audio):
                if q.full():
                    q.get_nowait()                     # drop the oldest rather than lag
                q.put_nowait(pcm)

    # ---- status for the UI ----------------------------------------------------

    def status(self):
        cat = self.cat.state if self.cat else None
        lo, hi = self.demod.passband()
        out = {
            "type": "status",
            "simulator": self.simulator,
            "lan": {"radio": f"{self.link.radio_ip}:{self.link.radio_port}",
                    "listening": f"{self.link.bind_ip}:{self.link.local_port}",
                    "receiving": self.link.receiving,
                    "packets_per_second": round(self.link.packets_per_second, 1),
                    "samples_per_second": round(self.link.packets_per_second * protocol.IQ_SAMPLES_PER_PACKET),
                    "lo_hz": self.link.frequency_hz, "fft_size": self.link.spectrum.fft_size,
                    "recording": self.link.recording,
                    "last_recording": self.link.last_recording.name if self.link.last_recording else None},
            "cat": {"connected": self.cat_connected, "error": self.cat_error or (self.cat.last_error if self.cat else None),
                    "transport": cat["transport"] if cat else None, "address": cat["address"] if cat else None},
            "rig": {"freq": self.frequency, "freq_other": self.freq_other, "mode": self.mode,
                    "split": self.split, "ptt": self.ptt, "passband": [lo, hi],
                    "low": self.demod.low, "high": self.demod.high, "pitch": self.demod.pitch,
                    "squelch": self.demod.squelch_dbfs, "iq_level_dbfs": round(self.iq_level_dbfs, 1)},
            "ptt_owner": self.guard.owner, "ptt_release_reason": self.guard.last_release_reason,
            "rigctl": {"port": self.rigctl.port, "clients": self.rigctl.clients,
                       "running": self.rigctl._server is not None},
            "cluster": self.cluster.status,
            "callsign": self.settings["callsign"], "grid": self.settings["grid"],
            "wspr": self.wspr_service.status(),
            "time": time.time(),
        }
        if cat:
            out["rig"].update({k: cat[k] for k in (
                "vfo", "filter", "atu", "antenna", "att", "rit", "xit", "rit_hz", "af", "rf", "sql",
                "power", "mic", "comp_level", "nr_level", "nb_level", "keyer_speed", "preamp", "agc", "nb", "nr", "anf",
                "comp", "vox", "lock", "s_raw", "po_raw", "swr_raw", "alc_raw", "comp_raw", "vd_raw",
                "id_raw", "unsupported")})
        return out
