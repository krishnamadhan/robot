"""
Audio pipeline stub — STT/wake word removed in Phase A.
Preserved as stub so all imports in cosmo_demo.py remain valid.
"""
from enum import Enum
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


class ListenState(str, Enum):
    PASSIVE   = "passive"
    LISTENING = "listening"
    THINKING  = "thinking"
    SPEAKING  = "speaking"


class ListeningPipeline:
    """No-op stub. Voice pipeline removed — Cosmo is reactive, not verbal."""

    def __init__(self) -> None:
        self._current_person: Optional[str] = None
        self._current_emotion: Optional[str] = None

    async def start(self) -> bool:
        log.info("audio_pipeline.stub", note="STT/wake-word removed in Phase A")
        return False

    async def stop(self) -> None:
        pass

    def update_person(self, person_id: Optional[str],
                      emotion: Optional[str] = None) -> None:
        self._current_person = person_id
        if emotion:
            self._current_emotion = emotion

    @property
    def state(self) -> ListenState:
        return ListenState.PASSIVE

    @property
    def is_running(self) -> bool:
        return False


audio_pipeline = ListeningPipeline()
