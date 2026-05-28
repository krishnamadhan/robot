"""
Wake word detection.

Priority order:
  1. Porcupine (if PICOVOICE_KEY env var set) — best accuracy, <50ms, needs free account
  2. OpenWakeWord (always available, no account) — good accuracy, ~80ms, "hey jarvis"
  3. STT fallback — always works, ~1.5s latency

To use Porcupine instead (optional upgrade):
  1. Get free key at console.picovoice.ai (requires company email)
  2. export PICOVOICE_KEY="your-key"
  3. pm2 restart cosmo
"""

import asyncio
import os
import struct
import time
from pathlib import Path
from typing import Optional

import numpy as np

from core.event_bus import bus, Event, EventType, EventPriority
from utils.logger import get_logger

log = get_logger(__name__)

WAKE_WORDS          = ["hey cosmo", "cosmo", "hey robot", "okay cosmo"]
ENERGY_THRESHOLD    = 500
COOLDOWN_S          = 1.5
CUSTOM_PPM_PATH     = Path.home() / ".robot/models/porcupine/hey-cosmo.ppn"
FALLBACK_KEYWORD    = "computer"
OWW_THRESHOLD       = 0.65  # confidence threshold for OWW detection

# Active wake word label for OWW bundled models.
# Options: "hey_jarvis" (bundled, works now), "hey_mycroft", "alexa"
# To use custom "hey_cosmo": train via openwakeword, save to
#   ~/.robot/models/hey_cosmo.tflite, then set OWW_CUSTOM_MODEL_PATH.
# Env override: COSMO_WAKE_LABEL=hey_cosmo  (useful for testing trained models)
OWW_WAKE_LABEL      = os.environ.get("COSMO_WAKE_LABEL", "hey_jarvis")
OWW_CUSTOM_MODEL_PATH = Path.home() / ".robot/models/hey_cosmo.tflite"


# ── OpenWakeWord detector (primary, no account required) ─────────────────────

class OpenWakeWordDetector:
    """
    Streaming OpenWakeWord detector.
    Processes mic chunks directly — detects in ~80ms, no signup needed.
    Wake word: "Hey Jarvis"
    """

    def __init__(self) -> None:
        self._model = None
        self._available = False
        self._label = ""

    def load(self) -> bool:
        try:
            from openwakeword.model import Model as OWWModel
            if OWW_CUSTOM_MODEL_PATH.exists():
                # Custom trained model (e.g. hey_cosmo.tflite)
                self._model = OWWModel(wakeword_models=[str(OWW_CUSTOM_MODEL_PATH)])
                self._label = OWW_WAKE_LABEL
                log.info("oww.loaded_custom", keyword=self._label,
                         model_path=str(OWW_CUSTOM_MODEL_PATH))
            else:
                # Bundled models: alexa, hey_mycroft, hey_jarvis, timer, weather
                self._model = OWWModel()
                self._label = OWW_WAKE_LABEL
                log.info("oww.loaded_bundled", keyword=self._label,
                         models=list(self._model.models.keys()))
            self._available = True
            return True
        except Exception as e:
            log.warning("oww.load_failed", error=str(e)[:80])
            return False

    def process_chunk(self, chunk: bytes) -> bool:
        """Feed a raw mic chunk (int16 mono 16kHz). Returns True if wake word detected."""
        if not self._available or not self._model:
            return False
        try:
            audio = np.frombuffer(chunk, dtype=np.int16)
            scores = self._model.predict(audio)
            score = float(scores.get(self._label, 0.0))
            if score > 0.15:
                log.debug("oww.score", score=round(score, 3),
                          triggered=score >= OWW_THRESHOLD)
            return score >= OWW_THRESHOLD
        except Exception as e:
            log.warning("oww.predict_error", error=str(e)[:80])
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def keyword_label(self) -> str:
        return OWW_WAKE_LABEL


# ── Porcupine detector (optional upgrade, needs free Picovoice account) ───────

class PorcupineDetector:
    """
    Streaming Porcupine wake word detector.
    Faster and more accurate than OWW, but requires a Picovoice access key.
    Set PICOVOICE_KEY env var to activate.
    """

    def __init__(self) -> None:
        self._porcupine = None
        self._available = False
        self._keyword_label = ""
        self._frame_length = 512
        self._partial: bytes = b""

    def load(self) -> bool:
        key = os.environ.get("PICOVOICE_KEY", "").strip()
        if not key:
            log.info("porcupine.skipped", reason="PICOVOICE_KEY not set")
            return False
        try:
            import pvporcupine
            # Use custom model if present, else fall back to built-in keyword
            if CUSTOM_PPM_PATH.exists():
                self._porcupine = pvporcupine.create(
                    access_key=key,
                    keyword_paths=[str(CUSTOM_PPM_PATH)],
                )
                self._keyword_label = "hey cosmo"
            else:
                self._porcupine = pvporcupine.create(
                    access_key=key,
                    keywords=[FALLBACK_KEYWORD],
                )
                self._keyword_label = FALLBACK_KEYWORD
            self._frame_length = self._porcupine.frame_length
            self._available = True
            log.info("porcupine.loaded", keyword=self._keyword_label,
                     frame_length=self._frame_length)
            return True
        except Exception as e:
            log.warning("porcupine.load_failed", error=str(e)[:80])
            return False

    def process_chunk(self, chunk: bytes) -> bool:
        if not self._available or not self._porcupine:
            return False
        self._partial += chunk
        frame_bytes = self._frame_length * 2
        while len(self._partial) >= frame_bytes:
            frame_raw = self._partial[:frame_bytes]
            self._partial = self._partial[frame_bytes:]
            pcm = list(struct.unpack_from(f"{self._frame_length}h", frame_raw))
            if self._porcupine.process(pcm) >= 0:
                return True
        return False

    def delete(self) -> None:
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def keyword_label(self) -> str:
        return self._keyword_label


# ── STT-based fallback ────────────────────────────────────────────────────────

class WakeWordDetector:
    """STT-based wake word — used when neither Porcupine nor OWW is available."""

    def is_wake_word(self, text: str) -> Optional[str]:
        text_lower = text.lower().strip()
        for ww in WAKE_WORDS:
            if ww in text_lower:
                return ww
        return None

    def check_energy(self, audio_chunk: bytes) -> bool:
        arr = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(arr ** 2))) > ENERGY_THRESHOLD


# ── Shared event publisher ────────────────────────────────────────────────────

_last_wake: list[float] = [0.0]


async def publish_wake_word(word: str, confidence: float = 1.0) -> bool:
    """Publish WAKE_WORD event with cooldown guard. Returns True if published."""
    now = time.monotonic()
    if now - _last_wake[0] < COOLDOWN_S:
        return False
    _last_wake[0] = now
    log.info("wake_word.detected", word=word, confidence=confidence)
    await bus.publish(Event(
        type=EventType.WAKE_WORD,
        data={"word": word, "confidence": confidence},
        priority=EventPriority.HIGH,
    ))
    return True


# ── Singletons — loaded in priority order ────────────────────────────────────

porcupine_detector   = PorcupineDetector()
oww_detector         = OpenWakeWordDetector()
wake_word_detector   = WakeWordDetector()

porcupine_detector.load()   # no-op if no PICOVOICE_KEY
if not porcupine_detector.is_available:
    oww_detector.load()     # always succeeds if openwakeword is installed
