"""G7250 LAN protocol: command frames and IQ payload decoding.

Everything here comes from two sources (see README):
  * What has been seen working on a real G7250.
  * Static analysis of Xiegu's ExtIO_G7250.dll (frequency clamp, frame layout).
"""
import struct

import numpy as np

RADIO_PORT = 50002          # radio listens for commands here
LOCAL_PORT = 50001          # PC receives the IQ stream here
DEFAULT_RADIO_IP = "192.168.3.199"

CMD_HEADER = bytes((0xAA, 0x55, 0x55, 0xAA))
CMD_SET_FREQUENCY = 0x0001

IQ_PAYLOAD_BYTES = 768
IQ_SAMPLES_PER_PACKET = 192     # 768 bytes / (16-bit I + 16-bit Q)
NOMINAL_SAMPLE_RATE = 48_000

FREQ_MIN_HZ = 100_000           # the Xiegu DLL clamps to this range
FREQ_MAX_HZ = 54_000_000

_FRAME = struct.Struct(">4sHQ")  # header, command, 64-bit big-endian argument


def clamp_frequency(hz: int) -> int:
    return max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, int(hz)))


def build_tune_frame(hz: int) -> bytes:
    """14-byte 'set LO frequency' frame, e.g. 14.074 MHz ->
    AA 55 55 AA 00 01 00 00 00 00 00 D6 C0 90."""
    return _FRAME.pack(CMD_HEADER, CMD_SET_FREQUENCY, clamp_frequency(hz))


def parse_command_frame(data: bytes):
    """Return (command, argument) for a 14-byte command frame, else None."""
    if len(data) != _FRAME.size:
        return None
    header, command, argument = _FRAME.unpack(data)
    if header != CMD_HEADER:
        return None
    return command, argument


def iq_from_payload(payload: bytes, swap_iq: bool = False) -> np.ndarray:
    """Decode one IQ payload into complex64 samples scaled to +/-1.0 full scale.

    Samples are interleaved 16-bit I, Q. Little-endian is assumed: it gives
    noise-sized values on real packet dumps, big-endian does not.
    I/Q orientation is not yet confirmed, hence swap_iq.
    """
    raw = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
    i, q = raw[0::2], raw[1::2]
    if swap_iq:
        i, q = q, i
    return (i + 1j * q).astype(np.complex64)


def encode_iq(samples: np.ndarray) -> bytes:
    """Inverse of iq_from_payload, used by the simulator."""
    inter = np.empty(samples.size * 2, dtype=np.float32)
    inter[0::2] = samples.real
    inter[1::2] = samples.imag
    return np.clip(np.round(inter * 32767.0), -32768, 32767).astype("<i2").tobytes()
