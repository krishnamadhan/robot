"""
TTS stub — speech output removed in Phase A.
Cosmo is now reactive (sounds + expressions), not verbal.
All methods are no-ops so callers in behavior/engine.py don't crash.
"""
import asyncio

from utils.logger import get_logger

log = get_logger(__name__)


class TTSEngine:
    """No-op TTS. sounds.py handles all audio output."""

    async def speak(self, text: str, interrupt: bool = True) -> None:
        log.debug("tts.speak_disabled", preview=text[:40])

    async def speak_instant(self, key: str) -> bool:
        return False

    async def speak_streaming(self, sentence_gen) -> str:
        async for _ in sentence_gen:
            pass
        return ""

    async def play_sound(self, sound_type: str) -> None:
        pass

    def set_mood_params(self, mood: float, energy: float) -> None:
        pass

    @property
    def is_available(self) -> bool:
        return False

    @property
    def is_speaking(self) -> bool:
        return False


tts = TTSEngine()
