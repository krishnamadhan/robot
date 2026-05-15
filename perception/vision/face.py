"""
Face detection and recognition pipeline.

Detection:    OpenCV Haarcascade (fast, offline, no download)
Recognition:  SFace neural network via onnxruntime.
              Better than LBPH: fixed pre-trained embeddings, no retraining needed.
              cv2.face (LBPH) is empty in this opencv-contrib build — SFace is
              the available alternative and produces higher quality 128-dim embeddings.

Storage:
  ~/.robot/memory/faces/
  ├── embeddings.json    # {name: [[128-d array], ...], ...}
  └── samples/
      ├── Madhan/        # 10 face images (kept for re-enrollment)
      └── Indhu/

Recognition: cosine similarity. Threshold ~0.36 (SFace paper: 0.363 for 1e-4 FAR).
A match scores > threshold; best match wins if multiple persons enrolled.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

FACES_DIR = Path.home() / ".robot" / "memory" / "faces"
EMBEDDINGS_PATH = FACES_DIR / "embeddings.json"
SAMPLES_DIR = FACES_DIR / "samples"
SFACE_MODEL_PATH = Path.home() / ".robot" / "models" / "face_recognition_sface.onnx"

# Cosine similarity threshold — above this = recognized
COSINE_THRESHOLD = 0.45     # raised from 0.363 — reduces photo/screen false positives
FACE_INPUT_SIZE = (112, 112)  # SFace input


@dataclass
class FaceDetection:
    bbox: Tuple[int, int, int, int]   # x, y, w, h (OpenCV format)
    face_img: np.ndarray              # BGR 112×112


@dataclass
class RecognitionResult:
    bbox: Tuple[int, int, int, int]
    person_id: Optional[str]
    name: Optional[str]
    confidence: float                 # 0.0–1.0 (cosine similarity)
    face_img: np.ndarray = field(repr=False, default=None)


class FaceEngine:
    """
    Face detection (haarcascade) + recognition (SFace embeddings).

    Enrollment stores embeddings from face crops.
    Recognition: mean embedding per person, cosine similarity.
    """

    def __init__(self) -> None:
        cascade_dir = cv2.data.haarcascades
        self._cascade_alt = cv2.CascadeClassifier(
            cascade_dir + "haarcascade_frontalface_alt2.xml"
        )
        self._cascade_default = cv2.CascadeClassifier(
            cascade_dir + "haarcascade_frontalface_default.xml"
        )
        # SFace via onnxruntime
        self._ort_session = None
        self._ort_input_name: Optional[str] = None
        # In-memory embeddings per person: {name: np.ndarray (N, 128)}
        self._embeddings: Dict[str, np.ndarray] = {}
        self._trained = False

    def load(self) -> bool:
        """Load SFace model + saved embeddings. Returns True if model ready."""
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

        # Load SFace model
        if not SFACE_MODEL_PATH.exists():
            log.warning("face_engine.sface_model_missing", path=str(SFACE_MODEL_PATH))
            return False

        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3  # suppress ONNX graph warnings
            self._ort_session = ort.InferenceSession(
                str(SFACE_MODEL_PATH),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._ort_input_name = self._ort_session.get_inputs()[0].name
            log.info("face_engine.sface_loaded")
        except Exception as e:
            log.warning("face_engine.sface_load_failed", error=str(e))
            return False

        # Load stored embeddings
        if EMBEDDINGS_PATH.exists():
            try:
                raw = json.loads(EMBEDDINGS_PATH.read_text())
                self._embeddings = {
                    name: np.array(vecs, dtype=np.float32)
                    for name, vecs in raw.items()
                }
                self._trained = bool(self._embeddings)
                log.info("face_engine.embeddings_loaded",
                          persons=list(self._embeddings.keys()),
                          counts={n: len(v) for n, v in self._embeddings.items()})
            except Exception as e:
                log.warning("face_engine.embeddings_load_failed", error=str(e))

        return True

    # ── Detection ────────────────────────────────────────────────────────────

    def detect_faces(self, frame: np.ndarray) -> List[FaceDetection]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        faces = self._cascade_alt.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        if len(faces) == 0:
            faces = self._cascade_default.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
            )

        results = []
        h_frame, w_frame = frame.shape[:2]
        for (x, y, w, h) in faces:
            # Crop with 10% padding
            pad = int(0.10 * min(w, h))
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(w_frame, x + w + pad)
            y2 = min(h_frame, y + h + pad)
            crop = frame[y1:y2, x1:x2]
            face_resized = cv2.resize(crop, FACE_INPUT_SIZE)
            results.append(FaceDetection(bbox=(x, y, w, h), face_img=face_resized))
        return results

    # ── Embedding ────────────────────────────────────────────────────────────

    def _embed(self, face_bgr_112: np.ndarray) -> Optional[np.ndarray]:
        """Run SFace model, return normalized 128-d embedding."""
        if self._ort_session is None:
            return None
        try:
            # SFace input: (1, 3, 112, 112) float32, BGR, mean-subtracted
            img = face_bgr_112.astype(np.float32)
            img -= np.array([104.0, 117.0, 123.0], dtype=np.float32)
            inp = img.transpose(2, 0, 1)[np.newaxis]   # (1, 3, 112, 112)
            output = self._ort_session.run(None, {self._ort_input_name: inp})[0][0]
            norm = np.linalg.norm(output)
            if norm == 0:
                return None
            return output / norm
        except Exception as e:
            log.error("face_engine.embed_error", error=str(e))
            return None

    # ── Recognition ──────────────────────────────────────────────────────────

    def recognize(self, detection: FaceDetection) -> RecognitionResult:
        if not self._trained or self._ort_session is None:
            return RecognitionResult(
                bbox=detection.bbox, person_id=None, name=None,
                confidence=0.0, face_img=detection.face_img,
            )

        query_emb = self._embed(detection.face_img)
        if query_emb is None:
            return RecognitionResult(
                bbox=detection.bbox, person_id=None, name=None,
                confidence=0.0, face_img=detection.face_img,
            )

        best_name: Optional[str] = None
        best_score = 0.0

        for name, stored_embs in self._embeddings.items():
            # Mean embedding for this person
            mean_emb = stored_embs.mean(axis=0)
            mean_emb /= (np.linalg.norm(mean_emb) + 1e-8)
            score = float(np.dot(query_emb, mean_emb))   # cosine similarity [-1, 1]
            if score > best_score:
                best_score = score
                best_name = name

        if best_score >= COSINE_THRESHOLD and best_name:
            person_id = f"person_{best_name.lower().replace(' ', '_')}"
            # Scale confidence: threshold=0 → 0.5, 1.0 → 1.0
            confidence = (best_score - COSINE_THRESHOLD) / (1.0 - COSINE_THRESHOLD)
            confidence = max(0.0, min(1.0, confidence))
        else:
            person_id = None
            best_name = None
            confidence = 0.0

        return RecognitionResult(
            bbox=detection.bbox,
            person_id=person_id,
            name=best_name,
            confidence=round(confidence, 3),
            face_img=detection.face_img,
        )

    def process_frame(self, frame: np.ndarray) -> List[RecognitionResult]:
        detections = self.detect_faces(frame)
        return [self.recognize(d) for d in detections]

    # ── Enrollment ───────────────────────────────────────────────────────────

    def enroll_person(self, name: str, face_images: List[np.ndarray]) -> bool:
        """
        Enroll a person from a list of BGR face images (any size).
        Computes SFace embeddings and stores them.
        face_images: BGR crops, any size — will be resized internally.
        """
        if self._ort_session is None:
            log.error("face_engine.enroll_no_model")
            return False

        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        person_dir = SAMPLES_DIR / name
        person_dir.mkdir(parents=True, exist_ok=True)

        new_embeddings = []
        existing_count = len(list(person_dir.glob("*.png")))

        for i, img in enumerate(face_images):
            # Save sample image
            sample_path = person_dir / f"sample_{existing_count + i:04d}.png"
            resized_for_save = cv2.resize(img, FACE_INPUT_SIZE)
            cv2.imwrite(str(sample_path), resized_for_save)

            # Compute embedding
            face_112 = cv2.resize(img, FACE_INPUT_SIZE)
            emb = self._embed(face_112)
            if emb is not None:
                new_embeddings.append(emb)

        if not new_embeddings:
            log.error("face_engine.enroll_no_embeddings", name=name)
            return False

        new_emb_array = np.stack(new_embeddings)

        # Merge with existing embeddings for this person
        if name in self._embeddings:
            self._embeddings[name] = np.vstack([self._embeddings[name], new_emb_array])
        else:
            self._embeddings[name] = new_emb_array

        self._trained = True
        self._save_embeddings()
        log.info("face_engine.enrolled",
                  name=name,
                  new_samples=len(new_embeddings),
                  total_samples=len(self._embeddings[name]))
        return True

    def _save_embeddings(self) -> None:
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        raw = {name: embs.tolist() for name, embs in self._embeddings.items()}
        EMBEDDINGS_PATH.write_text(json.dumps(raw))

    def forget_person(self, name: str) -> bool:
        import shutil
        person_dir = SAMPLES_DIR / name
        if person_dir.exists():
            shutil.rmtree(person_dir)
        if name in self._embeddings:
            del self._embeddings[name]
            self._trained = bool(self._embeddings)
            self._save_embeddings()
            log.info("face_engine.forgotten", name=name)
            return True
        return False

    def list_enrolled(self) -> List[str]:
        return list(self._embeddings.keys())

    @property
    def is_trained(self) -> bool:
        return self._trained


# Module-level singleton
face_engine = FaceEngine()
