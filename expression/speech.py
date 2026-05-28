"""
TTS engine — Piper (default) + XTTS v2 voice cloning (per-person profiles).

Default: Piper offline TTS (fast, 1.5s, always available)
Cloned:  XTTS v2 activated when a known person's voice profile exists.
         Lazy-loaded on first use — model is ~1.8 GB, kept in RAM while active.

Binary: /usr/local/bin/piper
Model:  ~/.robot/models/piper/en_US-lessac-medium.onnx  (22050 Hz mono s16le)
Voices: ~/.robot/memory/voices/<name>/reference.wav
"""

import asyncio
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

_MODEL_PATH  = Path.home() / ".robot/models/piper/en_US-lessac-medium.onnx"
_PIPER_BIN   = Path("/usr/local/bin/piper")
_VOICES_DIR  = Path.home() / ".robot/memory/voices"
_RATE        = 22050

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}


def _piper_available() -> bool:
    return _PIPER_BIN.exists() and _MODEL_PATH.exists()


def _voice_ref_path(name: str) -> Optional[Path]:
    p = _VOICES_DIR / name.lower() / "reference.wav"
    return p if p.exists() else None


class TTSEngine:
    """
    Non-blocking TTS with two backends:
      - Piper (default, fast)
      - XTTS v2 (activated via set_voice_profile, lazy-loaded)

    speak() always returns immediately. Audio plays in a background thread.
    interrupt=True (default) kills any currently playing speech before starting.
    """

    def __init__(self) -> None:
        self._lock          = threading.Lock()
        self._speaking      = False
        self._muted_until   = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._available     = _piper_available()

        # XTTS state
        self._xtts_model    = None          # lazy loaded
        self._xtts_ref_wav: Optional[str] = None
        self._xtts_name:    Optional[str] = None
        self._xtts_lock     = threading.Lock()

        if not self._available:
            log.warning("tts.unavailable",
                        piper=str(_PIPER_BIN), model=str(_MODEL_PATH))
        else:
            log.info("tts.ready", model=_MODEL_PATH.name, rate=_RATE)

    # ── Voice profile ──────────────────────────────────────────────────────────

    def set_voice_profile(self, name: Optional[str]) -> None:
        """Switch to a cloned voice profile (or None to revert to Piper)."""
        if name is None:
            with self._xtts_lock:
                self._xtts_ref_wav = None
                self._xtts_name    = None
            log.info("tts.voice_profile_cleared")
            return

        ref = _voice_ref_path(name)
        if ref is None:
            log.info("tts.voice_profile_missing", name=name)
            return

        with self._xtts_lock:
            if self._xtts_name == name:
                return  # already active
            self._xtts_ref_wav = str(ref)
            self._xtts_name    = name

        log.info("tts.voice_profile_set", name=name, ref=str(ref))

        # Warm-load the model in background so first speech isn't delayed
        threading.Thread(target=self._load_xtts, daemon=True).start()

    def _load_xtts(self) -> None:
        with self._xtts_lock:
            if self._xtts_model is not None:
                return
        try:
            from TTS.api import TTS
            log.info("tts.xtts_loading")
            model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
            with self._xtts_lock:
                self._xtts_model = model
            log.info("tts.xtts_ready")
        except Exception as e:
            log.error("tts.xtts_load_failed", error=str(e)[:80])

    # ── Piper backend ──────────────────────────────────────────────────────────

    def _kill_current(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except Exception:
                pass
        self._proc = None

    def _speak_piper(self, text: str) -> None:
        paplay = None
        try:
            piper = subprocess.Popen(
                [str(_PIPER_BIN), "--model", str(_MODEL_PATH), "--output_raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            with self._lock:
                self._proc = piper

            try:
                raw_audio, _ = piper.communicate(text.encode("utf-8"), timeout=10.0)
            except subprocess.TimeoutExpired:
                piper.kill()
                piper.communicate()
                log.error("tts.piper_timeout", text_len=len(text))
                return

            if not raw_audio:
                return

            paplay = subprocess.Popen(
                ["paplay", "--raw",
                 f"--rate={_RATE}", "--channels=1", "--format=s16le"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_PW_ENV,
            )
            paplay.stdin.write(raw_audio)
            paplay.stdin.close()
            paplay.wait()
        except Exception as e:
            log.error("tts.piper_error", error=str(e)[:80])
            if paplay and paplay.poll() is None:
                paplay.kill()

    # ── XTTS backend ───────────────────────────────────────────────────────────

    def _speak_xtts(self, text: str, ref_wav: str) -> bool:
        with self._xtts_lock:
            model = self._xtts_model

        if model is None:
            log.warning("tts.xtts_not_ready_yet", fallback="piper")
            return False

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name

            t0 = time.monotonic()
            model.tts_to_file(
                text=text,
                speaker_wav=ref_wav,
                language="en",
                file_path=tmp,
            )
            elapsed = time.monotonic() - t0
            log.info("tts.xtts_synth", chars=len(text), elapsed_s=round(elapsed, 1))

            subprocess.run(
                ["paplay", tmp],
                env=_PW_ENV,
                check=False,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            log.error("tts.xtts_error", error=str(e)[:80])
            return False
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ── Unified speak thread ───────────────────────────────────────────────────

    def _speak_thread(self, text: str) -> None:
        with self._lock:
            self._speaking = True
        try:
            with self._xtts_lock:
                ref_wav  = self._xtts_ref_wav
                name     = self._xtts_name

            if ref_wav and name:
                success = self._speak_xtts(text, ref_wav)
                if not success:
                    self._speak_piper(text)
            else:
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
                 voice=self._xtts_name or "piper")
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
        pass  # future: adjust piper speed via --length_scale

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
        return self._xtts_name or "piper"


tts = TTSEngine()
