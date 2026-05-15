"""
Emotion detection using FER+ ONNX model (offline).

Model: emotion-ferplus-8.onnx (~34MB)
  Input:  (1, 1, 64, 64) float32 grayscale
  Output: (1, 8) logits → softmax → 8 emotion classes
  Classes: neutral, happiness, surprise, sadness, anger, disgust, fear, contempt
  Source:  ONNX Model Zoo (FER+ dataset, Microsoft)

Pipeline:
  Face bbox → crop → resize 64x64 → ONNX inference → 5-frame smoothed softmax

Smoothing: rolling average over last N frames prevents 1-frame flickers
from triggering spurious emotion events.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.event_bus import Event, EventType, bus
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

MODEL_PATH = Path.home() / ".robot" / "models" / "emotion-ferplus-8.onnx"

EMOTION_LABELS = [
    "neutral", "happiness", "surprise", "sadness",
    "anger", "disgust", "fear", "contempt",
]

# Map FER+ labels → event-friendly names
EMOTION_MAP = {
    "neutral":   "neutral",
    "happiness": "happy",
    "surprise":  "surprised",
    "sadness":   "sad",
    "anger":     "angry",
    "disgust":   "disgusted",
    "fear":      "scared",
    "contempt":  "contempt",
}

SMOOTHING_FRAMES = 5
CONFIDENCE_THRESHOLD = 0.45   # below this = don't report emotion


@dataclass
class EmotionResult:
    emotion: str                     # "happy", "sad", etc. (mapped)
    raw_label: str                   # original FER+ label
    confidence: float                # 0.0–1.0 (smoothed softmax)
    all_scores: Dict[str, float]     # all 8 emotions with scores
    track_id: str = ""               # linked to person tracker


class EmotionDetector:
    """
    Runs emotion inference on face crops.
    Maintains per-track smoothing history.

    Usage:
        detector = EmotionDetector()
        await detector.start()
        # Then call process() each time you have a face crop
        result = detector.predict(face_bgr_crop, track_id="P001")
    """

    def __init__(self) -> None:
        self._session = None
        self._available = False
        self._input_name: Optional[str] = None
        self._smooth_history: Dict[str, Deque] = {}   # track_id → deque of score arrays
        self._last_emit: Dict[str, Tuple[str, float]] = {}   # track_id → (emotion, ts)
        self._emit_cooldown_s = 2.0   # don't re-emit same emotion within 2s

    def load(self) -> bool:
        """Load ONNX session. Returns True if successful."""
        if not MODEL_PATH.exists():
            log.warning("emotion_detector.model_missing", path=str(MODEL_PATH))
            return False
        try:
            import onnxruntime as ort
            sess_opts = ort.SessionOptions()
            sess_opts.intra_op_num_threads = 2
            sess_opts.inter_op_num_threads = 1
            sess_opts.log_severity_level = 3  # suppress ONNX graph warnings
            self._session = ort.InferenceSession(
                str(MODEL_PATH),
                sess_options=sess_opts,
                providers=["CPUExecutionProvider"],
            )
            self._input_name = self._session.get_inputs()[0].name
            self._available = True
            log.info("emotion_detector.loaded", model=MODEL_PATH.name)
            return True
        except Exception as e:
            log.warning("emotion_detector.load_failed", error=str(e))
            return False

    def predict(self, face_bgr: np.ndarray, track_id: str = "default") -> Optional[EmotionResult]:
        """
        Predict emotion for a face crop (BGR, any size).
        Returns smoothed EmotionResult or None if confidence too low.
        """
        if not self._available or self._session is None:
            return None

        try:
            # Preprocess: grayscale → 64×64 → normalize to [-1, 1] → (1,1,64,64)
            gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY) if len(face_bgr.shape) == 3 else face_bgr
            resized = cv2.resize(gray, (64, 64)).astype(np.float32)
            resized = (resized - 127.5) / 127.5   # normalize to [-1, 1]
            inp = resized.reshape(1, 1, 64, 64)

            # Inference
            outputs = self._session.run(None, {self._input_name: inp})
            logits = outputs[0][0]   # shape (8,)

            # Softmax
            exp_logits = np.exp(logits - logits.max())
            probs = exp_logits / exp_logits.sum()

            # Smooth over last N frames
            if track_id not in self._smooth_history:
                self._smooth_history[track_id] = deque(maxlen=SMOOTHING_FRAMES)
            self._smooth_history[track_id].append(probs)
            smoothed = np.mean(list(self._smooth_history[track_id]), axis=0)

            # Top emotion
            top_idx = int(smoothed.argmax())
            confidence = float(smoothed[top_idx])

            if confidence < CONFIDENCE_THRESHOLD:
                return None

            raw_label = EMOTION_LABELS[top_idx]
            emotion = EMOTION_MAP[raw_label]
            all_scores = {
                EMOTION_MAP[EMOTION_LABELS[i]]: round(float(smoothed[i]), 3)
                for i in range(len(EMOTION_LABELS))
            }

            return EmotionResult(
                emotion=emotion,
                raw_label=raw_label,
                confidence=round(confidence, 3),
                all_scores=all_scores,
                track_id=track_id,
            )

        except Exception as e:
            log.error("emotion_detector.predict_error", error=str(e))
            return None

    async def process_and_emit(
        self,
        face_bgr: np.ndarray,
        track_id: str,
        person_id: Optional[str] = None,
    ) -> Optional[EmotionResult]:
        """
        Predict emotion and emit to event bus if changed + confident.
        Runs inference in executor to avoid blocking event loop.
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.predict, face_bgr, track_id)

        if result is None:
            return None

        # Cooldown: only emit if emotion changed or enough time passed
        last = self._last_emit.get(track_id)
        now = time.monotonic()
        if last:
            last_emotion, last_ts = last
            if last_emotion == result.emotion and (now - last_ts) < self._emit_cooldown_s:
                return result   # return result but don't spam bus

        self._last_emit[track_id] = (result.emotion, now)
        await bus.publish(Event(
            type=EventType.EMOTION_DETECTED,
            data={
                "track_id": track_id,
                "person_id": person_id,
                "emotion": result.emotion,
                "confidence": result.confidence,
                "all_scores": result.all_scores,
            },
            source="emotion_detector",
        ))

        telemetry.increment(f"emotion.{result.emotion}")
        log.debug("emotion_detector.detected",
                   emotion=result.emotion,
                   confidence=result.confidence,
                   track_id=track_id)
        return result

    def clear_track(self, track_id: str) -> None:
        """Remove smoothing history when a person leaves frame."""
        self._smooth_history.pop(track_id, None)
        self._last_emit.pop(track_id, None)

    @property
    def is_available(self) -> bool:
        return self._available


emotion_detector = EmotionDetector()
