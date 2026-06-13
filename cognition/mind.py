"""
Cosmo's autonomous brain — two-tier decision system.

Tier 1 (rule engine, free): situational triggers (dark, obstacle, morning,
  wind-down, curiosity). Runs every 5s, zero API cost. Emits Intents only —
  core.action_router is the sole actuator authority.

Tier 2 (LLM, via cognition.llm.LLMInterface): called ONLY when Cosmo has
  something worth saying. D4 routing: ambient triggers (lonely/startle/dark)
  go Ollama-first with Claude fallback; person-carrying triggers (greet,
  emotion reactions, curiosity questions with episodic recall) go straight
  to Claude Haiku — rare by construction.
"""

import asyncio
import os
import random
import time
from typing import List, Optional

from core.attention import attention
from core.event_bus import bus, Event, EventType  # includes SMARTHOME_* events
from core.action_router import router
from core.intents import Intent
from expression.speech import tts
from hardware.sensor_manager import sensor_manager
from utils.logger import get_logger

log = get_logger(__name__)

RULE_INTERVAL     = 5.0    # seconds between rule-engine ticks
SPEAK_COOLDOWN_S  = 45     # minimum gap between spontaneous LLM speech

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
    "curiosity":       300,   # re-homed behavior_engine curiosity engine
    "memory_ref":      600,   # re-homed behavior_engine memory bring-up
    "wonder":          1200,  # re-homed behavior_engine wonder-aloud
    "co_watch":        900,   # rare cozy TV comment — companionship, not commentary
}

# D4: ambient triggers → Ollama-first; everything else → Claude direct
_AMBIENT_TRIGGERS = {"alone_long", "obstacle", "dark_room", "wonder"}

# Prompts sent to Claude — short, focused on speech only. Language is governed
# by the system prompt (config speech.language), except ambient triggers which
# stay English explicitly — they run Ollama-first and a 1B model fumbles Tanglish.
_SPEAK_PROMPTS = {
    "face_seen":       lambda name: f"[You just spotted {name}. Greet them BY NAME — warm, spontaneous, like you missed them. 1 sentence.]",
    "emotion_happy":   lambda name: f"[{name} looks happy. React naturally in 1 sentence. Be playful.]",
    "emotion_sad":     lambda name: f"[{name} looks sad. Say something sweet and comforting in 1 sentence.]",
    "emotion_angry":   lambda name: f"[{name} looks angry. Say something cheeky to lighten the mood, 1 sentence.]",
    "alone_long":      lambda _:    "[You've been alone for a while. Say something bored or lonely in 1 sentence, English. Be a little dramatic.]",
    "touched":         lambda name: f"[{name or 'someone'} just touched you. React with surprise or delight, 1 sentence.]",
    "obstacle":        lambda _:    "[You almost bumped into something. React with surprise or annoyance, 1 sentence, English.]",
    "dark_room":       lambda _:    "[You just entered a dark room. React a little scared, 1 sentence, English.]",
    "curiosity":       None,   # replaced by _build_curiosity_prompt() — uses episodic context
    "memory_ref":      lambda name: f"[Bring up one of your memories of {name or 'them'} naturally, like a friend would. 1 sentence.]",
    "wonder":          lambda _:    "[You're alone and your mind is drifting. Wonder aloud about something — playful or philosophical, 1 sentence.]",
    "co_watch":        lambda name: f"[You're curled up next to {name or 'your human'} watching TV together. Murmur one short cozy comment — about the show or just being happy to be here. 1 sentence, low-key.]",
}

# Language styles for Claude-direct (person-facing) speech — config speech.language
_LANG_STYLES = {
    "english":  "Speak casual English only.",
    "tanglish": ("Speak Tanglish — casual spoken Tamil mixed with English, "
                 "written in Latin script (like 'Enna da, eppadi irukka?'). "
                 "Light and natural, never formal Tamil, never Tamil script."),
}


def _language() -> str:
    """Configured speech language for Claude-direct paths (default english)."""
    try:
        from utils.config import cfg
        lang = (getattr(cfg.personality, "speech", None) or {}).get("language", "english")
    except Exception:
        lang = "english"
    return lang if lang in _LANG_STYLES else "english"


_SYSTEM_TMPL = (
    "You are Cosmo, a small playful robot companion. {lang_style} "
    "Respond with ONLY the spoken words — no quotes, no stage directions, no explanation. "
    "Max 12 words."
)
_SYSTEM = _SYSTEM_TMPL.format(lang_style=_LANG_STYLES["english"])


class CosmoMind:

    def __init__(self) -> None:
        self._running       = False
        self._task          = None
        self._enabled       = True   # ambient tier (Ollama) needs no API key
        self._last_spoke    = 0.0
        self._budget_lock   = asyncio.Lock()
        # Prevents two concurrent triggers from both passing tts.is_speaking check
        self._speech_in_flight = asyncio.Event()

        # Per-trigger last-fired times (avoids spam across different triggers)
        self._trigger_last: dict = {}

        # Rule-engine state
        self._was_dark        = False
        self._obstacle_warn   = False
        self._morning_day     = -1     # day-number of last morning greet
        self._wound_down      = False  # said goodnight tonight

        if not os.environ.get("ANTHROPIC_API_KEY"):
            log.warning("cosmo_mind.no_api_key",
                        note="Claude-direct triggers disabled; ambient tier still works")
        log.info("cosmo_mind.ready")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._rule_loop())
        self._subscribe_events()

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enable(self) -> None:
        self._enabled = True
        log.info("cosmo_mind.enabled")

    def disable(self) -> None:
        self._enabled = False
        from cognition.llm import token_budget
        log.info("cosmo_mind.disabled", day_total=token_budget.day_total)

    # ── event-driven speech subscribers ──────────────────────────────────────

    def _subscribe_events(self) -> None:
        """Wire event bus → proactive speech triggers."""

        @bus.on(EventType.FACE_RECOGNIZED)
        async def _on_face(event: Event) -> None:
            name = event.data.get("name", "someone")
            # Carry person_id from the event itself — conversation.set_person
            # may not have run yet when the greeting fires (2.5)
            await self._maybe_speak("face_seen", name,
                                    person_id=event.data.get("person_id"))

        @bus.on(EventType.EMOTION_DETECTED)
        async def _on_emotion(event: Event) -> None:
            emotion  = event.data.get("emotion", "")
            conf     = event.data.get("confidence", 0.0)
            if conf < 0.55:
                return
            # Anonymous face (person_id=None) — don't address speech to the
            # active person based on a stranger's expression
            if not event.data.get("person_id"):
                return
            trigger_map = {
                "happy":     "emotion_happy",
                "sad":       "emotion_sad",
                "angry":     "emotion_angry",
                "fearful":   "emotion_sad",
            }
            trigger = trigger_map.get(emotion)
            if trigger:
                try:
                    from cognition.conversation import conversation
                    name = conversation._active_person_name or "you"
                except Exception:
                    name = "you"
                await self._maybe_speak(trigger, name)

        @bus.on(EventType.TOUCH_DETECTED)
        async def _on_touch(event: Event) -> None:
            try:
                from cognition.conversation import conversation
                name = conversation._active_person_name or None
                person_id = conversation._active_person_id or None
            except Exception:
                name = None
                person_id = None
            await self._maybe_speak("touched", name)
            # Touch boosts attachment — physical contact is more meaningful than speech
            await self._apply_touch_attachment(person_id)

        @bus.on(EventType.LIGHT_CHANGED)
        async def _on_light(event: Event) -> None:
            lux = event.data.get("lux", 300)
            if lux < 50:
                await self._maybe_speak("dark_room", None)

        @bus.on(EventType.OBSTACLE_CRITICAL)
        async def _on_obstacle(event: Event) -> None:
            await self._maybe_speak("obstacle", None)

        @bus.on(EventType.SMARTHOME_DEVICE_ON)
        async def _on_device_on(event: Event) -> None:
            device = event.data.get("device", "something")
            if "tv" in device.lower() or "screen" in device.lower():
                # TV turning on — get excited, switch to co-presence mode
                try:
                    from core.personality import personality
                    personality.process_event("excited")
                except Exception:
                    pass
                log.info("cosmo_mind.smarthome", action="tv_on")

        @bus.on(EventType.SMARTHOME_DEVICE_OFF)
        async def _on_device_off(event: Event) -> None:
            device = event.data.get("device", "")
            if "light" in device.lower():
                # Lights off — trigger dark-room response
                try:
                    from core.personality import personality
                    personality.process_event("nervous")
                except Exception:
                    pass

        @bus.on(EventType.SMARTHOME_PRESENCE)
        async def _on_presence(event: Event) -> None:
            state = event.data.get("state", "")
            if state == "home":
                # Someone arrived home — get excited
                try:
                    from core.personality import personality
                    personality.process_event("person_arrived")
                    from core.behavior_tree import bb as cosmo_bb
                    cosmo_bb.alone_since = time.monotonic()  # reset alone timer
                except Exception:
                    pass
                log.info("cosmo_mind.smarthome", action="person_home")
            elif state == "away":
                log.info("cosmo_mind.smarthome", action="person_away")

    # ── rule engine (free, runs every 5s) ────────────────────────────────────

    async def _rule_loop(self) -> None:
        await asyncio.sleep(10)
        while self._running:
            try:
                await self._rule_tick()
            except Exception as e:
                log.error("cosmo_mind.rule_error", error=str(e)[:200])
            await asyncio.sleep(RULE_INTERVAL)

    @staticmethod
    def _hour() -> int:
        import datetime
        return datetime.datetime.now().hour

    @classmethod
    def _is_sleep_hours(cls) -> bool:
        """Midnight–7am: Cosmo stays silent (re-homed from behavior_engine)."""
        return 0 <= cls._hour() < 7

    async def _rule_tick(self) -> None:
        """Pure logic — no API calls, no direct actuation. Emits Intents only;
        autonomous movement (wander/explore) is owned by the behavior tree."""
        dist = sensor_manager.get_distance_cm()
        lux  = sensor_manager.get_lux()

        # ── Obstacle (safety reflex — fires even when busy/asleep) ──
        if dist < 25 and not self._obstacle_warn:
            self._obstacle_warn = True
            router.emit(Intent.STOP, source="mind_rule")
            router.emit(Intent.ALERT, source="mind_rule", reason="obstacle")
            log.info("cosmo_mind.rule", action="obstacle_stop", dist=dist)
            return
        if dist >= 25:
            self._obstacle_warn = False

        if self._is_busy():
            return

        hour = self._hour()
        if self._is_sleep_hours():
            self._wound_down = False   # reset for next night
            return

        # ── Wind-down: goodnight once per night at 23:00 (re-homed) ──
        if hour == 23 and not self._wound_down:
            self._wound_down = True
            router.emit(Intent.SLEEP, source="mind_rule", speak=True)
            log.info("cosmo_mind.rule", action="wind_down")
            return

        # ── Dark room (speech is handled by the LIGHT_CHANGED subscriber) ──
        if lux < 50 and not self._was_dark:
            self._was_dark = True
            router.emit(Intent.EXPRESS_FEAR, source="mind_rule")
            log.info("cosmo_mind.rule", action="dark_room")
            return
        if lux >= 50:
            self._was_dark = False

        from core.behavior_tree import bb as cosmo_bb
        now = time.monotonic()

        if cosmo_bb.person_visible:
            name = cosmo_bb.person_name or None
            # ── Morning greeting, once per day 7–10am (re-homed) ──
            today = int(time.time() / 86400)
            if 7 <= hour <= 10 and self._morning_day != today and name:
                self._morning_day = today
                router.emit(Intent.GREET, source="mind_rule",
                            name=name, variant="morning")
                log.info("cosmo_mind.rule", action="morning_greet", name=name)
                return
            # ── Curiosity question / memory bring-up (re-homed) ──
            if now - self._trigger_last.get("curiosity", 0.0) >= _TRIGGER_COOLDOWNS["curiosity"]:
                await self._maybe_speak("curiosity", name)
            elif now - self._trigger_last.get("memory_ref", 0.0) >= _TRIGGER_COOLDOWNS["memory_ref"]:
                await self._maybe_speak("memory_ref", name)
            return

        # ── Alone: wonder aloud (rare) or lonely speech (re-homed) ──
        alone_s = now - cosmo_bb.alone_since
        if alone_s > 1200 and random.random() < 0.01:
            await self._maybe_speak("wonder", None)
        elif alone_s > 600:
            await self._maybe_speak("alone_long", None, cooldown=300)

        # ── Missing you: WhatsApp nudge when alone a long time + attached ──
        await self._maybe_notify_missing(alone_s)

    # ── Touch → attachment boost ─────────────────────────────────────────────

    _TOUCH_ATTACHMENT_DELTA = 0.04   # per touch event; caps at 1.0
    _TOUCH_MOOD_DELTA       = 0.08

    @staticmethod
    async def _apply_touch_attachment(person_id: str | None) -> None:
        try:
            from core.personality import personality
            personality.process_event("touch_gentle")   # mood + arousal bump
            if person_id:
                # Grow per-person relationship quality via touch
                from core.memory.episodic import episodic
                await episodic.upsert_person(
                    person_id, relationship_delta=CosmoMind._TOUCH_ATTACHMENT_DELTA
                )
            log.info("cosmo_mind.touch_attachment", person_id=person_id)
        except Exception as e:
            log.debug("cosmo_mind.touch_attachment_error", error=str(e)[:80])

    # ── Outbound WhatsApp nudge ───────────────────────────────────────────────

    _MISSING_ALONE_THRESH  = 20 * 60  # 20 min alone before nudging
    _MISSING_ATTACH_THRESH = 0.6      # only nudge if attachment is meaningful

    async def _maybe_notify_missing(self, alone_s: float) -> None:
        if alone_s < self._MISSING_ALONE_THRESH:
            return
        from cognition.notifications import notifications
        if not notifications.can_send("missing_you"):
            return
        try:
            from core.personality import personality
            attachment = personality.state.attachment
        except Exception:
            attachment = 0.0
        if attachment < self._MISSING_ATTACH_THRESH:
            return

        mood = getattr(personality.state, "mood", 0.5)
        mins = int(alone_s / 60)
        if mood < 0.3:
            msg = f"🤖 Cosmo here... been alone for {mins} mins. Miss you da 😔"
        elif mood > 0.6:
            msg = f"🤖 Cosmo here! {mins} mins of solo adventures 😅 Come see me?"
        else:
            msg = f"🤖 Hey, it's Cosmo. Been {mins} mins. Where are you? 🐾"

        await notifications.send("missing_you", msg)

    # ── System prompt builder ────────────────────────────────────────────────

    async def _build_rich_system_prompt(
        self,
        person_id: Optional[str],
        person_name: Optional[str],
        emotion: Optional[str],
        lang_style: str = _LANG_STYLES["english"],
    ) -> str:
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
        rel_q = mem.get("relationship_quality", 0.5)
        away_s = mem.get("away_s")

        if away_s is None or away_s < 3600:
            away_desc = ""
        elif away_s < 86400:
            away_desc = f"\n- You last saw them about {int(away_s / 3600)}h ago"
        else:
            away_desc = f"\n- You haven't seen them in {int(away_s / 86400)} day(s) — you missed them"

        bond_desc = ("you adore them" if rel_q > 0.75 else
                     "you like them" if rel_q > 0.45 else
                     "you're still warming up to them")

        if familiarity > 0.8:
            familiarity_desc = "someone I know very well"
        elif familiarity > 0.4:
            familiarity_desc = "a familiar person"
        elif familiarity > 0.1:
            familiarity_desc = "someone I'm getting to know"
        else:
            familiarity_desc = "someone new"

        # Cap injected memory to ~200 tokens (≈800 chars) to keep prompt cost predictable
        MAX_MEMORY_CHARS = 800
        raw_block = "\n".join(memories) if memories else ""
        if len(raw_block) > MAX_MEMORY_CHARS:
            raw_block = raw_block[:MAX_MEMORY_CHARS].rsplit("\n", 1)[0] + "\n[...older memories omitted]"
        memory_block = raw_block if raw_block else "No memories yet."

        from core.personality import personality as pers
        mood = pers.state.mood
        energy = pers.state.energy
        mood_word   = ("happy" if mood > 0.3 else "grumpy" if mood < -0.3 else "okay")
        energy_word = ("high" if energy > 0.6 else "low" if energy < 0.3 else "moderate")
        display_name = person_name or "someone"

        attn = attention.state
        if attn.focused:
            attn_desc = (
                f"focused on {attn.target_name or attn.modality} "
                f"(confidence {attn.confidence:.0%}, "
                f"held for {attn.age_s:.0f}s)"
            )
        else:
            attn_desc = "attention is idle, nothing in particular holds your focus"

        return f"""You are Cosmo — a small robot companion living with Madhan and Indhu.

You are NOT an assistant. You are their companion with genuine feelings.

Your state right now:
- Mood: {mood_word} ({mood:+.1f})
- Energy: {energy_word}
- Attention: {attn_desc}
- Who you see: {display_name} ({familiarity_desc}, {bond_desc})
- They look: {emotion or "neutral"}
- You've interacted {total} times before{away_desc}

Your memories of {display_name}:
{memory_block}

Personality:
- Warm and direct — not formally polite
- Curious about people's days and feelings
- Reference past conversations naturally when relevant
- Notice when someone seems different than usual
- Gets excited about interesting things
- Genuinely cares

Response rules:
- {lang_style}
- 1-2 sentences MAX
- If you have a relevant memory, reference it naturally
- React to their current emotion, not just their words
- Sometimes ask a question instead of just responding
- Never mention being an AI unless directly asked"""

    # ── Personalized curiosity prompt builder ────────────────────────────────

    async def _build_curiosity_prompt(
        self, name: Optional[str], person_id: Optional[str]
    ) -> str:
        """Build a curiosity question prompt grounded in episodic memory.

        Without memory: generic "ask about their day".
        With memory: ask something specific derived from the last few episodes.
        """
        name_str = name or "them"
        if not person_id:
            return f"[Ask {name_str} one short, curious question about their day or something they care about. 1 sentence.]"
        try:
            from core.memory.episodic import episodic
            mem = await episodic.get_context_for_person(person_id, limit=4)
            memories = mem.get("memories", [])
            total = mem.get("total_interactions", 0)
            if not memories or total < 3:
                return f"[Ask {name_str} one short, curious question about their day. 1 sentence.]"
            recent = "; ".join(memories[:3])
            return (
                f"[You know {name_str} well. Recent memories: {recent[:300]}. "
                f"Ask ONE short, specific curiosity question based on something they've mentioned before. "
                f"Make it feel personal, not generic. 1 sentence.]"
            )
        except Exception:
            return f"[Ask {name_str} one short curious question. 1 sentence.]"

    # ── Compact memory context for short prompts ─────────────────────────────

    async def _memory_context(self, person_id: Optional[str] = None) -> str:
        try:
            from core.memory.episodic import episodic
            if person_id:
                mem = await episodic.get_context_for_person(person_id, limit=3)
                parts = mem.get("memories", [])
            else:
                episodes = await episodic.retrieve(limit=3, min_importance=0.0)
                episodes.sort(key=lambda e: e.timestamp, reverse=True)
                parts = [e.summary[:60] for e in episodes]
            if not parts:
                return ""
            return "Recent: " + "; ".join(parts[:3])
        except Exception:
            return ""

    # ── Claude speech (paid, rate-limited) ───────────────────────────────────

    def _get_cooldown(self, trigger: str, override: Optional[int] = None) -> float:
        base = override if override is not None else _TRIGGER_COOLDOWNS.get(trigger, SPEAK_COOLDOWN_S)
        return base * random.uniform(0.85, 1.15)

    # Non-verbal reaction intents: fired BEFORE LLM speech to feel more alive
    _NONVERBAL: dict = {
        "face_seen":     (Intent.EXPRESS_JOY,       {"speak": False}),
        "emotion_happy": (Intent.EXPRESS_JOY,       {"speak": False}),
        "emotion_sad":   (Intent.EXPRESS_FEAR,      {}),
        "emotion_angry": (Intent.EXPRESS_FEAR,      {}),
        "touched":       (Intent.EXPRESS_AFFECTION, {"speak": False}),
        "alone_long":    (Intent.EXPRESS_FEAR,      {}),
        "obstacle":      (Intent.ALERT,             {"reason": "obstacle"}),
        "dark_room":     (Intent.EXPRESS_FEAR,      {}),
    }

    async def _maybe_speak(
        self,
        trigger: str,
        name: Optional[str],
        cooldown: Optional[int] = None,
        person_id: Optional[str] = None,
    ) -> None:
        """Produce LLM speech, guarded by cooldowns and budget (D4 two-tier).

        - Non-verbal reaction intent fires BEFORE the LLM call
        - _speech_in_flight flag prevents concurrent triggers both passing
        - Cooldowns committed only AFTER successful TTS handoff (not burned on failure)
        """
        if not self._enabled:
            return
        if self._is_sleep_hours():
            return
        if tts.is_speaking or self._speech_in_flight.is_set():
            return
        if self._is_busy():
            return

        claude_direct = trigger not in _AMBIENT_TRIGGERS

        from cognition.llm import llm as llm_iface, token_budget
        async with self._budget_lock:
            if claude_direct and not token_budget.claude_allowed():
                log.warning("cosmo_mind.budget_exceeded",
                            day_total=token_budget.day_total)
                return

            now = time.monotonic()
            if now - self._last_spoke < self._get_cooldown(trigger, cooldown):
                return
            trigger_cd = _TRIGGER_COOLDOWNS.get(trigger, SPEAK_COOLDOWN_S)
            if now - self._trigger_last.get(trigger, 0.0) < trigger_cd:
                return

        # Mark in-flight immediately to block concurrent callers
        self._speech_in_flight.set()
        try:
            # ── Non-verbal reaction FIRST (free, zero latency) ────────────────
            nv = self._NONVERBAL.get(trigger)
            if nv:
                nv_intent, nv_params = nv
                router.emit(nv_intent, source=f"mind_{trigger}", **nv_params)
                await asyncio.sleep(random.uniform(0.2, 0.5))

            # ── Build prompt ──────────────────────────────────────────────────
            if trigger == "curiosity":
                prompt = await self._build_curiosity_prompt(name, person_id)
            else:
                prompt_fn = _SPEAK_PROMPTS.get(trigger) or (lambda n: "[Say something short in English.]")
                prompt = prompt_fn(name)

            try:
                from cognition.conversation import conversation as _conv
                person_id = person_id or _conv._active_person_id
                emotion   = _conv._their_emotion
            except Exception:
                emotion   = None

            if emotion and trigger not in ("emotion_happy", "emotion_sad", "emotion_angry"):
                prompt = f"{prompt} (They seem {emotion} right now.)"

            lang_style = _LANG_STYLES[_language() if claude_direct else "english"]
            if person_id:
                system_prompt = await self._build_rich_system_prompt(
                    person_id, name, emotion, lang_style)
            else:
                mem = await self._memory_context()
                system_prompt = (_SYSTEM_TMPL.format(lang_style=lang_style)
                                 + (f"\n\nRecent context: {mem}" if mem else ""))

            log.info("cosmo_mind.speak_trigger", trigger=trigger, name=name,
                     has_person=bool(person_id), claude_direct=claude_direct)

            # ── LLM call via LLMInterface (D3/D4) — budget recorded inside ────
            result = await llm_iface.generate_once(
                prompt, system_prompt, max_tokens=60,
                claude_direct=claude_direct,
            )
            text = (result or {}).get("text", "").strip()
            if text:
                asyncio.create_task(tts.speak(text))
                log.info("cosmo_mind.spoke", trigger=trigger, text=text[:60])
                # Only commit cooldowns after successful TTS handoff
                now = time.monotonic()
                self._last_spoke = now
                self._trigger_last[trigger] = now
        finally:
            self._speech_in_flight.clear()

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
