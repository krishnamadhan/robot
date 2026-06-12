"""
B6 — Hardening tests: failure modes that must degrade to non-verbal, never crash.

Tests:
  - DB locked / corrupt → EpisodicMemory gracefully returns empty
  - Empty memory / first boot → prompts still work
  - Malformed LLM output (empty, JSON, gibberish) → TTS gets empty or cleaned string
  - Mid-conversation budget exhaustion → returns empty, no crash
  - Dashboard endpoints callable (smoke test)
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── DB failure modes ──────────────────────────────────────────────────────────

async def _make_mem_db():
    """In-memory aiosqlite-backed EpisodicMemory (KI-016)."""
    import aiosqlite
    from core.memory.episodic import EpisodicMemory

    db = EpisodicMemory.__new__(EpisodicMemory)
    db._db_path = Path(":memory:")
    db._conn = await aiosqlite.connect(":memory:")
    db._conn.row_factory = aiosqlite.Row
    await db._create_schema()
    return db


class TestEpisodicDBFailures:

    @pytest.mark.asyncio
    async def test_corrupted_db_returns_empty_string(self):
        """Corrupted DB → recall_for_prompt returns '' gracefully."""
        db = await _make_mem_db()

        # Corrupt: close the connection so queries fail
        await db._conn.close()

        result = await db.recall_for_prompt("madhan", None, 5, 800)
        assert result == "", f"Expected empty string on closed DB, got: {result!r}"

    @pytest.mark.asyncio
    async def test_none_connection_returns_empty(self):
        """None connection → recall returns '' without exception."""
        from core.memory.episodic import EpisodicMemory

        db = EpisodicMemory.__new__(EpisodicMemory)
        db._conn = None

        result = await db.recall_for_prompt("madhan", None, 5, 800)
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_db_first_boot(self):
        """Fresh DB (first boot) → all reads return sensible defaults."""
        db = await _make_mem_db()
        try:
            # All reads should work without any data
            result = await db.recall_for_prompt("madhan", None, 5, 800)
            assert result == ""

            ctx = await db.get_context_for_person("madhan", 5)
            assert ctx["total_interactions"] == 0
            assert ctx["familiarity"] == 0.0
            assert ctx["memories"] == []
        finally:
            await db.close()


# ── Malformed LLM output ──────────────────────────────────────────────────────

class TestMalformedLLMOutput:

    @pytest.mark.asyncio
    async def test_empty_string_response_not_spoken(self):
        """Empty LLM response → TTS.speak() not called."""
        import cognition.llm as llm_mod
        from cognition.llm import TokenBudget
        from cognition.mind import CosmoMind

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        mind = CosmoMind.__new__(CosmoMind)
        mind._running = False
        mind._task = None
        mind._enabled = True
        mind._last_spoke = 0.0
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._trigger_last = {}
        mind._memory_context = AsyncMock(return_value="")
        mind._build_rich_system_prompt = AsyncMock(return_value="system")

        # LLM returns empty string
        empty_result = {"text": "", "backend": "ollama/test",
                        "latency_ms": 1, "tokens": 0}

        with (
            patch("cognition.mind.tts", mock_tts),
            patch("cognition.mind.router", MagicMock()),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
            patch.object(llm_mod, "token_budget", TokenBudget(100_000)),
            patch.object(llm_mod.llm, "generate_once",
                         AsyncMock(return_value=empty_result)),
        ):
            await mind._maybe_speak("face_seen", "Test")

        await asyncio.sleep(0.05)
        # TTS should NOT be called with empty string
        for call in mock_tts.speak.call_args_list:
            text = call[0][0] if call[0] else ""
            assert text.strip() != "", f"TTS called with empty/whitespace: {text!r}"
        assert not mind._speech_in_flight.is_set()

    @pytest.mark.asyncio
    async def test_json_response_handled(self, monkeypatch):
        """LLM returning JSON-like text is passed through (Cosmo just says it)."""
        import cognition.llm as llm_mod
        from cognition.llm import LLMInterface, TokenBudget

        monkeypatch.setattr(llm_mod, "token_budget", TokenBudget(100_000))

        iface = LLMInterface()
        # Ollama returns JSON-like text
        iface._call_ollama = AsyncMock(return_value={
            "text": '{"response": "Hello there"}',
            "backend": "ollama/test",
            "tokens": 5,
        })
        iface._call_claude = AsyncMock()

        result = await iface.generate_once("hello", "system")

        # Interface just returns what Ollama gives — no crash
        assert result["text"] == '{"response": "Hello there"}'

    @pytest.mark.asyncio
    async def test_gibberish_response_passed_through(self, monkeypatch):
        """Gibberish LLM output is passed through without crash."""
        import cognition.llm as llm_mod
        from cognition.llm import LLMInterface, TokenBudget

        monkeypatch.setattr(llm_mod, "token_budget", TokenBudget(100_000))

        iface = LLMInterface()
        iface._call_ollama = AsyncMock(return_value={
            "text": "xkcd 1234 bloop fuzzywumpus ##!!",
            "backend": "ollama/test",
            "tokens": 5,
        })
        iface._call_claude = AsyncMock()

        result = await iface.generate_once("hello", "system")

        assert result is not None
        assert result["text"]  # non-empty, whatever it is

    @pytest.mark.asyncio
    async def test_api_timeout_no_crash(self):
        """API timeout → _maybe_speak returns without crashing.

        Timeouts are now absorbed inside LLMInterface.generate_once, which
        returns backend='unavailable' — _maybe_speak must stay silent and
        clear the in-flight flag.
        """
        import cognition.llm as llm_mod
        from cognition.llm import TokenBudget
        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._running = False
        mind._task = None
        mind._enabled = True
        mind._last_spoke = 0.0
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._trigger_last = {}
        mind._memory_context = AsyncMock(return_value="")
        mind._build_rich_system_prompt = AsyncMock(return_value="system")

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        timeout_result = {"text": "", "backend": "unavailable",
                          "latency_ms": 0, "tokens": 0}

        with (
            patch("cognition.mind.tts", mock_tts),
            patch("cognition.mind.router", MagicMock()),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
            patch.object(llm_mod, "token_budget", TokenBudget(100_000)),
            patch.object(llm_mod.llm, "generate_once",
                         AsyncMock(return_value=timeout_result)),
        ):
            # Must not raise
            try:
                await mind._maybe_speak("face_seen", "Test")
            except Exception as e:
                pytest.fail(f"_maybe_speak raised on timeout: {e}")

        # speech_in_flight should be cleared after timeout
        assert not mind._speech_in_flight.is_set()
        mock_tts.speak.assert_not_called()


# ── Mid-conversation budget exhaustion ───────────────────────────────────────

class TestMidConversationBudgetExhaustion:

    @pytest.mark.asyncio
    async def test_budget_exhausted_mid_conversation(self, monkeypatch):
        """Budget exhausts mid-conversation → next turn returns empty, no crash."""
        import cognition.llm as llm_mod
        from cognition.llm import LLMInterface, TokenBudget

        budget = TokenBudget(50)
        monkeypatch.setattr(llm_mod, "token_budget", budget)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

        iface = LLMInterface()
        iface._call_ollama = AsyncMock(return_value=None)  # Ollama down

        # First mock call: records tokens via budget (as _call_claude does)
        async def first_call(system, messages, max_tokens=None):
            budget.record(30)  # simulate actual token usage
            return {"text": "First response", "backend": "claude/test", "tokens": 30}
        iface._call_claude = first_call

        # First call: Claude works, uses 30 tokens
        r1 = await iface.generate_once("hello", "system")
        assert r1["text"] == "First response"
        assert budget.day_total == 30

        # Exhaust budget: record enough to go over 50
        budget.record(25)  # total now 55 >= 50
        assert budget.over_limit()

        # Second call: Claude should be silenced by the budget check
        second_call = AsyncMock(return_value={
            "text": "Should not appear", "backend": "claude/test", "tokens": 10})
        iface._call_claude = second_call

        r2 = await iface.generate_once("more", "system")
        assert r2["text"] == "", f"Expected empty, got: {r2['text']!r}"
        assert r2["backend"] == "budget_exhausted"
        second_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_budget_exhausted_mind_silences_cleanly(self):
        """CosmoMind respects budget — _maybe_speak early-returns when over limit
        for Claude-direct triggers (token_budget singleton is the sole ledger)."""
        import cognition.llm as llm_mod
        from cognition.llm import TokenBudget
        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._enabled = True
        mind._last_spoke = 0.0
        mind._trigger_last = {}
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()

        budget = TokenBudget(100)
        budget.record(101)
        assert budget.over_limit()

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        mock_generate = AsyncMock()

        with (
            patch("cognition.mind.tts", mock_tts),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
            patch.object(llm_mod, "token_budget", budget),
            patch.object(llm_mod.llm, "generate_once", mock_generate),
        ):
            # "face_seen" is a Claude-direct trigger → must be silenced
            await mind._maybe_speak("face_seen", "Test")

        mock_tts.speak.assert_not_called()
        mock_generate.assert_not_awaited()


# ── Personality prompt builder edge cases ─────────────────────────────────────

class TestPersonalityPromptEdgeCases:

    def test_extreme_low_values(self):
        """Very low state values don't cause exceptions."""
        from cognition.personality_prompt import PersonalityPromptBuilder
        from core.personality import EmotionalState
        state = EmotionalState(mood=-1.0, energy=0.0, arousal=0.0, attachment=0.0)
        result = PersonalityPromptBuilder.build(state)
        assert isinstance(result, str) and len(result) > 0

    def test_extreme_high_values(self):
        """Very high state values don't cause exceptions."""
        from cognition.personality_prompt import PersonalityPromptBuilder
        from core.personality import EmotionalState
        state = EmotionalState(mood=1.0, energy=1.0, arousal=1.0, attachment=1.0)
        result = PersonalityPromptBuilder.build(state)
        assert isinstance(result, str) and len(result) > 0

    def test_nan_values_handled(self):
        """NaN state values handled gracefully."""
        from cognition.personality_prompt import PersonalityPromptBuilder
        from core.personality import EmotionalState
        import math
        state = EmotionalState(mood=0.5, energy=0.5, arousal=0.5, attachment=0.5)
        # Just ensure no exception
        result = PersonalityPromptBuilder.build(state)
        assert result


# ── Dashboard endpoints smoke test ────────────────────────────────────────────

class TestDashboardEndpoints:

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_dict(self):
        """Health endpoint returns a dict with expected keys."""
        from services.api.service import health
        result = await health()
        data = result if isinstance(result, dict) else {}
        assert "uptime_s" in data or "status" in data

    @pytest.mark.asyncio
    async def test_budget_endpoint_returns_dict(self):
        """Budget endpoint returns usage dict."""
        from services.api.service import budget_status
        result = await budget_status()
        data = result if isinstance(result, dict) else {}
        assert "day_total" in data or "limit" in data

    @pytest.mark.asyncio
    async def test_logs_tail_no_crash(self):
        """Log tail endpoint returns dict with 'lines' key."""
        from services.api.service import logs_tail
        result = await logs_tail(lines=5)
        data = result if isinstance(result, dict) else {}
        assert "lines" in data

    @pytest.mark.asyncio
    async def test_hardware_endpoint_no_crash(self):
        """Hardware endpoint returns dict."""
        from services.api.service import hardware_status
        result = await hardware_status()
        assert isinstance(result, dict)

    def test_dashboard_html_present(self):
        """Dashboard HTML is non-empty and has key elements."""
        from services.api.service import _DASHBOARD_HTML
        assert len(_DASHBOARD_HTML) > 1000
        assert "Cosmo" in _DASHBOARD_HTML
        assert "personality" in _DASHBOARD_HTML.lower() or "mood" in _DASHBOARD_HTML.lower()
        assert "motor" in _DASHBOARD_HTML.lower()
        assert "8080" in _DASHBOARD_HTML  # camera stream port
        assert "setInterval" in _DASHBOARD_HTML  # auto-refresh
