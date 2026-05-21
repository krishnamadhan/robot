"""
Voice Activity Detection using webrtcvad.

Segments continuous audio into complete speech utterances.
webrtcvad requires frames of exactly 10, 20, or 30ms at 16kHz.
At 16kHz/16-bit mono: 10ms = 320 bytes, 20ms = 640, 30ms = 960.
We use 30ms frames (aggressiveness 2 works well indoors).
"""

import asyncio
from typing import AsyncGenerator, Optional

import webrtcvad
import numpy as np

from utils.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30                          # webrtcvad frame duration
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 960 bytes


class VoiceActivityDetector:
    """Segments mic audio into complete speech utterances."""

    def __init__(self, aggressiveness: int = 1) -> None:
        self.vad = webrtcvad.Vad(aggressiveness)
        self._partial: bytes = b""     # leftover bytes between chunks

    def is_speech(self, frame: bytes) -> bool:
        """Check if a single 30ms frame contains speech."""
        if len(frame) != FRAME_BYTES:
            return False
        try:
            return self.vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            return False

    async def segment_speech(
        self,
        audio_source,           # MicrophoneInput or any object with read_chunk()
        min_speech_ms: int = 300,
        silence_timeout_ms: int = 800,
        max_duration_ms: int = 10000,
    ) -> AsyncGenerator[bytes, None]:
        """
        Yields complete speech segments (bytes, mono 16kHz int16).

        Waits silence_timeout_ms of silence after speech ends before yielding.
        Ignores segments shorter than min_speech_ms.
        Caps at max_duration_ms to prevent runaway captures.
        """
        frames_per_silence = silence_timeout_ms // FRAME_MS   # ~26 frames
        min_speech_frames = min_speech_ms // FRAME_MS          # ~10 frames
        max_frames = max_duration_ms // FRAME_MS

        speech_frames = []
        silence_count = 0
        in_speech = False
        frame_count = 0

        while True:
            chunk = await audio_source.read_chunk()
            self._partial += chunk

            # Process all complete 30ms frames
            while len(self._partial) >= FRAME_BYTES:
                frame = self._partial[:FRAME_BYTES]
                self._partial = self._partial[FRAME_BYTES:]
                speaking = self.is_speech(frame)

                if speaking:
                    in_speech = True
                    silence_count = 0
                    speech_frames.append(frame)
                    frame_count += 1
                elif in_speech:
                    speech_frames.append(frame)   # include trailing silence
                    silence_count += 1
                    frame_count += 1

                    if silence_count >= frames_per_silence:
                        # End of utterance
                        if len(speech_frames) >= min_speech_frames:
                            segment = b"".join(speech_frames)
                            duration_ms = len(speech_frames) * FRAME_MS
                            log.debug("vad.segment_ready",
                                      duration_ms=duration_ms,
                                      frames=len(speech_frames))
                            yield segment
                        speech_frames = []
                        silence_count = 0
                        in_speech = False
                        frame_count = 0

                if frame_count >= max_frames and in_speech:
                    # Safety cap — yield what we have
                    if len(speech_frames) >= min_speech_frames:
                        yield b"".join(speech_frames)
                    speech_frames = []
                    silence_count = 0
                    in_speech = False
                    frame_count = 0

    async def capture_once(
        self,
        audio_source,
        timeout_ms: int = 8000,
        min_speech_ms: int = 300,
        silence_timeout_ms: int = 700,
    ) -> Optional[bytes]:
        """Capture a single utterance with timeout. Returns None on timeout."""
        try:
            segment = await asyncio.wait_for(
                self._first_segment(audio_source, min_speech_ms, silence_timeout_ms),
                timeout=timeout_ms / 1000,
            )
            return segment
        except asyncio.TimeoutError:
            log.debug("vad.capture_timeout")
            return None

    async def _first_segment(self, audio_source, min_speech_ms, silence_timeout_ms):
        async for segment in self.segment_speech(
            audio_source,
            min_speech_ms=min_speech_ms,
            silence_timeout_ms=silence_timeout_ms,
        ):
            return segment


vad = VoiceActivityDetector(aggressiveness=2)
