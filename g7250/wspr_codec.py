"""WSPR-2 encoder and decoder.

Both were written from K1JT's published protocol description, not from WSJT-X's source:
  * Encoder: Type 1 (CALL LOC4 dBm), 50-bit packing, K=32 r=1/2 convolutional code
    (0xF2D05351 / 0xE4613C47), bit-reversal interleave, 162-bit sync vector.
  * Decoder: 12 kHz slot -> 375 Hz complex baseband around 1500 Hz, candidate search on the
    average spectrum, coarse + refined time/frequency/drift fit against the sync vector,
    3-symbol coherent soft decisions, stack sequential decoder (Fano metric), unpack of
    types 1/2/3 (hashed calls via Jenkins lookup3, seed 146), subtraction and three passes.
The hot loops (spectrogram, coarse sync search, tone correlations) are vectorised with numpy.
"""
import heapq
import math

import numpy as np

SYMBOLS = 162
SYMBOL_SECONDS = 8192 / 12000
TONE_SPACING_HZ = 12000 / 8192
VALID_DBM = [d for d in range(61) if d % 10 in (0, 3, 7)]
POLY1, POLY2 = 0xF2D05351, 0xE4613C47
SYNC = np.array([
    1,1,0,0,0,0,0,0,1,0,0,0,1,1,1,0,0,0,1,0,0,1,0,1,1,1,1,0,0,0,0,0,0,0,1,0,0,1,0,1,0,0,0,0,0,0,1,0,
    1,1,0,0,1,1,0,1,0,0,0,1,1,0,1,0,0,0,0,1,1,0,1,0,1,0,1,0,1,0,0,1,0,0,1,0,1,1,0,0,0,1,1,0,1,0,1,0,
    0,0,1,0,0,0,0,0,1,0,0,1,0,0,1,1,1,0,1,1,0,0,1,1,0,1,0,0,0,1,1,1,0,0,0,0,0,1,0,1,0,0,1,1,0,0,0,0,
    0,0,0,1,1,0,1,0,1,1,0,0,0,1,1,0,0,0], dtype=np.int8)


def _rev8(v):
    r = 0
    for b in range(8):
        r |= ((v >> b) & 1) << (7 - b)
    return r


def _interleave_positions():
    pos, i = [], 0
    while len(pos) < SYMBOLS:
        j = _rev8(i)
        if j < SYMBOLS:
            pos.append(j)
        i += 1
    return np.array(pos)


CODED_BIT_POSITION = _interleave_positions()


def _parity(v):
    return bin(v).count("1") & 1


def _call_value(c):
    if "0" <= c <= "9":
        return ord(c) - 48
    if "A" <= c <= "Z":
        return ord(c) - 55
    return 36


def normalise_call(callsign):
    c = (callsign or "").strip().upper()
    if not 3 <= len(c) <= 6 or not all(ch.isascii() and (ch.isupper() or ch.isdigit()) for ch in c):
        return None
    if not c[2].isdigit() and c[1].isdigit():
        c = " " + c
    if len(c) > 6 or not c[2].isdigit():
        return None
    c = c.ljust(6)
    if any(ch.isdigit() for ch in c[3:]):
        return None
    return c


def normalise_locator(locator):
    l = (locator or "").strip().upper()
    if len(l) < 4:
        return None
    l = l[:4]
    return l if "A" <= l[0] <= "R" and "A" <= l[1] <= "R" and l[2].isdigit() and l[3].isdigit() else None


def validate(callsign, locator, dbm):
    if normalise_call(callsign) is None:
        return "Callsign must be a plain call of up to 6 characters with a digit in the 2nd or 3rd place (no / prefix or suffix)."
    if normalise_locator(locator) is None:
        return "Locator must be a Maidenhead square such as IO83."
    if dbm not in VALID_DBM:
        return "Power must be a WSPR level (0-60 dBm ending in 0, 3 or 7)."
    return None


def _call_field(call):
    n = _call_value(call[0])
    n = n * 36 + _call_value(call[1])
    n = n * 10 + _call_value(call[2])
    for ch in call[3:6]:
        n = n * 27 + (26 if ch == " " else _call_value(ch) - 10)
    return n


def encode_bits(data):
    """Channel symbols (0..3) for 50 source bits."""
    coded = []
    reg = 0
    for bit in range(81):
        reg = ((reg << 1) | ((data >> (49 - bit)) & 1 if bit < 50 else 0)) & 0xFFFFFFFF
        coded.append(_parity(reg & POLY1))
        coded.append(_parity(reg & POLY2))
    inter = np.zeros(SYMBOLS, dtype=np.int8)
    inter[CODED_BIT_POSITION] = coded
    return (SYNC + 2 * inter).astype(np.int8)


def encode(callsign, locator, dbm):
    err = validate(callsign, locator, dbm)
    if err:
        raise ValueError(err)
    call, grid = normalise_call(callsign), normalise_locator(locator)
    n = _call_field(call)
    m = (179 - 10 * (ord(grid[0]) - 65) - int(grid[2])) * 180 + 10 * (ord(grid[1]) - 65) + int(grid[3])
    m = m * 128 + dbm + 64
    return encode_bits((n << 22) | m)


def call_hash(call):
    """15-bit callsign hash (Jenkins lookup3, seed 146), as WSPR uses for hashed calls."""
    k = call.strip().upper().encode("ascii")
    M = 0xFFFFFFFF
    rot = lambda v, s: ((v << s) | (v >> (32 - s))) & M
    a = b = c = (0xDEADBEEF + len(k) + 146) & M
    i, ln = 0, len(k)
    while ln > 12:
        a = (a + k[i] + (k[i+1] << 8) + (k[i+2] << 16) + (k[i+3] << 24)) & M
        b = (b + k[i+4] + (k[i+5] << 8) + (k[i+6] << 16) + (k[i+7] << 24)) & M
        c = (c + k[i+8] + (k[i+9] << 8) + (k[i+10] << 16) + (k[i+11] << 24)) & M
        a = (a - c) & M; a ^= rot(c, 4); c = (c + b) & M
        b = (b - a) & M; b ^= rot(a, 6); a = (a + c) & M
        c = (c - b) & M; c ^= rot(b, 8); b = (b + a) & M
        a = (a - c) & M; a ^= rot(c, 16); c = (c + b) & M
        b = (b - a) & M; b ^= rot(a, 19); a = (a + c) & M
        c = (c - b) & M; c ^= rot(b, 4); b = (b + a) & M
        ln -= 12; i += 12
    if ln == 0:
        return c & 32767
    if ln >= 12: c = (c + (k[i+11] << 24)) & M
    if ln >= 11: c = (c + (k[i+10] << 16)) & M
    if ln >= 10: c = (c + (k[i+9] << 8)) & M
    if ln >= 9: c = (c + k[i+8]) & M
    if ln >= 8: b = (b + (k[i+7] << 24)) & M
    if ln >= 7: b = (b + (k[i+6] << 16)) & M
    if ln >= 6: b = (b + (k[i+5] << 8)) & M
    if ln >= 5: b = (b + k[i+4]) & M
    if ln >= 4: a = (a + (k[i+3] << 24)) & M
    if ln >= 3: a = (a + (k[i+2] << 16)) & M
    if ln >= 2: a = (a + (k[i+1] << 8)) & M
    a = (a + k[i]) & M
    c ^= b; c = (c - rot(b, 14)) & M
    a ^= c; a = (a - rot(c, 11)) & M
    b ^= a; b = (b - rot(a, 25)) & M
    c ^= b; c = (c - rot(b, 16)) & M
    a ^= c; a = (a - rot(c, 4)) & M
    b ^= a; b = (b - rot(a, 14)) & M
    c ^= b; c = (c - rot(b, 24)) & M
    return c & 32767


def encode_hashed(call, locator6, dbm):
    """<CALL> LOC6 form (test helper: the app transmits only Type 1)."""
    l = locator6.upper()
    n = _call_field(l[1:] + l[0])
    m = call_hash(call) * 128 - (dbm + 1) + 64
    return encode_bits((n << 22) | m)


def tones(symbols, audio_hz=1500.0, rate=48000, amplitude=0.5, lead_in_s=1.0):
    """Continuous-phase 4-FSK audio for a slot: silence for lead_in_s, then 162 symbols. audio_hz = tone 1.5 centre."""
    sps = SYMBOL_SECONDS * rate
    total = int(round(lead_in_s * rate + SYMBOLS * sps))
    out = np.zeros(total, dtype=np.float32)
    start = int(round(lead_in_s * rate))
    n = np.arange(total - start)
    k = np.minimum((n / sps).astype(int), SYMBOLS - 1)
    f = audio_hz + (np.asarray(symbols)[k] - 1.5) * TONE_SPACING_HZ
    phase = 2 * np.pi * np.cumsum(f) / rate
    ramp = np.minimum(1.0, np.minimum(n, len(n) - n) / (0.005 * rate))     # 5 ms soft start/stop
    out[start:] = (amplitude * np.sin(phase) * ramp).astype(np.float32)
    return out


# =============================================================== decoder

SAMPLE_RATE = 12000
BIG_FFT = 1 << 21
BASE_FFT = BIG_FFT // 32
RATE = 375.0
SYMBOL_SAMPLES = 256
TONE_HZ = RATE / SYMBOL_SAMPLES
SPEC_FFT = 512
SPEC_STEP = 128
BIN_HZ = RATE / SPEC_FFT
CENTRE_AUDIO_HZ = 1500.0
SEARCH_HZ = 110.0
MAX_CANDIDATES = 60
MAX_POPS = 150_000           # kept small: Python spends this budget slowly
STRONG_SYNC = 0.12
BLOCK_HALF = 1
SIGNAL_HALF = 6
SUBTRACT_HALF = 1
_N = np.arange(SYMBOL_SAMPLES)


def baseband(samples):
    """The 1312.5-1687.5 Hz part of the audio as complex samples at 375 Hz (0 Hz = 1500 Hz audio)."""
    n = min(len(samples), BIG_FFT)
    big = np.fft.fft(np.asarray(samples[:n], dtype=np.float64), BIG_FFT)
    centre = int(CENTRE_AUDIO_HZ * BIG_FFT / SAMPLE_RATE)
    idx = (np.arange(-BASE_FFT // 2, BASE_FFT // 2) + BASE_FFT) % BASE_FFT
    small = np.zeros(BASE_FFT, dtype=np.complex128)
    small[idx] = big[centre - BASE_FFT // 2: centre + BASE_FFT // 2] / BIG_FFT
    x = np.fft.ifft(small) * BASE_FFT
    return x[:min(BASE_FFT, n // 32)].copy()


def _snr(ratio):
    return int(round(max(-33.0, 10 * math.log10(max(7 * ratio, 1e-4)) - 34.9)))


def _candidates(x):
    steps = (len(x) - SYMBOL_SAMPLES) // SPEC_STEP + 1
    if steps < 2 * SYMBOLS:
        return [], None
    frames = np.zeros((steps, SPEC_FFT), dtype=np.complex128)
    for s in range(steps):
        frames[s, :SYMBOL_SAMPLES] = x[s * SPEC_STEP: s * SPEC_STEP + SYMBOL_SAMPLES]
    ps = np.abs(np.fft.fftshift(np.fft.fft(frames, axis=1), axes=1)) ** 2
    avg = ps.mean(axis=0)
    half = int(150 / BIN_HZ)
    band = np.sort(avg[SPEC_FFT // 2 - half: SPEC_FFT // 2 + half])
    noise = band[int(len(band) * 0.3)]
    if noise <= 0:
        return [], ps
    smooth = np.zeros(SPEC_FFT)
    for k in range(3, SPEC_FFT - 3):
        smooth[k] = avg[k - 3:k + 4].sum()
    reach = int(SEARCH_HZ / BIN_HZ)
    found = []
    for k in range(SPEC_FFT // 2 - reach, SPEC_FFT // 2 + reach + 1):
        ratio = smooth[k] / (7 * noise) - 1
        if ratio > 0.12 and smooth[k] >= smooth[k - 1] and smooth[k] > smooth[k + 1]:
            found.append((k, ratio))
    found.sort(key=lambda c: -c[1])
    return [((k - SPEC_FFT // 2) * BIN_HZ, _snr(r), k) for k, r in found[:MAX_CANDIDATES]], ps


def _coarse(ps, peak_bin):
    steps = ps.shape[0]
    k = np.arange(SYMBOLS)
    sign = np.where(SYNC == 1, 1.0, -1.0)
    best, fit = -np.inf, None
    lags = np.arange(-6, 17)
    for drift in range(-4, 5):
        dk = np.round(drift * (k - 81) / 162.0).astype(int)
        for f0 in range(peak_bin - 7, peak_bin + 2):
            b = f0 + dk
            okb = (b >= 0) & (b + 6 < SPEC_FFT)
            s = lags[:, None] + 2 * k[None, :]                      # (lags, 162)
            ok = okb[None, :] & (s >= 0) & (s < steps)
            sc = np.clip(s, 0, steps - 1)
            bc = np.clip(b, 0, SPEC_FFT - 7)[None, :]
            p0, p1, p2, p3 = (ps[sc, bc + j] for j in (0, 2, 4, 6))
            m = (p1 + p3 - p0 - p2) * sign[None, :]
            num = np.where(ok, m, 0).sum(axis=1)
            den = np.where(ok, p0 + p1 + p2 + p3, 0).sum(axis=1)
            i = int(np.argmax(num))
            if num[i] > best:
                best = num[i]
                fit = [(f0 - SPEC_FFT // 2) * BIN_HZ, drift * BIN_HZ, int(lags[i]) * SPEC_STEP, num[i] / den[i] if den[i] > 0 else 0.0]
    return fit


def _tone0(fit, k):
    return fit[0] + fit[1] * (k - 81) / 162.0


def _segments(x, start):
    idx = start + np.arange(SYMBOLS)[:, None] * SYMBOL_SAMPLES + _N[None, :]
    ok = (idx >= 0) & (idx < len(x))
    return np.where(ok, x[np.clip(idx, 0, len(x) - 1)], 0)


def _correlate_all(x, fit):
    """Correlation of every symbol with each of the four tones, reference phase 0 at the symbol's first sample."""
    seg = _segments(x, fit[2])                                      # (162, 256)
    f = _tone0(fit, np.arange(SYMBOLS))[:, None] + np.arange(4)[None, :] * TONE_HZ   # (162, 4)
    ref = np.exp(-2j * np.pi * f[:, :, None] * _N[None, None, :] / RATE)            # (162, 4, 256)
    return np.einsum("kn,kmn->km", seg, ref)


def _correlate_tone(x, fit, k, m):
    seg = _segments(x, fit[2])[k]
    return np.sum(seg * np.exp(-2j * np.pi * (_tone0(fit, k) + m * TONE_HZ) * _N / RATE))


def _sync_metric(e):
    a = np.sqrt(e)
    m = a[:, 1] + a[:, 3] - a[:, 0] - a[:, 2]
    num = np.sum(np.where(SYNC == 1, m, -m))
    den = a.sum()
    return num, (num / den if den > 0 else 0.0)


def _sync_of(x, fit):
    return _sync_metric(np.abs(_correlate_all(x, fit)) ** 2)[0]


def _steps(span, step):
    n = int(round(span / step))
    return [i * step for i in range(-n, n + 1) if i != 0]


def _best(start, variants, score):
    best = list(start)
    for f in variants(start):
        s = score(f)
        if s > best[3]:
            best = list(f[:3]) + [s]
    return best


def _refine(x, fit):
    score = lambda f: _sync_of(x, f)
    fit = list(fit[:3]) + [score(fit)]
    for span, step in ((128, 16), (16, 4), (4, 1)):
        fit = _best(fit, lambda f: [[f[0], f[1], f[2] + i * step, 0] for i in range(-span // step, span // step + 1)], score)
    for span, step in ((0.4, 0.1), (0.08, 0.02)):
        fit = _best(fit, lambda f: [[f[0] + d, f[1], f[2], 0] for d in _steps(span, step)], score)
    fit = _best(fit, lambda f: [[f[0], f[1] + d, f[2], 0] for d in _steps(1.5, 0.25)], score)
    fit = _best(fit, lambda f: [[f[0], f[1], f[2] + i, 0] for i in range(-4, 5)], score)
    fit = _best(fit, lambda f: [[f[0] + d, f[1], f[2], 0] for d in _steps(0.06, 0.02)], score)
    return fit[:3] + [_sync_metric(np.abs(_correlate_all(x, fit)) ** 2)[1]]


def _log_i0(v):
    v = np.asarray(v, dtype=np.float64)
    t = (v / 3.75) ** 2
    small = np.log(1 + t * (3.5156229 + t * (3.0899424 + t * (1.2067492 + t * (0.2659732 + t * (0.0360768 + t * 0.0045813))))))
    vv = np.maximum(v, 1e-9)
    large = vv - 0.5 * np.log(2 * np.pi * vv) + np.log(1 + 0.125 / vv)
    return np.where(v < 3.75, small, large)


def _bit_metric(llr, bit):
    l = llr if bit == 0 else -llr
    return -math.log1p(math.exp(-l)) / math.log(2) + 0.5


def stack_decode(llr, max_pops):
    """Stack algorithm over the 81-bit code tree (50 data + 31 tail bits) with the Fano metric."""
    metric = [[_bit_metric(llr[2 * d], c >> 1) + _bit_metric(llr[2 * d + 1], c & 1) for c in range(4)] for d in range(81)]
    heap = [(0.0, 0, 0)]                       # (-metric, depth, bits)
    for _ in range(max_pops):
        if not heap:
            return None
        neg, depth, bits = heapq.heappop(heap)
        if depth == 81:
            return bits
        for bit in ((0, 1) if depth < 50 else (0,)):
            nxt = (bits << 1) | bit if depth < 50 else bits
            reg = (nxt << max(0, depth + 1 - 50)) & 0xFFFFFFFF
            c = (_parity(reg & POLY1) << 1) | _parity(reg & POLY2)
            heapq.heappush(heap, (neg - metric[depth][c], depth + 1, nxt))
    return None


def _try_decode(x, fit, max_pops, block_half):
    c = _correlate_all(x, fit)
    psi = np.zeros(SYMBOLS)
    acc = 0.0
    for k in range(SYMBOLS):
        psi[k] = acc
        acc = (acc + 2 * np.pi * _tone0(fit, k) * SYMBOL_SAMPLES / RATE) % (2 * np.pi)
    c = c * np.exp(-1j * psi)[:, None]
    p = np.abs(c) ** 2
    s = SYNC.astype(int)
    rows = np.arange(SYMBOLS)
    noise = np.mean((p[rows, 1 - s] + p[rows, 3 - s]) / 2)
    peak = np.maximum(p[rows, s], p[rows, s + 2])
    scale = np.zeros(SYMBOLS)
    for k in range(SYMBOLS):
        a, b = max(0, k - SIGNAL_HALF), min(SYMBOLS - 1, k + SIGNAL_HALF)
        signal = max(peak[a:b + 1].mean() - noise, noise * 0.05)
        scale[k] = 2 * math.sqrt(signal) / noise
    llr = np.zeros(SYMBOLS)
    for k in range(SYMBOLS):
        a, b = max(0, k - block_half), min(SYMBOLS - 1, k + block_half)
        ln = b - a + 1
        zero, one = -np.inf, -np.inf
        for pattern in range(1 << ln):
            total = 0j
            for j in range(ln):
                bit = (pattern >> j) & 1
                total += c[a + j, s[a + j] + 2 * bit]
            v = float(_log_i0(scale[k] * abs(total)))
            if (pattern >> (k - a)) & 1 == 0:
                zero = np.logaddexp(zero, v)
            else:
                one = np.logaddexp(one, v)
        llr[k] = np.clip(zero - one, -16, 16)
    coded = llr[CODED_BIT_POSITION]
    data = stack_decode(coded, max_pops)
    if data is None:
        return None, 0
    return encode_bits(data), data


def _attempt(x, fit):
    clear = fit[3] >= STRONG_SYNC
    pops = MAX_POPS if clear else MAX_POPS // 6
    sym, data = _try_decode(x, fit, pops, BLOCK_HALF)
    if sym is not None and unpack(data):
        return sym, data, fit
    sym, data = _try_decode(x, fit, pops // 2, 0)
    if sym is not None and unpack(data):
        return sym, data, fit
    if not clear:
        return None, 0, fit
    for ds, dh in ((0, -2 * TONE_HZ), (0, 2 * TONE_HZ), (-8, 0.0), (8, 0.0), (0, -0.05), (0, 0.05)):
        f = [fit[0] + dh, fit[1], fit[2] + ds, fit[3]]
        sym, data = _try_decode(x, f, pops // 3, BLOCK_HALF)
        if sym is not None and unpack(data):
            return sym, data, f
    return None, 0, fit


def _polish(x, fit, symbols):
    def score(g):
        c = _correlate_all(x, g)[np.arange(SYMBOLS), symbols]
        psi, acc = np.zeros(SYMBOLS), 0.0
        for k in range(SYMBOLS):
            psi[k] = acc
            acc = (acc + 2 * np.pi * _tone0(g, k) * SYMBOL_SAMPLES / RATE) % (2 * np.pi)
        c = c * np.exp(-1j * psi)
        blocks = [c[i:i + 9].sum() for i in range(0, SYMBOLS, 9)]
        return float(np.sum(np.abs(blocks) ** 2))
    fit = list(fit[:3]) + [score(fit)]
    for _ in range(2):
        fit = _best(fit, lambda f: [[f[0], f[1], f[2] + i, 0] for i in range(-12, 13)], score)
        fit = _best(fit, lambda f: [[f[0] + d, f[1], f[2], 0] for d in _steps(0.1, 0.01)], score)
        fit = _best(fit, lambda f: [[f[0], f[1] + d, f[2], 0] for d in _steps(0.6, 0.05)], score)
    fit = _best(fit, lambda f: [[f[0] + d, f[1], f[2], 0] for d in _steps(0.02, 0.004)], score)
    fit = _best(fit, lambda f: [[f[0], f[1] + d, f[2], 0] for d in _steps(0.04, 0.01)], score)
    return fit


def _subtract(x, f, symbols):
    c = _correlate_all(x, f)[np.arange(SYMBOLS), symbols]
    rot, acc = np.zeros(SYMBOLS), 0.0
    for k in range(SYMBOLS):
        rot[k] = acc
        acc = (acc + 2 * np.pi * _tone0(f, k) * SYMBOL_SAMPLES / RATE) % (2 * np.pi)
    a = c * np.exp(-1j * rot) / SYMBOL_SAMPLES
    for k in range(SYMBOLS):
        lo, hi = max(0, k - SUBTRACT_HALF), min(SYMBOLS - 1, k + SUBTRACT_HALF)
        amp = a[lo:hi + 1].mean() * np.exp(1j * rot[k])
        start = f[2] + k * SYMBOL_SAMPLES
        w = 2 * np.pi * (_tone0(f, k) + symbols[k] * TONE_HZ) / RATE
        i = start + _N
        ok = (i >= 0) & (i < len(x))
        x[i[ok]] -= amp * np.exp(1j * w * _N[ok])


def _unpack_call(n):
    c = [" "] * 6
    for i in (5, 4, 3):
        v = n % 27; n //= 27
        c[i] = " " if v == 26 else chr(65 + v)
    c[2] = chr(48 + n % 10); n //= 10
    c1 = n % 36; n //= 36
    c[1] = chr(48 + c1) if c1 < 10 else chr(55 + c1)
    if n > 36:
        return None
    c[0] = " " if n == 36 else chr(48 + n) if n < 10 else chr(55 + n)
    return "".join(c)


def _compound(call, n3):
    if n3 < 60000:
        p = []
        for _ in range(3):
            v = n3 % 37; n3 //= 37
            p.insert(0, " " if v == 36 else chr(48 + v) if v < 10 else chr(55 + v))
        prefix = "".join(p).strip()
        return None if not prefix or " " in prefix else f"{prefix}/{call}"
    nc = n3 - 60000
    suffix = chr(48 + nc) if nc < 10 else chr(55 + nc) if nc < 36 else str(nc - 26) if nc <= 125 else None
    return None if suffix is None else f"{call}/{suffix}"


def unpack(data):
    """(kind, call, grid, dbm, hash) or None. 1: CALL LOC4; 2: compound call, no grid; 3: hashed call + LOC6."""
    n = (data >> 22) & 0x0FFFFFFF
    m = data & 0x3FFFFF
    field = _unpack_call(n)
    if field is None:
        return None
    ntype = (m & 127) - 64
    ngrid = m >> 7
    if ntype >= 0:
        if ntype > 62:
            return None
        call = field.strip()
        if normalise_call(call) is None:
            return None
        if ntype % 10 not in (0, 3, 7):
            nadd = 1 if ntype % 10 in (1, 4, 8) else 2
            comp = _compound(call, ngrid + 32768 * (nadd - 1))
            power = ntype - nadd
            if comp is None or power not in VALID_DBM:
                return None
            return 2, comp, "", power, 0
        if ngrid >= 32400:
            return None
        dlat, dlon = divmod(ngrid, 180)
        a, c = divmod(179 - dlat, 10)
        b, d = divmod(dlon, 10)
        if not 0 <= a <= 17 or b > 17:
            return None
        return 1, call, f"{chr(65 + a)}{chr(65 + b)}{c}{d}", ntype, 0
    dbm = -(ntype + 1)
    if dbm not in VALID_DBM or " " in field:
        return None
    g6 = field[5] + field[:5]
    if not ("A" <= g6[0] <= "R" and "A" <= g6[1] <= "R" and g6[2].isdigit() and g6[3].isdigit() and "A" <= g6[4] <= "X" and "A" <= g6[5] <= "X"):
        return None
    return 3, "", g6, dbm, ngrid


def decode(samples, hashed_calls=None, cancelled=lambda: False):
    """Decode a 12 kHz recording that starts at the top of the even minute.
    Returns a list of dicts: call, grid, dbm, snr, dt, audio_hz (tone centre), drift."""
    hashed_calls = hashed_calls if hashed_calls is not None else {}
    x = baseband(samples)
    results, pending3, failed, removed = [], [], [], []
    for _ in range(3):
        before = len(results)
        cands, ps = _candidates(x)
        for centre_hz, snr, peak_bin in cands:
            if cancelled():
                return results
            fit = _refine(x, _coarse(ps, peak_bin))
            if fit[3] < 0.08:
                continue
            if any(abs(fh - fit[0]) < 0.3 and abs(fs - fit[2]) < 16 and not any(abs(h - fit[0]) < 10 for h in removed[nr:])
                   for fh, fs, nr in failed):
                continue
            sym, data, fit = _attempt(x, fit)
            if sym is None:
                failed.append((fit[0], fit[2], len(removed)))
                continue
            msg = unpack(data)
            if msg is None:
                continue
            kind, call, grid, dbm, h = msg
            centre = CENTRE_AUDIO_HZ + fit[0] + 1.5 * TONE_HZ
            drift = int(round(fit[1]))
            dt = fit[2] / RATE - 1.0
            if kind == 3:
                if h in hashed_calls:
                    call = hashed_calls[h]
                else:
                    pending3.append((h, grid, dbm, snr, dt, centre, drift))
            if call and not any(r["call"] == call and r["grid"] == grid for r in results):
                if kind != 3:
                    hashed_calls[call_hash(call)] = call
                results.append({"call": call, "grid": grid, "dbm": dbm, "snr": snr, "dt": round(dt, 1),
                                "audio_hz": round(centre, 1), "drift": drift})
            _subtract(x, _polish(x, fit, sym), sym)
            removed.append(fit[0])
        if len(results) == before:
            break
    for h, grid, dbm, snr, dt, centre, drift in pending3:
        if h in hashed_calls and not any(r["call"] == hashed_calls[h] and r["grid"] == grid for r in results):
            results.append({"call": hashed_calls[h], "grid": grid, "dbm": dbm, "snr": snr, "dt": round(dt, 1),
                            "audio_hz": round(centre, 1), "drift": drift})
    return results
