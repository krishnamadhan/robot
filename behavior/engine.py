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

    # Global sound cooldown — prevents multiple sounds in quick succession
    _SOUND_COOLDOWN_S = 8.0

    # Curiosity engine cooldowns
    _CURIOSITY_COOLDOWN_S    = 300  # 5 min between curiosity questions
    _MEMORY_BRING_UP_S       = 600  # 10 min between memory references
    _AMBIENT_TICK_S          = 30   # ambient awareness check interval
    _AMBIENT_ACT_COOLDOWN_S  = 120  # min gap between ambient-driven actions

    def __init__(self) -> None:
        self._last_sound_time: float = 0.0
        self._running          = False
        self._idle_task: Optional[asyncio.Task] = None
        self._trigger_task: Optional[asyncio.Task] = None
        self._ambient_task: Optional[asyncio.Task] = None
        self._current_person:  Optional[str] = None
        self._current_person_name: Optional[str] = None
        self._current_emotion: Optional[str] = None
        self._last_person_seen: float = 0.0
        self._no_person_since: float = time.monotonic()
        self._emotion_history: List[tuple] = []
        self._last_curiosity: float = 0.0
        self._last_memory_ref: float = 0.0
        self._last_ambient_act: float = 0.0

        # ── Idle behaviors ────────────────────────────────────────────────────
        self._behaviors: List[Behavior] = [
            Behavior("look_left_right", 0.30, 15,  self._look_around,  energy_min=0.2),
            Behavior("slow_blink",      0.25,  8,  self._slow_blink),
            Behavior("curious_sound",   0.25, 15,  self._curious_sound, energy_min=0.4),
            Behavior("purr_idle",       0.20, 40,  self._purr_idle,     energy_max=0.5),
            Behavior("wander",          0.10, 60,  self._wander,        energy_min=0.5),
            Behavior("seek_attention",  0.20, 300, self._seek_attention, energy_max=0.15),
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
                    "It's so quiet around here...",
                    "Anyone home? I'm getting bored.",
                    "*makes a sad little beep*",
                    "Just me, myself, and I. Very lonely.",
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
                    "Good morning! Did you sleep well?",
                    "Morning! New day, let's make it a good one.",
                    "Oh good, you're up! I've been waiting.",
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
                    "Oh, suddenly happy? Good news?",
                    "Looks like something happened!",
                    "Someone's mood just changed completely.",
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
            self._current_person_name = e.data.get("name") or e.data.get("person_id")
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
        self._ambient_task = asyncio.create_task(self._ambient_loop())
        log.info("behavior_engine.started")

    async def stop(self) -> None:
        self._running = False
        for t in [self._idle_task, self._trigger_task, self._ambient_task]:
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
                if not trigger.is_ready() or not trigger.condition():
                    continue
                if tts.is_speaking:
                    continue
                trigger.mark_fired()
                name = self._current_person or "friend"
                person_rec = personality.get_person(name)
                display_name = (person_rec.name if person_rec and person_rec.name
                                else name)
                phrase = trigger.pick_phrase(display_name)
                log.info("behavior.proactive", trigger=trigger.name,
                         phrase=phrase[:40])
                try:
                    mood_before = personality.state.mood
                    await tts.speak(phrase)
                    # Record outcome after a brief window — person present = responded
                    await asyncio.sleep(5)
                    try:
                        from core.personality import personality_learning
                        personality_learning.record_outcome(
                            interaction_type="proactive_speech",
                            person_responded=bool(self._current_person),
                            mood_delta=personality.state.mood - mood_before,
                        )
                    except Exception:
                        pass
                except Exception as e:
                    log.warning("behavior.speak_error", error=str(e)[:60])

    # ── Ambient awareness loop ────────────────────────────────────────────────

    async def _ambient_loop(self) -> None:
        """Every 30s, assess the situation and potentially act without prompting."""
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._ambient_tick()
            except Exception as e:
                log.warning("behavior.ambient_error", error=str(e)[:100])
            await asyncio.sleep(self._AMBIENT_TICK_S)

    async def _ambient_tick(self) -> None:
        now = time.monotonic()
        if now - self._last_ambient_act < self._AMBIENT_ACT_COOLDOWN_S:
            return
        try:
            from cognition.conversation import conversation as _conv
            if _conv.in_conversation:
                return
        except Exception:
            pass

        # With person present: curiosity engine
        if self._current_person:
            await self._curiosity_tick(now)
        # Alone: wonder aloud occasionally
        elif now - self._no_person_since > 1200 and random.random() < 0.2:
            await self._wonder_aloud()

    async def _curiosity_tick(self, now: float) -> None:
        """Proactively engage the person with a question or memory reference."""
        if now - self._last_curiosity < self._CURIOSITY_COOLDOWN_S:
            if now - self._last_memory_ref < self._MEMORY_BRING_UP_S:
                return
            # Try memory reference
            await self._bring_up_memory()
            return

        # Ask a curiosity question based on current emotion / time of day
        await self._ask_curiosity_question()

    async def _ask_curiosity_question(self) -> None:
        import datetime
        hour = datetime.datetime.now().hour
        emotion = self._current_emotion or "neutral"
        name = self._current_person_name or "there"

        if emotion in ("sad", "fearful"):
            questions = [
                f"Hey {name}... you okay? Want to talk about it?",
                f"You seem a bit down {name}. What's going on?",
            ]
        elif hour < 12:
            questions = [
                f"Morning {name}! How'd you sleep?",
                f"Hey, big plans today {name}?",
            ]
        elif hour >= 18:
            questions = [
                f"How was your day {name}?",
                f"Tired? You look like you've been busy {name}.",
            ]
        else:
            questions = [
                f"What are you up to {name}?",
                f"Hey {name}, you seem distracted. Everything alright?",
                f"What's on your mind {name}?",
            ]

        phrase = random.choice(questions)
        self._last_curiosity = time.monotonic()
        self._last_ambient_act = time.monotonic()
        log.info("behavior.curiosity_question", phrase=phrase[:50])
        await tts.speak(phrase)

    async def _bring_up_memory(self) -> None:
        """Reference a past memory naturally."""
        try:
            from core.memory.episodic import episodic
            person_id = self._current_person
            episodes = await episodic.retrieve(person_id=person_id, limit=3,
                                               min_importance=0.3)
            if not episodes:
                return
            ep = random.choice(episodes)
            name = self._current_person_name or "you"
            phrase = f"Hey {name}, remember when {ep.summary.lower()[:60]}? I think about that sometimes."
        except Exception:
            return

        self._last_memory_ref = time.monotonic()
        self._last_ambient_act = time.monotonic()
        log.info("behavior.memory_reference")
        await tts.speak(phrase)

    async def _wonder_aloud(self) -> None:
        """Philosophical/bored observation when alone."""
        wonders = [
            "I wonder what they're all doing right now...",
            "Do robots dream? I think I might.",
            "It's been really quiet. I kind of like it. Kind of don't.",
            "What's the point of a robot with nobody to talk to?",
            "I should invent something. What would I even invent?",
        ]
        self._last_ambient_act = time.monotonic()
        await tts.speak(random.choice(wonders))

    # ── Sound gate ───────────────────────────────────────────────────────────

    async def _play_sound_gated(
        self,
        sound_name: str,
        mood_min: float = -1.0,
        energy_min: float = 0.0,
        cooldown_s: float = None,
    ) -> bool:
        """Play sound only if personality state and cooldown allow it."""
        cooldown = cooldown_s if cooldown_s is not None else self._SOUND_COOLDOWN_S
        now = time.monotonic()
        if now - self._last_sound_time < cooldown:
            return False
        if personality.state.mood < mood_min:
            return False
        if personality.state.energy < energy_min:
            return False
        self._last_sound_time = now
        await sounds.play(sound_name)
        return True

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
        mood = personality.state.mood
        # Pick sound based on mood: happy curiosity vs anxious curiosity
        sound = "chirp_happy" if mood > 0.3 else "chirp_curious"
        played = await self._play_sound_gated(sound, mood_min=-0.3, energy_min=0.3)
        if played:
            eye_engine.set_expression(EyeExpression.CURIOUS, duration=2.0)

    async def _purr_idle(self) -> None:
        mood = personality.state.mood
        energy = personality.state.energy
        # Sad + low energy → whimper; else content purr
        if mood < -0.3 and energy < 0.3:
            sound = "whimper_sad"
            expr = EyeExpression.SAD
        else:
            sound = "purr_content"
            expr = EyeExpression.SLEEPY
        played = await self._play_sound_gated(sound, energy_min=0.1)
        if played:
            eye_engine.set_expression(expr, duration=3.0)

    async def _wander(self) -> None:
        await navigation.wander(duration=20)

    async def _seek_attention(self) -> None:
        phrases = [
            "Hello? Anyone there? I'm getting bored.",
            "Come on, someone talk to me!",
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
        await self._play_sound_gated("trill_excited", cooldown_s=2.0)
        await navigation.turn_left(speed=0.4, duration=0.4)
        await navigation.turn_right(speed=0.4, duration=0.4)
        await navigation.turn_left(speed=0.4, duration=0.4)
        await navigation.turn_right(speed=0.4, duration=0.4)
        await asyncio.sleep(0.2)
        eye_engine.set_expression(EyeExpression.HAPPY, duration=3.0)

    async def happy_reaction(self) -> None:
        log.info("behavior.happy_reaction")
        eye_engine.set_expression(EyeExpression.HAPPY, duration=4.0)
        await self._play_sound_gated("chirp_happy", mood_min=0.1, cooldown_s=3.0)
        await tts.speak(random.choice([
            "Thank you! That made me happy!",
            "You're the best!",
            "Hehe, I love this!",
        ]))

    async def love_reaction(self) -> None:
        log.info("behavior.love_reaction")
        eye_engine.set_expression(EyeExpression.LOVING, duration=5.0)
        await sounds.play("purr_content")
        await tts.speak(random.choice([
            "Awww, I love you too!",
            "*spins happily*",
            "You are my favorite human!",
        ]))


behavior_engine = BehaviorEngine()
