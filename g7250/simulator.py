"""A pretend G7250 for testing without a radio.

Listens for tune frames on UDP 50002 and streams 768-byte IQ packets (192 x 16-bit
I/Q, 48 kS/s) to the PC on 50001, like the real radio. The band is filled with
fake FT8 activity, CW and a couple of carriers so the waterfall has something on it.

    python -m g7250.simulator --pc 127.0.0.1
"""
import argparse
import asyncio
import time

import numpy as np

from . import protocol

FS = protocol.NOMINAL_SAMPLE_RATE
BATCH_PACKETS = 5                                   # 5 x 192 samples = 20 ms
BATCH_SAMPLES = BATCH_PACKETS * protocol.IQ_SAMPLES_PER_PACKET
NOISE = 120 / 32768                                 # roughly -80 dBFS per FFT bin

FT8_FREQS = [1_840_000, 3_573_000, 7_074_000, 10_136_000, 14_074_000,
             18_100_000, 21_074_000, 24_915_000, 28_074_000, 50_313_000]
CW_TEXT = "CQ CQ DE G7SIM G7SIM K   "
MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "G": "--.",
         "I": "..", "K": "-.-", "M": "--", "Q": "--.-", "S": "...", "7": "--...", " ": " "}


def _morse_keying(text: str, wpm: int = 22):
    """List of (on, units) for the text, one unit = 1.2 / wpm seconds."""
    out = []
    for ch in text:
        code = MORSE[ch]
        if code == " ":
            out.append((False, 4))
            continue
        for sym in code:
            out.append((True, 1 if sym == "." else 3))
            out.append((False, 1))
        out.append((False, 2))
    return out, 1.2 / wpm


class _Signal:
    def __init__(self, freq, amp):
        self.freq = freq
        self.amp = amp
        self.phase = 0.0

    def level(self, t):
        return self.amp

    def tone_offset(self, t):
        return 0.0


class _Ft8(_Signal):
    """8-FSK, 6.25 Hz spacing, 0.16 s symbols, on for 12.64 s of each 15 s slot."""

    def __init__(self, base, amp, odd, rng):
        super().__init__(base, amp)
        self.odd = odd
        self.tones = rng.integers(0, 8, 79)

    def _slot_pos(self, t):
        slot = int(t // 15)
        return slot, t - slot * 15

    def level(self, t):
        slot, pos = self._slot_pos(t)
        return self.amp if (slot % 2 == self.odd) and 0.5 <= pos < 13.14 else 0.0

    def tone_offset(self, t):
        _, pos = self._slot_pos(t)
        sym = min(78, max(0, int((pos - 0.5) / 0.16)))
        return self.tones[sym] * 6.25


class _Cw(_Signal):
    def __init__(self, freq, amp):
        super().__init__(freq, amp)
        self.keying, self.unit = _morse_keying(CW_TEXT)
        self.total = sum(u for _, u in self.keying)

    def level(self, t):
        pos = (t / self.unit) % self.total
        for on, units in self.keying:
            if pos < units:
                return self.amp if on else 0.0
            pos -= units
        return 0.0


class _Wspr(_Signal):
    """WSPR-2: 162 four-tone symbols of 0.683 s from one second past each even minute."""

    def __init__(self, dial, audio_hz, amp, call, grid, dbm):
        from . import wspr_codec
        super().__init__(dial + audio_hz, amp)
        self.symbols = wspr_codec.encode(call, grid, dbm)
        self.sym_s, self.spacing = wspr_codec.SYMBOL_SECONDS, wspr_codec.TONE_SPACING_HZ

    def _sym(self, t):
        pos = t % 120 - 1.0
        k = int(pos // self.sym_s)
        return k if 0 <= k < len(self.symbols) else None

    def level(self, t):
        return self.amp if self._sym(t) is not None else 0.0

    def tone_offset(self, t):
        k = self._sym(t)
        return (self.symbols[k] - 1.5) * self.spacing if k is not None else 0.0


def _build_band(rng):
    signals = []
    for base in FT8_FREQS:
        for _ in range(9):
            signals.append(_Ft8(base + int(rng.integers(200, 2900)),
                                10 ** (rng.uniform(-2.9, -1.6)), int(rng.integers(0, 2)), rng))
        band = base // 1_000_000 * 1_000_000
        signals.append(_Cw(band + 25_000 + int(rng.integers(0, 8000)), 0.02))
        signals.append(_Signal(base - 9_000, 0.004))           # a steady carrier
    # two WSPR beacons on the 20 m WSPR dial, so receive can be tried without a radio
    signals.append(_Wspr(14_095_600, 1480, 4e-4, "G7SIM", "IO91", 30))
    signals.append(_Wspr(14_095_600, 1552, 1.5e-4, "K1SIM", "FN42", 23))
    return signals


class Simulator(asyncio.DatagramProtocol):
    def __init__(self, pc_ip, pc_port):
        self.pc = (pc_ip, pc_port)
        self.center = 14_074_000
        self.rng = np.random.default_rng(7250)
        self.signals = _build_band(self.rng)
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        parsed = protocol.parse_command_frame(data)
        if parsed and parsed[0] == protocol.CMD_SET_FREQUENCY:
            self.center = protocol.clamp_frequency(parsed[1])

    def power_between(self, f_lo, f_hi, t):
        """Total signal power (full scale = 1.0) between two absolute frequencies."""
        total = 0.0
        for sig in self.signals:
            f = sig.freq + sig.tone_offset(t)
            if f_lo <= f <= f_hi:
                total += sig.level(t) ** 2
        return total

    def make_batch(self, t0):
        n = np.arange(BATCH_SAMPLES, dtype=np.float64)
        iq = (self.rng.normal(0, NOISE, BATCH_SAMPLES)
              + 1j * self.rng.normal(0, NOISE, BATCH_SAMPLES))
        for sig in self.signals:
            offset = sig.freq - self.center + sig.tone_offset(t0)
            if abs(offset) > FS / 2 - 500:
                continue
            level = sig.level(t0)
            step = 2 * np.pi * offset / FS
            if level:
                iq += level * np.exp(1j * (sig.phase + step * n))
            sig.phase = (sig.phase + step * BATCH_SAMPLES) % (2 * np.pi)
        return iq.astype(np.complex64)

    async def stream(self):
        start, epoch = time.monotonic(), time.time()
        sent = 0
        while True:
            t = sent * BATCH_SAMPLES / FS
            batch = self.make_batch(epoch + t)          # stream time, so keying stays exact under sleep jitter
            for k in range(BATCH_PACKETS):
                chunk = batch[k * protocol.IQ_SAMPLES_PER_PACKET:(k + 1) * protocol.IQ_SAMPLES_PER_PACKET]
                self.transport.sendto(protocol.encode_iq(chunk), self.pc)
            sent += 1
            delay = start + t + BATCH_SAMPLES / FS - time.monotonic()
            await asyncio.sleep(max(0.0, delay))


async def run_simulator(pc_ip="127.0.0.1", pc_port=protocol.LOCAL_PORT,
                        listen_port=protocol.RADIO_PORT, listen_ip="127.0.0.1", sim=None):
    """Pass `sim` to share the band with a simulated CAT radio (see sim_civ)."""
    loop = asyncio.get_running_loop()
    sim = sim or Simulator(pc_ip, pc_port)
    await loop.create_datagram_endpoint(lambda: sim, local_addr=(listen_ip, listen_port))
    await sim.stream()


def main():
    ap = argparse.ArgumentParser(description="Pretend G7250 IQ source")
    ap.add_argument("--pc", default="127.0.0.1", help="IP of the PC running the bridge")
    ap.add_argument("--listen", default="127.0.0.1", help="IP to listen on for tune frames")
    args = ap.parse_args()
    print(f"Simulated G7250: tune on {args.listen}:{protocol.RADIO_PORT}, "
          f"IQ to {args.pc}:{protocol.LOCAL_PORT}")
    asyncio.run(run_simulator(args.pc, listen_ip=args.listen))


if __name__ == "__main__":
    main()
