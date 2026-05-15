"""
Full audio interaction pipeline: listen → wake word → STT → LLM → TTS.

States:
  PASSIVE   — always listening for wake word (energy gate, low CPU)
  LISTENING — wake word heard, capturing full utterance
  THINKING  — STT + LLM processing
  SPEAKING  — TTS output playing
"""

import asyncio
import time
from enum import Enum
from typing import Optional

from core.event_bus import bus, Event, EventType, EventPriority
from core.personality import personality
from cognition.conversation import conversation
from expression.speech import tts
from perception.audio.mic import mic, SAMPLE_RATE, CHUNK_SIZE
from perception.audio.vad import vad, FRAME_BYTES
from perception.audio.stt import stt
from perception.audio.wake_word import wake_word_detector, porcupine_detector, oww_detector, publish_wake_word
from utils.logger import get_logger

log = get_logger(__name__)

# How many chunks to buffer before running energy+STT wake word check
WAKE_WINDOW_CHUNKS = 8      # ~256ms of audio per check (was 25/800ms)
MAX_UTTERANCE_S = 10


class ListenState(str, Enum):
    PASSIVE = "passive"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class ListeningPipeline:
    """Manages the full audio interaction loop."""

    def __init__(self) -> None:
        self._state = ListenState.PASSIVE
        self._running = False
        self._current_person: Optional[str] = None
        self._current_emotion: Optional[str] = None
        self._wake_buffer: bytes = b""

    async def start(self) -> bool:
        if not mic.is_available:
            log.warning("audio_pipeline.no_mic")
            return False

        # Load STT model
        if not stt.is_available:
            ok = stt.load()
            if not ok:
                log.warning("audio_pipeline.stt_load_failed")

        ok = await mic.start_stream()
        if not ok:
            return False

        self._running = True

        # Subscribe to vision events to track who's present
        @bus.on(EventType.FACE_RECOGNIZED)
        async def _on_face(event: Event) -> None:
            self._current_person = event.data.get("person_id")

        @bus.on(EventType.PERSON_LOST)
        async def _on_lost(event: Event) -> None:
            self._current_person = None
            self._current_emotion = None

        @bus.on(EventType.EMOTION_DETECTED)
        async def _on_emotion(event: Event) -> None:
            self._current_emotion = event.data.get("emotion")

        asyncio.create_task(self._passive_loop())
        log.info("audio_pipeline.started", stt=stt.is_available)
        await tts.play_sound("boot_chime")
        return True

    async def _passive_loop(self) -> None:
        """Continuously listen for wake word — pick fastest available detector."""
        if porcupine_detector.is_available:
            await self._passive_loop_streaming(porcupine_detector)
        elif oww_detector.is_available:
            await self._passive_loop_streaming(oww_detector)
        else:
            await self._passive_loop_stt()

    async def _passive_loop_streaming(self, detector) -> None:
        """Generic streaming path for Porcupine or OWW — processes each mic chunk."""
        log.info("audio_pipeline.wake_mode",
                 mode=type(detector).__name__,
                 keyword=detector.keyword_label)
        while self._running:
            if self._state != ListenState.PASSIVE or tts.is_speaking:
                await asyncio.sleep(0.05)
                continue
            chunk = await mic.read_chunk()
            if detector.process_chunk(chunk):
                fired = await publish_wake_word(detector.keyword_label)
                if fired:
                    asyncio.create_task(self._handle_wake_word(detector.keyword_label))

    async def _passive_loop_stt(self) -> None:
        """STT fallback path — batches ~256ms of audio then runs Whisper."""
        log.info("audio_pipeline.wake_mode", mode="stt_fallback",
                 hint="Set PICOVOICE_KEY for <50ms wake word detection")
        chunks_collected = 0
        while self._running:
            if self._state != ListenState.PASSIVE:
                await asyncio.sleep(0.1)
                continue

            chunk = await mic.read_chunk()
            self._wake_buffer += chunk
            chunks_collected += 1

            if chunks_collected >= WAKE_WINDOW_CHUNKS:
                chunks_collected = 0
                buf = self._wake_buffer
                self._wake_buffer = b""

                if not wake_word_detector.check_energy(buf):
                    continue

                if stt.is_available:
                    result = await stt.transcribe(buf)
                    if result and result.text:
                        matched = wake_word_detector.is_wake_word(result.text)
                        if matched:
                            fired = await publish_wake_word(matched, result.confidence)
                            if fired:
                                asyncio.create_task(self._handle_wake_word(matched))

    async def _handle_wake_word(self, word: str) -> None:
        if self._state != ListenState.PASSIVE or tts.is_speaking:
            log.debug("audio_pipeline.wake_ignored",
                      reason="speaking" if tts.is_speaking else "busy",
                      state=self._state)
            return

        log.info("audio_pipeline.wake_word_triggered", word=word)
        self._state = ListenState.LISTENING

        # Acknowledge — beep + eye animation
        await tts.play_sound("beep_ack")

        # Capture the actual utterance
        audio = await vad.capture_once(
            mic,
            timeout_ms=MAX_UTTERANCE_S * 1000,
            min_speech_ms=400,
            silence_timeout_ms=350,
        )

        if not audio:
            log.debug("audio_pipeline.no_utterance")
            self._state = ListenState.PASSIVE
            return

        # STT — disable vad_filter: we already know there's speech (post wake-word capture)
        self._state = ListenState.THINKING
        result = await stt.transcribe(audio, vad_filter=False)

        if not stt.is_meaningful(result):
            log.debug("audio_pipeline.utterance_not_meaningful",
                       text=result.text if result else "")
            await tts.speak("Sorry, I didn't catch that da.")
            self._state = ListenState.PASSIVE
            return

        log.info("audio_pipeline.heard", text=result.text)

        # Update conversation context from vision
        if self._current_person and not conversation.in_conversation:
            await conversation.start_session(self._current_person, self._current_person)
        conversation.set_emotion(self._current_emotion or "neutral")

        # Check for fast intents before hitting LLM
        try:
            from cognition.intent import intent_parser
            intent = await intent_parser.parse_and_publish(result.text)
            if intent:
                log.info("audio_pipeline.intent_matched", intent=intent.name)
                # Let behavior engine handle it; still fall through for verbal response
        except Exception:
            pass

        # Generate response
        self._state = ListenState.SPEAKING
        t0 = time.monotonic()
        try:
            response = await conversation.respond(
                user_text=result.text,
                person_id=self._current_person,
                speak=True,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.info("audio_pipeline.responded",
                      latency_ms=latency_ms,
                      text=response["text"][:60])
        except Exception as e:
            log.error("audio_pipeline.respond_error", error=str(e))
            await tts.speak("Hmm, something went wrong da. Try again?")

        # conversation.respond fires TTS as a task (fire-and-forget) — wait for it
        # so we don't accept new wake words while still speaking
        wait_start = time.monotonic()
        while tts.is_speaking:
            await asyncio.sleep(0.05)
            if time.monotonic() - wait_start > 30:  # safety cap
                break

        self._state = ListenState.PASSIVE

    def update_person(self, person_id: Optional[str], emotion: Optional[str] = None) -> None:
        self._current_person = person_id
        if emotion:
            self._current_emotion = emotion

    async def stop(self) -> None:
        self._running = False
        await mic.stop_stream()
        log.info("audio_pipeline.stopped")

    @property
    def state(self) -> ListenState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running


audio_pipeline = ListeningPipeline()
