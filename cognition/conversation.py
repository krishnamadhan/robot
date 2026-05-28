"""
Conversation manager — orchestrates the full interaction loop.

Triggered by: person detected + input (keyboard now, wake word later).
Pipeline: context gather → LLM generate → TTS speak → update memory

Context assembly pulls from:
  - Working memory (current session)
  - Episodic memory (past interactions with this person)
  - Personality engine (current mood/energy)
  - Vision loop (who is present, their emotion)
  - Spatial memory (what room we're in)
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from cognition.llm import llm
from core.event_bus import Event, EventType, bus
from core.memory.episodic import Episode, episodic
from core.memory.working import wm
from core.personality import personality
from expression.speech import tts
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

MEMORY_RECALL_LIMIT = 3   # inject this many past memories into prompt


class ConversationManager:
    """
    Manages a conversation session with a person.

    Usage:
        cm = ConversationManager()
        response = await cm.respond("How are you feeling?", person_id="madhan")
        print(response["text"])
    """

    # Resume a prior thread within this window (seconds)
    THREAD_RESUME_WINDOW = 600

    def __init__(self) -> None:
        self._active_person_id: Optional[str] = None
        self._active_person_name: Optional[str] = None
        self._their_emotion: Optional[str] = None
        self._session_start: float = 0.0
        self._in_conversation = False
        self._respond_lock: Optional[asyncio.Lock] = None
        # Per-person thread cache: person_id → {history, last_active, name}
        self._threads: Dict[str, Dict] = {}

    @property
    def _lock(self) -> asyncio.Lock:
        if self._respond_lock is None:
            self._respond_lock = asyncio.Lock()
        return self._respond_lock

    def set_person(self, person_id: str, name: Optional[str], emotion: Optional[str] = None) -> None:
        self._active_person_id = person_id
        self._active_person_name = name or person_id
        self._their_emotion = emotion

    def set_emotion(self, emotion: str) -> None:
        self._their_emotion = emotion

    async def start_session(self, person_id: str, name: Optional[str] = None) -> None:
        if self._in_conversation:
            return
        self._active_person_id = person_id
        self._active_person_name = name
        self._session_start = time.monotonic()
        self._in_conversation = True

        # Resume prior thread if within the window, else start fresh
        thread = self._threads.get(person_id)
        now = time.monotonic()
        if thread and (now - thread["last_active"]) < self.THREAD_RESUME_WINDOW:
            wm.clear_conversation()
            for role, content in thread["history"]:
                wm.add_turn(role, content)
            log.info("conversation.resumed_thread", person_id=person_id,
                     turns=len(thread["history"]))
        else:
            wm.clear_conversation()
            log.info("conversation.started", person_id=person_id, name=name)

        await bus.publish(Event(
            type=EventType.CONVERSATION_START,
            data={"person_id": person_id, "name": name},
            source="conversation",
        ))

    async def end_session(self) -> None:
        if not self._in_conversation:
            return
        duration_s = time.monotonic() - self._session_start
        self._in_conversation = False

        # Store episode in long-term memory
        conv = wm.get_conversation()
        if conv:
            turns = len([m for m in conv if m["role"] == "user"])
            user_msgs = [m["content"] for m in conv if m["role"] == "user"]
            name = self._active_person_name or "someone"
            emotion = f" ({self._their_emotion})" if self._their_emotion else ""

            if user_msgs:
                first = user_msgs[0][:70].rstrip()
                if len(user_msgs[0]) > 70:
                    first += "…"
                if turns == 1:
                    summary = f'{name}{emotion} said: "{first}"'
                else:
                    summary = f'{name}{emotion}, {turns} turns — started: "{first}"'
            else:
                summary = f"Chat with {name}{emotion} ({turns} turns)"

            await episodic.store(Episode(
                episode_type="conversation",
                summary=summary,
                emotional_valence=personality.state.mood,
                importance=min(1.0, 0.3 + turns * 0.05),
                person_id=self._active_person_id,
                raw_data={
                    "turns": turns,
                    "duration_s": round(duration_s, 1),
                    "messages": user_msgs[:5],
                },
            ))

        # Save thread for possible resume within THREAD_RESUME_WINDOW
        if self._active_person_id and conv:
            # Keep last 10 turns (20 messages) to cap memory
            self._threads[self._active_person_id] = {
                "history": [(m["role"], m["content"]) for m in conv[-20:]],
                "last_active": time.monotonic(),
                "name": self._active_person_name,
            }

        wm.clear_conversation()
        await bus.publish(Event(
            type=EventType.CONVERSATION_END,
            data={"person_id": self._active_person_id, "duration_s": round(duration_s, 1)},
            source="conversation",
        ))
        log.info("conversation.ended", duration_s=round(duration_s, 1))

    async def respond(
        self,
        user_text: str,
        person_id: Optional[str] = None,
        speak: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate and optionally speak a Cosmo response.
        Returns dict with text, backend, latency_ms.
        """
        if self._lock.locked():
            log.warning("conversation.respond_dropped", reason="concurrent_call_in_progress")
            return {"text": "", "backend": "dropped", "latency_ms": 0}

        async with self._lock:
            return await self._respond_inner(user_text, person_id, speak)

    async def _respond_inner(
        self,
        user_text: str,
        person_id: Optional[str] = None,
        speak: bool = True,
    ) -> Dict[str, Any]:
        # Budget guard — audio pipeline bypasses mind.py directly; check here
        try:
            from cognition.mind import cosmo_mind
            if cosmo_mind._budget.over_limit():
                log.warning("conversation.budget_exceeded")
                return {"text": "", "backend": "budget_exceeded", "latency_ms": 0}
        except Exception:
            pass

        pid = person_id or self._active_person_id
        pname = self._active_person_name
        self._mood_before_respond = personality.state.mood

        # Build context for LLM
        context = await self._build_context(pid)

        # Get conversation history from working memory
        history = wm.get_conversation()

        # Generate response — use streaming for lower perceived latency
        t0 = time.monotonic()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        use_streaming = bool(api_key) and speak

        if use_streaming:
            sentence_gen = llm.generate_streaming(
                user_message=user_text,
                conversation_history=history,
                context=context,
            )
            response_text = await tts.speak_streaming(sentence_gen)
            result = {
                "text": response_text,
                "backend": f"claude/{llm._claude_model}+stream",
                "latency_ms": int((time.monotonic() - t0) * 1000),
            }
        else:
            result = await llm.generate(
                user_message=user_text,
                conversation_history=history,
                context=context,
            )
            response_text = result["text"]
            if speak:
                asyncio.create_task(tts.speak(response_text))

        # Update working memory
        wm.add_turn("user", user_text)
        wm.add_turn("assistant", response_text)

        # Store per-turn episode immediately (don't wait for session end)
        if pid and user_text.strip():
            asyncio.create_task(episodic.store(Episode(
                episode_type="conversation_turn",
                summary=(
                    f'{pname or "someone"} said: "{user_text[:80].rstrip()}"'
                    + (f'… Cosmo: "{response_text[:60].rstrip()}"' if response_text else "")
                ),
                emotional_valence=personality.state.mood,
                importance=0.4,
                person_id=pid,
            )))

        # Update personality — good interaction
        if pid:
            personality.update_person(pid, name=pname)
        personality.process_event("good_interaction", person_id=pid)

        # Trait drift — conversation = person engaged
        try:
            from core.personality import personality_learning
            mood_before = getattr(self, "_mood_before_respond", personality.state.mood)
            mood_delta = personality.state.mood - mood_before
            personality_learning.record_outcome(
                interaction_type="conversation",
                person_responded=bool(user_text.strip()),
                mood_delta=mood_delta,
            )
        except Exception:
            pass

        # Publish event
        await bus.publish(Event(
            type=EventType.RESPONSE_READY,
            data={"text": response_text, "backend": result.get("backend")},
            source="conversation",
        ))

        telemetry.increment("conversation.turns")
        log.info("conversation.response",
                  backend=result.get("backend"),
                  latency_ms=result.get("latency_ms"),
                  text_preview=response_text[:60])

        return result

    async def _build_context(self, person_id: Optional[str]) -> Dict[str, Any]:
        """Assemble context dict for LLM prompt injection."""
        persons_present = []
        if person_id:
            p = personality.get_person(person_id)
            name = p.name if p else (self._active_person_name or "someone")
            persons_present = [name]

        # Recall relevant memories — use recall_for_prompt for I5
        memories = ""
        if person_id:
            try:
                # Extract keywords from current user_text if available
                memories = await episodic.recall_for_prompt(
                    person_id=person_id,
                    limit=MEMORY_RECALL_LIMIT + 2,
                    max_chars=800,
                )
            except Exception:
                # Fallback to simple retrieve
                try:
                    episodes = await episodic.retrieve(
                        person_id=person_id,
                        limit=MEMORY_RECALL_LIMIT,
                        min_importance=0.2,
                    )
                    if episodes:
                        memories = "; ".join(e.summary[:80] for e in episodes)
                except Exception:
                    pass

        return {
            "mood_desc": personality.describe(),
            "persons_present": persons_present,
            "their_emotion": self._their_emotion,
            "memories": memories,
        }

    @property
    def in_conversation(self) -> bool:
        return self._in_conversation


conversation = ConversationManager()
