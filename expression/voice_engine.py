"""
Voice synthesis engines for Cosmo.

Piper is the default, local, fallback-safe engine. Remote Voicebox is an
experimental HTTP client for cloned voice synthesis; it is never required for
speech to work.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

PIPER_MODEL_PATH = Path.home() / ".robot/models/piper/en_US-lessac-medium.onnx"
PIPER_BIN = Path("/usr/local/bin/piper")
PIPER_RATE = 22050
VOICES_DIR = Path.home() / ".robot/memory/voices"


@dataclass(frozen=True)
class VoiceProfile:
    """A locally consented voice profile."""

    name: str
    reference_wav: Path
    consent_json: Path


@dataclass(frozen=True)
class SynthesisResult:
    """Audio returned by a voice engine."""

    audio: bytes
    sample_rate: int
    encoding: str
    engine: str


class VoiceEngineError(RuntimeError):
    """Raised when a voice engine cannot synthesize or transcribe."""


class VoiceEngine:
    """Synchronous voice-engine contract used from TTSEngine's worker thread."""

    name = "voice"

    def is_available(self) -> bool:
        return True

    def synthesize(self, text: str, profile: Optional[VoiceProfile] = None) -> SynthesisResult:
        raise NotImplementedError


def _voice_slug(name: str) -> str:
    return "".join(ch for ch in name.strip().lower().replace(" ", "_")
                   if ch.isalnum() or ch in ("_", "-"))


def voice_profile_path(name: str) -> Path:
    return VOICES_DIR / _voice_slug(name)


def load_voice_profile(name: str) -> Optional[VoiceProfile]:
    """Return a profile only when both reference audio and consent metadata exist."""
    root = voice_profile_path(name)
    ref = root / "reference.wav"
    consent = root / "consent.json"
    if not ref.exists() or not consent.exists():
        return None
    try:
        data = json.loads(consent.read_text())
    except Exception:
        log.warning("voice.profile_consent_unreadable", name=name, path=str(consent))
        return None
    if not data.get("consent_granted"):
        log.warning("voice.profile_consent_missing", name=name, path=str(consent))
        return None
    return VoiceProfile(name=data.get("name") or name.title(),
                        reference_wav=ref,
                        consent_json=consent)


class PiperVoiceEngine(VoiceEngine):
    """Local Piper synthesis. Produces raw mono signed-16-bit PCM."""

    name = "piper"

    def __init__(
        self,
        *,
        bin_path: Path = PIPER_BIN,
        model_path: Path = PIPER_MODEL_PATH,
        sample_rate: int = PIPER_RATE,
        timeout_s: float = 10.0,
    ) -> None:
        self.bin_path = bin_path
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return self.bin_path.exists() and self.model_path.exists()

    def synthesize(self, text: str, profile: Optional[VoiceProfile] = None) -> SynthesisResult:
        try:
            piper = subprocess.Popen(
                [str(self.bin_path), "--model", str(self.model_path), "--output_raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            try:
                raw_audio, _ = piper.communicate(text.encode("utf-8"), timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                piper.kill()
                piper.communicate()
                raise VoiceEngineError("piper timed out") from exc
        except VoiceEngineError:
            raise
        except Exception as exc:
            raise VoiceEngineError(str(exc)) from exc

        if not raw_audio:
            raise VoiceEngineError("piper returned no audio")
        return SynthesisResult(
            audio=raw_audio,
            sample_rate=self.sample_rate,
            encoding="s16le",
            engine=self.name,
        )


class RemoteVoiceboxEngine(VoiceEngine):
    """
    HTTP client for the future voicebox service.

    Expected V1 contract:
      POST /generate   JSON {text, voice, reference_wav?}
        -> audio/wav bytes, octet-stream bytes, or JSON {audio_base64, sample_rate, encoding}
      POST /transcribe JSON {audio_base64, sample_rate, encoding}
        -> JSON {text}
    """

    name = "remote_voicebox"

    def __init__(self, base_url: str, *, timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> Optional["RemoteVoiceboxEngine"]:
        url = os.getenv("VOICEBOX_URL", "").strip()
        if not url:
            return None
        timeout = float(os.getenv("VOICEBOX_TIMEOUT_S", "15"))
        return cls(url, timeout_s=timeout)

    def synthesize(self, text: str, profile: Optional[VoiceProfile] = None) -> SynthesisResult:
        if profile is None:
            raise VoiceEngineError("remote voicebox requires a consented voice profile")

        payload = {
            "text": text,
            "voice": profile.name,
            "reference_wav": str(profile.reference_wav),
        }
        body, content_type = self._post_json("/generate", payload)
        if content_type.startswith("application/json"):
            try:
                data = json.loads(body.decode("utf-8"))
                audio = base64.b64decode(data["audio_base64"])
                rate = int(data.get("sample_rate") or PIPER_RATE)
                encoding = str(data.get("encoding") or "wav")
            except Exception as exc:
                raise VoiceEngineError("invalid voicebox generate JSON") from exc
            if not audio:
                raise VoiceEngineError("voicebox returned empty audio")
            return SynthesisResult(audio=audio, sample_rate=rate,
                                   encoding=encoding, engine=self.name)

        if not body:
            raise VoiceEngineError("voicebox returned empty audio")
        encoding = "wav" if "wav" in content_type else "bytes"
        return SynthesisResult(audio=body, sample_rate=PIPER_RATE,
                               encoding=encoding, engine=self.name)

    def transcribe(self, audio: bytes, *, sample_rate: int, encoding: str = "wav") -> str:
        payload = {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "sample_rate": sample_rate,
            "encoding": encoding,
        }
        body, _ = self._post_json("/transcribe", payload)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise VoiceEngineError("invalid voicebox transcribe JSON") from exc
        return str(data.get("text") or "").strip()

    def _post_json(self, path: str, payload: dict) -> tuple[bytes, str]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read()
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as exc:
            raise VoiceEngineError(f"voicebox HTTP {exc.code}") from exc
        except Exception as exc:
            raise VoiceEngineError(str(exc)) from exc
        log.info("voicebox.http",
                 path=path, elapsed_ms=int((time.monotonic() - t0) * 1000),
                 bytes=len(body))
        return body, content_type.split(";")[0].strip().lower()
