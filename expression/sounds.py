"""
Sound engine — programmatic audio expressions via numpy.

All sounds are generated on-the-fly with numpy. No audio files.
Output via paplay → PipeWire → JBL Flip 5 BT speaker.

Usage:
    await sounds.play("chirp_happy")
    await sounds.play_tone(440, 0.5)
"""

import asyncio
import subprocess
import time
from typing import Dict, Optional

import numpy as np

from core.event_bus import Event, EventType, bus
from utils.logger import get_logger

log = get_logger(__name__)

RATE = 22050   # Hz
AMP  = 0.55    # default amplitude (0-1), keep below 0.7 to avoid clipping


# ── Audio primitives ──────────────────────────────────────────────────────────

def _t(duration: float) -> np.ndarray:
    return np.linspace(0, duration, int(RATE * duration), endpoint=False)


def _sine(freq: float, duration: float, amp: float = AMP) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * freq * _t(duration))).astype(np.float32)


def _chirp(start_hz: float, end_hz: float, duration: float,
           amp: float = AMP) -> np.ndarray:
    t = _t(duration)
    freq = np.linspace(start_hz, end_hz, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return (amp * np.sin(phase)).astype(np.float32)


def _envelope(s: np.ndarray, attack: float = 0.02,
              release: float = 0.06) -> np.ndarray:
    n = len(s)
    att = int(RATE * attack)
    rel = int(RATE * release)
    env = np.ones(n, dtype=np.float32)
    if att > 0:
        env[:att] = np.linspace(0, 1, att)
    if rel > 0 and rel < n:
        env[-rel:] = np.linspace(1, 0, rel)
    return s * env


def _fade(s: np.ndarray, fade_s: float = 0.02) -> np.ndarray:
    return _envelope(s, attack=fade_s, release=fade_s)


def _mix(*arrays: np.ndarray) -> np.ndarray:
    max_len = max(len(a) for a in arrays)
    out = np.zeros(max_len, dtype=np.float32)
    for a in arrays:
        out[:len(a)] += a
    return np.clip(out, -1.0, 1.0)


def _concat(*arrays: np.ndarray) -> np.ndarray:
    return np.concatenate(arrays)


def _silence(duration: float) -> np.ndarray:
    return np.zeros(int(RATE * duration), dtype=np.float32)


def _vibrato(freq: float, duration: float, rate: float = 6.0,
             depth: float = 8.0, amp: float = AMP) -> np.ndarray:
    t = _t(duration)
    mod = freq + depth * np.sin(2 * np.pi * rate * t)
    phase = 2 * np.pi * np.cumsum(mod) / RATE
    return (amp * np.sin(phase)).astype(np.float32)


# ── Sound generators ──────────────────────────────────────────────────────────

def _gen_chirp_happy() -> np.ndarray:
    return _envelope(_chirp(400, 800, 0.2, amp=0.6))


def _gen_trill_excited() -> np.ndarray:
    parts = []
    for _ in range(8):
        f = 600 if len(parts) % 2 == 0 else 900
        parts.append(_envelope(_sine(f, 0.05, amp=0.55)))
    return _concat(*parts)


def _gen_purr_content() -> np.ndarray:
    base = _sine(80, 2.0, amp=0.3)
    h2   = _sine(160, 2.0, amp=0.15)
    h3   = _sine(240, 2.0, amp=0.08)
    purr = _mix(base, h2, h3)
    # amplitude modulate at 20 Hz (purr rhythm)
    t = _t(2.0)
    mod = 0.7 + 0.3 * np.sin(2 * np.pi * 20 * t)
    return (purr * mod).astype(np.float32)


def _gen_chime_greeting() -> np.ndarray:
    c = _envelope(_sine(523, 0.5, amp=0.5), release=0.3)
    e = _envelope(_sine(659, 0.4, amp=0.4), release=0.3)
    g = _envelope(_sine(784, 0.35, amp=0.35), release=0.3)
    n = int(RATE * 0.1)
    e_padded = _concat(_silence(0.08), e)
    g_padded = _concat(_silence(0.16), g)
    length = max(len(c), len(e_padded), len(g_padded))
    for arr in [c, e_padded, g_padded]:
        arr.resize(length, refcheck=False)
    return _mix(c, e_padded, g_padded)


def _gen_chirp_curious() -> np.ndarray:
    up   = _chirp(500, 700, 0.15, amp=0.5)
    down = _chirp(700, 600, 0.15, amp=0.5)
    return _envelope(_concat(up, down))


def _gen_beep_ack() -> np.ndarray:
    return _envelope(_sine(800, 0.1, amp=0.6))


def _gen_beep_thinking() -> np.ndarray:
    on  = _envelope(_sine(400, 0.12, amp=0.4))
    off = _silence(0.08)
    return _concat(on, off, on, off, on)


def _gen_whimper_sad() -> np.ndarray:
    return _envelope(_chirp(400, 200, 0.5, amp=0.4))


def _gen_whimper_lonely() -> np.ndarray:
    return _envelope(_vibrato(300, 0.8, rate=5.0, depth=15, amp=0.4))


def _gen_alert_beep() -> np.ndarray:
    beep = _envelope(_sine(1000, 0.15, amp=0.65))
    gap  = _silence(0.05)
    return _concat(beep, gap, beep, gap, beep)


def _gen_boot_chime() -> np.ndarray:
    notes = [262, 330, 392, 523]  # C-E-G-C (middle C octave)
    parts = []
    for i, freq in enumerate(notes):
        s = _envelope(_sine(freq, 0.18, amp=0.45), release=0.08)
        parts.append(s)
        parts.append(_silence(0.02))
    return _concat(*parts)


def _gen_sleep_exhale() -> np.ndarray:
    base = _chirp(300, 150, 0.6, amp=0.25)
    noise = np.random.uniform(-0.08, 0.08, len(base)).astype(np.float32)
    return _envelope(base + noise, attack=0.05, release=0.2)


def _gen_yawn() -> np.ndarray:
    up   = _chirp(200, 400, 0.5, amp=0.35)
    down = _chirp(400, 200, 0.7, amp=0.35)
    return _envelope(_concat(up, down), attack=0.1, release=0.15)


def _gen_purr_petted() -> np.ndarray:
    base = _sine(100, 3.0, amp=0.3)
    h2   = _sine(150, 3.0, amp=0.12)
    purr = _mix(base, h2)
    t = _t(3.0)
    mod = 0.6 + 0.4 * np.sin(2 * np.pi * 25 * t)
    return _envelope((purr * mod).astype(np.float32), attack=0.1, release=0.2)


def _gen_battery_low() -> np.ndarray:
    beeps = []
    for freq in [600, 500, 400]:
        beeps.append(_envelope(_sine(freq, 0.2, amp=0.55), release=0.1))
        beeps.append(_silence(0.1))
    return _concat(*beeps)


def _gen_battery_ok() -> np.ndarray:
    return _envelope(_chirp(400, 600, 0.25, amp=0.5))


def _gen_click() -> np.ndarray:
    noise = np.random.uniform(-1, 1, int(RATE * 0.02)).astype(np.float32)
    return _envelope(noise * 0.4, attack=0.001, release=0.01)


# ── Sound registry ────────────────────────────────────────────────────────────

_GENERATORS = {
    "chirp_happy":    _gen_chirp_happy,
    "trill_excited":  _gen_trill_excited,
    "purr_content":   _gen_purr_content,
    "chime_greeting": _gen_chime_greeting,
    "chirp_curious":  _gen_chirp_curious,
    "beep_ack":       _gen_beep_ack,
    "beep_thinking":  _gen_beep_thinking,
    "whimper_sad":    _gen_whimper_sad,
    "whimper_lonely": _gen_whimper_lonely,
    "alert_beep":     _gen_alert_beep,
    "boot_chime":     _gen_boot_chime,
    "sleep_exhale":   _gen_sleep_exhale,
    "yawn":           _gen_yawn,
    "purr_petted":    _gen_purr_petted,
    "battery_low":    _gen_battery_low,
    "battery_ok":     _gen_battery_ok,
    "click":          _gen_click,
}

_EVENT_SOUND = {
    EventType.TOUCH_DETECTED:   "purr_petted",
    EventType.PICKUP_DETECTED:  "chirp_happy",
    EventType.WAKE_WORD:        "beep_ack",
    EventType.FACE_RECOGNIZED:  "chime_greeting",
    EventType.BATTERY_CRITICAL: "battery_low",
    EventType.MOTION_DETECTED:  "chirp_curious",
}


# ── Sound engine ──────────────────────────────────────────────────────────────

def _play_raw(samples: np.ndarray) -> None:
    audio = np.clip(samples, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)
    try:
        import os
        env = {**os.environ,
               "XDG_RUNTIME_DIR": "/run/user/1000",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
        proc = subprocess.Popen(
            ["paplay", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16le"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
        proc.stdin.write(pcm.tobytes())
        proc.stdin.close()
        proc.wait()
    except Exception as e:
        log.debug("sounds.play_error", error=str(e)[:60])


class SoundEngine:

    def __init__(self) -> None:
        self._muted_until: float = 0.0

    async def start(self) -> None:
        for evt_type, sound_name in _EVENT_SOUND.items():
            async def _handler(event: Event, _name=sound_name) -> None:
                await self.play(_name)
            bus.on(evt_type)(_handler)
        log.info("sounds.started")

    def generate(self, name: str) -> np.ndarray:
        gen = _GENERATORS.get(name)
        if not gen:
            log.warning("sounds.unknown", name=name)
            return _gen_beep_ack()
        return gen()

    async def play(self, name: str) -> None:
        if time.monotonic() < self._muted_until:
            return
        samples = self.generate(name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _play_raw, samples)
        log.debug("sounds.played", name=name)

    async def play_tone(self, freq: float, duration: float,
                        amp: float = 0.5) -> None:
        samples = _envelope(_sine(freq, duration, amp=amp))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _play_raw, samples)

    def mute(self, seconds: float = 30) -> None:
        self._muted_until = time.monotonic() + seconds
        log.info("sounds.muted", seconds=seconds)


sounds = SoundEngine()
