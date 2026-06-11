"""
Parallel vision pipeline — three independent async loops.

Architecture:
  Loop 1 (15 FPS): haarcascade detection  — fast, never blocks
  Loop 2 (5 FPS):  SFace recognition      — medium, gated on person present
  Loop 3 (2 FPS):  emotion detection      — slow, gated on face confirmed

Each loop runs in its own asyncio task and uses run_in_executor for
CPU-bound work so they never block the event loop or each other.

Shared mutable state is safe under CPython GIL for simple attribute writes.

Emits to event bus:
  PERSON_DETECTED      — person enters frame
  PERSON_LOST          — person leaves frame (5s grace)
  FACE_RECOGNIZED      — known person confirmed (CONFIRM_FRAMES consecutive)
  FACE_UNKNOWN         — unrecognized face
  EMOTION_DETECTED     — smoothed emotion reading
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from core.event_bus import Event, EventType, bus
from core.personality import personality
from perception.vision.camera import camera
from perception.vision.face import FaceEngine, RecognitionResult
from core.capabilities import Capability, registry as cap_registry
from perception.vision.emotion import EmotionDetector
from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


_EMOTION_PERSONALITY_MAP = {
    "happy":     ("laughter_nearby", 1.0),
    "sad":       None,
    "surprised": None,
    "angry":     ("loud_noise", 0.5),
    "scared":    ("loud_noise", 0.3),
    "neutral":   None,
    "disgusted": None,
    "contempt":  None,
}


class VisionLoop:
    """
    Parallel 3-loop vision pipeline.

    Detection runs at 15 FPS. Recognition and emotion run at their own
    independent rates and share state with the detection loop through
    simple Python attribute writes (safe under GIL).
    """

    DETECT_FPS          = 15.0
    RECOGNIZE_FPS       = 5.0
    EMOTION_FPS         = 2.0

    PERSON_ARRIVAL_ALONE_S = 60.0
    CONFIRM_FRAMES      = 3
    REFIRE_COOLDOWN_S   = 30.0

    def __init__(self) -> None:
        self._face_engine      = FaceEngine()
        self._emotion_detector = EmotionDetector()
        self._running          = False
        self._tasks: List[asyncio.Task] = []

        # Written by detection loop, read by recognition + emotion
        self._current_frame:      Optional[np.ndarray] = None
        self._current_frame_ts:   float                = 0.0
        self._current_detections: list                 = []
        self._person_present:     bool                 = False

        # Written by recognition loop, read by emotion loop
        self._confirmed_person_id:   Optional[str] = None
        self._confirmed_person_name: Optional[str] = None

        # Tracking helpers
        self._last_person_seen: float      = 0.0
        self._was_alone:        bool       = True
        self._consec:           Dict[str, int]   = {}
        self._last_fired:       Dict[str, float] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._face_engine.load()
        await loop.run_in_executor(None, self._emotion_detector.load)

        if not self._emotion_detector.is_available:
            log.warning("vision_loop.emotion_unavailable")

        self._running = True
        self._tasks = [
            asyncio.create_task(self._detection_loop(),   name="vision.detect"),
            asyncio.create_task(self._recognition_loop(), name="vision.recognize"),
            asyncio.create_task(self._emotion_loop(),     name="vision.emotion"),
        ]
        log.info("vision_loop.started",
                  mode="parallel",
                  detect_fps=self.DETECT_FPS,
                  recognize_fps=self.RECOGNIZE_FPS,
                  emotion_fps=self.EMOTION_FPS,
                  enrolled=self._face_engine.list_enrolled(),
                  emotion=self._emotion_detector.is_available)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

    # ── Loop 1: Detection (15 FPS) ────────────────────────────────────────────

    async def _detection_loop(self) -> None:
        interval = 1.0 / self.DETECT_FPS
        loop = asyncio.get_event_loop()
        last_frame_obj = None

        while self._running:
            t0 = time.monotonic()

            frame_obj = camera.latest_frame
            if frame_obj is None or frame_obj.is_stale(500) or frame_obj is last_frame_obj:
                await asyncio.sleep(interval)
                continue

            last_frame_obj = frame_obj
            frame = frame_obj.image

            detections = await loop.run_in_executor(
                None, self._face_engine.detect_faces, frame
            )
            cap_registry.mark_seen(Capability.VISION, "frame processed")

            # Update shared state
            self._current_frame      = frame
            self._current_frame_ts   = frame_obj.timestamp
            self._current_detections = detections
            was_present              = self._person_present
            self._person_present     = len(detections) > 0

            if detections:
                alone_duration = time.monotonic() - self._last_person_seen
                self._last_person_seen = time.monotonic()
                just_arrived = self._was_alone
                self._was_alone = False

                if just_arrived:
                    personality.process_event("person_arrived")
                    if alone_duration > self.PERSON_ARRIVAL_ALONE_S:
                        personality.state.arousal = min(1.0, personality.state.arousal + 0.2)
                    await bus.publish(Event(
                        type=EventType.PERSON_DETECTED,
                        data={"alone_duration_s": round(alone_duration, 1)},
                        source="vision_loop",
                    ))
                    telemetry.increment("vision.person_detected")
            else:
                now = time.monotonic()
                if not self._was_alone and (now - self._last_person_seen) > 5.0:
                    self._was_alone = True
                    self._confirmed_person_id   = None
                    self._confirmed_person_name = None
                    self._consec.clear()
                    self._last_fired.clear()
                    personality.process_event("person_left")
                    await bus.publish(Event(
                        type=EventType.PERSON_LOST,
                        data={"alone_since": now},
                        source="vision_loop",
                    ))

            elapsed = time.monotonic() - t0
            telemetry.gauge("vision.detect_ms", elapsed * 1000)
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ── Loop 2: Recognition (5 FPS) ──────────────────────────────────────────

    async def _recognition_loop(self) -> None:
        interval = 1.0 / self.RECOGNIZE_FPS
        loop = asyncio.get_event_loop()
        last_frame_ts = 0.0

        while self._running:
            if not self._person_present:
                await asyncio.sleep(0.2)
                continue

            frame      = self._current_frame
            frame_ts   = self._current_frame_ts
            detections = self._current_detections

            if frame is None or not detections or frame_ts == last_frame_ts:
                await asyncio.sleep(interval)
                continue

            last_frame_ts = frame_ts
            t0 = time.monotonic()

            for det in detections:
                result = await loop.run_in_executor(
                    None, self._face_engine.recognize, det
                )
                cap_registry.mark_seen(Capability.FACE_ID, "recognition ran")
                await self._handle_recognition(result, frame, det)

            elapsed = time.monotonic() - t0
            telemetry.gauge("vision.recognize_ms", elapsed * 1000)
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ── Loop 3: Emotion (2 FPS) ───────────────────────────────────────────────

    async def _emotion_loop(self) -> None:
        interval = 1.0 / self.EMOTION_FPS
        loop = asyncio.get_event_loop()

        while self._running:
            if not self._person_present or not self._emotion_detector.is_available:
                await asyncio.sleep(0.5)
                continue

            frame      = self._current_frame
            detections = self._current_detections

            if frame is None or not detections:
                await asyncio.sleep(interval)
                continue

            det  = detections[0]
            x, y, w, h = det.bbox
            if w < 48 or h < 48:
                await asyncio.sleep(interval)
                continue

            face_crop  = frame[y:y+h, x:x+w]
            track_id   = f"face_{x//50}_{y//50}"
            person_id  = self._confirmed_person_id

            t0 = time.monotonic()
            emotion_result = await self._emotion_detector.process_and_emit(
                face_crop, track_id=track_id, person_id=person_id
            )
            if emotion_result:
                cap_registry.mark_seen(Capability.EMOTION_READ, "emotion read")
                await self._handle_emotion(emotion_result, person_id)

            elapsed = time.monotonic() - t0
            telemetry.gauge("vision.emotion_ms", elapsed * 1000)
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ── Handlers (shared logic) ───────────────────────────────────────────────

    async def _handle_recognition(
        self,
        result: RecognitionResult,
        frame: np.ndarray,
        det,
    ) -> None:
        x, y, w, h = result.bbox
        frame_w = frame.shape[1] if frame is not None else 320
        bbox_center_x = round(((x + w / 2) / frame_w) * 2.0 - 1.0, 3)
        track_id = f"face_{x//50}_{y//50}"

        if result.person_id:
            self._consec[track_id] = self._consec.get(track_id, 0) + 1
            if self._consec[track_id] < self.CONFIRM_FRAMES:
                return

            personality.update_person(result.person_id, name=result.name)

            now  = time.monotonic()
            last = self._last_fired.get(result.person_id, 0.0)
            if now - last >= self.REFIRE_COOLDOWN_S:
                self._last_fired[result.person_id] = now
                self._confirmed_person_id   = result.person_id
                self._confirmed_person_name = result.name
                personality.process_event("good_interaction", person_id=result.person_id)
                await bus.publish(Event(
                    type=EventType.FACE_RECOGNIZED,
                    data={
                        "person_id":      result.person_id,
                        "name":           result.name,
                        "confidence":     result.confidence,
                        "bbox_center_x":  bbox_center_x,
                    },
                    source="vision_loop",
                ))
                telemetry.increment(f"face.recognized.{result.name}")
                log.info("vision_loop.recognized",
                          name=result.name, conf=result.confidence)
        else:
            await bus.publish(Event(
                type=EventType.FACE_UNKNOWN,
                data={},
                source="vision_loop",
            ))
            personality.state.arousal = min(1.0, personality.state.arousal + 0.15)

    async def _handle_emotion(self, emotion_result, person_id: Optional[str]) -> None:
        emotion = emotion_result.emotion

        mapping = _EMOTION_PERSONALITY_MAP.get(emotion)
        if mapping:
            event_key, weight = mapping
            impacts = cfg.personality.event_impacts.get(event_key, {})
            for dim, delta in impacts.items():
                old = getattr(personality.state, dim, None)
                if old is not None:
                    setattr(personality.state, dim,
                            old + delta * weight * emotion_result.confidence)
            personality.state.clamp()
        elif emotion == "sad" and emotion_result.confidence > 0.6:
            personality.state.mood = max(-0.2, personality.state.mood - 0.05)
            await bus.publish(Event(
                type=EventType.MOOD_CHANGED,
                data={"trigger": "emotion_contagion_sad", "person_id": person_id},
                source="vision_loop",
            ))

    # ── Public helpers ────────────────────────────────────────────────────────

    def get_face_engine(self) -> FaceEngine:
        return self._face_engine

    def get_emotion_detector(self) -> EmotionDetector:
        return self._emotion_detector


vision_loop = VisionLoop()
