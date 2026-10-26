"""Demodulates the 48 kS/s IQ stream into 12 kHz audio for listening over the LAN.

The IQ is centred on the tuned (VFO) frequency, so the passband sits at a fixed offset
from 0 Hz: +low..+high for USB, -high..-low for LSB, +/- half the width for CW/AM/FM.
"""
import numpy as np
from scipy import signal

FS = 48_000
AUDIO_FS = 12_000
DECIM = FS // AUDIO_FS
BLOCK = 960                      # 20 ms of IQ per processing step

DEFAULT_PASSBAND = {"USB": (100, 2900), "LSB": (100, 2900), "DIG": (100, 3000),
                    "RTTY": (100, 3000), "RTTY-R": (100, 3000), "CW": (350, 850),
                    "CW-R": (350, 850), "AM": (0, 4500), "FM": (0, 6000)}


class Demodulator:
    def __init__(self, agc=True, as_float=False):
        self.agc = agc                       # off for decoders (WSPR): fixed gain keeps the noise statistics honest
        self.as_float = as_float             # float32 numpy output instead of int16 PCM bytes
        self.mode = "USB"
        self.low, self.high = DEFAULT_PASSBAND["USB"]
        self.pitch = 600
        self.offset_hz = 0.0                 # VFO minus IQ centre: the passband need not sit at the centre
        self._mix_phase = 0.0
        self.squelch_dbfs = None             # None = open
        self._pending = np.zeros(0, dtype=np.complex64)
        self._n = 0                           # running sample index (BFO phase, decimation)
        self._gain = 1.0
        self._dc_zi = np.zeros(1)
        self._prev = 1 + 0j
        self.level_dbfs = -150.0              # passband power, for squelch and display
        self._lp = signal.firwin(95, 5000, fs=FS)
        self._lp_zi = np.zeros(len(self._lp) - 1)
        self._design()

    # ---- configuration ----------------------------------------------------

    def configure(self, mode=None, low=None, high=None, pitch=None):
        if mode and mode != self.mode:
            self.mode = mode
            if low is None and high is None:
                self.low, self.high = DEFAULT_PASSBAND.get(mode, (100, 2900))
        if low is not None:
            self.low = max(0, int(low))
        if high is not None:
            self.high = max(self.low + 50, int(high))
        if pitch is not None:
            self.pitch = int(pitch)
        self._design()

    def passband(self):
        """(lo, hi) in Hz relative to the tuned frequency."""
        m = self.mode
        if m in ("USB", "DIG", "RTTY"):
            return self.low, self.high
        if m in ("LSB", "RTTY-R"):
            return -self.high, -self.low
        if m in ("CW", "CW-R"):
            half = (self.high - self.low) / 2
            return -half, half
        return -self.high, self.high

    def _design(self):
        lo, hi = self.passband()
        centre, half = (lo + hi) / 2, max(60.0, (hi - lo) / 2)
        n = np.arange(255) - 127
        base = signal.firwin(255, half, fs=FS, window=("kaiser", 8.0))   # ~80 dB stopband
        self._taps = (base * np.exp(2j * np.pi * centre * n / FS)).astype(np.complex128)
        self._zi = np.zeros(len(self._taps) - 1, dtype=np.complex128)

    # ---- processing -------------------------------------------------------

    def push(self, iq: np.ndarray):
        """Feed IQ; returns int16 PCM bytes at 12 kHz (possibly empty)."""
        buf = np.concatenate((self._pending, iq))
        out = []
        while buf.size >= BLOCK:
            out.append(self._block(buf[:BLOCK]))
            buf = buf[BLOCK:]
        self._pending = buf
        if self.as_float:
            return np.concatenate(out) if out else np.zeros(0, np.float32)
        return b"".join(out)

    def _block(self, x):
        if self.offset_hz:
            # bring the VFO to 0 Hz; phase carried across blocks so a moving offset stays click-free
            step = -2 * np.pi * self.offset_hz / FS
            x = x * np.exp(1j * (self._mix_phase + step * np.arange(x.size)))
            self._mix_phase = (self._mix_phase + step * x.size) % (2 * np.pi)
        y, self._zi = signal.lfilter(self._taps, 1.0, x.astype(np.complex128), zi=self._zi)
        power = float(np.mean(np.abs(y) ** 2)) + 1e-20
        self.level_dbfs = 10 * np.log10(power)
        n = self._n + np.arange(x.size)
        m = self.mode
        if m in ("CW", "CW-R"):
            bfo = self.pitch if m == "CW" else -self.pitch
            a = (y * np.exp(2j * np.pi * bfo * n / FS)).real
        elif m == "AM":
            # envelope through a DC blocker: y[n] = x[n] - x[n-1] + 0.9995 y[n-1]
            a, self._dc_zi = signal.lfilter([1, -1], [1, -0.9995], np.abs(y), zi=self._dc_zi)
        elif m == "FM":
            prev = np.concatenate(([self._prev], y[:-1]))
            a = np.angle(y * np.conj(prev)) / np.pi * 0.5
            self._prev = y[-1]
        else:
            a = y.real
        self._n += x.size

        # AGC: fast attack, slow recovery, ramped across the block to avoid clicks
        if not self.agc:
            a, self._lp_zi = signal.lfilter(self._lp, 1.0, a * 100.0, zi=self._lp_zi)
            phase = (-(self._n - x.size)) % DECIM
            return a[phase::DECIM].astype(np.float32)
        peak = float(np.max(np.abs(a))) + 1e-9
        target = min(2000.0, 0.35 / peak) if m != "FM" else 1.0
        new_gain = target if target < self._gain else self._gain + (target - self._gain) * 0.04
        ramp = np.linspace(self._gain, new_gain, a.size)
        self._gain = new_gain
        a = a * ramp

        if self.squelch_dbfs is not None and self.level_dbfs < self.squelch_dbfs:
            a = a * 0.0

        a, self._lp_zi = signal.lfilter(self._lp, 1.0, a, zi=self._lp_zi)
        phase = (-(self._n - x.size)) % DECIM
        a = a[phase::DECIM]
        if self.as_float:
            return a.astype(np.float32)
        return np.clip(a * 32767, -32768, 32767).astype("<i2").tobytes()
