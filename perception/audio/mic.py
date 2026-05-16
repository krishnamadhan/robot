"""
Microphone input pipeline.

Device priority:
  1. INMP441 I2S (when it arrives)
  2. Logitech C920 webcam mic (stereo → mono downmix)
  3. Any USB audio device
  4. System default

C920 notes:
  - PyAudio index 0, hw:2,0
  - 2 channels stereo at 16kHz
  - We downmix L+R to mono for Whisper compatibility
"""

import asyncio
import threading
from collections import deque
from typing import Optional

import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
CHUNK_SIZE = 512       # ~32ms per chunk
CHANNELS = 1           # output mono (downmixed from stereo if needed)

# Keywords to match device names in priority order
_PRIORITY_KEYWORDS = ["inmp441", "i2s", "c920", "logitech", "webcam", "usb"]


class MicrophoneInput:
    """Continuous audio capture with async chunk delivery."""

    def __init__(self) -> None:
        self._device_index: int = -1
        self._device_channels: int = 1
        self._device_name: str = "unknown"
        self._pa = None
        self._stream = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._available = False
        self._find_device()

    def _find_device(self) -> None:
        try:
            import pyaudio
            pa = pyaudio.PyAudio()

            found_index = -1
            found_channels = 1
            found_name = ""

            for keyword in _PRIORITY_KEYWORDS:
                for i in range(pa.get_device_count()):
                    d = pa.get_device_info_by_index(i)
                    if (d["maxInputChannels"] > 0
                            and keyword.lower() in d["name"].lower()):
                        found_index = i
                        found_channels = int(d["maxInputChannels"])
                        found_name = d["name"]
                        break
                if found_index >= 0:
                    break

            if found_index < 0:
                try:
                    d = pa.get_default_input_device_info()
                    found_index = int(d["index"])
                    found_channels = int(d["maxInputChannels"])
                    found_name = d["name"]
                except Exception:
                    pass

            pa.terminate()

            if found_index >= 0:
                self._device_index = found_index
                self._device_channels = found_channels
                self._device_name = found_name
                self._available = True
                log.info("mic.device_found",
                         name=found_name, index=found_index,
                         channels=found_channels)
            else:
                log.warning("mic.no_input_device")

        except Exception as e:
            log.warning("mic.init_failed", error=str(e))

    async def start_stream(self) -> bool:
        if not self._available:
            return False
        self._loop = asyncio.get_event_loop()
        self._running = True
        thread = threading.Thread(target=self._capture_thread, daemon=True)
        thread.start()
        log.info("mic.stream_started", device=self._device_name)
        return True

    def _capture_thread(self) -> None:
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self._device_channels,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK_SIZE * self._device_channels,
            )
            self._pa = pa
            self._stream = stream
            log.info("mic.capturing")

            while self._running:
                try:
                    raw = stream.read(
                        CHUNK_SIZE * self._device_channels,
                        exception_on_overflow=False,
                    )
                    mono = self._to_mono(raw)
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._enqueue(mono), self._loop
                        )
                except Exception as e:
                    if self._running:
                        log.error("mic.read_error", error=str(e))
        finally:
            try:
                if self._stream:
                    self._stream.stop_stream()
                    self._stream.close()
            except Exception:
                pass
            pa.terminate()

    def _to_mono(self, raw: bytes) -> bytes:
        # INMP441 outputs audio on left channel only; right is silent.
        # Take L channel (index 0) rather than averaging to avoid halving amplitude.
        if self._device_channels == 1:
            return raw
        arr = np.frombuffer(raw, dtype=np.int16)
        arr = arr.reshape(-1, self._device_channels)
        return arr[:, 0].tobytes()

    async def _enqueue(self, chunk: bytes) -> None:
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # Drop oldest chunk to make room
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
            except Exception:
                pass

    async def read_chunk(self) -> bytes:
        return await self._queue.get()

    async def stop_stream(self) -> None:
        self._running = False
        log.info("mic.stream_stopped")

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def device_index(self) -> int:
        return self._device_index


mic = MicrophoneInput()
