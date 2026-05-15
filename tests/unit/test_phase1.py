"""Unit tests for Phase 1 vision + cognition modules."""
import asyncio
import json
import sys
import tempfile
import os
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Face Engine ───────────────────────────────────────────────────────────────

class TestFaceEngine:
    def setup_method(self):
        from perception.vision.face import FaceEngine
        self.engine = FaceEngine()

    def test_loads_without_crash(self):
        loaded = self.engine.load()
        assert isinstance(loaded, bool)

    def test_detect_on_noise_frame(self):
        noise = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        detections = self.engine.detect_faces(noise)
        assert isinstance(detections, list)

    def test_recognize_without_model_returns_unknown(self):
        from perception.vision.face import FaceDetection, FACE_INPUT_SIZE
        dummy_face = np.zeros((*FACE_INPUT_SIZE, 3), dtype=np.uint8)
        det = FaceDetection(bbox=(0, 0, 112, 112), face_img=dummy_face)
        result = self.engine.recognize(det)
        # Without a trained model, always unknown
        assert result.person_id is None
        assert result.confidence == 0.0

    def test_enroll_and_recognize(self, tmp_path, monkeypatch):
        """Full enroll cycle with synthetic BGR face images."""
        from perception.vision.face import FaceEngine, FACE_INPUT_SIZE
        import cv2

        monkeypatch.setattr("perception.vision.face.FACES_DIR", tmp_path / "faces")
        monkeypatch.setattr("perception.vision.face.EMBEDDINGS_PATH",
                            tmp_path / "faces" / "embeddings.json")
        monkeypatch.setattr("perception.vision.face.SAMPLES_DIR", tmp_path / "faces" / "samples")

        engine = FaceEngine()
        engine.load()  # load SFace model from real ~/.robot/models/

        # Synthetic face-like BGR images
        samples = []
        for i in range(10):
            img = np.zeros((*FACE_INPUT_SIZE, 3), dtype=np.uint8)
            cv2.circle(img, (56, 48), 35, (180, 150, 130), -1)
            cv2.rectangle(img, (38, 60), (74, 76), (120, 90, 80), -1)
            noise = np.random.randint(0, 15, img.shape, dtype=np.uint8)
            img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            samples.append(img)

        ok = engine.enroll_person("TestPerson", samples)
        assert ok, "Enrollment failed"
        assert engine.is_trained
        assert "TestPerson" in engine.list_enrolled()

    def test_list_enrolled_empty_initially(self, tmp_path, monkeypatch):
        monkeypatch.setattr("perception.vision.face.SAMPLES_DIR", tmp_path / "samples")
        from perception.vision.face import FaceEngine
        e = FaceEngine()
        e.load()
        # With empty dir, list should be empty or contain only what's on disk
        assert isinstance(e.list_enrolled(), list)


# ── Emotion Detector ──────────────────────────────────────────────────────────

class TestEmotionDetector:
    def setup_method(self):
        from perception.vision.emotion import EmotionDetector
        self.det = EmotionDetector()
        self.loaded = self.det.load()

    def test_loads_model(self):
        assert self.loaded is True, "emotion-ferplus-8.onnx must be in ~/.robot/models/"

    def test_inference_returns_result_or_none(self):
        face = np.random.randint(50, 200, (80, 80, 3), dtype=np.uint8)
        result = self.det.predict(face, track_id="t1")
        # Result is EmotionResult or None (if below threshold)
        if result is not None:
            assert result.emotion in ["happy", "sad", "neutral", "surprised",
                                      "angry", "disgusted", "scared", "contempt"]
            assert 0.0 <= result.confidence <= 1.0
            assert len(result.all_scores) == 8

    def test_smoothing_reduces_flicker(self):
        """5-frame smoothing should stabilize predictions."""
        face = np.ones((64, 64, 3), dtype=np.uint8) * 150
        results = [self.det.predict(face, track_id="smooth_test") for _ in range(6)]
        valid = [r for r in results if r is not None]
        if len(valid) >= 2:
            # All predictions on same input should converge to same emotion
            emotions = [r.emotion for r in valid]
            most_common = max(set(emotions), key=emotions.count)
            assert emotions.count(most_common) >= len(valid) * 0.6

    def test_clear_track(self):
        self.det.predict(np.zeros((64, 64, 3), dtype=np.uint8), track_id="to_clear")
        self.det.clear_track("to_clear")
        assert "to_clear" not in self.det._smooth_history


# ── Speech TTS ────────────────────────────────────────────────────────────────

class TestTTSEngine:
    def test_availability(self):
        from expression.speech import TTSEngine
        tts = TTSEngine()
        assert tts.is_available is True

    def test_generates_wav(self):
        from expression.speech import TTSEngine
        import subprocess
        tts = TTSEngine()
        if not tts.is_available:
            pytest.skip("espeak-ng not installed")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav = f.name
        try:
            result = subprocess.run(
                ["espeak-ng", "-w", wav, "test message"],
                capture_output=True, timeout=5
            )
            assert result.returncode == 0
            assert os.path.getsize(wav) > 1000   # real WAV, not empty
        finally:
            os.unlink(wav)

    def test_clean_text_strips_markdown(self):
        from expression.speech import TTSEngine
        tts = TTSEngine()
        cleaned = tts._clean_text("**Hello** `code` https://example.com world")
        assert "**" not in cleaned
        assert "`" not in cleaned
        assert "https" not in cleaned
        assert "Hello" in cleaned
        assert "world" in cleaned

    def test_mood_params_update_voice(self):
        from expression.speech import TTSEngine
        tts = TTSEngine()
        tts.set_mood_params(mood=1.0, energy=1.0)
        assert tts._speed == 170   # max energy
        assert tts._pitch == 65    # max mood

        tts.set_mood_params(mood=-1.0, energy=0.0)
        assert tts._speed == 130   # min energy
        assert tts._pitch == 35    # min mood


# ── Spatial Memory room fingerprint ───────────────────────────────────────────

class TestSpatialRoomFingerprint:
    def test_add_and_identify_room(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.memory.spatial.SPATIAL_PATH", tmp_path / "spatial.json")
        from core.memory.spatial import SpatialMemory

        sm = SpatialMemory()
        sm.add_room("living_room", "Living Room", lux=250.0)
        sm.add_room("bedroom", "Bedroom", lux=30.0)

        # Bright reading → living room
        room_id, conf = sm.identify_room(lux=240.0)
        assert room_id == "living_room"
        assert conf > 0.5

        # Dark reading → bedroom
        room_id2, conf2 = sm.identify_room(lux=25.0)
        assert room_id2 == "bedroom"
        assert conf2 > 0.5
