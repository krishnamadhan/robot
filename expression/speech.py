"""
TTS via Piper — offline, runs entirely on Pi.

Binary: /usr/local/bin/piper
Model:  ~/.robot/models/piper/en_US-lessac-medium.onnx  (22050 Hz mono s16le)

Priority interruption mirrors sounds.py — higher priority speech kills current.
All playback is non-blocking (background thread).
"""

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

_MODEL_PATH = Path.home() / ".robot/models/piper/en_US-lessac-medium.onnx"
_PIPER_BIN  = Path("/usr/local/bin/piper")
_RATE       = 22050

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}


def _piper_available() -> bool:
    return _PIPER_BIN.exists() and _MODEL_PATH.exists()


class TTSEngine:
    """
    Non-blocking Piper TTS.

    speak() always returns immediately. Audio plays in a background thread.
    interrupt=True (default) kills any currently playing speech before starting.
    """

    def __init__(self) -> None:
        self._lock        = threading.Lock()
        self._speaking    = False
        self._muted_until = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._available   = _piper_available()
        if not self._available:
            log.warning("tts.unavailable",
                        piper=str(_PIPER_BIN), model=str(_MODEL_PATH))
        else:
            log.info("tts.ready", model=_MODEL_PATH.name, rate=_RATE)

    def _kill_current(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except Exception:
                pass
        self._proc = None

    def _speak_thread(self, text: str) -> None:
        with self._lock:
            self._speaking = True
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
                piper.communicate()  # drain pipes
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
            log.error("tts.error", error=str(e)[:80])
            if paplay and paplay.poll() is None:
                paplay.kill()
        finally:
            with self._lock:
                self._speaking = False
                self._proc = None

    async def speak(self, text: str, interrupt: bool = True) -> None:
        if not self._available or not text.strip():
            return
        if time.monotonic() < self._muted_until:
            return
        if interrupt:
            with self._lock:
                self._kill_current()
        log.info("tts.speak", preview=text[:60])
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


tts = TTSEngine()
