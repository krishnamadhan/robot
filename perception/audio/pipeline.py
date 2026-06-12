"""
Audio pipeline — mic → VAD → wake word → STT → conversation → TTS.

State machine:
  PASSIVE   — continuously feeding mic chunks to wake word detector
  LISTENING — wake word heard, capturing utterance via VAD
  THINKING  — STT transcribed, LLM responding
  SPEAKING  — TTS playing back response

Detectors tried in priority order: Porcupine → OWW → STT fallback.
"""

import asyncio
import datetime
import time
from enum import Enum
from typing import Optional

from core.event_bus import bus, Event, EventType, EventPriority
from expression.speech import tts
from perception.audio.mic import mic, SAMPLE_RATE
from perception.audio.stt import stt
from perception.audio.vad import vad
from perception.audio.wake_word import (
    oww_detector, porcupine_detector, wake_word_detector,
    publish_wake_word, load_detectors, WakeWordDetector,
)
from utils.logger import get_logger

log = get_logger(__name__)

# How long to wait for an utterance after wake word before giving up
LISTEN_TIMEOUT_MS = 8000
# How long TTS blocks more wake detections after responding
POST_SPEAK_COOLDOWN_S = 2.0


class ListenState(str, Enum):
    PASSIVE   = "passive"
    LISTENING = "listening"
    THINKING  = "thinking"
    SPEAKING  = "speaking"


class ListeningPipeline:
    """Full voice pipeline as a long-running async task."""

    def __init__(self) -> None:
        self._state         = ListenState.PASSIVE
        self._task: Optional[asyncio.Task] = None
        self._running       = False
        self._current_person: Optional[str] = None
        self._current_emotion: Optional[str] = None

        # Backend resolved in start() — detectors load lazily, not at import
        self._wake_backend = "stt"

        # Ambient loudness: per-second RMS buckets (ts, level 0–1), ~2 min.
        # Feeds cognition/activity.py (TV vs quiet-room inference).
        from collections import deque
        self._amb_buckets: "deque" = deque(maxlen=120)
        self._amb_acc = 0.0
        self._amb_n = 0
        self._amb_bucket_start = 0.0

    async def start(self) -> bool:
        if self._running:
            return True

        self._wake_backend = load_detectors()

        # Load STT (needed for LISTENING→THINKING and for STT fallback wake)
        if not stt.is_available:
            stt.load()

        if not mic.is_available:
            log.warning("audio_pipeline.no_mic")
            return False

        ok = await mic.start_stream()
        if not ok:
            log.warning("audio_pipeline.mic_start_failed")
            return False

        self._running = True
        self._task = asyncio.create_task(self._run(), name="audio_pipeline")
        log.info("audio_pipeline.started", wake_backend=self._wake_backend,
                 stt=stt.is_available)
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await mic.stop_stream()
        stt.unload()

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        log.info("audio_pipeline.listening", backend=self._wake_backend)
        while self._running:
            try:
                chunk = await mic.read_chunk()
            except Exception as e:
                log.error("audio_pipeline.mic_read_error", error=str(e)[:80])
                await asyncio.sleep(0.1)
                continue

            self._note_ambient(chunk)

            if self._state != ListenState.PASSIVE:
                continue   # don't stack wake-word checks while busy

            detected = await self._check_wake_word(chunk)
            if detected:
                await self._handle_wake()

    # ── Ambient loudness ──────────────────────────────────────────────────────

    def _note_ambient(self, chunk: bytes) -> None:
        """Accumulate per-second RMS levels (normalized 0–1) from mic chunks."""
        try:
            import numpy as np
            samples = np.frombuffer(chunk, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
            level = min(1.0, rms / 8000.0)
        except Exception:
            return

        now = time.monotonic()
        if now - self._amb_bucket_start >= 1.0:
            if self._amb_n:
                self._amb_buckets.append(
                    (self._amb_bucket_start, self._amb_acc / self._amb_n))
            self._amb_bucket_start = now
            self._amb_acc = 0.0
            self._amb_n = 0
        self._amb_acc += level
        self._amb_n += 1

    def ambient_stats(self, window_s: float = 60.0) -> dict:
        """Avg + peak normalized loudness over the trailing window."""
        cutoff = time.monotonic() - window_s
        levels = [lvl for ts, lvl in self._amb_buckets if ts >= cutoff]
        if not levels:
            return {"avg": 0.0, "peak": 0.0, "n": 0}
        return {"avg": sum(levels) / len(levels), "peak": max(levels), "n": len(levels)}

    # ── Wake word detection ───────────────────────────────────────────────────

    async def _check_wake_word(self, chunk: bytes) -> bool:
        if self._wake_backend == "porcupine":
            return porcupine_detector.process_chunk(chunk)
        if self._wake_backend == "oww":
            return oww_detector.process_chunk(chunk)
        # STT fallback: accumulate ~2s of audio, transcribe, check text
        return False   # STT fallback is handled via _stt_wake_loop (see below)

    async def _handle_wake(self) -> None:
        keyword = (
            porcupine_detector.keyword_label if self._wake_backend == "porcupine"
            else oww_detector.keyword_label  if self._wake_backend == "oww"
            else "hey cosmo"
        )
        published = await publish_wake_word(keyword, confidence=1.0)
        if not published:
            return  # cooldown in effect

        self._state = ListenState.LISTENING
        log.info("audio_pipeline.wake", keyword=keyword)

        # Sleep gate — don't process during 00:00–07:00
        if 0 <= datetime.datetime.now().hour < 7:
            await tts.speak("Shh, I'm sleeping.")
            self._state = ListenState.PASSIVE
            return

        utterance = await self._capture_utterance()
        if not utterance:
            log.debug("audio_pipeline.no_utterance")
            from expression.sounds import sounds
            asyncio.create_task(sounds.play("chirp_curious"))
            self._state = ListenState.PASSIVE
            return

        self._state = ListenState.THINKING
        result = await stt.transcribe(utterance, vad_filter=False)

        if not stt.is_meaningful(result):
            log.debug("audio_pipeline.utterance_not_meaningful",
                      text=result.text if result else "")
            self._state = ListenState.PASSIVE
            return

        text = result.text
        log.info("audio_pipeline.heard", text=text[:80])

        # Publish speech event so behavior tree / other listeners can react
        await bus.publish(Event(
            type=EventType.SPEECH_DETECTED,
            data={
                "text": text,
                "confidence": result.confidence,
                "person_id": self._current_person,
            },
            priority=EventPriority.HIGH,
        ))

        await self._respond(text)
        self._state = ListenState.PASSIVE

    # ── VAD-based utterance capture ───────────────────────────────────────────

    async def _capture_utterance(self) -> Optional[bytes]:
        """Capture one full utterance after wake word, with timeout."""
        return await vad.capture_once(
            mic,
            timeout_ms=LISTEN_TIMEOUT_MS,
            min_speech_ms=250,
            silence_timeout_ms=1000,
        )

    # ── LLM response + TTS ───────────────────────────────────────────────────

    async def _respond(self, text: str) -> None:
        self._state = ListenState.SPEAKING
        try:
            from cognition.conversation import conversation
            if self._current_person:
                conversation.set_person(
                    self._current_person,
                    name=self._current_person,
                    emotion=self._current_emotion,
                )
            result = await conversation.respond(
                text,
                person_id=self._current_person,
            )
            if result and result.get("text"):
                await tts.speak(result["text"], interrupt=True)
                # Brief cooldown so mic doesn't immediately re-trigger on TTS audio
                await asyncio.sleep(POST_SPEAK_COOLDOWN_S)
        except Exception as e:
            log.error("audio_pipeline.respond_error", error=str(e)[:120])

    # ── Person context (updated by vision loop) ───────────────────────────────

    def update_person(self, person_id: Optional[str],
                      emotion: Optional[str] = None) -> None:
        self._current_person = person_id
        if emotion:
            self._current_emotion = emotion

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def state(self) -> ListenState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running


audio_pipeline = ListeningPipeline()
