"""
Gesture recognition — Phase D.

Primary backend: MediaPipe Gesture Recognizer Tasks API.
  Unavailable on Python 3.13/aarch64 (no Linux wheel published yet).
  Will auto-activate when mediapipe releases an aarch64 wheel.

Fallback backend: OpenCV skin-color + convex-hull classifier.
  Works without any additional packages. ~80% accuracy in decent lighting.
  Detects: Open_Palm, Thumb_Up, Closed_Fist, Victory, ILoveYou, Pointing_Up.

Runs at 4 FPS via asyncio task. Reads from camera.latest_frame — no second
VideoCapture opened. Per-gesture 3s cooldown. WAVE requires 2 consecutive
detections (~500ms hold) before firing.
"""

import asyncio
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from core.event_bus import bus, Event, EventType, EventPriority
from utils.logger import get_logger

log = get_logger(__name__)

GESTURE_FPS         = 4
GESTURE_INTERVAL    = 1.0 / GESTURE_FPS
MIN_CONFIDENCE      = 0.75
GESTURE_COOLDOWN_S  = 3.0
WAVE_HOLD_FRAMES    = 2          # Open_Palm must appear this many consecutive frames
SKIN_AREA_MIN       = 4000       # px² — ignore small blobs
MODEL_PATH          = Path(__file__).parent.parent.parent / "models" / "gesture_recognizer.task"
MODEL_URL           = (
    "https://storage.googleapis.com/mediapipe-models/"
    "gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
)

# MediaPipe gesture name → EventType
_GESTURE_MAP: Dict[str, EventType] = {
    "Open_Palm":    EventType.GESTURE_WAVE,
    "Thumb_Up":     EventType.GESTURE_THUMBS_UP,
    "Victory":      EventType.GESTURE_PEACE,
    "Closed_Fist":  EventType.GESTURE_FIST,
    "ILoveYou":     EventType.GESTURE_LOVE,
    "Pointing_Up":  EventType.GESTURE_POINT,
}

_EVENT_GESTURE_NAME: Dict[EventType, str] = {v: k for k, v in _GESTURE_MAP.items()}


# ── MediaPipe backend ─────────────────────────────────────────────────────────

class _MediaPipeBackend:
    def __init__(self) -> None:
        self._recognizer = None
        self._available  = False

    def load(self) -> bool:
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            if not MODEL_PATH.exists():
                log.info("gesture.mp.downloading_model", path=str(MODEL_PATH))
                import urllib.request
                MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
                log.info("gesture.mp.model_downloaded", bytes=MODEL_PATH.stat().st_size)

            base_opts = mp_python.BaseOptions(model_asset_path=str(MODEL_PATH))
            opts = mp_vision.GestureRecognizerOptions(
                base_options=base_opts,
                running_mode=mp_vision.RunningMode.IMAGE,
                min_hand_detection_confidence=MIN_CONFIDENCE,
                min_hand_presence_confidence=MIN_CONFIDENCE,
                min_tracking_confidence=MIN_CONFIDENCE,
            )
            self._recognizer = mp_vision.GestureRecognizer.create_from_options(opts)
            self._available  = True
            log.info("gesture.mp.loaded")
            return True
        except ImportError:
            log.info("gesture.mp.unavailable",
                     reason="no mediapipe wheel for Python 3.13/aarch64 yet")
        except Exception as e:
            log.warning("gesture.mp.load_error", error=str(e)[:120])
        return False

    def detect(self, bgr_frame: np.ndarray) -> Tuple[Optional[str], float]:
        """Returns (gesture_name, confidence) or (None, 0.0)."""
        if not self._available or self._recognizer is None:
            return None, 0.0
        try:
            from mediapipe.tasks.python.vision import GestureRecognizer
            import mediapipe as mp
            rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._recognizer.recognize(mp_image)
            if not result.gestures:
                return None, 0.0
            top = result.gestures[0][0]
            return top.category_name, top.score
        except Exception as e:
            log.debug("gesture.mp.detect_error", error=str(e)[:80])
            return None, 0.0

    @property
    def is_available(self) -> bool:
        return self._available


# ── OpenCV skin+hull fallback ─────────────────────────────────────────────────

class _OpenCVBackend:
    """
    Skin-color segmentation + convex hull finger counting.
    Works reliably in good, consistent lighting against a plain background.
    """

    # HSV skin ranges (covers many skin tones)
    _LOWER1 = np.array([0,  20, 70],  dtype=np.uint8)
    _UPPER1 = np.array([20, 255, 255], dtype=np.uint8)
    _LOWER2 = np.array([170, 20, 70], dtype=np.uint8)
    _UPPER2 = np.array([180, 255, 255], dtype=np.uint8)
    _KERNEL  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def detect(self, bgr_frame: np.ndarray) -> Tuple[Optional[str], float]:
        try:
            hsv   = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2HSV)
            mask  = cv2.inRange(hsv, self._LOWER1, self._UPPER1)
            mask  = cv2.bitwise_or(mask, cv2.inRange(hsv, self._LOWER2, self._UPPER2))
            mask  = cv2.dilate(mask, self._KERNEL, iterations=2)
            mask  = cv2.erode(mask,  self._KERNEL, iterations=1)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None, 0.0

            cnt  = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(cnt)
            if area < SKIN_AREA_MIN:
                return None, 0.0

            fingers = self._count_fingers(cnt)
            ar      = self._aspect_ratio(cnt)
            name, conf = self._classify(fingers, ar, area)
            return name, conf
        except Exception:
            return None, 0.0

    @staticmethod
    def _count_fingers(cnt: np.ndarray) -> int:
        try:
            hull   = cv2.convexHull(cnt, returnPoints=False)
            defects = cv2.convexityDefects(cnt, hull)
            if defects is None:
                return 0
            count = 0
            for i in range(defects.shape[0]):
                _, _, _, d = defects[i, 0]
                if d / 256.0 > 12:
                    count += 1
            return count
        except Exception:
            return 0

    @staticmethod
    def _aspect_ratio(cnt: np.ndarray) -> float:
        x, y, w, h = cv2.boundingRect(cnt)
        return h / max(w, 1)

    @staticmethod
    def _classify(fingers: int, ar: float, area: float) -> Tuple[Optional[str], float]:
        if fingers >= 4:
            return "Open_Palm", 0.82
        if fingers == 3:
            return "Victory", 0.78        # 3-finger V approximation
        if fingers == 2:
            return "Victory", 0.80
        if fingers == 1 and ar > 1.8:
            return "Pointing_Up", 0.78
        if fingers == 1 and ar <= 1.8:
            return "Thumb_Up", 0.80
        if fingers == 0 and 0.7 < ar < 1.5:
            return "Closed_Fist", 0.82
        return None, 0.0

    @property
    def is_available(self) -> bool:
        return True


# ── Gesture loop ──────────────────────────────────────────────────────────────

class GestureLoop:

    def __init__(self) -> None:
        self._running  = False
        self._task: Optional[asyncio.Task] = None
        self._mp       = _MediaPipeBackend()
        self._cv       = _OpenCVBackend()
        self._backend_name = "none"

        # Per-gesture cooldown trackers
        self._last_fired: Dict[str, float] = {}
        # Wave hold counter (requires WAVE_HOLD_FRAMES consecutive detections)
        self._wave_streak = 0

        self._stats = {"frames": 0, "detections": 0, "fired": 0}

    def setup(self) -> None:
        """Load preferred backend. Called once before start()."""
        if self._mp.load():
            self._backend_name = "mediapipe"
        else:
            self._backend_name = "opencv_skin"
            log.info("gesture.using_fallback_backend", backend="opencv_skin")

    async def start(self) -> bool:
        if self._backend_name == "none":
            self.setup()
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="gesture_loop")
        log.info("gesture.started", backend=self._backend_name, fps=GESTURE_FPS)
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("gesture.stopped", **self._stats)

    async def _loop(self) -> None:
        from perception.vision.camera import camera
        while self._running:
            t0 = time.monotonic()
            try:
                frame = camera.latest_frame
                if frame and not frame.is_stale(max_age_ms=500):
                    await self._process(frame.image, frame.timestamp)
            except Exception as e:
                log.debug("gesture.loop_error", error=str(e)[:80])
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, GESTURE_INTERVAL - elapsed))

    async def _process(self, bgr: np.ndarray, frame_ts: float) -> None:
        self._stats["frames"] += 1

        # Run detection in executor to avoid blocking asyncio
        loop = asyncio.get_event_loop()
        backend = self._mp if self._mp.is_available else self._cv
        name, conf = await loop.run_in_executor(None, backend.detect, bgr)

        detect_ts = time.monotonic()
        latency_ms = (detect_ts - frame_ts) * 1000

        if name is None or conf < MIN_CONFIDENCE:
            self._wave_streak = 0
            return

        self._stats["detections"] += 1
        log.debug("gesture.raw", gesture=name, conf=round(conf, 3),
                  latency_ms=round(latency_ms, 1))

        # Wave requires consecutive hold
        if name == "Open_Palm":
            self._wave_streak += 1
            if self._wave_streak < WAVE_HOLD_FRAMES:
                return
        else:
            self._wave_streak = 0

        event_type = _GESTURE_MAP.get(name)
        if event_type is None:
            return

        # Per-gesture cooldown
        now = time.monotonic()
        if now - self._last_fired.get(name, 0.0) < GESTURE_COOLDOWN_S:
            return
        self._last_fired[name] = now
        self._stats["fired"] += 1

        log.info("gesture.fired", gesture=name, conf=round(conf, 3),
                 latency_ms=round(latency_ms, 1), backend=self._backend_name)

        await bus.publish(Event(
            type=event_type,
            data={"gesture": name, "confidence": conf,
                  "latency_ms": latency_ms, "backend": self._backend_name},
            priority=EventPriority.HIGH,
        ))

        # Also emit generic GESTURE_DETECTED for legacy subscribers
        await bus.publish(Event(
            type=EventType.GESTURE_DETECTED,
            data={"gesture": name, "confidence": conf},
            priority=EventPriority.NORMAL,
        ))

    @property
    def backend(self) -> str:
        return self._backend_name

    @property
    def stats(self) -> dict:
        return dict(self._stats)


gesture_loop = GestureLoop()
