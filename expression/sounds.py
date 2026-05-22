"""
Sound engine — Phase B.

All sounds synthesised with numpy. Non-blocking playback via sounddevice.
Priority-based interruption: higher priority sounds interrupt lower ones immediately.
Same or lower priority sounds are dropped (never queued).

Priority levels (lower number = higher priority):
  P.HIGH   (1): surprised_ding, angry_buzz, wake_chime
  P.MEDIUM (2): greet_owner, greet_indhu, greet_stranger, wave_response, thumbs_up
  P.NORMAL (3): happy_chirp, excited_trill, sad_whimper, confused_bloop, love_sound
  P.LOW    (4): all others
"""
import os
import subprocess
import threading
import time
from enum import IntEnum
from typing import Callable, Dict, Optional

import numpy as np

# Ensure PipeWire/PulseAudio can connect when running under PM2
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

try:
    import sounddevice as sd
    # Only use sounddevice if a real output device exists.
    # On Pi with PipeWire BT, the default output is -1 (ALSA sees no sink).
    # In that case we fall back to paplay which talks PipeWire natively.
    _SD_AVAILABLE = (sd.default.device[1] >= 0)
except Exception:
    _SD_AVAILABLE = False

from core.event_bus import Event, EventType, bus
from utils.logger import get_logger

log = get_logger(__name__)

RATE = 22050


# ── Priority ──────────────────────────────────────────────────────────────────

class P(IntEnum):
    HIGH   = 1
    MEDIUM = 2
    NORMAL = 3
    LOW    = 4


# ── Audio primitives ──────────────────────────────────────────────────────────

def _t(dur: float) -> np.ndarray:
    return np.linspace(0, dur, int(RATE * dur), endpoint=False)


def _sine(freq: float, dur: float, amp: float = 0.5) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * freq * _t(dur))).astype(np.float32)


def _chirp(f0: float, f1: float, dur: float, amp: float = 0.5) -> np.ndarray:
    t = _t(dur)
    phase = 2 * np.pi * np.cumsum(np.linspace(f0, f1, len(t))) / RATE
    return (amp * np.sin(phase)).astype(np.float32)


def _env(s: np.ndarray, attack: float = 0.02, release: float = 0.06) -> np.ndarray:
    n, att, rel = len(s), int(RATE * attack), int(RATE * release)
    e = np.ones(n, dtype=np.float32)
    if att > 0:
        e[:att] = np.linspace(0, 1, att)
    if 0 < rel < n:
        e[-rel:] = np.linspace(1, 0, rel)
    return s * e


def _mix(*arrs: np.ndarray) -> np.ndarray:
    n = max(len(a) for a in arrs)
    out = np.zeros(n, dtype=np.float32)
    for a in arrs:
        out[:len(a)] += a
    return np.clip(out, -1.0, 1.0)


def _cat(*arrs: np.ndarray) -> np.ndarray:
    return np.concatenate(arrs)


def _sil(dur: float) -> np.ndarray:
    return np.zeros(int(RATE * dur), dtype=np.float32)


# ── New sound generators (Phase B) ───────────────────────────────────────────

def _gen_greet_owner() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.12, 0.3)) for f in [262, 330, 392]])


def _gen_greet_indhu() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.12, 0.25), release=0.05) for f in [262, 330, 392, 523]])


def _gen_greet_stranger() -> np.ndarray:
    return _env(_sine(440, 0.2, 0.15))


def _gen_happy_chirp() -> np.ndarray:
    return _env(_chirp(880, 1100, 0.15, 0.45))


def _gen_excited_trill() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.15, 0.4), release=0.03) for f in [330, 392, 440, 523, 659]])


def _gen_sad_whimper() -> np.ndarray:
    return _env(_chirp(600, 300, 0.4, 0.2), attack=0.05, release=0.1)


def _gen_confused_bloop() -> np.ndarray:
    return _env(_cat(_chirp(400, 700, 0.15, 0.4), _chirp(700, 400, 0.15, 0.4)))


def _gen_surprised_ding() -> np.ndarray:
    return _env(_sine(1200, 0.08, 0.5), attack=0.005, release=0.04)


def _gen_wave_response() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.1, 0.4), release=0.04) for f in [330, 392, 440, 659]])


def _gen_thumbs_up() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.08, 0.45), attack=0.005, release=0.02) for f in [262, 330, 392]])


def _gen_bored_sigh() -> np.ndarray:
    return _cat(
        _env(_chirp(500, 380, 0.3, 0.15), release=0.05),
        _env(_chirp(380, 300, 0.3, 0.15), release=0.1),
    )


def _gen_curious_pip() -> np.ndarray:
    return _env(_chirp(400, 600, 0.12, 0.4))


def _gen_yawn_sweep() -> np.ndarray:
    return _env(_chirp(800, 200, 1.2, 0.2), attack=0.1, release=0.2)


def _gen_love_sound() -> np.ndarray:
    chord = _mix(_sine(262, 0.6, 0.15), _sine(330, 0.6, 0.15), _sine(392, 0.6, 0.15))
    return _env(chord, attack=0.05, release=0.15)


def _gen_angry_buzz() -> np.ndarray:
    t = _t(0.2)
    wave = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in [1, 3, 5, 7, 9])
    wave = wave.astype(np.float32)
    mx = float(np.abs(wave).max()) or 1.0
    return _env((wave / mx * 0.4).astype(np.float32), attack=0.01, release=0.05)


def _gen_sleep_breath() -> np.ndarray:
    return _env(_sine(200, 1.0, 0.05), attack=0.15, release=0.25)


def _gen_wake_chime() -> np.ndarray:
    return _cat(*[_env(_sine(f, 0.08, 0.5), attack=0.005, release=0.03)
                  for f in [262, 294, 330, 392, 523]])


def _gen_motor_happy() -> np.ndarray:
    return _env(_chirp(400, 700, 0.15, 0.35))


# ── Existing generators (preserved for event bus + cosmo_demo wiring) ─────────

def _gen_chirp_happy() -> np.ndarray:
    return _env(_chirp(400, 800, 0.2, 0.6))


def _gen_purr_content() -> np.ndarray:
    t = _t(2.0)
    mod = (0.7 + 0.3 * np.sin(2 * np.pi * 20 * t)).astype(np.float32)
    base = _mix(_sine(80, 2.0, 0.3), _sine(160, 2.0, 0.15), _sine(240, 2.0, 0.08))
    return (base * mod).astype(np.float32)


def _gen_beep_ack() -> np.ndarray:
    return _env(_sine(800, 0.1, 0.6))


def _gen_chime_greeting() -> np.ndarray:
    c = _env(_sine(523, 0.5, 0.5), release=0.3)
    e_p = _cat(_sil(0.08), _env(_sine(659, 0.4, 0.4), release=0.3))
    g_p = _cat(_sil(0.16), _env(_sine(784, 0.35, 0.35), release=0.3))
    n = max(len(c), len(e_p), len(g_p))
    pads = [np.concatenate([a, np.zeros(n - len(a), dtype=np.float32)]) for a in [c, e_p, g_p]]
    return _mix(*pads)


# Legacy — used by behavior engine and cosmo_demo event reactions

def _gen_trill_excited() -> np.ndarray:
    parts = [_env(_sine(600 if i % 2 == 0 else 900, 0.05, 0.55)) for i in range(8)]
    return _cat(*parts)


def _gen_chirp_curious() -> np.ndarray:
    return _env(_cat(_chirp(500, 700, 0.15, 0.5), _chirp(700, 600, 0.15, 0.5)))


def _gen_whimper_sad() -> np.ndarray:
    return _env(_chirp(400, 200, 0.5, 0.4))


def _gen_whimper_lonely() -> np.ndarray:
    t = _t(0.8)
    mod = 300 + 15 * np.sin(2 * np.pi * 5 * t)
    phase = 2 * np.pi * np.cumsum(mod) / RATE
    return _env((0.4 * np.sin(phase)).astype(np.float32))


def _gen_purr_petted() -> np.ndarray:
    t = _t(3.0)
    mod = (0.6 + 0.4 * np.sin(2 * np.pi * 25 * t)).astype(np.float32)
    base = _mix(_sine(100, 3.0, 0.3), _sine(150, 3.0, 0.12))
    return _env((base * mod).astype(np.float32), attack=0.1, release=0.2)


def _gen_battery_low() -> np.ndarray:
    parts = []
    for freq in [600, 500, 400]:
        parts += [_env(_sine(freq, 0.2, 0.55), release=0.1), _sil(0.1)]
    return _cat(*parts)


def _gen_sleep_exhale() -> np.ndarray:
    base = _chirp(300, 150, 0.6, 0.25)
    noise = np.random.uniform(-0.08, 0.08, len(base)).astype(np.float32)
    return _env(base + noise, attack=0.05, release=0.2)


def _gen_boot_chime() -> np.ndarray:
    parts = []
    for freq in [262, 330, 392, 523]:
        parts += [_env(_sine(freq, 0.18, 0.45), release=0.08), _sil(0.02)]
    return _cat(*parts)


# ── Sound registries ──────────────────────────────────────────────────────────

# Primary registry — all Phase B sounds, iterable for testing
SOUNDS: Dict[str, Callable[[], np.ndarray]] = {
    # P1 — interrupts everything
    "surprised_ding": _gen_surprised_ding,
    "angry_buzz":     _gen_angry_buzz,
    "wake_chime":     _gen_wake_chime,
    # P2
    "greet_owner":    _gen_greet_owner,
    "greet_indhu":    _gen_greet_indhu,
    "greet_stranger": _gen_greet_stranger,
    "wave_response":  _gen_wave_response,
    "thumbs_up":      _gen_thumbs_up,
    # P3
    "happy_chirp":    _gen_happy_chirp,
    "excited_trill":  _gen_excited_trill,
    "sad_whimper":    _gen_sad_whimper,
    "confused_bloop": _gen_confused_bloop,
    "love_sound":     _gen_love_sound,
    # P4 new
    "bored_sigh":     _gen_bored_sigh,
    "curious_pip":    _gen_curious_pip,
    "yawn_sweep":     _gen_yawn_sweep,
    "sleep_breath":   _gen_sleep_breath,
    "motor_happy":    _gen_motor_happy,
    # P4 existing — wired in cosmo_demo and event bus
    "chirp_happy":    _gen_chirp_happy,
    "purr_content":   _gen_purr_content,
    "beep_ack":       _gen_beep_ack,
    "chime_greeting": _gen_chime_greeting,
}

_SOUND_PRIORITY: Dict[str, P] = {
    "surprised_ding": P.HIGH,
    "angry_buzz":     P.HIGH,
    "wake_chime":     P.HIGH,
    "greet_owner":    P.MEDIUM,
    "greet_indhu":    P.MEDIUM,
    "greet_stranger": P.MEDIUM,
    "wave_response":  P.MEDIUM,
    "thumbs_up":      P.MEDIUM,
    "happy_chirp":    P.NORMAL,
    "excited_trill":  P.NORMAL,
    "sad_whimper":    P.NORMAL,
    "confused_bloop": P.NORMAL,
    "love_sound":     P.NORMAL,
    "bored_sigh":     P.LOW,
    "curious_pip":    P.LOW,
    "yawn_sweep":     P.LOW,
    "sleep_breath":   P.LOW,
    "motor_happy":    P.LOW,
    "chirp_happy":    P.LOW,
    "purr_content":   P.LOW,
    "beep_ack":       P.LOW,
    "chime_greeting": P.LOW,
}

# Legacy — for backward compat with behavior engine and cosmo_demo reactions
_LEGACY: Dict[str, Callable[[], np.ndarray]] = {
    "trill_excited":  _gen_trill_excited,
    "chirp_curious":  _gen_chirp_curious,
    "whimper_sad":    _gen_whimper_sad,
    "whimper_lonely": _gen_whimper_lonely,
    "purr_petted":    _gen_purr_petted,
    "battery_low":    _gen_battery_low,
    "sleep_exhale":   _gen_sleep_exhale,
    "boot_chime":     _gen_boot_chime,
}


# ── Playback engine ───────────────────────────────────────────────────────────

_PW_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
}


def _play_raw_paplay(samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    try:
        proc = subprocess.Popen(
            ["paplay", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16le"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_PW_ENV,
        )
        proc.stdin.write(pcm.tobytes())
        proc.stdin.close()
        proc.wait()
    except Exception as e:
        log.debug("sounds.paplay_error", error=str(e)[:60])


class SoundEngine:
    """
    Non-blocking sound playback with priority interruption.

    play() always returns immediately — audio runs in a background thread.
    Higher priority sounds kill the current sound and play immediately.
    Same/lower priority sounds are silently dropped.
    """

    _NO_PRIORITY = int(P.LOW) + 1  # sentinel meaning nothing is playing

    SOUNDS = SOUNDS  # exposed for external iteration/testing

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._current_priority: int = self._NO_PRIORITY
        self._muted_until: float = 0.0

    def generate(self, name: str) -> Optional[np.ndarray]:
        gen = SOUNDS.get(name) or _LEGACY.get(name)
        if gen is None:
            log.warning("sounds.unknown", name=name)
            return None
        try:
            return gen()
        except Exception as e:
            log.warning("sounds.generate_error", name=name, error=str(e)[:60])
            return None

    def _play_thread(self, samples: np.ndarray, priority: int) -> None:
        with self._lock:
            if self._playing:
                if priority >= self._current_priority:
                    return  # drop: same or lower priority
                # Interrupt: higher priority wins
                if _SD_AVAILABLE:
                    sd.stop()
            self._current_priority = priority
            self._playing = True

        try:
            if _SD_AVAILABLE:
                sd.play(samples, RATE)
                sd.wait()
            else:
                _play_raw_paplay(samples)
        except Exception as e:
            log.debug("sounds.sd_error", error=str(e)[:60])
            if _SD_AVAILABLE:
                try:
                    _play_raw_paplay(samples)
                except Exception:
                    pass
        finally:
            with self._lock:
                if self._current_priority == priority:
                    self._playing = False
                    self._current_priority = self._NO_PRIORITY

    async def play(self, name: str) -> None:
        import asyncio
        if time.monotonic() < self._muted_until:
            return
        samples = self.generate(name)
        if samples is None:
            return
        priority = int(_SOUND_PRIORITY.get(name, P.LOW))
        log.info("sounds.play", sound=name, priority=priority)
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._play_thread, samples, priority)

    async def play_tone(self, freq: float, duration: float, amp: float = 0.5) -> None:
        import asyncio
        samples = _env(_sine(freq, duration, amp))
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._play_thread, samples, int(P.NORMAL))

    def mute(self, seconds: float = 30) -> None:
        self._muted_until = time.monotonic() + seconds
        log.info("sounds.muted", seconds=seconds)

    async def start(self) -> None:
        import asyncio
        _event_map = {
            EventType.TOUCH_DETECTED:   "purr_petted",
            EventType.PICKUP_DETECTED:  "happy_chirp",
            EventType.WAKE_WORD:        "wake_chime",
            EventType.FACE_RECOGNIZED:  "chime_greeting",
            EventType.BATTERY_CRITICAL: "battery_low",
            # MOTION_DETECTED removed — cosmo_demo.py already handles it with
            # person-presence context; having both caused double beeps on every event.
        }
        for evt_type, sound_name in _event_map.items():
            async def _handler(event: Event, _name=sound_name) -> None:
                await self.play(_name)
            bus.on(evt_type)(_handler)
        log.info("sounds.started",
                 backend="sounddevice" if _SD_AVAILABLE else "paplay",
                 sounds=len(SOUNDS))


sounds = SoundEngine()
