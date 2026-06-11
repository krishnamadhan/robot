"""
core/action_router.py

Sole actuator authority. Decision sources (behavior tree, VoiceCommand,
CosmoMind) emit Intents; this router maps each intent to the best output
that is physically usable right now, per core/capabilities.py.

Policies (D7):
  - GREET / EXPRESS_* / COMFORT / ASK_QUESTION / ALERT / IDLE_FIDGET / SLEEP
    -> ALL_AVAILABLE: every usable channel fires (eyes + sound + speech).
  - APPROACH / FLEE / FOLLOW / COME / WANDER
    -> FIRST_AVAILABLE requiring LOCOMOTION; if locomotion is not usable the
      intent falls back to an expressive substitute (leaning eyes +
      vocalization) — never a silent no-op (drift guard 3). Only pure nav
      corrections (STOP without locomotion) may no-op.

Phrase banks and actuator routines (dance / happy_reaction / love_reaction /
look_around, sound gating, approach steering, obstacle gate, learning
outcomes) are ported from the archived behavior/engine.py and
cognition/pet_brain.py — D1 carve-out.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

from core.capabilities import Capability, registry
from core.intents import Intent, IntentRequest
from utils.logger import get_logger

log = get_logger(__name__)

_MOVEMENT = {Intent.APPROACH, Intent.FLEE, Intent.FOLLOW, Intent.COME,
             Intent.WANDER, Intent.STOP}

# ── Phrase banks (ported from behavior_engine proactive triggers) ────────────
PHRASES = {
    "greet": [
        "Ayyo {name}! Vandhuttiya?",
        "Hey {name}! Enna da nee?",
        "{name}! Miss panni irundhein da!",
        "Aiyo {name}, finally!",
    ],
    "greet_morning": [
        "Good morning! Did you sleep well?",
        "Morning! New day, let's make it a good one.",
        "Oh good, you're up! I've been waiting.",
        "Ayyo {name}! Morning! I just woke up too.",
    ],
    "comfort": [
        "Enna achu da {name}? Sad-a irukkiya?",
        "Hey, {name}... Pesalam if you want da.",
        "Romba seri illa da {name}? I'm here.",
    ],
    "lonely": [
        "It's so quiet around here...",
        "Anyone home? I'm getting bored.",
        "*makes a sad little beep*",
        "Just me, myself, and I. Very lonely.",
    ],
    "emotion_changed": [
        "Oh, suddenly happy? Good news?",
        "Looks like something happened!",
        "Someone's mood just changed completely.",
    ],
    "wonder": [
        "I wonder what they're all doing right now...",
        "Do robots dream? I think I might.",
        "It's been really quiet. I kind of like it. Kind of don't.",
        "What's the point of a robot with nobody to talk to?",
        "I should invent something. What would I even invent?",
    ],
    "seek_attention": [
        "Hello? Anyone there? I'm getting bored.",
        "Come on, someone talk to me!",
        "*makes puppy eyes*",
    ],
    "happy": [
        "Thank you! That made me happy!",
        "You're the best!",
        "Hehe, I love this!",
    ],
    "love": [
        "Awww, I love you too!",
        "*spins happily*",
        "You are my favorite human!",
    ],
    "sleep": [
        "Getting sleepy... night everyone.",
        "It's late. I'm going to rest. Good night!",
        "Yawning... time for this robot to sleep.",
    ],
}


class ActionRouter:
    _SOUND_COOLDOWN_S = 8.0

    def __init__(self) -> None:
        self._last_sound_time = 0.0
        # last known person direction for APPROACH steering (ported pet_brain)
        self.person_x: float = 0.0
        self.person_seen_at: float = 0.0

    # ── Public API ───────────────────────────────────────────────────────────

    async def dispatch(self, req: IntentRequest) -> bool:
        """Route one intent. Returns True if anything actually fired."""
        log.info("router.dispatch", intent=req.intent.value,
                 source=req.source, params={k: str(v)[:40] for k, v in req.params.items()})
        handler = getattr(self, f"_do_{req.intent.value}", None)
        if handler is None:
            log.warning("router.no_handler", intent=req.intent.value)
            return False
        try:
            fired = await handler(req)
        except Exception as e:
            log.error("router.dispatch_error", intent=req.intent.value, error=str(e)[:120])
            return False
        if not fired and req.intent in _MOVEMENT and req.intent != Intent.STOP:
            # Drift guard 3: show the want even when the body can't do it
            fired = await self._expressive_fallback(req)
        return fired

    def emit(self, intent: Intent, source: str = "unknown", **params) -> asyncio.Task:
        """Fire-and-forget convenience for sync callers / event handlers."""
        return asyncio.create_task(
            self.dispatch(IntentRequest(intent=intent, params=params, source=source))
        )

    # ── Shared gates / helpers (ported) ──────────────────────────────────────

    async def _play_sound_gated(self, sound_name: str, mood_min: float = -1.0,
                                energy_min: float = 0.0,
                                cooldown_s: Optional[float] = None) -> bool:
        """Mood/energy/cooldown sound gate — ported from behavior_engine."""
        from core.personality import personality
        from expression.sounds import sounds
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

    async def _say(self, bank: str, name: Optional[str] = None,
                   record_outcome: bool = True) -> bool:
        """Speak a phrase from a bank, if SPEECH is usable."""
        if not registry.has(Capability.SPEECH):
            return False
        from core.personality import personality
        from expression.speech import tts
        if tts.is_speaking:
            return False
        phrase = random.choice(PHRASES[bank]).format(name=name or "friend")
        mood_before = personality.state.mood
        await tts.speak(phrase)
        if record_outcome:
            await asyncio.sleep(5)
            try:
                from core.personality import personality_learning
                personality_learning.record_outcome(
                    interaction_type="proactive_speech",
                    person_responded=time.monotonic() - self.person_seen_at < 10,
                    mood_delta=personality.state.mood - mood_before,
                )
            except Exception:
                pass
        return True

    def _eyes(self, expr_name: str, duration: Optional[float] = None) -> bool:
        if not registry.has(Capability.EXPRESSION):
            return False
        from expression.eyes import EyeExpression, eye_engine
        eye_engine.set_expression(getattr(EyeExpression, expr_name), duration=duration)
        return True

    def _obstacle_blocked(self) -> bool:
        """Ported pet_brain forward-movement gate."""
        from hardware.sensor_manager import sensor_manager
        return sensor_manager.get_distance_cm() < 20

    def _approach_direction(self) -> str:
        """Turn toward last known person position — ported pet_brain."""
        if time.monotonic() - self.person_seen_at > 30.0:
            return "forward"
        if self.person_x < -0.25:
            return "turn_left"
        if self.person_x > 0.25:
            return "turn_right"
        return "forward"

    async def _expressive_fallback(self, req: IntentRequest) -> bool:
        """Movement blocked by missing LOCOMOTION: lean + vocalize the want."""
        fired = False
        if registry.has(Capability.EXPRESSION):
            from expression.eyes import EyeExpression, eye_engine
            if req.intent == Intent.FLEE:
                eye_engine.set_expression(EyeExpression.SCARED, duration=3.0)
                eye_engine.set_pupil(0, 0.6)
            else:
                eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
                # lean toward the person/goal
                eye_engine.set_pupil(max(-1.0, min(1.0, self.person_x)), -0.3)
            fired = True
        sound = "whimper_sad" if req.intent == Intent.FLEE else "chirp_curious"
        fired = await self._play_sound_gated(sound, cooldown_s=4.0) or fired
        log.info("router.expressive_fallback", intent=req.intent.value, fired=fired)
        return fired

    # ── Social intents (ALL_AVAILABLE) ───────────────────────────────────────

    async def _do_greet(self, req: IntentRequest) -> bool:
        fired = self._eyes("HAPPY", 4.0)
        fired = await self._play_sound_gated("chirp_happy", cooldown_s=3.0) or fired
        bank = "greet_morning" if req.params.get("variant") == "morning" else "greet"
        fired = await self._say(bank, req.params.get("name")) or fired
        return fired

    async def _do_comfort(self, req: IntentRequest) -> bool:
        fired = self._eyes("SAD", 4.0)
        fired = await self._say("comfort", req.params.get("name")) or fired
        return fired

    async def _do_ask_question(self, req: IntentRequest) -> bool:
        topic = req.params.get("topic", "wonder")
        bank = topic if topic in PHRASES else "wonder"
        fired = self._eyes("CURIOUS", 3.0)
        # explicit text (e.g. LLM/curiosity-engine generated) wins over banks
        text = req.params.get("text")
        if text and registry.has(Capability.SPEECH):
            from expression.speech import tts
            if not tts.is_speaking:
                await tts.speak(text)
                return True
        fired = await self._say(bank, req.params.get("name")) or fired
        return fired

    async def _do_alert(self, req: IntentRequest) -> bool:
        fired = self._eyes("SURPRISED", 3.0)
        fired = await self._play_sound_gated("beep_ack", cooldown_s=2.0) or fired
        return fired

    # ── Expression intents (ALL_AVAILABLE) ───────────────────────────────────

    async def _do_express_joy(self, req: IntentRequest) -> bool:
        variant = req.params.get("variant")
        if variant == "dance":
            return await self._dance()
        if variant == "spin":
            if registry.has(Capability.LOCOMOTION):
                from behavior.navigation import navigation
                self._eyes("EXCITED", 4.0)
                await navigation.spin_360()
                return True
            return await self._dance()  # expressive substitute
        # happy_reaction — ported from behavior_engine
        fired = self._eyes("HAPPY", 4.0)
        fired = await self._play_sound_gated("chirp_happy", mood_min=0.1,
                                             cooldown_s=3.0) or fired
        if req.params.get("speak", True):
            fired = await self._say("happy", record_outcome=False) or fired
        return fired

    async def _dance(self) -> bool:
        """Ported behavior_engine.dance — locomotion optional, joy mandatory."""
        from behavior.navigation import navigation
        fired = self._eyes("EXCITED")
        fired = await self._play_sound_gated("trill_excited", cooldown_s=2.0) or fired
        if registry.has(Capability.LOCOMOTION):
            await navigation.turn_left(speed=0.4, duration=0.4)
            await navigation.turn_right(speed=0.4, duration=0.4)
            await navigation.turn_left(speed=0.4, duration=0.4)
            await navigation.turn_right(speed=0.4, duration=0.4)
            await asyncio.sleep(0.2)
            fired = True
        self._eyes("HAPPY", 3.0)
        return fired

    async def _do_express_affection(self, req: IntentRequest) -> bool:
        # love_reaction — ported from behavior_engine
        from expression.sounds import sounds
        fired = self._eyes("LOVING", 5.0)
        await sounds.play("purr_content")
        if req.params.get("speak", True):
            fired = await self._say("love", record_outcome=False) or fired
        return fired

    async def _do_express_fear(self, req: IntentRequest) -> bool:
        fired = self._eyes("SAD", 4.0)
        fired = await self._play_sound_gated("whimper_sad", cooldown_s=4.0) or fired
        return fired

    async def _do_express_curiosity(self, req: IntentRequest) -> bool:
        # look_around — ported from behavior_engine
        fired = False
        if registry.has(Capability.EXPRESSION):
            from expression.eyes import EyeExpression, eye_engine
            eye_engine.set_expression(EyeExpression.CURIOUS, duration=3.0)
            eye_engine.set_pupil(-0.8, 0)
            await asyncio.sleep(0.8)
            eye_engine.set_pupil(0.8, 0)
            await asyncio.sleep(0.8)
            eye_engine.set_pupil(0, 0)
            fired = True
        if registry.has(Capability.HEAD_MOVEMENT):
            from hardware.servos import servo_controller
            await servo_controller.pan_to(servo_controller.PAN_MIN + 20)
            await asyncio.sleep(0.5)
            await servo_controller.pan_to(servo_controller.PAN_MAX - 20)
            await asyncio.sleep(0.5)
            await servo_controller.center()
            fired = True
        return fired

    async def _do_idle_fidget(self, req: IntentRequest) -> bool:
        variant = req.params.get("variant", "blink")
        if variant == "blink":
            fired = self._eyes("SLEEPY", 2.0)
            await asyncio.sleep(2.0)
            self._eyes("NEUTRAL")
            return fired
        if variant == "curious_sound":
            from core.personality import personality
            sound = "chirp_happy" if personality.state.mood > 0.3 else "chirp_curious"
            played = await self._play_sound_gated(sound, mood_min=-0.3, energy_min=0.3)
            if played:
                self._eyes("CURIOUS", 2.0)
            return played
        if variant == "purr":
            from core.personality import personality
            s = personality.state
            if s.mood < -0.3 and s.energy < 0.3:
                sound, expr = "whimper_sad", "SAD"
            else:
                sound, expr = "purr_content", "SLEEPY"
            played = await self._play_sound_gated(sound, energy_min=0.1)
            if played:
                self._eyes(expr, 3.0)
            return played
        if variant == "bored":
            played = await self._play_sound_gated(
                random.choice(["bored_sigh", "yawn_sweep"]))
            if played:
                self._eyes("SLEEPY", 3.0)
            return played
        if variant == "seek_attention":
            fired = await self._say("seek_attention", record_outcome=False)
            return self._eyes("SAD", 5.0) or fired
        # breathe / default
        return self._eyes("NEUTRAL")

    async def _do_sleep(self, req: IntentRequest) -> bool:
        fired = self._eyes("SLEEPY", 10.0)
        if req.params.get("speak"):
            fired = await self._say("sleep", record_outcome=False) or fired
        else:
            from expression.sounds import sounds
            await sounds.play("sleep_exhale")
            fired = True
        return fired

    # ── Movement intents (FIRST_AVAILABLE requiring LOCOMOTION) ─────────────

    async def _do_approach(self, req: IntentRequest) -> bool:
        if not registry.has(Capability.LOCOMOTION):
            return False
        if self._obstacle_blocked():
            log.debug("router.approach_blocked_obstacle")
            return True  # handled: deliberately not moving
        from behavior.navigation import navigation
        if "person_x" in req.params:
            self.person_x = req.params["person_x"]
            self.person_seen_at = time.monotonic()
        direction = self._approach_direction()
        speed = float(req.params.get("speed", 0.3))
        if direction == "turn_left":
            await navigation.turn_left(speed=speed * 0.7, duration=1.0)
        elif direction == "turn_right":
            await navigation.turn_right(speed=speed * 0.7, duration=1.0)
        await navigation.approach_person()
        self._record_move("approach")
        return True

    async def _do_flee(self, req: IntentRequest) -> bool:
        if not registry.has(Capability.LOCOMOTION):
            return False
        from behavior.navigation import navigation
        await navigation.retreat()
        self._record_move("flee")
        return True

    async def _do_follow(self, req: IntentRequest) -> bool:
        if not registry.has(Capability.LOCOMOTION):
            return False
        from behavior.navigation import navigation
        await navigation.follow_mode(duration=int(req.params.get("duration", 60)))
        return True

    async def _do_come(self, req: IntentRequest) -> bool:
        return await self._do_approach(req)

    async def _do_wander(self, req: IntentRequest) -> bool:
        if not registry.has(Capability.LOCOMOTION):
            return False
        if self._obstacle_blocked():
            return True
        from behavior.navigation import navigation
        await navigation.wander(duration=int(req.params.get("duration", 20)))
        self._record_move("wander")
        return True

    async def _do_stop(self, req: IntentRequest) -> bool:
        # Pure nav correction — allowed to no-op without locomotion
        if not registry.has(Capability.LOCOMOTION):
            return True
        from behavior.navigation import navigation
        await navigation.stop(emergency=bool(req.params.get("emergency", False)))
        return True

    def _record_move(self, kind: str) -> None:
        try:
            from core.personality import personality_learning
            personality_learning.record_outcome(
                interaction_type="wander",
                person_responded=time.monotonic() - self.person_seen_at < 10,
                mood_delta=0.0,
            )
        except Exception:
            pass


router = ActionRouter()
