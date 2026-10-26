"""CAT control over the G7250's USB CI-V port.

One gate to the radio: every transaction (write + reply) goes through a single worker
in priority order: PTT release > PTT key > user actions > external apps > background
polls. Polls are skipped, not queued, when anything else is waiting.

Anything the radio refuses with FA is recorded as unsupported and never polled again,
so the UI can grey out controls this firmware doesn't offer.
"""
import asyncio
import itertools
import threading
import time

from . import civ

PRI_RELEASE, PRI_KEY, PRI_USER, PRI_EXTERNAL, PRI_POLL = range(5)
REPLY_TIMEOUT = 0.25
PROBE_TIMEOUT = 0.35


class Unsupported(Exception):
    pass


class CatTimeout(Exception):
    pass


# ---------------------------------------------------------------- transports

class SerialTransport:
    """pyserial in a reader thread, handing bytes to the event loop."""

    def __init__(self, port: str, baud: int):
        self.port, self.baud = port, baud
        self._serial = None
        self._thread = None
        self._running = False

    @property
    def name(self):
        return f"USB CAT · {self.port} · {self.baud}"

    async def open(self, on_bytes):
        import serial  # pyserial
        loop = asyncio.get_running_loop()
        self._serial = await loop.run_in_executor(
            None, lambda: serial.Serial(self.port, self.baud, timeout=0.05, write_timeout=1))
        # Xiegu USB serial ports key the transmitter on RTS/DTR on some models: hold them low.
        self._serial.rts = False
        self._serial.dtr = False
        self._running = True

        def reader():
            while self._running:
                try:
                    data = self._serial.read(256)
                except Exception:
                    break
                if data:
                    loop.call_soon_threadsafe(on_bytes, data)

        self._thread = threading.Thread(target=reader, name="civ-reader", daemon=True)
        self._thread.start()

    def write(self, data: bytes):
        self._serial.write(data)

    def close(self):
        self._running = False
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass


class SimTransport:
    """Talks to an in-process simulated radio, including the bus echo."""

    def __init__(self, radio):
        self.radio = radio
        self._on_bytes = None

    @property
    def name(self):
        return "Simulated CAT · CI-V"

    async def open(self, on_bytes):
        self._on_bytes = on_bytes

    def write(self, data: bytes):
        loop = asyncio.get_running_loop()
        loop.call_later(0.002, self._on_bytes, data)                 # bus echo
        for reply in self.radio.handle(data):
            loop.call_later(0.008, self._on_bytes, reply)

    def close(self):
        pass


def list_serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description or "",
             "vid": f"{p.vid:04X}" if p.vid else None, "pid": f"{p.pid:04X}" if p.pid else None}
            for p in list_ports.comports()]


# ---------------------------------------------------------------- meter scales

def _interp(raw, table):
    for (r0, v0), (r1, v1) in zip(table, table[1:]):
        if raw <= r1:
            return v0 + (v1 - v0) * (raw - r0) / max(1, r1 - r0)
    return table[-1][1]


def s_meter(raw):
    """Icom scale: 0 = S0, 120 = S9, 241 = S9+60. Returns (label, dB over S9, approx dBm)."""
    if raw <= 120:
        s = raw / 120 * 9
        over = (s - 9) * 6
        label = f"S{max(0, round(s))}"
    else:
        over = (raw - 120) / 121 * 60
        label = f"S9+{round(over / 10) * 10:d}" if over >= 5 else "S9"
    return label, round(over, 1), round(-73 + over, 1)


def po_watts(raw, max_w=100):
    return round(_interp(raw, [(0, 0), (143, 50), (213, 100), (255, 120)]) * max_w / 100, 1)


def swr_value(raw):
    return round(_interp(raw, [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (255, 9.9)]), 2)


def comp_db(raw):
    return round(_interp(raw, [(0, 0), (130, 15), (241, 30)]), 1)


def vd_volts(raw):
    return round(_interp(raw, [(0, 0), (13, 10), (241, 16)]), 1)


def id_amps(raw):
    return round(_interp(raw, [(0, 0), (97, 10), (146, 15), (241, 25)]), 1)


# ---------------------------------------------------------------- the link

DEFAULT_STATE = {
    "connected": False, "transport": None, "address": None,
    "vfo": "A", "freq": None, "freq_other": None, "mode": None, "filter": 1, "data": False,
    "split": False, "ptt": False, "atu": False, "antenna": 1, "att": False, "rit_hz": 0,
    "rit": False, "xit": False,
    "af": None, "rf": None, "sql": None, "power": None, "mic": None, "comp_level": None,
    "nr_level": None, "nb_level": None,
    "preamp": None, "agc": None, "nb": None, "nr": None, "anf": None, "comp": None,
    "vox": None, "lock": None,
    "s_raw": 0, "po_raw": 0, "swr_raw": 0, "alc_raw": 0, "comp_raw": 0, "vd_raw": None,
    "id_raw": None, "unsupported": [],
}


class CatLink:
    def __init__(self, transport, address=None, on_change=None):
        self.transport = transport
        self.address = address                      # None = probe the candidates
        self.on_change = on_change
        self.state = dict(DEFAULT_STATE)
        self.seq = 0
        self._parser = civ.FrameParser()
        self._incoming = asyncio.Queue()
        self._queue = asyncio.PriorityQueue()
        self._counter = itertools.count()
        self._unsupported = set()
        self._tasks = []
        self._fails = 0
        self.last_error = None

    # ---- lifecycle -------------------------------------------------------

    async def connect(self):
        await self.transport.open(self._on_bytes)
        self._tasks.append(asyncio.create_task(self._worker()))
        candidates = [self.address] if self.address else list(civ.CANDIDATE_ADDRESSES)
        for addr in candidates:
            self.address = addr
            try:
                await self.request(civ.CMD_READ_FREQ, timeout=PROBE_TIMEOUT, retries=1)
                break
            except (CatTimeout, Unsupported):
                continue
        else:
            self.close()
            raise CatTimeout("No CI-V reply at " + ", ".join(f"{a:02X}h" for a in candidates))
        self._update(connected=True, transport=self.transport.name, address=f"{self.address:02X}h")
        await self._read_all()
        self._tasks.append(asyncio.create_task(self._poll_loop()))

    def close(self):
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        self.transport.close()
        self._update(connected=False)

    # ---- low level ---------------------------------------------------------

    def _on_bytes(self, data):
        for frame in self._parser.feed(data):
            if frame.from_addr == civ.CONTROLLER:
                continue                              # our own echo
            if frame.to_addr == civ.BROADCAST:
                self._transceive(frame)
            else:
                self._incoming.put_nowait(frame)

    def _transceive(self, frame):
        """Unsolicited updates when the radio's CI-V transceive is on."""
        if frame.cmd == civ.CMD_FREQ_TRANSCEIVE and len(frame.body) >= 5:
            self._update(freq=civ.decode_freq(frame.body[:5]))
        elif frame.cmd == civ.CMD_MODE_TRANSCEIVE and frame.body:
            self._update(mode=civ.MODES.get(frame.body[0], self.state["mode"]))

    def _update(self, **changes):
        changed = False
        for k, v in changes.items():
            if self.state.get(k) != v:
                self.state[k] = v
                changed = True
        if changed:
            self.seq += 1
            if self.on_change:
                self.on_change(changes)

    async def request(self, cmd, sub=None, data=b"", priority=PRI_USER, timeout=REPLY_TIMEOUT, retries=2):
        """Queue one transaction and wait for the radio's reply frame."""
        key = (cmd, sub)
        if key in self._unsupported:
            raise Unsupported(f"{cmd:02X} {'' if sub is None else f'{sub:02X}'}")
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((priority, next(self._counter), (cmd, sub, data, timeout, retries), fut))
        return await fut

    async def _worker(self):
        while True:
            _, _, (cmd, sub, data, timeout, retries), fut = await self._queue.get()
            if fut.cancelled():
                continue
            try:
                fut.set_result(await self._transact(cmd, sub, data, timeout, retries))
            except Exception as exc:          # deliver to the caller
                if not fut.done():
                    fut.set_exception(exc)

    async def _transact(self, cmd, sub, data, timeout, retries):
        frame_bytes = civ.build(self.address, cmd, sub, data)
        for _ in range(retries):
            while not self._incoming.empty():
                self._incoming.get_nowait()
            self.transport.write(frame_bytes)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    frame = await asyncio.wait_for(self._incoming.get(), left)
                except asyncio.TimeoutError:
                    break
                if frame.from_addr != self.address:
                    continue
                if frame.is_nak:
                    self._unsupported.add((cmd, sub))
                    self._update(unsupported=sorted(f"{c:02X}{'' if s is None else f' {s:02X}'}"
                                                    for c, s in self._unsupported))
                    raise Unsupported(f"{cmd:02X}")
                if frame.is_ack or frame.cmd == cmd and (sub is None or frame.body[:1] == bytes([sub])):
                    self._fails = 0
                    return frame
        self._fails += 1
        if self._fails >= 8 and self.state["connected"]:
            self.last_error = "Radio stopped answering"
            self._update(connected=False)
        raise CatTimeout(f"No reply to {cmd:02X}")

    # ---- reads ---------------------------------------------------------------

    async def read_freq(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_READ_FREQ, priority=pri)
        self._update(freq=civ.decode_freq(f.body[:5]))
        if not self.state["connected"] and self._fails == 0:
            self._update(connected=True)

    async def read_mode(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_READ_MODE, priority=pri)
        self._update(mode=civ.MODES.get(f.body[0], f"{f.body[0]:02X}"),
                     filter=f.body[1] if len(f.body) > 1 else 1)

    async def read_other_vfo(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_VFO_FREQ, 0x01, priority=pri)
        self._update(freq_other=civ.decode_freq(f.body[1:6]))

    async def read_data(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_DATA, 0x06, priority=pri)
        self._update(data=bool(f.body[1]) if len(f.body) > 1 else False)

    async def read_level(self, name, pri=PRI_POLL):
        f = await self.request(civ.CMD_LEVEL, civ.LEVELS[name], priority=pri)
        self._update(**{name: civ.decode_level(f.body[1:3])})

    async def read_meter(self, name, pri=PRI_POLL):
        f = await self.request(civ.CMD_METER, civ.METERS[name], priority=pri)
        self._update(**{name: civ.decode_level(f.body[1:3])})

    async def read_func(self, name, pri=PRI_POLL):
        f = await self.request(civ.CMD_FUNC, civ.FUNCS[name], priority=pri)
        v = f.body[1]
        self._update(**{name: civ.AGC_NAMES.get(v, v) if name == "agc" else v if name == "preamp" else bool(v)})

    async def read_ptt(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_TX, 0x00, priority=pri)
        self._update(ptt=bool(f.body[1]))

    async def read_atu(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_TX, 0x01, priority=pri)
        self._update(atu=bool(f.body[1]))

    async def read_split(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_SPLIT, priority=pri)
        self._update(split=bool(f.body[0]))

    async def read_att(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_ATT, priority=pri)
        self._update(att=bool(f.body[0]))

    async def read_antenna(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_ANTENNA, priority=pri)
        self._update(antenna=f.body[0] + 1)

    async def read_rit(self, pri=PRI_POLL):
        f = await self.request(civ.CMD_RIT, 0x00, priority=pri)
        self._update(rit_hz=civ.decode_offset(f.body[1:4]))
        f = await self.request(civ.CMD_RIT, 0x01, priority=pri)
        self._update(rit=bool(f.body[1]))
        f = await self.request(civ.CMD_RIT, 0x02, priority=pri)
        self._update(xit=bool(f.body[1]))

    def _slow_reads(self):
        reads = [self.read_other_vfo, self.read_split, self.read_att, self.read_antenna,
                 self.read_atu, self.read_rit, self.read_data]
        reads += [lambda n=n: self.read_level(n) for n in civ.LEVELS]
        reads += [lambda n=n: self.read_func(n) for n in civ.FUNCS]
        reads += [lambda: self.read_meter("vd_raw"), lambda: self.read_meter("id_raw")]
        return reads

    async def _safe(self, coro):
        try:
            await coro
        except (Unsupported, CatTimeout):
            pass

    async def _read_all(self):
        for read in (self.read_freq, self.read_mode, self.read_ptt):
            await self._safe(read(PRI_USER))
        for read in self._slow_reads():
            await self._safe(read())

    async def _poll_loop(self):
        slow = itertools.cycle(self._slow_reads())
        while True:
            await asyncio.sleep(0.2 if not self.state["ptt"] else 0.12)
            if not self._queue.empty():
                continue
            await self._safe(self.read_freq())
            await self._safe(self.read_mode())
            await self._safe(self.read_ptt())
            if self.state["ptt"]:
                for m in ("po_raw", "swr_raw", "alc_raw", "comp_raw"):
                    await self._safe(self.read_meter(m))
            else:
                await self._safe(self.read_meter("s_raw"))
                await self._safe(next(slow)())

    # ---- writes --------------------------------------------------------------

    async def set_freq(self, hz, pri=PRI_USER):
        await self.request(civ.CMD_SET_FREQ, data=civ.encode_freq(hz), priority=pri)
        self._update(freq=int(hz))

    async def set_other_freq(self, hz, pri=PRI_USER):
        await self.request(civ.CMD_VFO_FREQ, 0x01, civ.encode_freq(hz), priority=pri)
        self._update(freq_other=int(hz))

    async def set_mode(self, mode, filt=None, pri=PRI_USER):
        mode = mode.upper()
        data = mode == "DIG"
        base = "USB" if data else mode
        if base not in civ.MODE_CODES:
            raise ValueError(f"Unknown mode {mode}")
        filt = filt or self.state["filter"] or 1
        await self.request(civ.CMD_SET_MODE, data=bytes([civ.MODE_CODES[base], filt]), priority=pri)
        self._update(mode=base, filter=filt)
        try:
            await self.request(civ.CMD_DATA, 0x06, bytes([1 if data else 0, 1 if data else 0]), priority=pri)
            self._update(data=data)
        except Unsupported:
            pass

    async def select_vfo(self, vfo):
        await self.request(civ.CMD_VFO, 0x00 if vfo == "A" else 0x01)
        if vfo != self.state["vfo"]:
            self._update(vfo=vfo, freq=self.state["freq_other"], freq_other=self.state["freq"])

    async def swap_vfo(self):
        await self.request(civ.CMD_VFO, 0xB0)
        self._update(vfo="B" if self.state["vfo"] == "A" else "A",
                     freq=self.state["freq_other"], freq_other=self.state["freq"])

    async def equalize_vfo(self):
        await self.request(civ.CMD_VFO, 0xA0)
        self._update(freq_other=self.state["freq"])

    async def set_split(self, on):
        await self.request(civ.CMD_SPLIT, 0x01 if on else 0x00)
        self._update(split=bool(on))

    async def set_level(self, name, value):
        await self.request(civ.CMD_LEVEL, civ.LEVELS[name], civ.encode_level(value))
        self._update(**{name: int(value)})

    async def set_func(self, name, value):
        if name == "agc":
            raw = {v: k for k, v in civ.AGC_NAMES.items()}[value]
        else:
            raw = int(value)
        await self.request(civ.CMD_FUNC, civ.FUNCS[name], bytes([raw]))
        self._update(**{name: value if name in ("agc", "preamp") else bool(value)})

    async def set_att(self, on):
        await self.request(civ.CMD_ATT, data=bytes([0x20 if on else 0x00]))
        self._update(att=bool(on))

    async def set_antenna(self, n):
        await self.request(civ.CMD_ANTENNA, data=bytes([int(n) - 1]))
        self._update(antenna=int(n))

    async def set_rit(self, which, on):
        await self.request(civ.CMD_RIT, 0x01 if which == "rit" else 0x02, bytes([1 if on else 0]))
        self._update(**{which: bool(on)})

    async def set_rit_offset(self, hz):
        await self.request(civ.CMD_RIT, 0x00, civ.encode_offset(hz))
        self._update(rit_hz=int(hz))

    async def set_atu(self, on):
        await self.request(civ.CMD_TX, 0x01, bytes([1 if on else 0]))
        self._update(atu=bool(on))

    async def atu_tune(self):
        await self.request(civ.CMD_TX, 0x01, bytes([0x02]))

    async def send_cw(self, text):
        text = "".join(ch for ch in text.upper() if 32 <= ord(ch) < 127)[:30]
        await self.request(civ.CMD_CW_SEND, data=text.encode("ascii"))

    async def set_ptt(self, on):
        """Key or unkey, then read back. A release is retried until the radio confirms RX."""
        pri = PRI_KEY if on else PRI_RELEASE
        for attempt in range(1 if on else 4):
            try:
                await self.request(civ.CMD_TX, 0x00, bytes([1 if on else 0]), priority=pri)
                await self.read_ptt(pri)
            except (CatTimeout, Unsupported):
                if on:
                    raise
            if self.state["ptt"] == on:
                return True
            await asyncio.sleep(0.05)
        return self.state["ptt"] == on


class PttGuard:
    """Owns transmit. The UI must send a heartbeat while it holds PTT; a stalled or closed
    browser, a disconnected Hamlib client or the maximum TX time all force a release."""

    def __init__(self, get_cat, max_tx_seconds=180, heartbeat_timeout=1.0):
        self.get_cat = get_cat
        self.max_tx_seconds = max_tx_seconds
        self.heartbeat_timeout = heartbeat_timeout
        self.owner = None
        self.needs_heartbeat = False
        self.keyed_at = 0.0
        self.last_beat = 0.0
        self.last_release_reason = None

    async def key(self, owner, heartbeat=True):
        cat = self.get_cat()
        if not cat or not cat.state["connected"]:
            return False
        if self.owner and self.owner != owner:
            return False                  # someone else is transmitting
        self.owner, self.needs_heartbeat = owner, heartbeat
        self.keyed_at = self.last_beat = time.monotonic()
        try:
            ok = await cat.set_ptt(True)
        except Exception:
            ok = False
        if not ok:
            await self.release(owner, "key failed")   # any failed key is followed by a safety release
        return ok

    def beat(self, owner):
        if owner == self.owner:
            self.last_beat = time.monotonic()

    async def release(self, owner=None, reason="released"):
        if owner is not None and owner != self.owner:
            return
        self.owner = None
        self.last_release_reason = reason
        cat = self.get_cat()
        if cat and cat.state["connected"]:
            try:
                await cat.set_ptt(False)
            except Exception:
                pass

    async def run(self):
        while True:
            await asyncio.sleep(0.1)
            if not self.owner:
                continue
            now = time.monotonic()
            if self.needs_heartbeat and now - self.last_beat > self.heartbeat_timeout:
                await self.release(reason="heartbeat lost")
            elif now - self.keyed_at > self.max_tx_seconds:
                await self.release(reason=f"maximum TX time ({self.max_tx_seconds} s)")
