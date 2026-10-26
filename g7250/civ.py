"""Icom-style CI-V framing, as used across the Xiegu range.

Frame: FE FE <to> <from> <cmd> [sub] [data...] FD. Frequencies are 5-byte BCD, least
significant pair first. Every byte written to the bus is echoed back, so a reader must
skip frames whose sender is the controller.

The command numbers below are the standard Icom set that Xiegu documents for the
X6100/X6200. Xiegu has not published a G7250 CI-V document yet, so every command is
treated as optional: the CAT link marks anything the radio refuses (FA) as unsupported.
"""
from dataclasses import dataclass

PREAMBLE = b"\xFE\xFE"
EOM = 0xFD
ACK = 0xFB
NAK = 0xFA
CONTROLLER = 0xE0
BROADCAST = 0x00

# Tried in order when the address is set to auto. X6200/X6100, G90/G106, X5105/X108G.
CANDIDATE_ADDRESSES = (0xA4, 0x88, 0x70)

CMD_FREQ_TRANSCEIVE = 0x00
CMD_MODE_TRANSCEIVE = 0x01
CMD_READ_FREQ = 0x03
CMD_READ_MODE = 0x04
CMD_SET_FREQ = 0x05
CMD_SET_MODE = 0x06
CMD_VFO = 0x07
CMD_SPLIT = 0x0F
CMD_ATT = 0x11
CMD_ANTENNA = 0x12
CMD_LEVEL = 0x14
CMD_METER = 0x15
CMD_FUNC = 0x16
CMD_CW_SEND = 0x17
CMD_ID = 0x19
CMD_DATA = 0x1A
CMD_TX = 0x1C
CMD_RIT = 0x21
CMD_VFO_FREQ = 0x25
CMD_VFO_MODE = 0x26

MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
MODE_CODES = {v: k for k, v in MODES.items()}

# 0x14 levels (value 0-255)
LEVELS = {"af": 0x01, "rf": 0x02, "sql": 0x03, "nr_level": 0x06, "power": 0x0A, "keyer_speed": 0x0C,
          "mic": 0x0B, "comp_level": 0x0E, "nb_level": 0x12}
# 0x15 meters (raw 0-255, Icom scale)
METERS = {"s_raw": 0x02, "po_raw": 0x11, "swr_raw": 0x12, "alc_raw": 0x13,
          "comp_raw": 0x14, "vd_raw": 0x15, "id_raw": 0x16}
# 0x16 functions (on/off unless noted)
FUNCS = {"preamp": 0x02, "agc": 0x12, "nb": 0x22, "nr": 0x40, "anf": 0x41,
         "comp": 0x44, "vox": 0x46, "lock": 0x50}
AGC_NAMES = {0: "OFF", 1: "F", 2: "M", 3: "S"}


@dataclass(frozen=True)
class Frame:
    to_addr: int
    from_addr: int
    cmd: int
    body: bytes          # everything after the command byte

    @property
    def is_ack(self):
        return self.cmd == ACK

    @property
    def is_nak(self):
        return self.cmd == NAK


def build(to_addr: int, cmd: int, sub=None, data: bytes = b"", from_addr: int = CONTROLLER) -> bytes:
    sub_bytes = b"" if sub is None else bytes([sub])
    return PREAMBLE + bytes([to_addr, from_addr, cmd]) + sub_bytes + data + bytes([EOM])


def _bcd_byte(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def _bcd_value(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


def encode_freq(hz: int, nbytes: int = 5) -> bytes:
    hz = int(hz)
    out = bytearray()
    for _ in range(nbytes):
        out.append(_bcd_byte(hz % 100))
        hz //= 100
    return bytes(out)


def decode_freq(data: bytes) -> int:
    hz, mult = 0, 1
    for byte in data:
        hz += _bcd_value(byte) * mult
        mult *= 100
    return hz


def encode_level(value: int) -> bytes:
    """0-255 as the two-byte BCD 0000-0255 Icom uses for levels."""
    value = max(0, min(255, int(value)))
    return bytes([_bcd_byte(value // 100), _bcd_byte(value % 100)])


def decode_level(data: bytes) -> int:
    return _bcd_value(data[0]) * 100 + _bcd_value(data[1])


def encode_offset(hz: int) -> bytes:
    """RIT/XIT offset: 2-byte BCD of |Hz| (low pair first) + sign byte (01 = negative)."""
    mag = min(9999, abs(int(hz)))
    return encode_freq(mag, 2) + bytes([1 if hz < 0 else 0])


def decode_offset(data: bytes) -> int:
    hz = decode_freq(data[:2])
    return -hz if len(data) > 2 and data[2] else hz


class FrameParser:
    """Splits a byte stream into frames, dropping noise and collision garbage."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list:
        self._buf += data
        frames = []
        while True:
            start = self._buf.find(PREAMBLE)
            if start < 0:
                self._buf.clear()
                return frames
            if start:
                del self._buf[:start]
            end = self._buf.find(bytes([EOM]), 2)
            if end < 0:
                return frames
            raw = bytes(self._buf[:end + 1])
            del self._buf[:end + 1]
            # a collision can leave FE FE FE ... ; skip extra preamble bytes
            payload = raw[2:-1].lstrip(b"\xFE")
            if len(payload) >= 3:
                frames.append(Frame(payload[0], payload[1], payload[2], payload[3:]))
