"""A pretend G7250 CI-V port for testing without a radio.

Answers the same Icom-style commands the CAT link uses, echoes like the real bus, and
reads its S-meter from the IQ simulator's band so meters and waterfall agree. It does not
model anything Xiegu-specific: it is a test double, not a description of the G7250.
"""
import math
import time

from . import civ


class SimCivRadio:
    def __init__(self, address=0xA4, band=None):
        self.address = address
        self.band = band                    # g7250.simulator.Simulator, for S-meter levels
        self.vfo = "A"
        self.freq = {"A": 14_074_000, "B": 14_200_000}
        self.mode, self.filter, self.data = "USB", 1, False
        self.split = self.ptt = self.atu = self.att = False
        self.antenna = 0
        self.rit_hz, self.rit, self.xit = 0, False, False
        self.levels = {"af": 128, "rf": 255, "sql": 0, "nr_level": 128, "power": 255,
                       "mic": 107, "comp_level": 128, "nb_level": 80, "keyer_speed": 100}
        self.funcs = {"preamp": 1, "agc": 1, "nb": 0, "nr": 1, "anf": 1, "comp": 0, "vox": 0, "lock": 0}
        self._parser = civ.FrameParser()
        self.cw_sent = []

    @property
    def other(self):
        return "B" if self.vfo == "A" else "A"

    def handle(self, data: bytes):
        replies = []
        for f in self._parser.feed(data):
            if f.to_addr in (self.address, civ.BROADCAST) and f.from_addr == civ.CONTROLLER:
                reply = self._command(f.cmd, f.body)
                if reply is not None:
                    replies.append(civ.build(civ.CONTROLLER, reply[0], data=reply[1], from_addr=self.address))
        return replies

    def _ok(self):
        return civ.ACK, b""

    def _ng(self):
        return civ.NAK, b""

    def _command(self, cmd, body):
        sub = body[0] if body else None
        if cmd == civ.CMD_READ_FREQ:
            return cmd, civ.encode_freq(self.freq[self.vfo])
        if cmd == civ.CMD_SET_FREQ and len(body) >= 5:
            self.freq[self.vfo] = civ.decode_freq(body[:5])
            return self._ok()
        if cmd == civ.CMD_READ_MODE:
            return cmd, bytes([civ.MODE_CODES[self.mode], self.filter])
        if cmd == civ.CMD_SET_MODE and body:
            if body[0] not in civ.MODES:
                return self._ng()
            self.mode = civ.MODES[body[0]]
            self.filter = body[1] if len(body) > 1 else self.filter
            return self._ok()
        if cmd == civ.CMD_VFO:
            if sub in (0x00, 0x01):
                self.vfo = "A" if sub == 0 else "B"
            elif sub == 0xA0:
                self.freq[self.other] = self.freq[self.vfo]
            elif sub == 0xB0:
                self.freq["A"], self.freq["B"] = self.freq["B"], self.freq["A"]
            else:
                return self._ng()
            return self._ok()
        if cmd == civ.CMD_VFO_FREQ and sub in (0, 1):
            which = self.vfo if sub == 0 else self.other
            if len(body) >= 6:
                self.freq[which] = civ.decode_freq(body[1:6])
                return self._ok()
            return cmd, bytes([sub]) + civ.encode_freq(self.freq[which])
        if cmd == civ.CMD_SPLIT:
            if body:
                self.split = bool(body[0])
                return self._ok()
            return cmd, bytes([int(self.split)])
        if cmd == civ.CMD_ATT:
            if body:
                self.att = bool(body[0])
                return self._ok()
            return cmd, bytes([0x20 if self.att else 0])
        if cmd == civ.CMD_ANTENNA:
            if body:
                self.antenna = body[0] & 1
                return self._ok()
            return cmd, bytes([self.antenna])
        if cmd == civ.CMD_LEVEL:
            name = next((n for n, c in civ.LEVELS.items() if c == sub), None)
            if name is None:
                return self._ng()
            if len(body) >= 3:
                self.levels[name] = civ.decode_level(body[1:3])
                return self._ok()
            return cmd, bytes([sub]) + civ.encode_level(self.levels[name])
        if cmd == civ.CMD_METER:
            name = next((n for n, c in civ.METERS.items() if c == sub), None)
            if name is None:
                return self._ng()
            return cmd, bytes([sub]) + civ.encode_level(self._meter(name))
        if cmd == civ.CMD_FUNC:
            name = next((n for n, c in civ.FUNCS.items() if c == sub), None)
            if name is None:
                return self._ng()
            if len(body) >= 2:
                self.funcs[name] = body[1]
                return self._ok()
            return cmd, bytes([sub, self.funcs[name]])
        if cmd == civ.CMD_DATA and sub == 0x06:
            if len(body) >= 2:
                self.data = bool(body[1])
                return self._ok()
            return cmd, bytes([0x06, int(self.data), int(self.data)])
        if cmd == civ.CMD_TX and sub == 0x00:
            if len(body) >= 2:
                self.ptt = bool(body[1])
                return self._ok()
            return cmd, bytes([0, int(self.ptt)])
        if cmd == civ.CMD_TX and sub == 0x01:
            if len(body) >= 2:
                if body[1] == 2:
                    self.atu = True
                else:
                    self.atu = bool(body[1])
                return self._ok()
            return cmd, bytes([1, int(self.atu)])
        if cmd == civ.CMD_RIT and sub in (0, 1, 2):
            if sub == 0:
                if len(body) >= 4:
                    self.rit_hz = civ.decode_offset(body[1:4])
                    return self._ok()
                return cmd, bytes([0]) + civ.encode_offset(self.rit_hz)
            attr = "rit" if sub == 1 else "xit"
            if len(body) >= 2:
                setattr(self, attr, bool(body[1]))
                return self._ok()
            return cmd, bytes([sub, int(getattr(self, attr))])
        if cmd == civ.CMD_CW_SEND:
            self.cw_sent.append(body.decode("ascii", "replace"))
            return self._ok()
        if cmd == civ.CMD_ID and sub == 0x00:
            return cmd, bytes([0x00, self.address])
        return self._ng()

    # ---- meters ---------------------------------------------------------------

    def _passband(self):
        f = self.freq[self.vfo]
        if self.mode in ("USB", "RTTY"):
            return f + 100, f + 2900
        if self.mode == "LSB":
            return f - 2900, f - 100
        if self.mode in ("CW", "CW-R"):
            return f - 250, f + 250
        return f - 4000, f + 4000

    def _meter(self, name):
        if name == "s_raw":
            if self.ptt:
                return 0
            dbm = -121.0                                   # receiver noise ~ S2 with preamp
            if self.band:
                p = self.band.power_between(*self._passband(), time.time())
                if p > 0:
                    sig_dbm = 10 * math.log10(p) - 40         # sim full scale ~ -40 dBm
                    dbm = 10 * math.log10(10 ** (dbm / 10) + 10 ** (sig_dbm / 10))
            dbm -= 20 if self.att else 0
            dbm += 0 if self.funcs["preamp"] else -10
            over = dbm + 73
            raw = 120 + over * 121 / 60 if over > 0 else 120 + over * 120 / 54
            return int(max(0, min(255, raw)))
        if not self.ptt:
            return {"vd_raw": 158, "id_raw": 6}.get(name, 0)
        watts = self.levels["power"] / 255 * 100
        if name == "po_raw":
            return int(min(255, watts / 100 * 213))
        if name == "swr_raw":
            return 18 if self.atu else 40
        if name == "alc_raw":
            return int(self.levels["mic"] / 255 * 140)
        if name == "comp_raw":
            return int(self.levels["comp_level"] / 255 * 200) if self.funcs["comp"] else 0
        if name == "vd_raw":
            return 150
        if name == "id_raw":
            return int(30 + watts / 100 * 170)
        return 0
