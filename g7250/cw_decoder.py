"""Software Morse decoder.

Audio is mixed down at the CW pitch to a 500 Hz complex baseband, low-passed (4th-order
Butterworth whose cutoff follows the sender's speed, 30-70 Hz) and turned into an envelope.
An adaptive noise floor (with a key-up percentile check) and signal peak set the threshold
(65 % of peak, always above 2.2x noise); a leaky-vote glitch filter confirms key changes;
the dit length is learnt from the lower quartile of recent marks AND gaps. A tone squelch
only copies while a real carrier line has been seen in the last second, so hiss does not
print E E E. Pitch tracking follows an off-pitch station within +/-240 Hz.
The mixer/decimator is vectorised; the per-tick (2 ms) state machine is plain Python.
"""
import math

import numpy as np

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..",
    "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-", "+": ".-.-.",
    "-": "-....-", "(": "-.--.", ")": "-.--.-", ":": "---...", ";": "-.-.-.", "'": ".----.", '"': ".-..-.", "@": ".--.-.",
}
PATTERNS = {v: k for k, v in MORSE.items()}
PATTERNS.update({"-.--.": "<KN>", "...-.-": "<SK>", ".-...": "<AS>", "-.-.-": "<KA>", "........": "<HH>", "...-.": "<SN>"})

BASEBAND_RATE = 500
MIN_CUTOFF, MAX_CUTOFF, CUTOFF_PER_DOT_RATE = 30.0, 70.0, 1.5
SPEED_KNOWN = 8
THRESHOLD_FRACTION, OFF_HYSTERESIS = 0.65, 0.8
MIN_DIT_MS, MAX_DIT_MS = 1200 / 50, 1200 / 8
LINE_RATIO, TONE_HOLD_TICKS = 25.0, 500
SILENCE_RESET_TICKS = 1500
RUNAWAY_FAST, RUNAWAY_SLOW = 40, 8
FLOOR_WINDOW_MS, FLOOR_PERCENTILE, FLOOR_TO_MEAN = 1500, 0.2, 0.65


class _Biquad:
    def __init__(self):
        self.b0 = self.b1 = self.b2 = self.a1 = self.a2 = 0.0
        self.z1 = self.z2 = 0.0

    def low_pass(self, fs, fc, q):
        w = 2 * math.pi * fc / fs
        alpha, cos = math.sin(w) / (2 * q), math.cos(w)
        a0 = 1 + alpha
        self.b0 = (1 - cos) / 2 / a0; self.b1 = (1 - cos) / a0; self.b2 = self.b0
        self.a1 = -2 * cos / a0; self.a2 = (1 - alpha) / a0

    def next(self, x):
        y = self.b0 * x + self.z1
        self.z1 = self.b1 * x - self.a1 * y + self.z2
        self.z2 = self.b2 * x - self.a2 * y
        return y


class CwDecoder:
    def __init__(self, sample_rate=12000, pitch_hz=600, seed_wpm=20):
        self.sample_rate = 0
        self.lpf_i = [_Biquad(), _Biquad()]
        self.lpf_q = [_Biquad(), _Biquad()]
        self.cutoff = MAX_CUTOFF
        self.learned = 0
        self.phase = 0.0
        self.track_hz = 0.0
        self.ring_i = np.zeros(512); self.ring_q = np.zeros(512); self.ring_head = 0
        self.ticks_since_lock = 0
        self.ticks_since_tone = 10 ** 9
        self.tone_hits = 0
        self.quiet_ticks = 0
        self.squelch_open = False
        self.drop_until_gap = False
        self.noise = 0.0; self.peak = 0.0
        self.floor_ring = np.zeros(0); self.floor_at = 0; self.floor_count = 0
        self.warmup = 0
        self.mark = False
        self.candidate_ms = 0.0; self.state_ms = 0.0
        self.current = []
        self.recent = []
        self.char_flushed = self.word_flushed = self.line_flushed = True
        self.dit_ms = 60.0; self.home_dit_ms = 60.0
        self.offset_hz = 0.0
        self.lock_misses = 99
        self.leftover = np.zeros(0)
        self.configure(sample_rate, pitch_hz)
        self.seed_speed(seed_wpm)

    # ---- configuration -------------------------------------------------------
    @property
    def wpm(self):
        return 1200.0 / self.dit_ms

    @property
    def tone_heard(self):
        return self.ticks_since_tone <= TONE_HOLD_TICKS

    @property
    def snr_db(self):
        return 20 * math.log10(self.peak / self.noise) if self.warmup >= 50 and self.peak > self.noise > 0 else 0.0

    @property
    def pitch_lock(self):
        if self.lock_misses > 8:
            return "NO SIGNAL"
        return "LOCKED" if abs(self.offset_hz) <= 50 else "OFFSET"

    def seed_speed(self, wpm):
        self.home_dit_ms = self.dit_ms = min(MAX_DIT_MS, max(MIN_DIT_MS, 1200.0 / min(60, max(5, wpm))))
        self._update_filter()

    def set_home_speed(self, wpm):
        self.home_dit_ms = min(MAX_DIT_MS, max(MIN_DIT_MS, 1200.0 / min(60, max(5, wpm))))

    def new_station(self):
        self.learned = 1
        self._forget_speed()
        self.current.clear()
        self.char_flushed = self.word_flushed = self.line_flushed = True

    def _forget_speed(self):
        if self.dit_ms == self.home_dit_ms and self.learned == 0:
            return
        self.dit_ms = self.home_dit_ms
        self.recent.clear()
        self.learned = 0
        self._update_filter()

    def reset_after_transmit(self):
        self.ticks_since_tone = 10 ** 9
        self.warmup = 0; self.noise = 0.0; self.peak = 0.0
        self.floor_count = 0
        self.mark = False; self.candidate_ms = 0.0; self.state_ms = 0.0
        self.current.clear(); self.recent.clear(); self.learned = 0
        self.char_flushed = self.word_flushed = self.line_flushed = True
        self._update_filter()

    def _update_filter(self, force=False):
        if self.sample_rate <= 0:
            return
        target = MAX_CUTOFF if self.learned < SPEED_KNOWN else min(MAX_CUTOFF, max(MIN_CUTOFF, CUTOFF_PER_DOT_RATE * 1000.0 / self.dit_ms))
        if not force and abs(target - self.cutoff) < 0.1 * self.cutoff:
            return
        self.cutoff = target
        fs = self.sample_rate / self.decimation
        for pair in (self.lpf_i, self.lpf_q):
            pair[0].low_pass(fs, self.cutoff, 0.54119610)
            pair[1].low_pass(fs, self.cutoff, 1.30656296)

    def configure(self, sample_rate, pitch_hz):
        rate_changed = sample_rate != self.sample_rate
        self.sample_rate = sample_rate
        self.pitch_hz = min(max(pitch_hz, 100), min(3000, sample_rate / 2 - 100))
        if rate_changed:
            self.decimation = max(1, round(sample_rate / BASEBAND_RATE))
            self.tick_ms = 1000.0 * self.decimation / sample_rate
            self.leftover = np.zeros(0)
            self.lpf_i = [_Biquad(), _Biquad()]
            self.lpf_q = [_Biquad(), _Biquad()]
            self._update_filter(force=True)

    # ---- processing ------------------------------------------------------------
    def process(self, samples):
        """Feed mono float samples at sample_rate; returns decoded text (may be empty)."""
        x = np.concatenate((self.leftover, np.nan_to_num(np.asarray(samples, dtype=np.float64))))
        d = self.decimation
        usable = len(x) // d * d
        self.leftover = x[usable:]
        if not usable:
            return ""
        x = x[:usable]
        step = 2 * math.pi * (self.pitch_hz + self.track_hz) / self.sample_rate
        ph = self.phase + step * np.arange(usable)
        self.phase = (self.phase + step * usable) % (2 * math.pi)
        bi = (x * np.cos(ph)).reshape(-1, d).mean(axis=1)
        bq = (-x * np.sin(ph)).reshape(-1, d).mean(axis=1)
        out = []
        li0, li1, lq0, lq1 = self.lpf_i[0], self.lpf_i[1], self.lpf_q[0], self.lpf_q[1]
        for i, q in zip(bi, bq):
            fi = li1.next(li0.next(i)); fq = lq1.next(lq0.next(q))
            self._tick(math.sqrt(fi * fi + fq * fq) * 2, out)
            self.ring_i[self.ring_head] = i; self.ring_q[self.ring_head] = q
            self.ring_head = (self.ring_head + 1) % 512
            self.ticks_since_lock += 1
            if self.ticks_since_lock >= 125:
                self.ticks_since_lock = 0
                self._check_pitch_lock()
                li0, li1, lq0, lq1 = self.lpf_i[0], self.lpf_i[1], self.lpf_q[0], self.lpf_q[1]
        return "".join(out)

    def flush(self):
        out = []
        if self.mark:
            self.mark = False
            self.current.append(self.state_ms)
        self._emit(out)
        self.candidate_ms = self.state_ms = 0.0
        self.word_flushed = True
        return "".join(out)

    def _track_floor(self, env):
        size = max(50, int(FLOOR_WINDOW_MS / self.tick_ms))
        if len(self.floor_ring) != size:
            self.floor_ring = np.zeros(size); self.floor_at = self.floor_count = 0
        self.floor_ring[self.floor_at] = env
        self.floor_at = (self.floor_at + 1) % size
        if self.floor_count < size:
            self.floor_count += 1
            return
        if self.floor_at % 25:
            return
        floor = np.partition(self.floor_ring, int(size * FLOOR_PERCENTILE))[int(size * FLOOR_PERCENTILE)] / FLOOR_TO_MEAN
        if floor < self.noise:
            self.noise = max(1e-9, floor)

    def _tick(self, env, out):
        if self.warmup < 50:
            self.noise = (self.noise * self.warmup + env) / (self.warmup + 1)
            self.warmup += 1
            self.peak = self.noise
            return
        if (self.state_ms > 25) if not self.mark else (self.state_ms > 3000):
            sample = min(env, self.noise * 3)
            k = (0.01 if sample < self.noise else 0.004) if not self.mark else 0.002
            self.noise = max(1e-9, self.noise + (sample - self.noise) * k)
        self._track_floor(env)
        if env > self.peak:
            self.peak = env
        elif self.mark:
            self.peak += (env - self.peak) * 0.01
        else:
            self.peak *= 0.998
        self.peak = max(self.peak, self.noise)
        threshold = max(self.noise * 2.2, THRESHOLD_FRACTION * self.peak)
        if self.ticks_since_tone < 10 ** 9:
            self.ticks_since_tone += 1
        self.quiet_ticks += 1
        if self.quiet_ticks == SILENCE_RESET_TICKS and (self.wpm > RUNAWAY_FAST or self.wpm < RUNAWAY_SLOW):
            self._forget_speed()
        open_ = self.peak > self.noise * 3.0 and (self.tone_heard or self.peak > self.noise * 6.0)
        if open_ and not self.squelch_open:
            self.current.clear()
            self.drop_until_gap = self.mark or not self.char_flushed
        self.squelch_open = open_
        raw = open_ and (env > threshold * OFF_HYSTERESIS if self.mark else env > threshold)
        self.state_ms += self.tick_ms
        if raw != self.mark:
            self.candidate_ms += self.tick_ms
        else:
            self.candidate_ms = max(0.0, self.candidate_ms - self.tick_ms)
        hold = min(25.0, max(4.0, (0.2 if self.mark else 0.3) * self.dit_ms))
        if self.candidate_ms >= hold:
            self._transition(not self.mark, out)
        if not self.mark:
            self._check_gaps(out)

    def _transition(self, to_mark, out):
        ended = self.state_ms - self.candidate_ms
        if to_mark:
            self.mark = True
            if self.current and ended < 2.0 * self.dit_ms:
                self._learn(ended)
        else:
            self.mark = False
            if ended >= max(10.0, 0.4 * self.dit_ms):
                self.current.append(ended)
                self._learn(ended)
            self.char_flushed = self.word_flushed = self.line_flushed = False
        self.state_ms = self.candidate_ms
        self.candidate_ms = 0.0
        if not to_mark:
            self._check_gaps(out)

    def _check_gaps(self, out):
        if not self.char_flushed and self.state_ms >= 2.0 * self.dit_ms:
            self._emit(out); self.char_flushed = True
        if not self.word_flushed and self.state_ms >= 6.0 * self.dit_ms:
            out.append(" "); self.word_flushed = True
        if not self.line_flushed and self.state_ms >= max(2500, 20 * self.dit_ms):
            out.append("\n"); self.line_flushed = True

    def _emit(self, out):
        if self.drop_until_gap:
            self.drop_until_gap = False
            self.current.clear()
            return
        if not self.current:
            return
        if len(self.current) <= 9:
            pattern = "".join("." if m < 2.0 * self.dit_ms else "-" for m in self.current)
            out.append(PATTERNS.get(pattern, "*"))
        else:
            out.append("*")
        self.current.clear()

    def _learn(self, ms):
        if ms > 1000:
            return
        self.recent.append(ms)
        self.learned += 1
        if len(self.recent) > 16:
            self.recent.pop(0)
        n = len(self.recent)
        slowed = n >= 4 and all(d > 1.7 * self.dit_ms for d in self.recent[-4:])
        if slowed:
            self.recent = self.recent[-4:]
        if len(self.recent) < 4:
            return
        s = sorted(self.recent)
        quartile = s[len(s) // 4]
        near = [d for d in s if quartile * 0.5 <= d <= quartile * 1.6]
        est = sum(near) / len(near)
        self.dit_ms = min(MAX_DIT_MS, max(MIN_DIT_MS, est if slowed else self.dit_ms + (est - self.dit_ms) * 0.35))
        self._update_filter()

    # ---- pitch lock / tone squelch ----------------------------------------------------
    def _powers(self, freqs):
        fs = self.sample_rate / self.decimation
        idx = (self.ring_head + np.arange(512)) % 512
        z = self.ring_i[idx] + 1j * self.ring_q[idx]
        k = np.arange(512)
        ref = np.exp(-2j * np.pi * np.asarray(freqs)[:, None] * k[None, :] / fs)
        return np.abs(ref @ z) ** 2

    def _carrier_line_near(self, around):
        scan = np.arange(around - 8, around + 8.01, 0.5)
        p = self._powers(scan)
        i = int(np.argmax(p))
        peak, peak_hz = p[i], scan[i]
        offs = np.arange(3, 13)
        skirt = np.sort(self._powers(np.concatenate((peak_hz - offs, peak_hz + offs))))
        floor = skirt[len(skirt) // 2]
        return peak > 0 and peak >= LINE_RATIO * max(floor, 1e-30)

    def _check_pitch_lock(self):
        freqs = np.arange(-240, 241, 10)
        p = self._powers(freqs)
        i = int(np.argmax(p))
        best_hz, best = freqs[i], p[i]
        if self._carrier_line_near(best_hz) or (best_hz != 0 and self._carrier_line_near(0)):
            self.quiet_ticks = 0
            self.tone_hits += 1
            if self.tone_hits >= 1:
                self.ticks_since_tone = 0
        else:
            self.tone_hits = 0
        median = np.sort(p)[len(p) // 2]
        if best < median * 15:
            self.lock_misses += 1
            if self.lock_misses == 40 and self.track_hz != 0:
                self.track_hz = 0.0
            return
        measured = self.track_hz + best_hz
        self.offset_hz = measured if self.lock_misses > 8 else self.offset_hz + (measured - self.offset_hz) * 0.5
        self.lock_misses = 0
        if abs(self.offset_hz - self.track_hz) > 10:
            self.track_hz = float(min(240, max(-240, round(self.offset_hz / 5) * 5)))
            self.ring_i[:] = 0; self.ring_q[:] = 0


def keying_elements(text, wpm):
    """(key_down, milliseconds) for text, PARIS timing - used by the test bench and a PC keyer."""
    unit = 1200.0 / wpm
    out, have, word = [], False, False
    for raw in text.upper():
        if raw.isspace():
            word = have
            continue
        pat = MORSE.get(raw)
        if not pat:
            continue
        if have:
            out.append((False, (7 if word else 3) * unit))
        word = False
        for i, e in enumerate(pat):
            if i:
                out.append((False, unit))
            out.append((True, unit * (1 if e == "." else 3)))
        have = True
    return out
