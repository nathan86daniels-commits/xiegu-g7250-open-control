"""Neural CW decoder: our own CNN+GRU+CTC model (e4), the default decoder. Ham sense is deliberately
left out: plain Neural is the public default and copies faster.

  audio -> 4 kHz (streaming Kaiser low-pass decimator) -> STFT (256-point periodic Hann, 5 ms hop, centred) ->
  the 39 bins around the CW pitch (about +/-300 Hz) -> log magnitude -> minus the window's median -> /4 -> model.
  Output: CTC over " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?=.,", blank 0, class i + 1 = CHARS[i].

Low-delay copy (the default, 1 s): every 0.5 s the last 4 s of audio is decoded and only output at least
`lookahead` seconds old is kept, so copy appears about 1-1.5 s behind the audio. lookahead 0 = the original 10 s
windows stepped by 7 s (most accurate, 8-10 s late).
Every keyed station in the passband is copied (the model hears only about 60 Hz either side of where it is centred,
so it is run once per station), over by over with a new line when the sending station changes. Fast CW (above the
model's fast_wpm, 58 for e4) is played 1.3, 1.6 or 1.9 times slower to the model.

Inference runs on one background thread with ONNX Runtime limited to one thread.
"""
import json
import math
import queue
import threading
from pathlib import Path

import numpy as np

RATE = 4000
FFT_SIZE = 256
HOP = 20                       # 5 ms
BINS = 39
BIN_HZ = RATE / FFT_SIZE
CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/?=.,"
BLANK = 0
CLASSES = 43
MODEL_PATH = Path(__file__).resolve().parent / "cw_model" / "cw-model.onnx"

_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FFT_SIZE) / FFT_SIZE)).astype(np.float32)
_DFT = {}


def first_bin(pitch_hz):
    """round(pitch / 15.625) - 19 (half to even, as Python's round), kept in range."""
    return int(min(max(round(pitch_hz / BIN_HZ) - BINS // 2, 0), FFT_SIZE // 2 + 1 - BINS))


def frame_count(samples):
    return 0 if samples <= 0 else 1 + samples // HOP


def compute_features(audio, pitch_hz):
    """[frames, 39] float32 for audio already at 4 kHz (features.features)."""
    audio = np.asarray(audio, dtype=np.float32)
    frames = frame_count(audio.size)
    if frames <= 0:
        return np.zeros((0, BINS), np.float32)
    first = first_bin(pitch_hz)
    if first not in _DFT:
        n = np.arange(FFT_SIZE)
        angle = 2 * np.pi * np.outer(first + np.arange(BINS), n) / FFT_SIZE
        _DFT[first] = (np.cos(angle).T.copy(), np.sin(angle).T.copy())
    cos, sin = _DFT[first]
    pad = FFT_SIZE // 2
    padded = np.zeros(audio.size + 2 * FFT_SIZE, np.float32)
    padded[pad:pad + audio.size] = audio
    idx = (np.arange(frames) * HOP)[:, None] + np.arange(FFT_SIZE)[None, :]
    frame = (padded[idx] * _WINDOW).astype(np.float64)
    re, im = frame @ cos, frame @ sin
    out = np.log(np.sqrt(re * re + im * im) + 1e-7).astype(np.float32)
    median = float(np.median(out.astype(np.float64)))
    return ((out.astype(np.float64) - median) / 4.0).astype(np.float32)


# ---------------------------------------------------------------- streaming decimator

class CwDecimator:
    """Any rate -> 4 kHz: a linear-phase low-pass at 2 kHz (Kaiser-windowed sinc, beta 8) evaluated only at the
    output instants, with a 512-phase table. slow_down plays the audio that many times slower."""
    PHASES, BETA = 512, 8.0

    def __init__(self, input_rate, slow_down=1.0):
        self.input_rate, self.slow_down = input_rate, slow_down
        self.ratio = input_rate / (RATE * slow_down)
        self.half = max(4, math.ceil(12.5 * self.ratio))
        cutoff = 1.0 / self.ratio
        t = (np.arange(2 * self.half) - self.half + 1)[None, :] - (np.arange(self.PHASES) / self.PHASES)[:, None]
        x = cutoff * t
        sinc = np.where(np.abs(x) < 1e-12, 1.0, np.sin(np.pi * x) / np.where(np.abs(x) < 1e-12, 1.0, np.pi * x))
        r = t / self.half
        win = np.where(np.abs(r) >= 1, 0.0, np.i0(self.BETA * np.sqrt(np.clip(1 - r * r, 0, None))) / np.i0(self.BETA))
        taps = sinc * win
        self.table = (taps / taps.sum(axis=1, keepdims=True)).astype(np.float32)
        self.history = np.zeros(0, np.float32)
        self.history_start = 0
        self.input_count = 0
        self.output_index = 0

    def process(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        self.history = np.concatenate((self.history, samples))
        self.input_count += samples.size
        # how many outputs are computable: base + half < input_count
        last = int((self.input_count - self.half) / self.ratio) + 2
        idx = np.arange(self.output_index, max(self.output_index, last))
        pos = idx * self.ratio
        base = np.floor(pos).astype(np.int64)
        phase = np.round((pos - base) * self.PHASES).astype(np.int64)
        wrap = phase == self.PHASES
        phase[wrap] = 0
        base[wrap] += 1
        ok = base + self.half < self.input_count
        n = int(np.argmin(ok)) if not ok.all() else ok.size
        base, phase = base[:n], phase[:n]
        if n:
            k = (base - self.half + 1 - self.history_start)[:, None] + np.arange(2 * self.half)[None, :]
            valid = (k >= 0) & (k < self.history.size)
            vals = np.where(valid, self.history[np.clip(k, 0, max(0, self.history.size - 1))], 0.0).astype(np.float32)
            out = (vals * self.table[phase]).astype(np.float64).sum(axis=1).astype(np.float32)
        else:
            out = np.zeros(0, np.float32)
        self.output_index += n
        keep_from = int(math.floor(self.output_index * self.ratio)) - self.half
        drop = int(min(max(keep_from - self.history_start, 0), self.history.size))
        if drop:
            self.history = self.history[drop:]
            self.history_start += drop
        return out


# ---------------------------------------------------------------- stations in the passband

MAX_STATIONS = 3
_SEARCH_HZ, _FLOOR_HZ, _PRIMARY_HZ, _APART_HZ, _TAIL_S = 250, 150, 45, 75, 3.5
_THRESHOLD = 0.9 / 4


def _excess(features, start, offset):
    loud = np.percentile(features[start:].astype(np.float64), 90, axis=0)
    floor_values = loud[np.abs(offset) <= _FLOOR_HZ].astype(np.float32).astype(np.float64)
    return loud - np.percentile(floor_values, 30)


def find_centres(features, pitch_hz):
    """Offsets from the pitch (Hz) to centre the model on: the tuned station first, then up to two others."""
    frames = features.shape[0]
    if frames < 20:
        return [0.0]
    offset = (first_bin(pitch_hz) + np.arange(BINS)) * BIN_HZ - pitch_hz
    excess = _excess(features, 0, offset)
    tail = int(_TAIL_S * RATE / HOP)
    if frames > 2 * tail:
        excess = np.maximum(excess, _excess(features, frames - tail, offset))
    peaks = []
    for b in range(1, BINS - 1):
        if max(abs(offset[b - 1]), abs(offset[b]), abs(offset[b + 1])) > _SEARCH_HZ:
            continue
        here, left, right = excess[b], excess[b - 1], excess[b + 1]
        if here < _THRESHOLD or here < left or here < right:
            continue
        curve = left - 2 * here + right
        shift = 0.0 if abs(curve) < 1e-9 else 0.5 * (left - right) / curve
        peaks.append((here, offset[b] + min(max(shift, -0.5), 0.5) * BIN_HZ))
    peaks.sort(key=lambda p: -p[0])
    near = [p for p in peaks if abs(p[1]) <= _PRIMARY_HZ]
    centres = [near[0][1] if near else 0.0]
    for _, off in peaks:
        if len(centres) >= MAX_STATIONS:
            break
        if all(abs(off - c) >= _APART_HZ for c in centres):
            centres.append(off)
    return centres


# ---------------------------------------------------------------- sender speed (for the slow-down)

class CwSpeedEstimate:
    """The sender's speed on the decoder's own 4 kHz audio: the tone mixed to DC, an 8 ms boxcar, one envelope value
    per ms; over the last 6 s, the dot length, counted only when the marks clearly look like Morse."""
    BOX, STEP, HISTORY_MS, UPDATE_MS = RATE * 8 // 1000, RATE // 1000, 6000, 1000
    MIN_MARKS, MIN_CONTRAST_DB, MAX_WPM, MIN_MARK_MS, MIN_GAP_MS = 10, 15, 85, 8, 5

    def __init__(self):
        self.reset()

    def reset(self):
        self.env = []
        self.box_re = np.zeros(self.BOX - 1)
        self.box_im = np.zeros(self.BOX - 1)
        self.phase = 0.0
        self.step_count = 0
        self.since_update = 0
        self.wpm = 0.0

    def add(self, samples, pitch_hz):
        x = np.asarray(samples, dtype=np.float64)
        if not x.size:
            return False
        step = 2 * np.pi * pitch_hz / RATE
        ph = self.phase + step * np.arange(x.size)
        self.phase = float((self.phase + step * x.size) % (2 * np.pi))
        re = np.concatenate((self.box_re, x * np.cos(ph)))
        im = np.concatenate((self.box_im, -x * np.sin(ph)))
        cre, cim = np.concatenate(([0.0], np.cumsum(re))), np.concatenate(([0.0], np.cumsum(im)))
        self.box_re, self.box_im = re[-(self.BOX - 1):], im[-(self.BOX - 1):]
        # envelope at every STEP-th sample: the sum of the last BOX mixed samples
        first = self.STEP - 1 - self.step_count
        at = np.arange(first, x.size, self.STEP)
        self.step_count = (self.step_count + x.size) % self.STEP
        updated = False
        for j in at:
            end = j + self.BOX                      # index into re (which has BOX-1 history in front)
            sr, si = cre[end] - cre[end - self.BOX], cim[end] - cim[end - self.BOX]
            self.env.append(math.sqrt(sr * sr + si * si))
            if len(self.env) > self.HISTORY_MS:
                del self.env[0]
            self.since_update += 1
            if self.since_update >= self.UPDATE_MS:
                self.since_update = 0
                self.wpm = self._measure()
                updated = True
        return updated

    def _measure(self):
        if len(self.env) < self.HISTORY_MS // 2:
            return 0.0
        env = np.array(self.env, dtype=np.float32)
        s = np.sort(env)
        hi, lo = float(s[int(len(s) * 0.95)]), float(s[int(len(s) * 0.20)])
        if hi <= 0 or 20 * math.log10(hi / max(lo, 1e-12)) < self.MIN_CONTRAST_DB:
            return 0.0
        on = env > lo + 0.5 * (hi - lo)
        self._deglitch(on, True, self.MIN_MARK_MS)
        self._deglitch(on, False, self.MIN_GAP_MS)
        runs, start = [], 0
        for i in range(1, len(on) + 1):
            if i < len(on) and on[i] == on[start]:
                continue
            if start > 0 and i < len(on):
                runs.append((bool(on[start]), i - start))
            start = i
        marks = sorted(float(n) for m, n in runs if m)
        if len(marks) < self.MIN_MARKS:
            return 0.0
        guess = marks[int(len(marks) * 0.3)]
        short = [m for m in marks if m <= 2 * guess]
        unit = short[len(short) // 2]
        if unit < 1200.0 / self.MAX_WPM:
            return 0.0
        is_dot = lambda m: 0.7 * unit <= m <= 1.45 * unit
        is_dash = lambda m: 2.3 * unit <= m <= 3.9 * unit
        dots, dashes = sum(map(is_dot, marks)), sum(map(is_dash, marks))
        if dots < 0.2 * len(marks) or dashes < 0.15 * len(marks) or dots + dashes < 0.7 * len(marks):
            return 0.0
        gaps = [float(n) for m, n in runs if not m and n <= 10 * unit]
        if len(gaps) < 5 or sum(1 for g in gaps if 0.5 * unit <= g <= 1.6 * unit) < 0.3 * len(gaps):
            return 0.0
        units = sorted([m for m in marks if is_dot(m)] + [m / 3 for m in marks if is_dash(m)])
        return 1200.0 / units[len(units) // 2]

    @staticmethod
    def _deglitch(on, state, minimum):
        i, n = 0, len(on)
        while i < n:
            if on[i] != state:
                i += 1
                continue
            end = i
            while end < n and on[end] == state:
                end += 1
            if end - i < minimum and i > 0 and end < n:
                on[i:end] = not state
            i = end


# ---------------------------------------------------------------- the decoder

class _Station:
    def __init__(self, primary):
        self.offset = 0.0
        self.primary = primary
        self.previous = BLANK
        self.last_emitted = -math.inf
        self.held = ""
        self.held_from = 0.0
        self.confirmed = False


class _Job:
    def __init__(self, audio, pitch, first, last, slow_down=1.0, start=0.0, follow=False,
                 keep_from=None, keep_to=None, real_seconds=None, kind="decode"):
        self.audio, self.pitch, self.first, self.last = audio, pitch, first, last
        self.slow_down, self.start, self.follow = slow_down, start, follow
        self.keep_from, self.keep_to, self.real_seconds, self.kind = keep_from, keep_to, real_seconds, kind


class NeuralCwDecoder:
    WINDOW_S, EDGE_S = 10.0, 1.5
    OUTPUT_HOP_S = 0.010
    MINIMUM_FINAL_S = 0.5
    OVER_GAP_S = 2.5
    STRAY_CHARACTERS = 3
    FAST_WINDOW_S, FAST_STEP_S = 4.0, 0.5
    SLOW_DOWN_STEPS = (1.0, 1.3, 1.6, 1.9)
    DEFAULT_FAST_WPM, HYSTERESIS, UNKNOWN_SPEED_RESET_S = 44.0, 4.0, 8.0

    def __init__(self, model_path=MODEL_PATH, lookahead=1.0, inline=False):
        self.model_path = Path(model_path)
        self.fast_wpm = self._read_fast_wpm(self.model_path)
        self.follow_stations = True
        self.auto_slow_down = True
        self.status, self.ready = "LOADING MODEL", False
        self._session = None
        self._session_lock = threading.Lock()
        self._output = queue.SimpleQueue()
        self._stations, self._last_station, self._last_char = [], None, " "
        self.station_count = 0
        # stream state (caller's thread), 4 kHz
        self._window4k = int(self.WINDOW_S * RATE)
        self._step4k = int((self.WINDOW_S - 2 * self.EDGE_S) * RATE)
        self._fast_window4k = int(self.FAST_WINDOW_S * RATE)
        self._fast_step4k = int(self.FAST_STEP_S * RATE)
        self._buffer = np.zeros(self._window4k, np.float32)
        self._buffered = 0
        self._decimator = None
        self._first_window, self._flushed = True, True
        self._recent = np.zeros(0, np.float32)
        self._fast_total = 0
        self._since_step = 0
        self._kept_up_to = 0.0
        self._lookahead = 0.0
        self._last_pitch = 700.0
        self._window_start = 0.0
        self._speed = CwSpeedEstimate()
        self.slow_down = 1.0
        self.sender_wpm = 0.0
        self._since_speed = 0.0
        self._last_measure = 0.0
        self._inline = inline
        if inline:
            self._ensure_session()
        else:
            self._jobs = queue.Queue()
            self._worker = threading.Thread(target=self._work, name="Neural CW decoder", daemon=True)
            self._worker.start()
            self._jobs.put(_Job(None, 0, False, False, kind="load"))
        self.lookahead = lookahead

    # ---- settings -------------------------------------------------------------------------
    @staticmethod
    def _read_fast_wpm(model_path):
        try:
            wpm = float(json.loads(Path(model_path).with_suffix(".json").read_text())["fast_wpm"])
            return wpm if 20 <= wpm <= 80 else NeuralCwDecoder.DEFAULT_FAST_WPM
        except Exception:
            return NeuralCwDecoder.DEFAULT_FAST_WPM

    @property
    def lookahead(self):
        return self._lookahead

    @lookahead.setter
    def lookahead(self, value):
        value = min(max(float(value), 0.0), self.FAST_WINDOW_S - 2 * self.FAST_STEP_S)
        if value == self._lookahead:
            return
        self.flush(self._last_pitch)
        self._lookahead = value

    @property
    def low_delay(self):
        return self._lookahead > 0

    @classmethod
    def choose_slow_down(cls, current, sender_wpm, fast_wpm):
        if sender_wpm <= 0:
            return current
        level = cls.SLOW_DOWN_STEPS.index(current) if current in cls.SLOW_DOWN_STEPS else 0
        while level < len(cls.SLOW_DOWN_STEPS) - 1 and sender_wpm / cls.SLOW_DOWN_STEPS[level] > fast_wpm:
            level += 1
        while level > 0 and sender_wpm / cls.SLOW_DOWN_STEPS[level - 1] <= fast_wpm - cls.HYSTERESIS:
            level -= 1
        return cls.SLOW_DOWN_STEPS[level]

    # ---- stream (caller's thread) ----------------------------------------------------------
    def process(self, samples, rate, pitch_hz):
        samples = np.asarray(samples, dtype=np.float32)
        if not samples.size or rate <= 0:
            return
        block = max(1, rate // 10)          # act on a speed change promptly however much audio comes at once
        for at in range(0, samples.size, block):
            self._process_block(samples[at:at + block], rate, pitch_hz)

    def _process_block(self, samples, rate, pitch_hz):
        d = self._decimator
        if d is None or d.input_rate != rate or d.slow_down != self.slow_down:
            if d is not None and d.input_rate != rate:
                self.reset()
            self._decimator = CwDecimator(rate, self.slow_down)
        x = self._decimator.process(samples)
        model_pitch = pitch_hz / self.slow_down
        self._last_pitch = pitch_hz
        offset = 0
        while self.low_delay and offset < x.size:
            take = min(x.size - offset, self._fast_step4k - self._since_step)
            self._recent = np.concatenate((self._recent, x[offset:offset + take]))[-self._fast_window4k:]
            self._fast_total += take
            self._since_step += take
            offset += take
            self._flushed = False
            if self._since_step < self._fast_step4k:
                break
            self._since_step = 0
            self._enqueue_low_delay(model_pitch, last=False)
        while not self.low_delay and offset < x.size:
            take = min(x.size - offset, self._window4k - self._buffered)
            self._buffer[self._buffered:self._buffered + take] = x[offset:offset + take]
            self._buffered += take
            offset += take
            self._flushed = False
            if self._buffered < self._window4k:
                break
            self._enqueue(_Job(self._buffer.copy(), model_pitch, self._first_window, False, self.slow_down,
                               self._window_start, self.follow_stations))
            self._first_window = False
            self._window_start += self._step4k / RATE
            self._buffer[:self._window4k - self._step4k] = self._buffer[self._step4k:].copy()
            self._buffered = self._window4k - self._step4k
        if self.auto_slow_down:
            self._follow_speed(x, model_pitch, samples.size / rate, pitch_hz)
        elif self.slow_down != 1:
            self._change_slow_down(1.0, pitch_hz)

    def _follow_speed(self, x, model_pitch, seconds, pitch_hz):
        self._since_speed += seconds
        if self._speed.add(x, model_pitch):
            wpm = self._speed.wpm * self.slow_down
            agrees = wpm > 0 and self._last_measure > 0 and abs(wpm - self._last_measure) <= 0.15 * self._last_measure
            self._last_measure = wpm
            if agrees:
                self.sender_wpm = wpm
                self._since_speed = 0
                wanted = self.choose_slow_down(self.slow_down, self.sender_wpm, self.fast_wpm)
                if wanted != self.slow_down:
                    self._change_slow_down(wanted, pitch_hz)
                return
        if self._since_speed > self.UNKNOWN_SPEED_RESET_S:
            self.sender_wpm = 0.0
            self._since_speed = 0
            if self.slow_down != 1:
                self._change_slow_down(1.0, pitch_hz)

    def _change_slow_down(self, slow_down, pitch_hz):
        self.flush(pitch_hz)
        self.slow_down = slow_down
        self._decimator = None

    def _enqueue_low_delay(self, model_pitch, last):
        end = self._fast_total / RATE
        start = end - self._recent.size / RATE
        keep_to = math.inf if last else end - self._lookahead
        if not last and keep_to <= self._kept_up_to:
            return
        self._enqueue(_Job(self._recent.copy(), model_pitch, self._first_window, last, self.slow_down, start,
                           self.follow_stations,
                           keep_from=-math.inf if self._first_window else self._kept_up_to, keep_to=keep_to,
                           real_seconds=(max(0.0, end - self._kept_up_to) if last else keep_to - self._kept_up_to) / self.slow_down))
        self._first_window = False
        if not last:
            self._kept_up_to = keep_to

    def flush(self, pitch_hz):
        """Decodes what is buffered as a final window (end of an over: TX, retune, mode change), then starts afresh."""
        if self._flushed:
            return
        if self.low_delay:
            if self._recent.size >= self.MINIMUM_FINAL_S * RATE:
                self._enqueue_low_delay(pitch_hz / self.slow_down, last=True)
            self.reset()
            return
        skip = 0 if self._first_window else int(self.EDGE_S * RATE)
        if self._buffered - skip >= self.MINIMUM_FINAL_S * RATE:
            self._enqueue(_Job(self._buffer[:self._buffered].copy(), pitch_hz / self.slow_down, self._first_window, True,
                               self.slow_down, self._window_start, self.follow_stations))
        self.reset()

    def reset(self):
        self._decimator = None
        self._speed.reset()
        self._window_start = 0.0
        self._last_measure = 0.0
        self._buffered = 0
        self._recent = np.zeros(0, np.float32)
        self._fast_total = 0
        self._since_step = 0
        self._kept_up_to = 0.0
        self._first_window = True
        self._flushed = True
        self._enqueue(_Job(None, 0, False, False, kind="reset"))

    def take_text(self):
        out = []
        while True:
            try:
                out.append(self._output.get_nowait())
            except queue.Empty:
                return "".join(out)

    def close(self):
        if not self._inline:
            self._jobs.put(None)

    # ---- worker ---------------------------------------------------------------------------
    def _enqueue(self, job):
        if self._inline:
            self._execute(job)
        else:
            self._jobs.put(job)

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._execute(job)
            except Exception:
                self.ready, self.status = False, "MODEL ERROR"     # never take the app down

    def _execute(self, job):
        if job.kind == "load":
            self._ensure_session()
            return
        if job.kind == "reset":
            if self._last_char not in (" ", "\n"):
                self._output.put(" ")
                self._last_char = " "
            self._stations.clear()
            self._last_station = None
            return
        self._ensure_session()
        self._decode_window(job)

    def _ensure_session(self):
        if self._session is not None:
            return
        if not self.model_path.exists():
            self.ready, self.status = False, "NO MODEL FILE"
            return
        try:
            import onnxruntime as ort
        except ImportError:
            self.ready, self.status = False, "ONNXRUNTIME NOT INSTALLED"
            return
        try:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(str(self.model_path), opts, providers=["CPUExecutionProvider"])
            if "features" not in [i.name for i in session.get_inputs()] or "log_probs" not in [o.name for o in session.get_outputs()]:
                self.ready, self.status = False, "NOT A CW MODEL"
                return
            with self._session_lock:
                self._session = session
            self.ready, self.status = True, "READY"
        except Exception:
            self.ready, self.status = False, "MODEL WON'T LOAD"

    def _run(self, features, start_s, first, last, keep_from=None, keep_to=None):
        """(log_probs [n, 43], first_time) for the output frames this window owns, or None when it could not run."""
        frames = features.shape[0] - features.shape[0] % 2
        if frames < 4 or self._session is None:
            return None
        out = self._session.run(["log_probs"], {"features": features[:frames].reshape(1, 1, frames, BINS)})[0][0]
        if out.shape[1] != CLASSES:
            self.ready, self.status = False, "MODEL HAS WRONG OUTPUT"
            return None
        lo = keep_from if keep_from is not None else (start_s if first else start_s + self.EDGE_S)
        hi = keep_to if keep_to is not None else (math.inf if last else start_s + self.WINDOW_S - self.EDGE_S)
        t = start_s + np.arange(out.shape[0]) * self.OUTPUT_HOP_S
        keep = np.nonzero((t >= lo) & (t < hi))[0]
        if not keep.size:
            return np.zeros((0, CLASSES), np.float32), 0.0
        return out[keep[0]:keep[-1] + 1], start_s + keep[0] * self.OUTPUT_HOP_S

    def _decode_window(self, job):
        """The model centred on each station, then the copy merged over by over in time order."""
        features = compute_features(job.audio, job.pitch)
        centres = find_centres(features, job.pitch) if job.follow else [0.0]
        self.station_count = len(centres)
        used, emitted = [], []
        kept_end = math.inf if job.last else (job.keep_to if job.keep_to is not None else job.start + self.WINDOW_S - self.EDGE_S)
        for i, centre in enumerate(centres):
            primary = i == 0
            station = next((s for s in self._stations if s.primary == primary and s not in used
                            and (primary or abs(s.offset - centre) <= 30)), None)
            if station is None:
                station = _Station(primary)
                self._stations.append(station)
            station.offset = centre
            used.append(station)
            pitch = job.pitch + centre
            feats = features if first_bin(pitch) == first_bin(job.pitch) else compute_features(job.audio, pitch)
            piece = self._run(feats, job.start, job.first, job.last, job.keep_from, job.keep_to)
            if piece is None:
                return
            log_probs, first_time = piece
            for t, best in enumerate(np.argmax(log_probs, axis=1) if len(log_probs) else []):
                if best != BLANK and best != station.previous:
                    emitted.append((first_time + t * self.OUTPUT_HOP_S, station, CHARS[best - 1]))
                station.previous = int(best)
        self._stations = [s for s in self._stations if s in used]

        overs = []                               # [station, start, end, continues, text]
        for station in used:
            over = None
            for time, _, c in sorted((e for e in emitted if e[1] is station), key=lambda e: e[0]):
                if over is None or time - over[2] > self.OVER_GAP_S:
                    continuing = time - station.last_emitted <= self.OVER_GAP_S
                    if c == " " and not (over is None and continuing):
                        continue
                    over = [station, time, time, continuing, ""]
                    overs.append(over)
                over[4] += c
                over[2] = time
                station.last_emitted = time

        low_delay = job.keep_to is not None
        text = []
        for station, start, end, continues, piece in sorted(overs, key=lambda o: o[1]):
            if low_delay and not station.primary:
                if not continues:
                    station.held, station.confirmed = "", False
                if not station.confirmed:
                    station.held += piece
                    if sum(1 for c in station.held if c not in " \n") < self.STRAY_CHARACTERS:
                        continue                # held (or dropped)
                    piece, station.held, station.confirmed, continues = station.held, "", True, False
            else:
                letters = sum(1 for c in piece if c != " ")
                if not station.primary and not continues and end < kept_end - self.OVER_GAP_S and letters < self.STRAY_CHARACTERS:
                    continue
            if self._last_station is not None and station is not self._last_station:
                self._write(text, "\n")
            elif not continues:
                self._write(text, " ")
            for c in piece:
                self._write(text, c)
            self._last_station = station
        if text:
            self._output.put("".join(text))

    def _write(self, text, c):
        """No doubled spaces and no space at the start of a line."""
        if c == " " and self._last_char in (" ", "\n"):
            return
        if c == "\n" and self._last_char == "\n":
            return
        if c == "\n" and self._last_char == " " and text and text[-1] == " ":
            text.pop()
        text.append(c)
        self._last_char = c
