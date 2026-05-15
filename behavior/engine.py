"""
Behavior engine — idle behaviors + proactive speech triggers.

Idle behaviors fire probabilistically when Cosmo is alone.
Proactive triggers fire when conditions are met (person enters, emotion changes, etc.)
All behaviors are asynchronous and interruptible.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from behavior.navigation import navigation
from cognition.conversation import conversation
from core.event_bus import Event, EventPriority, EventType, bus
from core.personality import personality
from core.state_machine import sm as state_machine
from expression.eyes import EyeExpression, eye_engine
from expression.sounds import sounds
from expression.speech import tts
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Behavior:
    name:       str
    weight:     float
    cooldown:   float         # seconds between fires
    execute:    Callable
    energy_min: float = 0.0   # personality.energy must be >= this
    energy_max: float = 1.0   # personality.energy must be <= this
    last_fired: float = field(default=0.0, init=False)

    def is_ready(self) -> bool:
        energy = personality.state.energy
        return (
            time.monotonic() - self.last_fired >= self.cooldown
            and self.energy_min <= energy <= self.energy_max
        )

    def mark_fired(self) -> None:
        self.last_fired = time.monotonic()


@dataclass
class ProactiveTrigger:
    name:       str
    cooldown_m: float         # minutes between fires
    phrases:    List[str]
    condition:  Callable[[], bool]
    once_per_day: bool = False
    last_fired: float = field(default=0.0, init=False)
    last_fired_day: int = field(default=-1, init=False)

    def is_ready(self) -> bool:
        now = time.monotonic()
        if self.once_per_day:
            today = int(time.time() / 86400)
            if self.last_fired_day == today:
                return False
        return now - self.last_fired >= self.cooldown_m * 60

    def pick_phrase(self, name: str = "friend") -> str:
        phrase = random.choice(self.phrases)
        return phrase.format(name=name)

    def mark_fired(self) -> None:
        self.last_fired = time.monotonic()
        self.last_fired_day = int(time.time() / 86400)


class BehaviorEngine:
    """
    Manages idle behavior loop and proactive speech triggers.
    """

    IDLE_CHECK_INTERVAL = 4.0   # seconds between behavior candidates

    def __init__(self) -> None:
        self._running          = False
        self._idle_task: Optional[asyncio.Task] = None
        self._trigger_task: Optional[asyncio.Task] = None
        self._current_person:  Optional[str] = None
        self._current_emotion: Optional[str] = None
        self._last_person_seen: float = 0.0
        self._no_person_since: float = time.monotonic()
        self._emotion_history: List[tuple] = []

        # ── Idle behaviors ────────────────────────────────────────────────────
        self._behaviors: List[Behavior] = [
            Behavior("look_left_right", 0.30, 15,  self._look_around,  energy_min=0.2),
            Behavior("slow_blink",      0.25,  8,  self._slow_blink),
            Behavior("curious_sound",   0.15, 30,  self._curious_sound, energy_min=0.4),
            Behavior("wander",          0.10, 60,  self._wander,        energy_min=0.5),
            Behavior("seek_attention",  0.20, 120, self._seek_attention, energy_max=0.3),
            Behavior("breathing",       0.35,  5,  self._breathe),
        ]

        # ── Proactive triggers ────────────────────────────────────────────────
        self._triggers: List[ProactiveTrigger] = [
            ProactiveTrigger(
                name="greet_person",
                cooldown_m=30,
                phrases=[
                    "Ayyo {name}! Vandhuttiya?",
                    "Hey {name}! Enna da nee?",
                    "{name}! Miss panni irundhein da!",
                    "Aiyo {name}, finally!",
                ],
                condition=lambda: (
                    self._current_person is not None
                    and personality.state.mood > 0.3
                ),
            ),
            ProactiveTrigger(
                name="comfort_sad",
                cooldown_m=60,
                phrases=[
                    "Enna achu da {name}? Sad-a irukkiya?",
                    "Hey, {name}... Pesalam if you want da.",
                    "Romba seri illa da {name}? I'm here.",
                ],
                condition=lambda: self._current_emotion in ("sad", "fearful"),
            ),
            ProactiveTrigger(
                name="lonely",
                cooldown_m=45,
                phrases=[
                    "Romba bore aagudhu da...",
                    "Yaarum illaya? Sooo quiet.",
                    "*makes sad beeping noise*",
                    "Oru pause button irundha nalla irukkum... wait, naan thaan idle da.",
                ],
                condition=lambda: (
                    time.monotonic() - self._no_person_since > 20 * 60
                ),
            ),
            ProactiveTrigger(
                name="morning",
                cooldown_m=60,
                once_per_day=True,
                phrases=[
                    "Good morning da! Coffee kudichiya?",
                    "Vanakam! Nalla thoongina?",
                    "Aiyoh, new day! Enna panrom today?",
                ],
                condition=lambda: (
                    self._current_person is not None
                    and 6 <= (time.time() % 86400 / 3600) <= 10
                ),
            ),
            ProactiveTrigger(
                name="emotion_changed",
                cooldown_m=15,
                phrases=[
                    "Suddenly happy-a? Enna nalla news?",
                    "Looks like something happened!",
                    "Enna da suddenly mood change?",
                ],
                condition=self._emotion_changed_significantly,
            ),
        ]

    def _emotion_changed_significantly(self) -> bool:
        if len(self._emotion_history) < 2:
            return False
        prev = self._emotion_history[-2][1]
        curr = self._emotion_history[-1][1]
        pos  = {"happy", "surprised"}
        neg  = {"sad", "angry", "fearful"}
        return (prev in neg and curr in pos) or (prev in pos and curr in neg)

    # ── Start / Stop ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True

        @bus.on(EventType.FACE_RECOGNIZED)
        async def _on_face(e: Event) -> None:
            self._current_person = e.data.get("person_id")
            self._last_person_seen = time.monotonic()
            self._no_person_since = time.monotonic() + 99999  # reset

        @bus.on(EventType.PERSON_LOST)
        async def _on_lost(e: Event) -> None:
            if self._current_person:
                self._no_person_since = time.monotonic()
            self._current_person = None
            self._current_emotion = None

        @bus.on(EventType.EMOTION_DETECTED)
        async def _on_emotion(e: Event) -> None:
            emotion = e.data.get("emotion", "neutral")
            self._current_emotion = emotion
            self._emotion_history.append((time.monotonic(), emotion))
            self._emotion_history = self._emotion_history[-10:]

        self._idle_task    = asyncio.create_task(self._idle_loop())
        self._trigger_task = asyncio.create_task(self._trigger_loop())
        log.info("behavior_engine.started")

    async def stop(self) -> None:
        self._running = False
        for t in [self._idle_task, self._trigger_task]:
            if t and not t.done():
                t.cancel()

    # ── Idle behavior loop ────────────────────────────────────────────────────

    async def _idle_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.IDLE_CHECK_INTERVAL + random.uniform(-1, 1))

            if self._current_person:  # don't wander when interacting
                continue

            ready = [b for b in self._behaviors if b.is_ready()]
            if not ready:
                continue

            total = sum(b.weight for b in ready)
            r = random.uniform(0, total)
            cumulative = 0.0
            chosen: Optional[Behavior] = None
            for b in ready:
                cumulative += b.weight
                if r <= cumulative:
                    chosen = b
                    break

            if chosen:
                chosen.mark_fired()
                try:
                    await chosen.execute()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.warning("behavior.execute_error",
                                name=chosen.name, error=str(e)[:60])

    # ── Proactive trigger loop ────────────────────────────────────────────────

    async def _trigger_loop(self) -> None:
        while self._running:
            await asyncio.sleep(10)
            for trigger in self._triggers:
                if trigger.is_ready() and trigger.condition():
                    trigger.mark_fired()
                    name = self._current_person or "friend"
                    person_rec = personality.get_person(name)
                    display_name = (person_rec.name if person_rec and person_rec.name
                                    else name)
                    phrase = trigger.pick_phrase(display_name)
                    log.info("behavior.proactive", trigger=trigger.name,
                             phrase=phrase[:40])
                    try:
                        await tts.speak(phrase)
                    except Exception as e:
                        log.warning("behavior.speak_error", error=str(e)[:60])

    # ── Behavior implementations ──────────────────────────────────────────────

    async def _look_around(self) -> None:
        from hardware.servos import servo_controller
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
        eye_engine.set_pupil(-0.8, 0)
        await asyncio.sleep(0.8)
        eye_engine.set_pupil(0.8, 0)
        await asyncio.sleep(0.8)
        eye_engine.set_pupil(0, 0)
        if servo_controller.is_mock:
            log.debug("behavior.look_around")
        else:
            await servo_controller.pan_to(servo_controller.PAN_MIN + 20)
            await asyncio.sleep(0.5)
            await servo_controller.pan_to(servo_controller.PAN_MAX - 20)
            await asyncio.sleep(0.5)
            await servo_controller.center()

    async def _slow_blink(self) -> None:
        eye_engine.set_expression(EyeExpression.SLEEPY, duration=2.0)
        await asyncio.sleep(2.0)
        eye_engine.set_expression(EyeExpression.NEUTRAL)

    async def _curious_sound(self) -> None:
        await sounds.play("chirp_curious")
        eye_engine.set_expression(EyeExpression.CURIOUS, duration=2.0)

    async def _wander(self) -> None:
        await navigation.wander(duration=20)

    async def _seek_attention(self) -> None:
        phrases = [
            "Aiyoh, romba bore aagudhu da... someone talk to me?",
            "Hellooo? Yaarum illaya?",
            "*makes puppy eyes*",
        ]
        await tts.speak(random.choice(phrases))
        eye_engine.set_expression(EyeExpression.SAD, duration=5.0)

    async def _breathe(self) -> None:
        eye_engine.set_expression(EyeExpression.NEUTRAL)

    # ── Public commands ───────────────────────────────────────────────────────

    async def dance(self) -> None:
        log.info("behavior.dance")
        eye_engine.set_expression(EyeExpression.EXCITED)
        await sounds.play("trill_excited")
        await navigation.turn_left(speed=0.4, duration=0.4)
        await navigation.turn_right(speed=0.4, duration=0.4)
        await navigation.turn_left(speed=0.4, duration=0.4)
        await navigation.turn_right(speed=0.4, duration=0.4)
        await asyncio.sleep(0.2)
        eye_engine.set_expression(EyeExpression.HAPPY, duration=3.0)

    async def happy_reaction(self) -> None:
        log.info("behavior.happy_reaction")
        eye_engine.set_expression(EyeExpression.HAPPY, duration=4.0)
        await sounds.play("chirp_happy")
        await tts.speak(random.choice([
            "Thankyu da! Naan try pannuven!",
            "Aiyo, neeye best da!",
            "Hehe, romba happy aagudhu!",
        ]))

    async def love_reaction(self) -> None:
        log.info("behavior.love_reaction")
        eye_engine.set_expression(EyeExpression.LOVING, duration=5.0)
        await sounds.play("purr_content")
        await tts.speak(random.choice([
            "Awww... I love you too da!",
            "*spins happily*",
            "En muththam! You are my favorite human da!",
        ]))


behavior_engine = BehaviorEngine()
