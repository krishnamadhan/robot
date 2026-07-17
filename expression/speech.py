"""
TTS engine — Piper default/fallback + optional remote Voicebox clone client.

Binary: /usr/local/bin/piper
Model:  ~/.robot/models/piper/en_US-lessac-medium.onnx  (22050 Hz mono s16le)
Voices: ~/.robot/memory/voices/<name>/reference.wav + consent.json
Remote: set VOICE_ENGINE=voicebox or VOICE_ENGINE=replicate to enable
experimental cloned synthesis. Piper remains the fallback.
"""

import asyncio
import os
import subprocess
import tempfile
import threading
import time
from typing import Optional

from expression.voice_engine import (
    PIPER_MODEL_PATH,
    PIPER_RATE,
    PiperVoiceEngine,
    ReplicateVoiceEngine,
    RemoteVoiceboxEngine,
    SynthesisResult,
    VoiceEngineError,
    VoiceProfile,
    load_voice_profile,
)
from utils.logger import get_logger

log = get_logger(__name__)

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}


def _remote_from_env():
    engine = os.getenv("VOICE_ENGINE", "").strip().lower()
    if engine == "replicate":
        return ReplicateVoiceEngine.from_env()
    if engine in ("", "voicebox", "remote_voicebox"):
        return RemoteVoiceboxEngine.from_env()
    if engine == "piper":
        return None
    log.warning("tts.unknown_voice_engine", value=engine, fallback="piper")
    return None


class TTSEngine:
    """
    Non-blocking TTS with two synthesis backends:
      - Piper (default, local fallback)
      - Remote Voicebox (optional, requires VOICEBOX_URL + consented profile)

    speak() always returns immediately. Audio plays in a background thread.
    interrupt=True (default) kills any currently playing speech before starting.
    """

    def __init__(self) -> None:
        self._lock          = threading.Lock()
        self._speaking      = False
        self._muted_until   = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._piper         = PiperVoiceEngine()
        self._remote        = _remote_from_env()
        self._available     = self._piper.is_available()
        self._voice_profile: Optional[VoiceProfile] = None

        if not self._available:
            log.warning("tts.unavailable",
                        model=str(PIPER_MODEL_PATH))
        else:
            log.info("tts.ready", model=PIPER_MODEL_PATH.name, rate=PIPER_RATE)

        if self._remote:
            log.info("tts.clone_engine_enabled", engine=self._remote.name)
        else:
            log.info("tts.clone_engine_disabled",
                     hint="set VOICE_ENGINE=replicate or VOICEBOX_URL to enable cloned voice synthesis")

    # ── Voice profile ──────────────────────────────────────────────────────────

    def set_voice_profile(self, name: Optional[str]) -> None:
        """Activate a cloned voice for the named person, or None to revert to Piper."""
        if name is None:
            self._voice_profile = None
            log.info("tts.voice_profile_cleared")
            return

        profile = load_voice_profile(name)
        if profile is None:
            log.info("tts.voice_profile_missing_or_unconsented", name=name)
            return

        if self._voice_profile and self._voice_profile.name == profile.name:
            return  # already active

        self._voice_profile = profile
        log.info("tts.voice_profile_set", name=profile.name,
                 ref=str(profile.reference_wav))

    # ── Piper backend ──────────────────────────────────────────────────────────

    def _kill_current(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except Exception:
                pass
        self._proc = None

    def _play_result(self, result: SynthesisResult) -> None:
        if result.encoding == "s16le":
            self._play_raw(result.audio, result.sample_rate)
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
            f.write(result.audio)
        try:
            self._play_file(tmp)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    def _play_raw(self, raw_audio: bytes, sample_rate: int) -> None:
        paplay = None
        try:
            paplay = subprocess.Popen(
                ["paplay", "--raw",
                 f"--rate={sample_rate}", "--channels=1", "--format=s16le"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_PW_ENV,
            )
            with self._lock:
                self._proc = paplay
            paplay.stdin.write(raw_audio)
            paplay.stdin.close()
            # s16le mono: 2 bytes/sample.
            audio_s = len(raw_audio) / (2 * sample_rate)
            try:
                paplay.wait(timeout=audio_s + 5.0)
            except subprocess.TimeoutExpired:
                paplay.kill()
                paplay.wait()
                log.error("tts.paplay_timeout", audio_s=round(audio_s, 1))
        except Exception as e:
            log.error("tts.play_raw_error", error=str(e)[:80])
            if paplay and paplay.poll() is None:
                paplay.kill()

    def _play_file(self, path: str) -> None:
        paplay = None
        try:
            paplay = subprocess.Popen(
                ["paplay", path],
                stderr=subprocess.DEVNULL,
                env=_PW_ENV,
            )
            with self._lock:
                self._proc = paplay
            try:
                paplay.wait(timeout=60.0)
            except subprocess.TimeoutExpired:
                paplay.kill()
                paplay.wait()
                log.error("tts.paplay_timeout", audio_s=None)
        except Exception as e:
            log.error("tts.play_file_error", error=str(e)[:80])
            if paplay and paplay.poll() is None:
                paplay.kill()

    def _speak_piper(self, text: str) -> None:
        try:
            if not hasattr(self, "_piper"):
                self._piper = PiperVoiceEngine()
            result = self._piper.synthesize(text)
            self._play_result(result)
        except VoiceEngineError as e:
            log.error("tts.piper_error", error=str(e)[:80])
        except Exception as e:
            log.error("tts.piper_error", error=str(e)[:80])

    # ── Unified speak thread ───────────────────────────────────────────────────

    def _speak_thread(self, text: str) -> None:
        with self._lock:
            self._speaking = True
        try:
            profile = self._voice_profile
            if profile and self._remote:
                try:
                    result = self._remote.synthesize(text, profile)
                    self._play_result(result)
                    return
                except VoiceEngineError as e:
                    log.warning("tts.clone_engine_failed",
                                engine=self._remote.name,
                                error=str(e)[:120], fallback="piper")
            self._speak_piper(text)
        finally:
            with self._lock:
                self._speaking = False
                self._proc = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def speak(self, text: str, interrupt: bool = True) -> None:
        if not self._available or not text.strip():
            return
        if time.monotonic() < self._muted_until:
            return
        if interrupt:
            with self._lock:
                self._kill_current()
        log.info("tts.speak", preview=text[:60],
                 voice=self.active_voice)
        from utils.action_log import action_log
        action_log.record("speech", text[:60])
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._speak_thread, text)

    async def speak_instant(self, key: str) -> bool:
        return False

    async def speak_streaming(self, sentence_gen) -> str:
        full = ""
        async for sentence in sentence_gen:
            if sentence.strip():
                full += sentence + " "
                await self.speak(sentence, interrupt=False)
        return full.strip()

    async def play_sound(self, sound_type: str) -> None:
        pass

    def set_mood_params(self, mood: float, energy: float) -> None:
        pass

    def mute(self, seconds: float = 30) -> None:
        self._muted_until = time.monotonic() + seconds
        log.info("tts.muted", seconds=seconds)

    def stop(self) -> None:
        with self._lock:
            self._kill_current()

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def active_voice(self) -> str:
        if self._voice_profile and self._remote:
            return self._voice_profile.name
        return "piper"


tts = TTSEngine()
