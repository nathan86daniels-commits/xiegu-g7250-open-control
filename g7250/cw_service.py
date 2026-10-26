"""CW on the station: the decoders fed from the IQ stream, and CW messages sent by the radio's
own keyer over CI-V (command 17h, 30 characters per frame, FFh aborts).

Decoders: Neural (our own e4 model, the default) or Classic. The classic decoder always runs
for the speed, signal and lock indicators, and is the copy when Neural is off or its model can't load.
"""
import asyncio
import threading
import time

from .cw_decoder import CwDecoder
from .demod import Demodulator
from .neural_cw import NeuralCwDecoder

DECODERS = ("neural", "classic")
DELAYS = (1.0, 2.0, 0.0)            # neural copy delay choices for the DELAY button (0 = 10 s windows)
NEW_STATION_HZ = 250

MAX_TEXT = 20000


class CwService:
    def __init__(self, station):
        self.st = station
        self.enabled = True
        self.decoder = CwDecoder(12000, 600, 20)
        # fixed gain: an audio AGC lifts the noise in the gaps between elements until it looks like key-down
        self.demod = Demodulator(agc=False, as_float=True)
        self.demod.configure(mode="CW")
        self.text = ""
        self.offset = 0                 # characters dropped from the front (for incremental reads)
        self._last_freq = None
        self._was_tx = False
        self.sending = None
        self._send_task = None
        self._lock = threading.Lock()
        cfg = station.settings.get("cw_decoder", {})
        self.decoder_name = cfg.get("decoder", "neural") if cfg.get("decoder") in DECODERS else "neural"
        self.delay = cfg.get("delay", 1.0) if cfg.get("delay") in DELAYS else 1.0
        self.neural = None
        self._neural_open = False           # audio given to the neural decoder since its last flush
        if self.decoder_name == "neural":
            self._start_neural()

    def _start_neural(self):
        if self.neural is None:
            self.neural = NeuralCwDecoder(lookahead=self.delay)

    @property
    def using_neural(self):
        return self.decoder_name == "neural" and self.neural is not None and self.neural.status not in (
            "NO MODEL FILE", "ONNXRUNTIME NOT INSTALLED", "NOT A CW MODEL", "MODEL WON'T LOAD")

    def configure(self, decoder=None, delay=None):
        if decoder in DECODERS and decoder != self.decoder_name:
            self._flush_neural()
            self.decoder_name = decoder
            if decoder == "neural":
                self._start_neural()
        if delay in DELAYS and delay != self.delay:
            self.delay = delay
            if self.neural is not None:
                self.neural.lookahead = delay        # finishes the over so far first
        self.st.save_settings({"cw_decoder": {"decoder": self.decoder_name, "delay": self.delay}})

    def _flush_neural(self):
        if self.neural is not None and self._neural_open:
            self.neural.flush(self.st.demod.pitch)
            self._neural_open = False

    def _append(self, out):
        if not out:
            return
        with self._lock:
            self.text += out
            if len(self.text) > MAX_TEXT:
                cut = len(self.text) - MAX_TEXT
                self.text = self.text[cut:]
                self.offset += cut

    def _drain(self):
        if self.neural is not None:
            out = self.neural.take_text()
            if out and self.decoder_name == "neural":
                self._append(out)

    def feed(self, samples):
        st = self.st
        if not self.enabled or not st.mode.startswith("CW"):
            self._flush_neural()
            return
        if st.ptt:
            self._was_tx = True
            self._flush_neural()                 # own TX is not copy; the over so far is finished
            return
        if self._was_tx:
            self._was_tx = False
            self.decoder.reset_after_transmit()
        freq = st.frequency
        if self._last_freq is not None and abs(freq - self._last_freq) > NEW_STATION_HZ:
            self.decoder.new_station()
            self._flush_neural()
        if self._last_freq is None or abs(freq - self._last_freq) > NEW_STATION_HZ:
            self._last_freq = freq
        key = (st.mode, st.demod.low, st.demod.high, st.demod.pitch)
        if key != getattr(self, "_demod_key", None):        # rebuild filters only when the passband changes
            self._demod_key = key
            self.demod.pitch = st.demod.pitch
            self.demod.configure(mode=st.mode, low=st.demod.low, high=st.demod.high)
        audio = self.demod.push(samples)
        if audio.size:
            self.decoder.pitch_hz = st.demod.pitch
            out = self.decoder.process(audio * 4.0)
            if not self.using_neural:
                self._append(out)
            if self.neural is not None and self.decoder_name == "neural":
                self.neural.process(audio, 12000, st.demod.pitch)
                self._neural_open = True
        self._drain()

    def read(self, since=0):
        self._drain()
        with self._lock:
            start = max(0, since - self.offset)
            text, nxt = self.text[start:], self.offset + len(self.text)
        n = self.neural if self.decoder_name == "neural" else None
        wpm = n.sender_wpm if n is not None and n.sender_wpm > 0 else self.decoder.wpm
        return {"text": text, "next": nxt, "wpm": round(wpm, 1),
                "decoder": self.decoder_name, "delay": self.delay,
                "neural_status": n.status if n is not None else None, "stations": n.station_count if n is not None else None,
                "slow_down": n.slow_down if n is not None else None,
                "snr_db": round(self.decoder.snr_db, 1), "lock": self.decoder.pitch_lock,
                "offset_hz": round(self.decoder.offset_hz), "tone": self.decoder.tone_heard,
                "key_down": self.decoder.mark, "sending": self.sending}

    def clear(self):
        with self._lock:
            self.offset += len(self.text)
            self.text = ""

    async def send(self, text):
        """Send through the radio's keyer: split into 30-character CI-V frames at word boundaries."""
        cat = self.st.cat
        if not self.st.cat_connected:
            raise RuntimeError("Needs USB CAT")
        text = " ".join(text.replace("{CALL}", self.st.settings["callsign"] or "").upper().split())
        chunks, cur = [], ""
        for word in text.split(" "):
            nxt = (cur + " " + word).strip()
            if len(nxt) > 30 and cur:
                chunks.append(cur + " ")
                cur = word
            else:
                cur = nxt
        if cur:
            chunks.append(cur)
        self.sending = text
        try:
            for chunk in chunks:
                await cat.send_cw(chunk)
        finally:
            self.sending = None

    async def stop_sending(self):
        if self.st.cat_connected:
            await self.st.cat.request(0x17, data=bytes([0xFF]))
        self.sending = None
