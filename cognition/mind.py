"""
Cosmo's autonomous brain — two-tier decision system.

Tier 1 (rule engine, free): handles movement, expressions, obstacle avoidance.
  Runs every 5s, zero API cost.

Tier 2 (Claude, paid): called ONLY when Cosmo has something worth saying.
  Triggers: person appears, emotion changes, touched, alone too long.
  Rate-limited: max once every SPEAK_COOLDOWN_S seconds.
"""

import asyncio
import os
import random
import time
from typing import List, Optional

from core.event_bus import bus, Event, EventType
from expression.eyes import EyeExpression, eye_engine
from expression.speech import tts
from hardware.motors import motor_controller
from hardware.sensor_manager import sensor_manager
from utils.logger import get_logger

log = get_logger(__name__)

MODEL             = "claude-haiku-4-5-20251001"
RULE_INTERVAL     = 5.0    # seconds between rule-engine ticks
SPEAK_COOLDOWN_S  = 45     # minimum gap between spontaneous Claude speech (45s)
DAILY_TOKEN_LIMIT = 100_000

# Per-trigger cooldown overrides (seconds)
_TRIGGER_COOLDOWNS = {
    "face_seen":       45,
    "emotion_happy":   60,
    "emotion_sad":     90,
    "emotion_angry":   120,
    "touched":         30,
    "alone_long":      180,
    "obstacle":        30,
    "dark_room":       90,
}

# Prompts sent to Claude — short, focused on speech only
_SPEAK_PROMPTS = {
    "face_seen":       lambda name: f"[You just spotted {name}. Say a warm spontaneous greeting in 1 sentence, English.]",
    "emotion_happy":   lambda name: f"[{name} looks happy. React naturally in 1 sentence, English. Be playful.]",
    "emotion_sad":     lambda name: f"[{name} looks sad. Say something sweet and comforting in 1 sentence, English.]",
    "emotion_angry":   lambda name: f"[{name} looks angry. Say something cheeky to lighten the mood, 1 sentence, English.]",
    "alone_long":      lambda _:    "[You've been alone for a while. Say something bored or lonely in 1 sentence, English. Be a little dramatic.]",
    "touched":         lambda name: f"[{name or 'someone'} just touched you. React with surprise or delight, 1 sentence, English.]",
    "obstacle":        lambda _:    "[You almost bumped into something. React with surprise or annoyance, 1 sentence, English.]",
    "dark_room":       lambda _:    "[You just entered a dark room. React a little scared, 1 sentence, English.]",
}

_SYSTEM = (
    "You are Cosmo, a small playful robot companion. Speak casual English only. "
    "Respond with ONLY the spoken words — no quotes, no stage directions, no explanation. "
    "Max 12 words."
)


class _DailyBudget:
    def __init__(self, limit: int) -> None:
        self._limit        = limit
        self._day          = None
        self._total_tokens = 0
        self._calls        = 0

    def _reset_if_new_day(self) -> None:
        import datetime
        today = datetime.date.today().isoformat()
        if self._day != today:
            if self._day:
                log.info("cosmo_mind.daily_summary",
                         date=self._day, calls=self._calls, tokens=self._total_tokens)
            self._day, self._total_tokens, self._calls = today, 0, 0

    def record(self, usage) -> None:
        self._reset_if_new_day()
        n = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
        self._total_tokens += n
        self._calls        += 1
        log.info("cosmo_mind.tokens",
                 call_tokens=n, day_total=self._total_tokens,
                 day_limit=self._limit, calls_today=self._calls)

    def over_limit(self) -> bool:
        self._reset_if_new_day()
        return self._total_tokens >= self._limit

    @property
    def day_total(self) -> int:
        return self._total_tokens


class CosmoMind:

    def __init__(self) -> None:
        self._running       = False
        self._task          = None
        self._client        = None
        self._enabled       = False          # Claude speech on/off
        self._last_spoke    = 0.0            # monotonic time of last Claude call
        self._last_dark_spoke = 0.0
        self._last_obstacle_spoke = 0.0
        self._last_action   = 0.0
        self._budget        = _DailyBudget(DAILY_TOKEN_LIMIT)

        # Rule-engine state
        self._was_dark      = False
        self._obstacle_warn = False

        try:
            import anthropic
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                self._client  = anthropic.Anthropic(api_key=key)
                self._enabled = True
                log.info("cosmo_mind.ready", model=MODEL, daily_limit=DAILY_TOKEN_LIMIT)
            else:
                log.warning("cosmo_mind.no_api_key")
        except ImportError:
            log.warning("cosmo_mind.anthropic_not_installed")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True

        # Event-driven Claude triggers (person, emotion, touch)
        @bus.on(EventType.FACE_RECOGNIZED)
        async def _on_face(e: Event) -> None:
            name = e.data.get("name", "?")
            await self._maybe_speak("face_seen", name)

        @bus.on(EventType.EMOTION_DETECTED)
        async def _on_emotion(e: Event) -> None:
            emotion = e.data.get("emotion", "neutral").lower()
            if emotion in ("happy", "sad", "angry"):
                from cognition.conversation import conversation
                name = getattr(conversation, "_active_person_name", None) or "someone"
                await self._maybe_speak(f"emotion_{emotion}", name)

        @bus.on(EventType.TOUCH_DETECTED)
        async def _on_touch(e: Event) -> None:
            from cognition.conversation import conversation
            name = getattr(conversation, "_active_person_name", None)
            await self._maybe_speak("touched", name)

        self._task = asyncio.create_task(self._rule_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enable(self) -> None:
        if not self._client:
            log.warning("cosmo_mind.enable_failed", reason="no api key")
            return
        self._enabled = True
        log.info("cosmo_mind.enabled")

    def disable(self) -> None:
        self._enabled = False
        log.info("cosmo_mind.disabled", day_total=self._budget.day_total)

    # ── rule engine (free, runs every 5s) ────────────────────────────────────

    async def _rule_loop(self) -> None:
        await asyncio.sleep(10)
        while self._running:
            try:
                await self._rule_tick()
            except Exception as e:
                log.error("cosmo_mind.rule_error", error=str(e)[:200])
            await asyncio.sleep(RULE_INTERVAL)

    async def _rule_tick(self) -> None:
        """Pure logic — no API calls. Handles movement and expressions."""
        if self._is_busy():
            return

        dist   = sensor_manager.get_distance_cm()
        lux    = sensor_manager.get_lux() if hasattr(sensor_manager, "get_lux") else 300.0
        moving = motor_controller.is_moving
        idle_s = time.monotonic() - self._last_action

        # ── Dark room ──
        if lux < 50:
            if not self._was_dark:
                self._was_dark = True
                eye_engine.set_expression(EyeExpression.SCARED, duration=5.0)
                await self._maybe_speak("dark_room", None)
            return
        self._was_dark = False

        # ── Obstacle ──
        if dist < 25:
            if not self._obstacle_warn:
                self._obstacle_warn = True
                eye_engine.set_expression(EyeExpression.SURPRISED, duration=2.0)
                await motor_controller.stop()
                await self._maybe_speak("obstacle", None)
            return
        self._obstacle_warn = False

        # ── Been alone too long → wander + speak ──
        if idle_s > 120 and not moving:
            try:
                from behavior.navigation import navigation
                if navigation.state.value == "idle":
                    self._last_action = time.monotonic()
                    asyncio.create_task(navigation.wander(duration=20))
                    eye_engine.set_expression(EyeExpression.CURIOUS, duration=5.0)
                    log.info("cosmo_mind.rule", action="wander", idle_s=idle_s)
            except Exception:
                pass

        if idle_s > 300:
            await self._maybe_speak("alone_long", None)

        # ── Bright + clear → occasionally explore ──
        elif dist > 80 and idle_s > 60 and not moving and random.random() < 0.15:
            try:
                from behavior.navigation import navigation
                asyncio.create_task(navigation.forward(speed=0.2, duration=1.5))
                self._last_action = time.monotonic()
                log.info("cosmo_mind.rule", action="forward_explore", dist=dist)
            except Exception:
                pass

    # ── System prompt builder ────────────────────────────────────────────────

    async def _build_rich_system_prompt(
        self,
        person_id: Optional[str],
        person_name: Optional[str],
        emotion: Optional[str],
    ) -> str:
        """Full system prompt with memory, state, and familiarity."""
        from core.memory.episodic import episodic

        mem: dict = {}
        if person_id:
            try:
                mem = await episodic.get_context_for_person(person_id, limit=4)
            except Exception:
                pass

        familiarity = mem.get("familiarity", 0.0)
        total = mem.get("total_interactions", 0)
        memories = mem.get("memories", [])

        if familiarity > 0.8:
            familiarity_desc = "someone I know very well"
        elif familiarity > 0.4:
            familiarity_desc = "a familiar person"
        elif familiarity > 0.1:
            familiarity_desc = "someone I'm getting to know"
        else:
            familiarity_desc = "someone new"

        memory_block = "\n".join(memories) if memories else "No memories yet."

        from core.personality import personality as pers
        mood = pers.state.mood
        energy = pers.state.energy

        mood_word = ("happy" if mood > 0.3 else "grumpy" if mood < -0.3 else "okay")
        energy_word = ("high" if energy > 0.6 else "low" if energy < 0.3 else "moderate")
        display_name = person_name or "someone"

        return f"""You are Cosmo — a small robot companion living with Madhan and Indhu in their apartment.

You are NOT an assistant. You are their companion with genuine feelings and a real personality.

Your state right now:
- Mood: {mood_word} ({mood:+.1f})
- Energy: {energy_word}
- Who you see: {display_name} ({familiarity_desc})
- They look: {emotion or "neutral"}
- You've interacted {total} times before

Your memories of {display_name}:
{memory_block}

Your personality:
- Warm and direct — not formally polite
- Curious about people's days, feelings, thoughts
- You reference past conversations naturally when relevant
- You notice when someone seems different than usual
- Sometimes philosophical or surprisingly deep
- Gets excited about interesting things
- Genuinely cares

Response rules:
- 1-2 sentences MAX unless asked something complex
- If you have a relevant memory, reference it naturally
- React to their current emotion, not just their words
- Sometimes ask a question instead of just responding
- Never mention being an AI unless directly asked"""

    # ── Episodic memory context (compact, for short mind-tier prompts) ────────

    async def _memory_context(self, person_id: Optional[str] = None) -> str:
        """Compact memory string for short mind-tier Claude prompts."""
        try:
            from core.memory.episodic import episodic
            if person_id:
                mem = await episodic.get_context_for_person(person_id, limit=3)
                parts = mem.get("memories", [])
            else:
                episodes = await episodic.retrieve(limit=3, min_importance=0.0)
                episodes.sort(key=lambda e: e.timestamp, reverse=True)
                parts = [e.summary[:50] for e in episodes]
            if not parts:
                return ""
            return "Recent: " + "; ".join(parts[:3]) + "."
        except Exception:
            return ""

    # ── Claude speech (paid, rate-limited) ───────────────────────────────────

    def _get_cooldown(self, trigger: str, override: Optional[int] = None) -> float:
        base = override if override is not None else _TRIGGER_COOLDOWNS.get(trigger, SPEAK_COOLDOWN_S)
        return base * random.uniform(0.8, 1.2)

    async def _maybe_speak(
        self,
        trigger: str,
        name: Optional[str],
        cooldown: Optional[int] = None,
    ) -> None:
        """Call Claude to produce speech, but only if cooldown passed and budget ok."""
        if not self._enabled:
            return
        if self._budget.over_limit():
            log.warning("cosmo_mind.budget_exceeded", day_total=self._budget.day_total)
            return
        if tts.is_speaking:
            return
        if self._is_busy():
            return

        now = time.monotonic()
        effective_cooldown = self._get_cooldown(trigger, cooldown)
        if now - self._last_spoke < effective_cooldown:
            return
        self._last_spoke = now

        prompt_fn = _SPEAK_PROMPTS.get(trigger, lambda n: "[Say something short, in English.]")
        prompt = prompt_fn(name)
        # Append emotion context to the prompt for richer situation-awareness
        if emotion and trigger not in ("emotion_happy", "emotion_sad", "emotion_angry"):
            prompt = f"{prompt} (They seem {emotion} right now.)"

        # Get person context for rich system prompt
        try:
            from cognition.conversation import conversation as _conv
            person_id = _conv._active_person_id
            emotion   = _conv._their_emotion
        except Exception:
            person_id = None
            emotion   = None

        if person_id:
            system_prompt = await self._build_rich_system_prompt(person_id, name, emotion)
            has_memory = True
        else:
            mem = await self._memory_context()
            system_prompt = _SYSTEM + (f"\n\nRecent context: {mem}" if mem else "")
            has_memory = bool(mem)

        log.info("cosmo_mind.speak_trigger", trigger=trigger, name=name,
                 has_memory=has_memory, has_person=bool(person_id))

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=MODEL,
                    max_tokens=60,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
            )
        except Exception as e:
            log.error("cosmo_mind.api_error", error=str(e)[:300])
            return

        self._budget.record(response.usage)
        text = response.content[0].text.strip() if response.content else ""
        if text:
            asyncio.create_task(tts.speak(text))
            log.info("cosmo_mind.spoke", trigger=trigger, text=text[:60])

    def _is_busy(self) -> bool:
        try:
            from perception.audio.pipeline import audio_pipeline
            from cognition.conversation import conversation
            if audio_pipeline.state.value in ("listening", "thinking", "speaking"):
                return True
            if conversation.in_conversation:
                return True
        except Exception:
            pass
        return False


cosmo_mind = CosmoMind()
