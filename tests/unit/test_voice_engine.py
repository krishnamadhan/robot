import base64
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import urllib.error


class _Headers:
    def __init__(self, content_type="application/json"):
        self._content_type = content_type

    def get(self, key, default=None):
        if key.lower() == "content-type":
            return self._content_type
        return default


class _Response:
    def __init__(self, payload, content_type="application/json"):
        if isinstance(payload, bytes):
            self._body = payload
        else:
            self._body = json.dumps(payload).encode("utf-8")
        self.headers = _Headers(content_type)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class _VoiceboxMock:
    generate_status = 200
    generate_calls = 0
    transcribe_calls = 0
    generate_payload = b"RIFFfake-wav"

    @classmethod
    def reset(cls):
        cls.generate_status = 200
        cls.generate_calls = 0
        cls.transcribe_calls = 0

    @classmethod
    def urlopen(cls, req, timeout):
        payload = json.loads(req.data.decode("utf-8"))

        if req.full_url.endswith("/transcribe"):
            cls.transcribe_calls += 1
            assert payload["audio_base64"]
            return _Response({"text": "I consent to Cosmo cloning my voice for local lab use"})

        if req.full_url.endswith("/generate"):
            cls.generate_calls += 1
            assert payload["text"]
            assert payload["voice"] == "Madhan"
            if cls.generate_status >= 400:
                raise urllib.error.HTTPError(
                    req.full_url, cls.generate_status, "mock failure", hdrs=None, fp=None)
            return _Response({
                "audio_base64": base64.b64encode(cls.generate_payload).decode("ascii"),
                "sample_rate": 22050,
                "encoding": "wav",
            })
        raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=None)


class _ReplicateMock:
    create_calls = 0
    download_calls = 0
    payload = b"RIFFreplicate-wav"

    @classmethod
    def reset(cls):
        cls.create_calls = 0
        cls.download_calls = 0

    @classmethod
    def urlopen(cls, req, timeout):
        assert timeout == 3
        assert req.headers["Authorization"] == "Bearer test-token"

        if req.full_url == "https://api.replicate.com/v1/models/lucataco/xtts-v2/predictions":
            cls.create_calls += 1
            payload = json.loads(req.data.decode("utf-8"))
            assert payload["input"]["text"] == "hello"
            assert payload["input"]["language"] == "en"
            assert payload["input"]["speaker"].startswith("data:audio/wav;base64,")
            return _Response({
                "status": "succeeded",
                "output": "https://replicate.delivery/mock-output.wav",
            })

        if req.full_url == "https://replicate.delivery/mock-output.wav":
            cls.download_calls += 1
            return _Response(cls.payload, content_type="audio/wav")

        raise urllib.error.HTTPError(req.full_url, 404, "not found", hdrs=None, fp=None)


def _profile(tmp_path: Path):
    from expression.voice_engine import VoiceProfile

    ref = tmp_path / "reference.wav"
    consent = tmp_path / "consent.json"
    ref.write_bytes(b"wav")
    consent.write_text(json.dumps({"name": "Madhan", "consent_granted": True}))
    return VoiceProfile(name="Madhan", reference_wav=ref, consent_json=consent)


def test_remote_voicebox_generate_and_transcribe(tmp_path):
    from expression.voice_engine import RemoteVoiceboxEngine

    _VoiceboxMock.reset()
    with patch("expression.voice_engine.urllib.request.urlopen", _VoiceboxMock.urlopen):
        url = "http://voicebox.local"
        engine = RemoteVoiceboxEngine(url, timeout_s=2)
        text = engine.transcribe(b"abc", sample_rate=22050)
        result = engine.synthesize("hello", _profile(tmp_path))

    assert "consent" in text
    assert result.engine == "remote_voicebox"
    assert result.audio == _VoiceboxMock.generate_payload
    assert _VoiceboxMock.transcribe_calls == 1
    assert _VoiceboxMock.generate_calls == 1


def test_replicate_generate_downloads_output(tmp_path):
    from expression.voice_engine import ReplicateVoiceEngine

    _ReplicateMock.reset()
    with patch("expression.voice_engine.urllib.request.urlopen", _ReplicateMock.urlopen):
        engine = ReplicateVoiceEngine("test-token", timeout_s=3)
        result = engine.synthesize("hello", _profile(tmp_path))

    assert result.engine == "replicate"
    assert result.encoding == "wav"
    assert result.audio == _ReplicateMock.payload
    assert _ReplicateMock.create_calls == 1
    assert _ReplicateMock.download_calls == 1


def test_replicate_from_env_returns_none_without_token(monkeypatch):
    from expression.voice_engine import ReplicateVoiceEngine

    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    assert ReplicateVoiceEngine.from_env() is None


def test_load_voice_profile_requires_consent_json(tmp_path, monkeypatch):
    import expression.voice_engine as ve

    monkeypatch.setattr(ve, "VOICES_DIR", tmp_path)
    root = ve.voice_profile_path("Madhan")
    root.mkdir(parents=True)
    (root / "reference.wav").write_bytes(b"wav")

    assert ve.load_voice_profile("Madhan") is None

    (root / "consent.json").write_text(json.dumps({
        "name": "Madhan",
        "consent_granted": True,
    }))
    profile = ve.load_voice_profile("Madhan")
    assert profile is not None
    assert profile.name == "Madhan"


def test_tts_remote_failure_falls_back_to_piper_end_to_end(tmp_path):
    from expression.speech import TTSEngine
    from expression.voice_engine import PiperVoiceEngine, RemoteVoiceboxEngine

    _VoiceboxMock.reset()
    _VoiceboxMock.generate_status = 500
    with patch("expression.voice_engine.urllib.request.urlopen", _VoiceboxMock.urlopen):
        url = "http://voicebox.local"

        eng = TTSEngine.__new__(TTSEngine)
        eng._lock = threading.Lock()
        eng._speaking = False
        eng._muted_until = 0.0
        eng._proc = None
        eng._available = True
        eng._voice_profile = _profile(tmp_path)
        eng._remote = RemoteVoiceboxEngine(url, timeout_s=2)
        eng._piper = PiperVoiceEngine()

        piper_proc = MagicMock()
        piper_proc.communicate.return_value = (b"\x01\x00" * 2205, b"")

        paplay_proc = MagicMock()
        paplay_proc.stdin = MagicMock()
        paplay_proc.wait.return_value = 0
        paplay_proc.poll.return_value = 0

        with patch("expression.voice_engine.subprocess.Popen",
                   side_effect=[piper_proc, paplay_proc]):
            eng._speak_thread("fallback please")

    assert _VoiceboxMock.generate_calls == 1
    piper_proc.communicate.assert_called_once()
    paplay_proc.stdin.write.assert_called_once_with(b"\x01\x00" * 2205)
    assert eng.is_speaking is False
