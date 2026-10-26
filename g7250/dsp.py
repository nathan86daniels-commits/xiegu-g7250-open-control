"""Turns the IQ stream into averaged spectrum frames (dBFS, fftshifted)."""
import numpy as np


def blackman_harris(n: int) -> np.ndarray:
    k = np.arange(n) * 2.0 * np.pi / (n - 1)
    return (0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k)
            - 0.01168 * np.cos(3 * k)).astype(np.float32)


class SpectrumEngine:
    def __init__(self, fft_size: int = 2048, averaging: float = 0.5):
        self.fft_size = fft_size
        self.averaging = averaging          # 0 = none, closer to 1 = smoother
        self._window = blackman_harris(fft_size)
        self._norm = float(np.sum(self._window)) ** 2
        self._pending = np.zeros(0, dtype=np.complex64)
        self._avg_power = None
        self.latest_db = None               # float32 array, lowest frequency first
        self.seq = 0                        # bumps every new frame

    def push(self, samples: np.ndarray) -> None:
        buf = np.concatenate((self._pending, samples))
        n = self.fft_size
        while buf.size >= n:
            self._process(buf[:n])
            buf = buf[n:]
        self._pending = buf

    def _process(self, block: np.ndarray) -> None:
        spectrum = np.fft.fftshift(np.fft.fft(block * self._window))
        power = (spectrum.real ** 2 + spectrum.imag ** 2) / self._norm
        if self._avg_power is None or self.averaging <= 0:
            self._avg_power = power
        else:
            a = self.averaging
            self._avg_power = a * self._avg_power + (1 - a) * power
        self.latest_db = (10.0 * np.log10(self._avg_power + 1e-20)).astype(np.float32)
        self.seq += 1
