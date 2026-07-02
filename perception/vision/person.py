"""
Person detection and tracking.

Primary: yolo11n.pt via ultralytics (models/yolo11n.pt, 5.4MB).
Fallback: OpenCV HOG detector (no torch needed, lower accuracy).

Why not MediaPipe: no Python 3.13 wheels as of build date.
Why yolo11n over MobileNet SSD: better Pi 5 benchmarks;
ultralytics handles model download + inference in one package.

Detection runs at 8 FPS target; tracking runs at 30 FPS using
lightweight centroid assignment between detection frames.
"""

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.event_bus import Event, EventType, bus
from perception.vision.camera import Frame
from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)


@dataclass
class Detection:
    bbox: Tuple[int, int, int, int]    # x1, y1, x2, y2 (pixels)
    confidence: float
    distance_estimate: str             # "near" | "mid" | "far"
    position_h: str                    # "left" | "center" | "right"


@dataclass
class TrackedPerson:
    track_id: str
    bbox: Tuple[int, int, int, int]
    confidence: float
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    distance_estimate: str = "mid"
    position_h: str = "center"
    frames_present: int = 1
    frames_absent: int = 0

    def center(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return (x2 - x1) * (y2 - y1)

    def time_in_frame_s(self) -> float:
        return self.last_seen - self.first_seen

    def is_lost(self, max_absent: int = 15) -> bool:
        return self.frames_absent > max_absent


class _YOLOOnnxDetector:
    """yolo11n via onnxruntime — no torch (~400MB RSS saved vs ultralytics).

    onnxruntime is already resident for SFace + emotion, so this shares the
    runtime. Export: `yolo export model=yolo11n.pt format=onnx imgsz=320`.
    """

    def __init__(self, model_cfg: Dict[str, Any]) -> None:
        self._model_cfg = model_cfg
        self._session = None
        self._input_name = ""
        self._input_size = int(model_cfg.get("input_size", 320))
        self._iou = float(model_cfg.get("iou_threshold", 0.45))

    def initialize(self) -> bool:
        weights = self._model_cfg.get("weights_onnx", "models/yolo11n.onnx")
        path = Path(weights)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / weights
        if not path.exists():
            log.info("person_detector.onnx_weights_missing", path=str(path))
            return False
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                str(path), providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            log.info("person_detector.yolo_onnx_loaded", path=path.name)
            return True
        except Exception as e:
            log.warning("person_detector.yolo_onnx_unavailable", error=str(e))
            return False

    def detect(self, frame: np.ndarray, min_confidence: float) -> List[Detection]:
        if self._session is None:
            return []
        try:
            h0, w0 = frame.shape[:2]
            size = self._input_size
            # Letterbox to square, keep aspect
            scale = min(size / w0, size / h0)
            nw, nh = int(round(w0 * scale)), int(round(h0 * scale))
            pad_x, pad_y = (size - nw) / 2, (size - nh) / 2
            resized = cv2.resize(frame, (nw, nh))
            canvas = np.full((size, size, 3), 114, dtype=np.uint8)
            top, left = int(round(pad_y - 0.1)), int(round(pad_x - 0.1))
            canvas[top:top + nh, left:left + nw] = resized

            blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            out = self._session.run(None, {self._input_name: blob})[0]

            # Output (1, 84, N): rows 0-3 = cx,cy,w,h; row 4 = person score (class 0)
            preds = out[0]
            boxes_xywh = preds[0:4, :]
            person_scores = preds[4, :]
            keep = person_scores >= min_confidence
            if not keep.any():
                return []
            boxes_xywh = boxes_xywh[:, keep]
            scores = person_scores[keep]

            # xywh (letterboxed px) → xyxy in original frame coords
            cx, cy, bw, bh = boxes_xywh
            x1 = (cx - bw / 2 - left) / scale
            y1 = (cy - bh / 2 - top) / scale
            x2 = (cx + bw / 2 - left) / scale
            y2 = (cy + bh / 2 - top) / scale

            rects = [
                [int(max(0, x1[i])), int(max(0, y1[i])),
                 int(min(w0, x2[i]) - max(0, x1[i])), int(min(h0, y2[i]) - max(0, y1[i]))]
                for i in range(len(scores))
            ]
            idxs = cv2.dnn.NMSBoxes(rects, scores.tolist(), min_confidence, self._iou)
            detections = []
            for i in np.array(idxs).flatten():
                x, y, w, h = rects[int(i)]
                detections.append(
                    _make_detection((x, y, x + w, y + h), float(scores[int(i)]), w0, h0)
                )
            return detections
        except Exception as e:
            log.error("person_detector.yolo_onnx_error", error=str(e))
            return []


class _YOLODetector:
    def __init__(self, model_cfg: Dict[str, Any]) -> None:
        self._model_cfg = model_cfg
        self._model = None
        self._available = False

    def initialize(self) -> bool:
        try:
            from ultralytics import YOLO
            self._model = YOLO(self._model_cfg.get("weights", "yolo11n.pt"))
            self._available = True
            log.info("person_detector.yolo_loaded")
            return True
        except Exception as e:
            log.warning("person_detector.yolo_unavailable", error=str(e))
            return False

    def detect(self, frame: np.ndarray, min_confidence: float) -> List[Detection]:
        if not self._model:
            return []
        try:
            results = self._model(
                frame,
                classes=[0],     # class 0 = person in COCO
                conf=min_confidence,
                imgsz=self._model_cfg.get("input_size", 320),
                verbose=False,
            )
            detections = []
            h, w = frame.shape[:2]
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    conf = float(box.conf[0])
                    detections.append(_make_detection((x1, y1, x2, y2), conf, w, h))
            return detections
        except Exception as e:
            log.error("person_detector.yolo_error", error=str(e))
            return []


class _HOGDetector:
    """OpenCV HOG person detector — no additional packages required."""

    def __init__(self) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray, min_confidence: float) -> List[Detection]:
        try:
            small = cv2.resize(frame, (320, 240))
            rects, weights = self._hog.detectMultiScale(
                small,
                winStride=(8, 8),
                scale=1.05,
                padding=(4, 4),
            )
            h_orig, w_orig = frame.shape[:2]
            scale_x = w_orig / 320
            scale_y = h_orig / 240
            detections = []
            for i, (x, y, w, h) in enumerate(rects):
                conf = float(weights[i]) if i < len(weights) else 0.5
                if conf < min_confidence:
                    continue
                x1 = int(x * scale_x)
                y1 = int(y * scale_y)
                x2 = int((x + w) * scale_x)
                y2 = int((y + h) * scale_y)
                detections.append(_make_detection((x1, y1, x2, y2), conf, w_orig, h_orig))
            return detections
        except Exception as e:
            log.error("person_detector.hog_error", error=str(e))
            return []


def _make_detection(bbox: Tuple, conf: float, frame_w: int, frame_h: int) -> Detection:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2
    area = (x2 - x1) * (y2 - y1)
    frame_area = frame_w * frame_h

    # Heuristic: person area relative to frame → distance
    area_frac = area / frame_area if frame_area > 0 else 0
    if area_frac > 0.25:
        dist = "near"
    elif area_frac > 0.08:
        dist = "mid"
    else:
        dist = "far"

    if cx < frame_w / 3:
        pos_h = "left"
    elif cx > 2 * frame_w / 3:
        pos_h = "right"
    else:
        pos_h = "center"

    return Detection(bbox=bbox, confidence=conf, distance_estimate=dist, position_h=pos_h)


class PersonDetector:
    """
    Multi-person detector with centroid tracking.

    Detection runs at target_detection_fps via frame skipping.
    Between detection frames, tracks existing persons with bbox interpolation.
    """

    MAX_TRACK_AGE = 20          # frames before track is dropped
    MAX_CENTROID_DIST = 150     # pixels, max distance for track association

    def __init__(self) -> None:
        self._model_cfg = cfg.models.person_detection
        self._thresh_cfg = cfg.thresholds.vision
        self._min_conf = self._thresh_cfg.get("person_confidence_min", 0.5)
        self._target_fps = self._thresh_cfg.get("detection_fps", 8)
        self._tracks: Dict[str, TrackedPerson] = {}
        self._frame_count = 0
        self._detection_interval = max(1, int(30 / self._target_fps))
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Detector preference: ONNX (no torch) → ultralytics YOLO → HOG
        self._onnx = _YOLOOnnxDetector(self._model_cfg)
        self._yolo = _YOLODetector(self._model_cfg)
        self._hog = _HOGDetector()
        self._use_onnx = False
        self._use_yolo = False

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._use_onnx = await loop.run_in_executor(None, self._onnx.initialize)
        if not self._use_onnx:
            # Avoid ultralytics unless ONNX truly unavailable — its import pulls
            # torch (~400MB RSS) and stays resident even if the import FAILS later
            self._use_yolo = await loop.run_in_executor(None, self._yolo.initialize)
        if not self._use_onnx and not self._use_yolo:
            log.info("person_detector.using_hog_fallback")
        self._running = True
        self._task = asyncio.create_task(self._detect_loop(), name="person_detector")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _detect_loop(self) -> None:
        from perception.vision.camera import camera
        while self._running:
            await asyncio.sleep(1.0 / self._target_fps)
            frame = camera.latest_frame
            if frame is None or frame.is_stale(500):
                continue

            try:
                detections = await asyncio.get_event_loop().run_in_executor(
                    None, self._run_detection, frame.image
                )
                await self._update_tracks(detections)
                telemetry.increment("person_detector.frames")
            except Exception as e:
                log.error("person_detector.error", error=str(e))

    def _run_detection(self, image: np.ndarray) -> List[Detection]:
        if self._use_onnx:
            return self._onnx.detect(image, self._min_conf)
        if self._use_yolo:
            return self._yolo.detect(image, self._min_conf)
        return self._hog.detect(image, self._min_conf)

    async def _update_tracks(self, detections: List[Detection]) -> None:
        """Associate detections to existing tracks via nearest centroid."""
        matched_track_ids: set = set()
        matched_det_ids: set = set()

        track_list = list(self._tracks.values())
        det_centroids = [
            ((d.bbox[0] + d.bbox[2]) / 2, (d.bbox[1] + d.bbox[3]) / 2)
            for d in detections
        ]

        # Greedy nearest-centroid matching
        for track in track_list:
            tc = track.center()
            best_dist = float("inf")
            best_det_i = -1
            for i, dc in enumerate(det_centroids):
                if i in matched_det_ids:
                    continue
                dist = ((tc[0] - dc[0])**2 + (tc[1] - dc[1])**2) ** 0.5
                if dist < best_dist and dist < self.MAX_CENTROID_DIST:
                    best_dist = dist
                    best_det_i = i

            if best_det_i >= 0:
                d = detections[best_det_i]
                was_absent = track.frames_absent > 0
                track.bbox = d.bbox
                track.confidence = d.confidence
                track.last_seen = time.monotonic()
                track.frames_present += 1
                track.frames_absent = 0
                track.distance_estimate = d.distance_estimate
                track.position_h = d.position_h
                matched_track_ids.add(track.track_id)
                matched_det_ids.add(best_det_i)

                if was_absent:
                    await self._emit_detected(track)
            else:
                track.frames_absent += 1

        # New tracks for unmatched detections
        for i, d in enumerate(detections):
            if i not in matched_det_ids:
                track = TrackedPerson(
                    track_id=f"P{str(uuid.uuid4())[:4].upper()}",
                    bbox=d.bbox,
                    confidence=d.confidence,
                    distance_estimate=d.distance_estimate,
                    position_h=d.position_h,
                )
                self._tracks[track.track_id] = track
                await self._emit_detected(track)

        # Remove lost tracks
        lost = [tid for tid, t in self._tracks.items() if t.is_lost(self.MAX_TRACK_AGE)]
        for tid in lost:
            track = self._tracks.pop(tid)
            await bus.publish(Event(
                type=EventType.PERSON_LOST,
                data={"track_id": tid, "time_in_frame_s": track.time_in_frame_s()},
                source="person_detector",
            ))
            log.info("person_detector.lost", track_id=tid,
                      duration_s=round(track.time_in_frame_s(), 1))

        telemetry.gauge("person_detector.active_tracks", len(self._tracks))

    async def _emit_detected(self, track: TrackedPerson) -> None:
        x1, y1, x2, y2 = track.bbox
        cx = (x1 + x2) / 2
        # Normalize to -1.0 (far left) … +1.0 (far right)
        from perception.vision.camera import camera
        fw = camera.latest_frame.image.shape[1] if camera.latest_frame is not None else 640
        bbox_center_x = (cx / fw) * 2.0 - 1.0

        await bus.publish(Event(
            type=EventType.PERSON_DETECTED,
            data={
                "track_id": track.track_id,
                "confidence": round(track.confidence, 2),
                "distance": track.distance_estimate,
                "position": track.position_h,
                "bbox": track.bbox,
                "bbox_center_x": round(bbox_center_x, 3),
            },
            source="person_detector",
        ))

    @property
    def active_tracks(self) -> Dict[str, TrackedPerson]:
        return dict(self._tracks)

    @property
    def person_count(self) -> int:
        return sum(1 for t in self._tracks.values() if t.frames_absent == 0)

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": "yolo_onnx" if self._use_onnx else (cfg.models.person_detection.get("model", "yolo") if self._use_yolo else "hog"),
            "active_tracks": len(self._tracks),
            "persons_visible": self.person_count,
            "detection_interval_frames": self._detection_interval,
        }


person_detector = PersonDetector()
