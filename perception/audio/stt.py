"""
Speech-to-text using faster-whisper (offline, CPU int8).

Model: tiny.en — ~39MB, <1.5s for 5s utterance on Pi 5.
Upgrade to base.en if accuracy is poor.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

MODEL_SIZE = "tiny.en"
SAMPLE_RATE = 16000

_FILLER_WORDS = {"you", "the", "a", "um", "uh", "hmm", "hm", "ah", "oh", ""}


@dataclass
class STTResult:
    text: str
    confidence: float      # language probability 0-1
    duration_ms: int
    language: str


class SpeechToText:
    """Transcribes audio bytes (mono 16kHz int16) to text."""

    def __init__(self) -> None:
        self._model = None
        self._available = False

    def load(self) -> bool:
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                MODEL_SIZE, device="cpu", compute_type="int8"
            )
            self._available = True
            log.info("stt.loaded", model=MODEL_SIZE)
            return True
        except Exception as e:
            log.warning("stt.load_failed", error=str(e))
            return False

    async def transcribe(self, audio_bytes: bytes, vad_filter: bool = False) -> Optional[STTResult]:
        if not self._available or not audio_bytes:
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, vad_filter)

    def _transcribe_sync(self, audio_bytes: bytes, vad_filter: bool = False) -> Optional[STTResult]:
        t0 = time.monotonic()
        try:
            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = self._model.transcribe(
                audio,
                language="en",
                vad_filter=vad_filter,
                beam_size=1,
            )
            text = " ".join(s.text for s in segments).strip()
            duration_ms = int(len(audio) / SAMPLE_RATE * 1000)
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.info("stt.transcribed",
                      text=text[:60], latency_ms=latency_ms,
                      duration_ms=duration_ms,
                      confidence=round(info.language_probability, 2))
            return STTResult(
                text=text,
                confidence=info.language_probability,
                duration_ms=duration_ms,
                language=info.language,
            )
        except Exception as e:
            log.error("stt.error", error=str(e))
            return None

    def is_meaningful(self, result: Optional[STTResult]) -> bool:
        if result is None:
            return False
        words = result.text.lower().strip().split()
        return (
            len(result.text) > 3
            and result.confidence > 0.5
            and result.text.lower().strip() not in _FILLER_WORDS
            and len(words) >= 2
        )

    @property
    def is_available(self) -> bool:
        return self._available


stt = SpeechToText()
