"""
Idle motion loop — micro-level aliveness between behaviors (Phase 2.2).

Sits under the BT's idle behaviors (wander/fidget intents) and above the
eye engine's per-frame animation. Adds:

  - curiosity glances: pupil saccades; cadence and amplitude scale with the
    curiosity trait and current arousal
  - settling: pupils ease back to center instead of snapping
  - boredom fidgets: slow look-away + double blink when energy sags
  - micro-reactions: instant pupil dart on SOUND_DETECTED / LIGHT_CHANGED

Pure expression (D6) — emits no Intents, never touches motors. Defers to
any event-driven timed expression on the eye engine.
"""

import asyncio
import random
import time
from typing import Optional

from core.event_bus import Event, EventType, bus
from core.personality import personality, personality_learning
from expression.eyes import eye_engine
from utils.logger import get_logger

log = get_logger(__name__)

TICK_S = 0.1


class IdleMotion:

    GLANCE_BASE_S      = 9.0     # mean gap between glances at curiosity=0.5
    GLANCE_HOLD_S      = (0.4, 1.1)
    FIDGET_BASE_S      = 25.0    # mean gap between boredom fidgets
    SETTLE_RATE        = 3.0     # pupil units/s eased back to center
    REACT_HOLD_S       = 0.8     # micro-reaction dart hold
    MIN_ENERGY         = 0.2     # below this the face is asleep — hold still

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._next_glance = 0.0
        self._next_fidget = 0.0
        self._hold_until = 0.0       # pupil excursion active until then
        self._unsubs = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        now = time.monotonic()
        self._next_glance = now + self._glance_interval()
        self._next_fidget = now + self.FIDGET_BASE_S * random.uniform(0.5, 1.5)

        async def _on_sound(event: Event) -> None:
            self._micro_react(x=random.choice((-0.8, 0.8)), y=-0.2)

        async def _on_light(event: Event) -> None:
            self._micro_react(x=0.0, y=-0.6)   # glance up at the light

        bus.subscribe(_on_sound, event_types={EventType.SOUND_DETECTED})
        bus.subscribe(_on_light, event_types={EventType.LIGHT_CHANGED})
        self._unsubs = [_on_sound, _on_light]

        self._task = asyncio.create_task(self._loop(), name="idle_motion")
        log.info("idle_motion.started")

    async def stop(self) -> None:
        self._running = False
        for h in self._unsubs:
            bus.unsubscribe(h)
        self._unsubs = []
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _glance_interval(self) -> float:
        curiosity = personality_learning.get("curiosity")
        arousal = personality.state.arousal
        # curious + aroused → glances every few seconds; placid → rare
        factor = max(0.25, 1.6 - curiosity - 0.5 * arousal)
        if self._settled():
            factor *= 3.0   # co-presence: calm pet on the sofa, sparse glances
        return self.GLANCE_BASE_S * factor * random.uniform(0.6, 1.4)

    @staticmethod
    def _settled() -> bool:
        try:
            from core.behavior_tree import bb
            return bb.settled
        except Exception:
            return False

    def _micro_react(self, x: float, y: float) -> None:
        if not self._active():
            return
        eye_engine.set_pupil(x, y)
        self._hold_until = time.monotonic() + self.REACT_HOLD_S
        log.debug("idle_motion.micro_react", x=x, y=y)

    def _active(self) -> bool:
        """Hold still while asleep-tired or while an event expression plays."""
        return (self._running
                and personality.state.energy >= self.MIN_ENERGY
                and not eye_engine.event_expression_active)

    def _tick(self, now: float) -> None:
        if not self._active():
            return

        s = personality.state

        # Curiosity glance
        if now >= self._next_glance:
            self._next_glance = now + self._glance_interval()
            amp = 0.4 + 0.5 * s.arousal
            eye_engine.set_pupil(random.uniform(-amp, amp),
                                 random.uniform(-0.4, 0.2))
            self._hold_until = now + random.uniform(*self.GLANCE_HOLD_S)
            log.debug("idle_motion.glance")

        # Boredom fidget — look down-away + double blink when energy sags
        elif now >= self._next_fidget:
            self._next_fidget = now + self.FIDGET_BASE_S * random.uniform(0.6, 1.6)
            if s.energy < 0.55 and s.arousal < 0.5 and not self._settled():
                eye_engine.set_pupil(random.choice((-0.5, 0.5)), 0.5)
                eye_engine._next_blink = now   # provoke an immediate blink
                self._hold_until = now + 1.5
                log.debug("idle_motion.fidget")

        # Settling — ease pupils back to center after any excursion
        if now >= self._hold_until:
            st = eye_engine.get_state()
            step = self.SETTLE_RATE * TICK_S
            for axis in ("pupil_x", "pupil_y"):
                v = getattr(st, axis)
                if abs(v) <= step:
                    setattr(st, axis, 0.0)
                else:
                    setattr(st, axis, v - step * (1 if v > 0 else -1))

    async def _loop(self) -> None:
        while self._running:
            try:
                self._tick(time.monotonic())
            except Exception as e:
                log.error("idle_motion.tick_error", error=str(e)[:80])
            await asyncio.sleep(TICK_S)


idle_motion = IdleMotion()
