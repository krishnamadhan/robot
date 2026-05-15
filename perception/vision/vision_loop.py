"""
Integrated vision pipeline — ties together camera, person detection,
face recognition, and emotion detection into one async loop.

Runs at recognition_fps (default 3 FPS) since LBPH + emotion are
cheap but face detection is the bottleneck (~100ms per frame).

Emits to event bus:
  PERSON_DETECTED      — new track appears
  PERSON_LOST          — track disappears
  FACE_RECOGNIZED      — known person ID confirmed
  FACE_UNKNOWN         — unrecognized face
  EMOTION_DETECTED     — emotion reading with smoothing

Wires into personality:
  face.recognized(name) → mood +0.1, attachment +0.15
  face.unknown          → arousal +0.2
  emotion.happy         → mood +0.08 (mood contagion)
  emotion.sad           → triggers comfort behavior intent
  person_arrived after alone → excitement +0.2
"""

import asyncio
import time
from typing import Any, Dict, Optional

import cv2
import numpy as np

from core.event_bus import Event, EventType, bus
from core.personality import personality
from perception.vision.camera import camera
from perception.vision.face import FaceEngine, RecognitionResult
from perception.vision.emotion import EmotionDetector
from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


# Emotion → personality event mapping
_EMOTION_PERSONALITY_MAP = {
    "happy":     ("laughter_nearby", 1.0),    # mood contagion
    "sad":       None,                          # handled specially (comfort)
    "surprised": None,
    "angry":     ("loud_noise", 0.5),
    "scared":    ("loud_noise", 0.3),
    "neutral":   None,
    "disgusted": None,
    "contempt":  None,
}


class VisionLoop:
    """
    Async vision pipeline — runs as a background task.
    Integrates person detection + face recognition + emotion.
    """

    RECOGNITION_FPS = 3.0           # how often to run face+emotion pipeline
    PERSON_ARRIVAL_ALONE_S = 60.0   # if alone for this long, treat next arrival as "exciting"
    CONFIRM_FRAMES     = 3          # consecutive positive frames before firing FACE_RECOGNIZED
    REFIRE_COOLDOWN_S  = 30.0       # don't re-fire FACE_RECOGNIZED for the same person within this window

    def __init__(self) -> None:
        self._face_engine = FaceEngine()
        self._emotion_detector = EmotionDetector()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_person_seen: float = 0.0
        self._was_alone = True
        # Consecutive-frames gate: track_id → consecutive hit count
        self._consec: Dict[str, int] = {}
        # Debounce: person_id → last time FACE_RECOGNIZED was fired
        self._last_fired: Dict[str, float] = {}

    async def start(self) -> None:
        # Load models (synchronous but fast after first run)
        loop = asyncio.get_event_loop()
        self._face_engine.load()
        await loop.run_in_executor(None, self._emotion_detector.load)

        if not self._emotion_detector.is_available:
            log.warning("vision_loop.emotion_unavailable")

        self._running = True
        self._task = asyncio.create_task(self._loop(), name="vision_loop")
        log.info("vision_loop.started",
                  enrolled=self._face_engine.list_enrolled(),
                  emotion=self._emotion_detector.is_available)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        interval = 1.0 / self.RECOGNITION_FPS
        while self._running:
            t0 = time.monotonic()

            frame_obj = camera.latest_frame
            if frame_obj is None or frame_obj.is_stale(500):
                await asyncio.sleep(interval)
                continue

            frame = frame_obj.image
            try:
                await self._process_frame(frame)
            except Exception as e:
                log.error("vision_loop.frame_error", error=str(e), exc_info=True)

            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0, interval - elapsed))

    async def _process_frame(self, frame: np.ndarray) -> None:
        loop = asyncio.get_event_loop()

        # Run face detection in executor (OpenCV cascade is sync)
        detections = await loop.run_in_executor(
            None, self._face_engine.detect_faces, frame
        )

        if not detections:
            # No faces this frame — reset consecutive counters
            self._consec.clear()
            now = time.monotonic()
            if not self._was_alone and (now - self._last_person_seen) > 5.0:
                self._was_alone = True
                self._last_fired.clear()
                personality.process_event("person_left")
                log.info("vision_loop.person_left")
            return

        self._last_person_seen = time.monotonic()
        just_arrived = self._was_alone
        self._was_alone = False

        if just_arrived:
            alone_duration = time.monotonic() - self._last_person_seen
            if alone_duration > self.PERSON_ARRIVAL_ALONE_S:
                personality.process_event("person_arrived")
                personality.state.arousal = min(1.0, personality.state.arousal + 0.2)
            else:
                personality.process_event("person_arrived")

        for det in detections:
            # Recognition
            result = await loop.run_in_executor(None, self._face_engine.recognize, det)
            await self._handle_recognition(result, frame, det)

    async def _handle_recognition(
        self,
        result: RecognitionResult,
        frame: np.ndarray,
        det,
    ) -> None:
        x, y, w, h = result.bbox
        frame_w = frame.shape[1] if frame is not None else 640
        bbox_center_x = round(((x + w / 2) / frame_w) * 2.0 - 1.0, 3)
        face_crop = frame[y:y+h, x:x+w]
        track_id = f"face_{x//50}_{y//50}"   # coarse spatial ID

        if result.person_id:
            # Consecutive-frames gate: must see the same person N frames in a row
            self._consec[track_id] = self._consec.get(track_id, 0) + 1
            if self._consec[track_id] < self.CONFIRM_FRAMES:
                return  # not confirmed yet

            personality.update_person(result.person_id, name=result.name)

            # Debounce: fire FACE_RECOGNIZED at most once per REFIRE_COOLDOWN_S
            now = time.monotonic()
            last = self._last_fired.get(result.person_id, 0.0)
            if now - last >= self.REFIRE_COOLDOWN_S:
                self._last_fired[result.person_id] = now
                personality.process_event("good_interaction", person_id=result.person_id)
                await bus.publish(Event(
                    type=EventType.FACE_RECOGNIZED,
                    data={
                        "person_id": result.person_id,
                        "name": result.name,
                        "confidence": result.confidence,
                        "bbox_center_x": bbox_center_x,
                    },
                    source="vision_loop",
                ))
                telemetry.increment(f"face.recognized.{result.name}")
                log.info("vision_loop.recognized",
                          name=result.name, conf=result.confidence)
        else:
            # Unknown face
            await bus.publish(Event(
                type=EventType.FACE_UNKNOWN,
                data={},
                source="vision_loop",
            ))
            # Unknown person raises caution + curiosity
            personality.state.arousal = min(1.0, personality.state.arousal + 0.15)

        # Emotion detection (only if face crop is large enough)
        if w >= 48 and h >= 48 and self._emotion_detector.is_available:
            person_id = result.person_id
            emotion_result = await self._emotion_detector.process_and_emit(
                face_crop, track_id=track_id, person_id=person_id
            )
            if emotion_result:
                await self._handle_emotion(emotion_result, person_id)

    async def _handle_emotion(self, emotion_result, person_id: Optional[str]) -> None:
        emotion = emotion_result.emotion

        # Mood contagion — Cosmo picks up on others' emotions
        mapping = _EMOTION_PERSONALITY_MAP.get(emotion)
        if mapping:
            event_key, weight = mapping
            # Apply partial impact weighted by emotion confidence
            impacts = cfg.personality.event_impacts.get(event_key, {})
            for dim, delta in impacts.items():
                old = getattr(personality.state, dim, None)
                if old is not None:
                    setattr(personality.state, dim, old + delta * weight * emotion_result.confidence)
            personality.state.clamp()

        elif emotion == "sad" and emotion_result.confidence > 0.6:
            # Special: someone looks sad → Cosmo wants to comfort
            personality.state.mood = max(-0.2, personality.state.mood - 0.05)
            await bus.publish(Event(
                type=EventType.MOOD_CHANGED,
                data={"trigger": "emotion_contagion_sad", "person_id": person_id},
                source="vision_loop",
            ))

    def get_face_engine(self) -> FaceEngine:
        return self._face_engine

    def get_emotion_detector(self) -> EmotionDetector:
        return self._emotion_detector


vision_loop = VisionLoop()
