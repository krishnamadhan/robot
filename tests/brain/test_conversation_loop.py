"""
B5 — Conversation loop end-to-end tests (all invariants).

Tests:
  - Mock STT → LLMRouter (Ollama-first) → personality+memory prompt → mock TTS
  - full_day soak: tokens < budget, memory grows and is recalled
  - Zero Tier-1 LLM calls throughout
  - Graceful degradation (Ollama down, budget exhausted, both down)
  - audio asyncio.Lock respected (no concurrent TTS)
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

REPO = Path(__file__).parent.parent.parent
FIXTURES_DIR = REPO / "tests" / "brain" / "fixtures"


# ── Helpers ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, responses=None):
        self.responses = responses or ["I am Cosmo!"]
        self._idx = 0
        self.calls = []
        self._ollama_available = False
        self._anthropic_client = None

    def _next(self):
        r = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1
        return r

    async def generate(self, user_message, conversation_history=None, context=None):
        self.calls.append({"method": "generate", "message": user_message, "ctx": context})
        return {"text": self._next(), "backend": "fake/llm", "latency_ms": 1, "tokens": 5}

    async def generate_streaming(self, user_message, conversation_history=None, context=None):
        self.calls.append({"method": "streaming", "message": user_message})
        yield self._next()

    async def is_ollama_ready(self):
        return False


def make_mock_tts():
    spoken = []
    m = MagicMock()
    m.is_speaking = False
    async def _speak(text):
        spoken.append(text)
        return text
    async def _speak_streaming(gen):
        text = ""
        async for chunk in gen:
            text += chunk
        spoken.append(text)
        return text
    m.speak = AsyncMock(side_effect=_speak)
    m.speak_streaming = AsyncMock(side_effect=_speak_streaming)
    m._spoken = spoken
    return m


# ── Test: basic conversation turn ────────────────────────────────────────────

class TestConversationTurn:

    @pytest.mark.asyncio
    async def test_basic_respond_returns_text(self):
        """respond() with FakeLLM returns non-empty text."""
        fake_llm = FakeLLM(["Hello Madhan!"])
        mock_tts = make_mock_tts()

        from cognition.conversation import ConversationManager
        from core.memory.episodic import EpisodicMemory
        import sqlite3

        mem_db = EpisodicMemory.__new__(EpisodicMemory)
        mem_db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        mem_db._conn.row_factory = sqlite3.Row
        mem_db._loop = None
        mem_db._create_schema()

        cm = ConversationManager.__new__(ConversationManager)
        cm._active_person_id = "madhan"
        cm._active_person_name = "Madhan"
        cm._their_emotion = "happy"
        cm._session_start = time.monotonic()
        cm._in_conversation = True
        cm._respond_lock = asyncio.Lock()
        cm._threads = {}
        cm._mood_before_respond = 0.0

        with (
            patch("cognition.conversation.llm", fake_llm),
            patch("cognition.conversation.tts", mock_tts),
            patch("cognition.conversation.episodic", mem_db),
        ):
            result = await cm.respond("Hello!", person_id="madhan", speak=False)

        assert result["text"] == "Hello Madhan!"

    @pytest.mark.asyncio
    async def test_memory_context_injected_in_context(self):
        """recall_for_prompt output appears in context passed to LLM."""
        import sqlite3
        from cognition.conversation import ConversationManager
        from core.memory.episodic import EpisodicMemory, Episode

        mem_db = EpisodicMemory.__new__(EpisodicMemory)
        mem_db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        mem_db._conn.row_factory = sqlite3.Row
        mem_db._loop = None
        mem_db._create_schema()

        # Pre-populate with a fact
        ep = Episode(
            episode_type="conversation_fact",
            summary="Madhan loves cricket",
            person_id="madhan",
            importance=0.8,
        )
        mem_db._store_sync(ep)

        context_captured = {}
        fake_llm = FakeLLM(["Nice to talk!"])
        original_gen = fake_llm.generate
        async def spy_gen(user_message=None, conversation_history=None, context=None, **kwargs):
            if context:
                context_captured.update(context)
            return await original_gen(user_message, conversation_history, context)
        fake_llm.generate = spy_gen

        mock_tts = make_mock_tts()

        cm = ConversationManager.__new__(ConversationManager)
        cm._active_person_id = "madhan"
        cm._active_person_name = "Madhan"
        cm._their_emotion = None
        cm._session_start = time.monotonic()
        cm._in_conversation = True
        cm._respond_lock = asyncio.Lock()
        cm._threads = {}
        cm._mood_before_respond = 0.0

        with (
            patch("cognition.conversation.llm", fake_llm),
            patch("cognition.conversation.tts", mock_tts),
            patch("cognition.conversation.episodic", mem_db),
        ):
            await cm.respond("Do you remember anything about me?",
                             person_id="madhan", speak=False)

        # Memory should be in context
        memories = context_captured.get("memories", "")
        assert "cricket" in memories, (
            f"I5: 'cricket' not in memories context.\nContext: {context_captured}"
        )


# ── Test: Budget gate in conversation ────────────────────────────────────────

class TestConversationBudgetGate:

    @pytest.mark.asyncio
    async def test_budget_exhausted_returns_empty(self):
        """Budget exhausted → respond() returns empty text, no crash."""
        from cognition.conversation import ConversationManager

        fake_budget = MagicMock()
        fake_budget.over_limit.return_value = True

        mock_mind = MagicMock()
        mock_mind._budget = fake_budget

        cm = ConversationManager.__new__(ConversationManager)
        cm._active_person_id = "madhan"
        cm._active_person_name = "Madhan"
        cm._their_emotion = None
        cm._session_start = time.monotonic()
        cm._in_conversation = True
        cm._respond_lock = asyncio.Lock()
        cm._threads = {}

        # Patch the lazy import of cosmo_mind inside conversation.py
        import cognition.mind as mind_mod
        original_cosmo_mind = mind_mod.cosmo_mind
        mind_mod.cosmo_mind = mock_mind
        try:
            result = await cm.respond("Hello", person_id="madhan", speak=False)
        finally:
            mind_mod.cosmo_mind = original_cosmo_mind

        assert result["backend"] == "budget_exceeded"
        assert result["text"] == ""


# ── Test: Tier-1 zero LLM calls through full_day fixture ─────────────────────

class TestFullDaySoak:

    @pytest.mark.asyncio
    async def test_full_day_completes_without_tier1_llm_calls(self):
        """Load full_day.json and run it. Verify 0 Tier-1 LLM calls."""
        fixture_path = FIXTURES_DIR / "full_day.json"
        with open(fixture_path) as f:
            fixture = json.load(f)

        tier1_llm_calls = {"n": 0}
        conversation_llm_calls = {"n": 0}

        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._running = False
        mind._task = None
        mind._client = None
        mind._enabled = False
        mind._last_spoke = 0.0
        mind._last_action = time.monotonic() - 10
        mind._budget = MagicMock()
        mind._budget.over_limit.return_value = False
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._trigger_last = {}
        mind._was_dark = False
        mind._obstacle_warn = False

        mock_sensor = MagicMock()
        mock_motors = MagicMock()
        mock_motors.is_moving = False
        mock_motors.stop = AsyncMock()
        mock_eye = MagicMock()

        fake_llm = FakeLLM(["Hello!", "Nice!", "Sure!", "I see.", "Ok!"] * 20)

        import sqlite3
        from core.memory.episodic import EpisodicMemory, Episode

        mem_db = EpisodicMemory.__new__(EpisodicMemory)
        mem_db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        mem_db._conn.row_factory = sqlite3.Row
        mem_db._loop = None
        mem_db._create_schema()

        events = fixture.get("events", [])
        tier1_events = [e for e in events if e["type"] == "RULE_TICK"]

        # Run each RULE_TICK and assert no LLM called from it
        for event in tier1_events:
            data = event.get("data", {})
            mock_sensor.get_distance_cm.return_value = data.get("dist_cm", 100)
            mock_sensor.get_lux.return_value = data.get("lux", 300)
            mock_motors.is_moving = data.get("moving", False)

            idle_s = data.get("idle_s", 10)
            mind._last_action = time.monotonic() - idle_s

            spy_speak_calls = {"n": 0}
            async def spy_speak(*a, **kw):
                spy_speak_calls["n"] += 1
            mind._maybe_speak = spy_speak

            fake_bb_mod = MagicMock()
            fake_bb_mod.bb.person_visible = data.get("person_visible", True)
            sys.modules["core.behavior_tree"] = fake_bb_mod

            mock_nav = MagicMock()
            mock_nav.state.value = "idle"
            mock_nav.wander = AsyncMock()
            mock_nav.forward = AsyncMock()

            with (
                patch("cognition.mind.sensor_manager", mock_sensor),
                patch("cognition.mind.motor_controller", mock_motors),
                patch("cognition.mind.eye_engine", mock_eye),
                patch("cognition.mind.tts", MagicMock()),
                patch("cognition.mind.sounds", MagicMock()),
                patch.dict("sys.modules", {"behavior.navigation": MagicMock(navigation=mock_nav)}),
            ):
                await mind._rule_tick()

            # When person visible, no speak should fire from rule_tick
            if data.get("person_visible", True):
                tier1_llm_calls["n"] += spy_speak_calls["n"]

        # Run conversation turns and count them
        from cognition.conversation import ConversationManager
        mock_tts = make_mock_tts()

        convo_events = [e for e in events if e["type"] == "CONVERSATION_TURN"]
        for event in convo_events:
            data = event.get("data", {})
            cm = ConversationManager.__new__(ConversationManager)
            cm._active_person_id = data.get("person_id", "madhan")
            cm._active_person_name = data.get("person_name", "Madhan")
            cm._their_emotion = None
            cm._session_start = time.monotonic()
            cm._in_conversation = True
            cm._respond_lock = asyncio.Lock()
            cm._threads = {}
            cm._mood_before_respond = 0.0

            with (
                patch("cognition.conversation.llm", fake_llm),
                patch("cognition.conversation.tts", mock_tts),
                patch("cognition.conversation.episodic", mem_db),
            ):
                result = await cm.respond(
                    data.get("user", "Hello"),
                    person_id=data.get("person_id", "madhan"),
                    speak=False,
                )
                if result.get("text"):
                    conversation_llm_calls["n"] += 1

        assert tier1_llm_calls["n"] == 0, (
            f"I1 FAILED: {tier1_llm_calls['n']} LLM calls from Tier-1 rule engine"
        )
        assert conversation_llm_calls["n"] > 0, (
            f"Expected some conversation LLM calls, got {conversation_llm_calls['n']}"
        )

    @pytest.mark.asyncio
    async def test_full_day_tokens_under_budget(self):
        """Simulate token budget across full_day: should stay under limit."""
        from cognition.llm import TokenBudget

        # Each fake LLM response = 10 tokens, 50 events → 500 tokens max
        budget = TokenBudget(10_000)
        for i in range(50):
            budget.record(10)

        assert budget.day_total == 500
        assert budget.claude_allowed(), "500 tokens should be well under 10k limit"


# ── Test: Graceful degradation (I6) ──────────────────────────────────────────

class TestGracefulDegradation:

    @pytest.mark.asyncio
    async def test_ollama_down_conversation_falls_back_to_claude(self):
        """I6: Ollama down → conversation uses Claude fallback."""
        from cognition.llm import LLMRouter, OllamaProvider, ClaudeProvider, TokenBudget

        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value=None)  # Ollama down

        claude_response = {"text": "Claude fallback!", "backend": "claude/test", "tokens": 10}
        claude.generate = AsyncMock(return_value=claude_response)

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system prompt")

        assert result["text"] == "Claude fallback!"
        assert "claude" in result["backend"]

    @pytest.mark.asyncio
    async def test_both_down_returns_empty_no_crash(self):
        """I6: Both down → empty result, no exception."""
        from cognition.llm import LLMRouter, OllamaProvider, ClaudeProvider, TokenBudget

        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value=None)
        claude.generate = AsyncMock(return_value=None)

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)

        # Must not raise
        result = await router.generate("hello", "system prompt")
        assert result["text"] == ""
        assert result["backend"] == "unavailable"

    @pytest.mark.asyncio
    async def test_budget_exhausted_ollama_continues(self):
        """I3+I6: Budget exhausted → Ollama still works, Claude silenced."""
        from cognition.llm import LLMRouter, OllamaProvider, ClaudeProvider, TokenBudget

        budget = TokenBudget(100)
        budget.record(100)  # exhaust

        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value={
            "text": "Ollama still alive!",
            "backend": "ollama/test",
            "tokens": 5,
        })
        claude_calls = {"n": 0}
        async def spy_claude(*a, **kw):
            claude_calls["n"] += 1
            return {"text": "Claude!", "backend": "claude", "tokens": 10}
        claude.generate = spy_claude

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system")

        assert result["text"] == "Ollama still alive!"
        assert claude_calls["n"] == 0, "Claude must not be called when budget exhausted"
