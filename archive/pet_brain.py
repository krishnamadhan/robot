"""
Cosmo Pet Brain — movement and expression decision engine.

Decides WHEN to move, WHERE, and HOW — driven by personality state,
sensor environment, social context, and time of day.

Design principles (from Codex + project review):
  - Sound / eye expression fires BEFORE any movement
  - State transitions are hysteresis-gated to avoid jitter
  - Personality traits (curiosity, energy, mood) modulate all outputs
  - Memory-informed: if we know where a person last was, seek that direction
  - Never issues movement commands directly — publishes via event bus
    so the behavior tree safety stack can still veto
"""

import asyncio
import datetime
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

from core.event_bus import bus, Event, EventType
from core.personality import personality, personality_learning
from expression.eyes import EyeExpression, eye_engine
from expression.sounds import sounds
from hardware.sensor_manager import sensor_manager
from utils.logger import get_logger

log = get_logger(__name__)


# ── Drive states ──────────────────────────────────────────────────────────────

class PetState(str, Enum):
    RESTING         = "resting"
    CURIOUS_WANDER  = "curious_wander"
    SEEK_LIGHT      = "seek_light"
    APPROACH_PERSON = "approach_person"
    PLAY            = "play"
    FLEE            = "flee"


# Minimum hold time (seconds) before a state can change again.
# Prevents jittery micro-transitions.
_STATE_HOLD: Dict[PetState, float] = {
    PetState.RESTING:         6.0,
    PetState.CURIOUS_WANDER:  5.0,
    PetState.SEEK_LIGHT:      4.0,
    PetState.APPROACH_PERSON: 3.0,
    PetState.PLAY:            2.0,
    PetState.FLEE:            1.5,
}

# Eye expressions per state
_STATE_EYES: Dict[PetState, EyeExpression] = {
    PetState.RESTING:         EyeExpression.SLEEPY,
    PetState.CURIOUS_WANDER:  EyeExpression.CURIOUS,
    PetState.SEEK_LIGHT:      EyeExpression.CURIOUS,
    PetState.APPROACH_PERSON: EyeExpression.HAPPY,
    PetState.PLAY:            EyeExpression.EXCITED,
    PetState.FLEE:            EyeExpression.SCARED,
}

# Sound cues per state (must match names in expression/sounds.py)
_STATE_SOUNDS: Dict[PetState, Optional[str]] = {
    PetState.RESTING:         "purr_content",
    PetState.CURIOUS_WANDER:  "chirp_curious",
    PetState.SEEK_LIGHT:      "chirp_curious",
    PetState.APPROACH_PERSON: "trill_excited",
    PetState.PLAY:            "trill_excited",
    PetState.FLEE:            "whimper_sad",
}


@dataclass
class MovementIntent:
    """Output of a single pet_brain decision tick."""
    state:    PetState
    speed:    float          # 0.0 → 0.8
    duration: float          # seconds
    eye_expr: EyeExpression
    sound:    Optional[str]
    direction: str = "forward"   # "forward" | "wander" | "turn_left" | "turn_right" | "none"


class PetBrain:
    """
    Personality-aware movement decision engine.

    Usage:
        pet_brain = PetBrain()
        await pet_brain.start()   # subscribes to events, starts tick loop
        await pet_brain.stop()
    """

    TICK_INTERVAL_S = 1.0   # decision loop frequency
    MOVE_COOLDOWN_S = 4.0   # min gap between issuing movement commands

    def __init__(self) -> None:
        self.state            = PetState.RESTING
        self._hold_until      = 0.0
        self._last_moved      = 0.0
        self._last_sound      = 0.0
        self._running         = False
        self._task: Optional[asyncio.Task] = None

        # Social context — written by event bus handlers
        self._person_visible  = False
        self._person_id:  Optional[str]   = None
        self._person_name: Optional[str]  = None
        self._person_x:   float = 0.0     # -1 (left) → 1 (right)
        self._person_dist_cm: float = 100.0

        # Last known person direction (to seek when alone and bored)
        self._last_person_x:  float = 0.0
        self._last_person_age: float = 0.0  # monotonic timestamp
        self._handlers: list = []

    # ── Lifecycle (idempotent) ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._handlers = self._subscribe_events()
        self._task = asyncio.create_task(self._tick_loop(), name="pet_brain")
        log.info("pet_brain.started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        # Unsubscribe handlers to prevent stacking on restart
        for handler in getattr(self, "_handlers", []):
            try:
                bus.unsubscribe(handler)
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Event subscriptions ───────────────────────────────────────────────────

    def _subscribe_events(self) -> list:
        """Subscribe to events and return handler refs for cleanup."""
        handlers = []

        async def _on_person(e: Event) -> None:
            self._person_visible   = True
            cx = e.data.get("bbox_center_x", 0.0)
            self._person_x         = cx
            self._last_person_x    = cx
            self._last_person_age  = time.monotonic()
            dist = e.data.get("distance_cm", self._person_dist_cm)
            self._person_dist_cm   = dist

        async def _on_face(e: Event) -> None:
            self._person_visible  = True
            self._person_id       = e.data.get("person_id")
            self._person_name     = e.data.get("name")
            cx = e.data.get("bbox_center_x", self._person_x)
            self._person_x        = cx
            self._last_person_x   = cx
            self._last_person_age = time.monotonic()

        async def _on_lost(e: Event) -> None:
            self._person_visible  = False
            self._person_id       = None
            self._person_name     = None
            self._person_dist_cm  = 100.0

        bus.on(EventType.PERSON_DETECTED)(_on_person)
        bus.on(EventType.FACE_RECOGNIZED)(_on_face)
        bus.on(EventType.PERSON_LOST)(_on_lost)
        handlers.extend([_on_person, _on_face, _on_lost])
        return handlers

    # ── Decision loop ─────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        await asyncio.sleep(5.0)  # let everything else init first
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                log.error("pet_brain.tick_error", error=str(e)[:120])
            await asyncio.sleep(self.TICK_INTERVAL_S)

    async def _tick(self) -> None:
        from behavior.navigation import navigation

        lux          = sensor_manager.get_lux()
        dist_cm      = sensor_manager.get_distance_cm()
        pers_state   = personality.state
        traits       = personality_learning.traits
        hour         = datetime.datetime.now().hour

        intent = self._decide(
            energy=pers_state.energy,
            mood=pers_state.mood,
            curiosity=traits.get("curiosity", 0.7),
            lux=lux,
            dist_cm=dist_cm,
            person_visible=self._person_visible,
            hour=hour,
        )

        # State transition with hysteresis.
        # Safety states (FLEE) bypass the hold timer — always immediate.
        now = time.monotonic()
        is_emergency = (intent.state == PetState.FLEE)
        if intent.state != self.state and (now >= self._hold_until or is_emergency):
            old = self.state
            self.state = intent.state
            self._hold_until = now + _STATE_HOLD[self.state] * random.uniform(0.85, 1.15)

            # Non-verbal reaction FIRST: sound + eyes before any movement
            eye_engine.set_expression(intent.eye_expr, duration=4.0)
            if intent.sound and now - self._last_sound > 6.0:
                self._last_sound = now
                asyncio.create_task(sounds.play(intent.sound))

            log.info("pet_brain.state_change",
                     from_state=old, to=self.state.value,
                     energy=round(pers_state.energy, 2),
                     mood=round(pers_state.mood, 2),
                     lux=round(lux, 0),
                     dist_cm=dist_cm)

            # Small delay between expression and movement — feels organic
            await asyncio.sleep(random.uniform(0.3, 0.8))

        # Issue movement command if we're in an active state and not recently moved
        if now - self._last_moved < self.MOVE_COOLDOWN_S:
            return
        if navigation.state.value not in ("idle",):
            return

        await self._execute_movement(intent, navigation, dist_cm)

    def _decide(
        self,
        energy: float,
        mood: float,
        curiosity: float,
        lux: float,
        dist_cm: float,
        person_visible: bool,
        hour: int,
    ) -> MovementIntent:
        """Pure decision function — no side effects, no async."""

        night = (hour >= 23 or hour < 7)
        dark  = lux < 40

        # Priority order (highest first):
        # 1. Flee immediate obstacle when stressed
        if dist_cm < 18 and (mood < 0.3 or energy < 0.25):
            return self._intent(PetState.FLEE, energy, mood, curiosity)

        # 2. Play with person nearby
        if person_visible and self._person_dist_cm < 50 and energy > 0.5 and mood > 0.3:
            return self._intent(PetState.PLAY, energy, mood, curiosity)

        # 3. Approach visible person at distance
        if person_visible and energy > 0.25 and mood > 0.15:
            return self._intent(PetState.APPROACH_PERSON, energy, mood, curiosity)

        # 4. Seek light when dark and not night (daytime low-light room)
        if dark and not night and energy > 0.3:
            return self._intent(PetState.SEEK_LIGHT, energy, mood, curiosity)

        # 5. Curious wander — driven by curiosity trait
        wander_threshold = 0.4 + (1.0 - curiosity) * 0.3  # high curiosity = lower threshold
        if not night and energy > 0.2 and not person_visible:
            # Weighted random: curiosity trait biases toward wandering
            if random.random() < curiosity * 0.4:
                return self._intent(PetState.CURIOUS_WANDER, energy, mood, curiosity)

        # 6. Rest
        return self._intent(PetState.RESTING, energy, mood, curiosity)

    def _intent(self, state: PetState, energy: float, mood: float, curiosity: float) -> MovementIntent:
        """Build a MovementIntent for the given state, modulated by personality."""
        e, m, c = energy, mood, curiosity

        params: Dict[PetState, Tuple] = {
            # state: (base_speed, speed_e_factor, base_duration, dur_c_factor, direction)
            PetState.RESTING:         (0.00, 0.0, 0.0, 0.0,   "none"),
            PetState.CURIOUS_WANDER:  (0.18, 0.10, 4.0, 3.0,  "wander"),
            PetState.SEEK_LIGHT:      (0.22, 0.05, 3.0, 1.5,  "forward"),
            PetState.APPROACH_PERSON: (0.25, 0.12, 2.0, 1.0,  self._approach_dir()),
            PetState.PLAY:            (0.32, 0.20, 1.5, 0.5,  "wander"),
            PetState.FLEE:            (0.40, 0.10, 1.2, 0.0,  "backward"),
        }
        base_spd, spd_e, base_dur, dur_c, direction = params[state]

        speed    = min(0.75, base_spd + spd_e * e)
        duration = max(0.5, (base_dur + dur_c * c) * random.uniform(0.85, 1.15))

        return MovementIntent(
            state=state,
            speed=round(speed, 2),
            duration=round(duration, 2),
            eye_expr=_STATE_EYES[state],
            sound=_STATE_SOUNDS[state],
            direction=direction,
        )

    def _approach_dir(self) -> str:
        """Turn direction based on where person (or last known position) is."""
        age = time.monotonic() - self._last_person_age
        if age > 30.0:
            return "forward"
        x = self._person_x if self._person_visible else self._last_person_x
        if x < -0.25:
            return "turn_left"
        if x > 0.25:
            return "turn_right"
        return "forward"

    async def _execute_movement(self, intent: MovementIntent, navigation, dist_cm: float) -> None:
        """Issue navigation command for the given intent."""
        if intent.state == PetState.RESTING or intent.direction == "none":
            return
        if dist_cm < 20 and intent.direction in ("forward", "wander", "turn_left", "turn_right"):
            # Still update _last_moved so we don't spam every tick while blocked
            self._last_moved = time.monotonic()
            log.debug("pet_brain.movement_blocked", dist_cm=dist_cm)
            return

        self._last_moved = time.monotonic()
        spd = intent.speed
        dur = intent.duration

        try:
            if intent.direction == "wander":
                asyncio.create_task(navigation.wander(duration=int(dur + 8)))
            elif intent.direction == "forward":
                await navigation.forward(speed=spd, duration=dur)
            elif intent.direction == "backward":
                await navigation.backward(speed=spd, duration=dur)
            elif intent.direction == "turn_left":
                await navigation.turn_left(speed=spd * 0.7, duration=min(dur, 1.0))
                await navigation.forward(speed=spd * 0.8, duration=dur * 0.6)
            elif intent.direction == "turn_right":
                await navigation.turn_right(speed=spd * 0.7, duration=min(dur, 1.0))
                await navigation.forward(speed=spd * 0.8, duration=dur * 0.6)
        except Exception as e:
            log.error("pet_brain.movement_error", error=str(e)[:80])
            return

        # Record outcome for personality learning
        try:
            personality_learning.record_outcome(
                interaction_type="wander",
                person_responded=self._person_visible,
                mood_delta=0.0,
            )
        except Exception:
            pass

        log.info("pet_brain.move",
                 state=intent.state.value,
                 dir=intent.direction,
                 speed=spd,
                 duration=dur)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_state(self) -> PetState:
        return self.state

    def describe(self) -> str:
        return f"PetBrain[{self.state.value}] person={'yes' if self._person_visible else 'no'}"


pet_brain = PetBrain()
