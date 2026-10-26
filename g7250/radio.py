"""UDP link to the G7250: sends tune frames, receives and records the IQ stream."""
import asyncio
import socket
import time
from collections import deque
from pathlib import Path

from . import protocol
from .dsp import SpectrumEngine


class _IqProtocol(asyncio.DatagramProtocol):
    def __init__(self, link: "RadioLink"):
        self.link = link

    def datagram_received(self, data, addr):
        self.link._on_datagram(data, addr)


class RadioLink:
    def __init__(self, radio_ip: str, bind_ip: str = "0.0.0.0",
                 local_port: int = protocol.LOCAL_PORT,
                 radio_port: int = protocol.RADIO_PORT,
                 swap_iq: bool = False):
        self.radio_ip = radio_ip
        self.bind_ip = bind_ip
        self.local_port = local_port
        self.radio_port = radio_port
        self.swap_iq = swap_iq
        self.spectrum = SpectrumEngine()
        self.frequency_hz = None            # last frequency we *requested*
        self.iq_packets = 0
        self.other_packets = 0
        self.bytes_received = 0
        self.last_sender = None
        self._arrivals = deque()            # (time, samples) for the rate window
        self._transport = None
        self._recording = None
        self.last_recording = None
        self.on_iq = None                   # optional callback(samples) for the demodulator

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.bind((self.bind_ip, self.local_port))
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _IqProtocol(self), sock=sock)

    def close(self) -> None:
        self.stop_recording()
        if self._transport:
            self._transport.close()

    # ---- commands -------------------------------------------------------

    def tune(self, hz: int) -> bytes:
        """Ask the radio to retune. It sends no reply; the IQ shows the change."""
        frame = protocol.build_tune_frame(hz)
        self._transport.sendto(frame, (self.radio_ip, self.radio_port))
        hz = protocol.clamp_frequency(hz)
        if hz != self.frequency_hz:
            self.spectrum._avg_power = None    # don't smear the old window into the new one
        self.frequency_hz = hz
        return frame

    # ---- receive --------------------------------------------------------

    def _on_datagram(self, data: bytes, addr) -> None:
        self.bytes_received += len(data)
        self.last_sender = addr
        if len(data) != protocol.IQ_PAYLOAD_BYTES:
            self.other_packets += 1
            return
        self.iq_packets += 1
        now = time.monotonic()
        self._arrivals.append(now)
        while self._arrivals and now - self._arrivals[0] > 2.0:
            self._arrivals.popleft()
        samples = protocol.iq_from_payload(data, self.swap_iq)
        self.spectrum.push(samples)
        if self.on_iq:
            self.on_iq(samples)
        if self._recording:
            self._write_recording(data, now)

    @property
    def packets_per_second(self) -> float:
        if len(self._arrivals) < 2:
            return 0.0
        span = self._arrivals[-1] - self._arrivals[0]
        return (len(self._arrivals) - 1) / span if span > 0 else 0.0

    @property
    def receiving(self) -> bool:
        return bool(self._arrivals) and time.monotonic() - self._arrivals[-1] < 1.0

    # ---- recording ------------------------------------------------------

    def start_recording(self, folder: Path, seconds: float) -> Path:
        self.stop_recording()
        folder.mkdir(parents=True, exist_ok=True)
        freq = self.frequency_hz or 0
        path = folder / f"g7250-{time.strftime('%Y%m%d-%H%M%S')}-{freq}Hz.iq16"
        self._recording = {"file": path.open("wb"), "path": path,
                           "until": time.monotonic() + seconds, "bytes": 0}
        return path

    def _write_recording(self, data: bytes, now: float) -> None:
        rec = self._recording
        rec["file"].write(data)
        rec["bytes"] += len(data)
        if now >= rec["until"]:
            self.stop_recording()

    def stop_recording(self):
        rec, self._recording = self._recording, None
        if rec:
            rec["file"].close()
            self.last_recording = rec["path"]
        return rec["path"] if rec else None

    @property
    def recording(self) -> bool:
        return self._recording is not None
