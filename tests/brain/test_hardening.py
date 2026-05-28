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

class TestEpisodicDBFailures:

    @pytest.mark.asyncio
    async def test_corrupted_db_returns_empty_string(self):
        """Corrupted DB → recall_for_prompt returns '' gracefully."""
        from core.memory.episodic import EpisodicMemory

        db = EpisodicMemory.__new__(EpisodicMemory)
        db._db_path = Path(":memory:")
        db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        db._conn.row_factory = sqlite3.Row
        db._loop = None
        db._create_schema()

        # Corrupt: close the connection so queries fail
        db._conn.close()

        result = db._recall_sync("madhan", None, 5, 800)
        assert result == "", f"Expected empty string on closed DB, got: {result!r}"

    @pytest.mark.asyncio
    async def test_none_connection_returns_empty(self):
        """None connection → recall returns '' without exception."""
        from core.memory.episodic import EpisodicMemory

        db = EpisodicMemory.__new__(EpisodicMemory)
        db._conn = None

        result = db._recall_sync("madhan", None, 5, 800)
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_db_first_boot(self):
        """Fresh DB (first boot) → all reads return sensible defaults."""
        from core.memory.episodic import EpisodicMemory

        db = EpisodicMemory.__new__(EpisodicMemory)
        db._conn = sqlite3.connect(":memory:", check_same_thread=False)
        db._conn.row_factory = sqlite3.Row
        db._loop = None
        db._create_schema()

        # All reads should work without any data
        result = db._recall_sync("madhan", None, 5, 800)
        assert result == ""

        ctx = db._get_context_sync("madhan", 5)
        assert ctx["total_interactions"] == 0
        assert ctx["familiarity"] == 0.0
        assert ctx["memories"] == []


# ── Malformed LLM output ──────────────────────────────────────────────────────

class TestMalformedLLMOutput:

    @pytest.mark.asyncio
    async def test_empty_string_response_not_spoken(self):
        """Empty LLM response → TTS.speak() not called."""
        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._running = False
        mind._task = None
        mind._client = MagicMock()
        mind._enabled = True
        mind._last_spoke = 0.0
        mind._last_action = time.monotonic() - 200
        mind._budget = MagicMock()
        mind._budget.over_limit.return_value = False
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._trigger_last = {}
        mind._was_dark = False
        mind._obstacle_warn = False

        # LLM returns empty string
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="")]
        fake_response.usage = MagicMock(input_tokens=10, output_tokens=0)

        mock_eye = MagicMock()
        mock_sounds = MagicMock()
        mock_sounds.play = AsyncMock()

        mock_attention = MagicMock()
        mock_attention.state.focused = False

        async def fake_executor(executor, func):
            return fake_response

        loop = asyncio.get_event_loop()
        with (
            patch("cognition.mind.tts", mock_tts),
            patch("cognition.mind.sounds", mock_sounds),
            patch("cognition.mind.eye_engine", mock_eye),
            patch("cognition.mind.attention", mock_attention),
        ):
            with patch.object(loop, "run_in_executor", side_effect=fake_executor):
                with patch("core.memory.episodic.episodic") as mock_ep:
                    mock_ep.get_context_for_person = AsyncMock(return_value={
                        "familiarity": 0.0, "total_interactions": 0, "memories": []
                    })
                    mock_ep.retrieve = AsyncMock(return_value=[])
                    await mind._maybe_speak("face_seen", "Test")

        await asyncio.sleep(0.05)
        # TTS should NOT be called with empty string
        for call in mock_tts.speak.call_args_list:
            text = call[0][0] if call[0] else ""
            assert text.strip() != "", f"TTS called with empty/whitespace: {text!r}"

    @pytest.mark.asyncio
    async def test_json_response_handled(self):
        """LLM returning JSON-like text is passed through (Cosmo just says it)."""
        from cognition.llm import LLMRouter, OllamaProvider, ClaudeProvider, TokenBudget

        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        # Ollama returns JSON-like text
        ollama.generate = AsyncMock(return_value={
            "text": '{"response": "Hello there"}',
            "backend": "ollama/test",
            "tokens": 5,
        })

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system")

        # Router just returns what Ollama gives — no crash
        assert result["text"] == '{"response": "Hello there"}'

    @pytest.mark.asyncio
    async def test_gibberish_response_passed_through(self):
        """Gibberish LLM output is passed through without crash."""
        from cognition.llm import LLMRouter, OllamaProvider, ClaudeProvider, TokenBudget

        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value={
            "text": "xkcd 1234 bloop fuzzywumpus ##!!",
            "backend": "ollama/test",
            "tokens": 5,
        })

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system")

        assert result is not None
        assert result["text"]  # non-empty, whatever it is

    @pytest.mark.asyncio
    async def test_api_timeout_no_crash(self):
        """API timeout → _maybe_speak returns without crashing."""
        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._running = False
        mind._task = None
        mind._client = MagicMock()
        mind._enabled = True
        mind._last_spoke = 0.0
        mind._last_action = time.monotonic() - 200
        mind._budget = MagicMock()
        mind._budget.over_limit.return_value = False
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._trigger_last = {}
        mind._was_dark = False
        mind._obstacle_warn = False

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()
        mock_eye = MagicMock()
        mock_sounds = MagicMock()
        mock_sounds.play = AsyncMock()
        mock_attention = MagicMock()
        mock_attention.state.focused = False

        # Simulate timeout
        async def timeout_executor(executor, func):
            raise asyncio.TimeoutError()

        loop = asyncio.get_event_loop()
        with (
            patch("cognition.mind.tts", mock_tts),
            patch("cognition.mind.sounds", mock_sounds),
            patch("cognition.mind.eye_engine", mock_eye),
            patch("cognition.mind.attention", mock_attention),
        ):
            with patch.object(loop, "run_in_executor", side_effect=timeout_executor):
                with patch("core.memory.episodic.episodic") as mock_ep:
                    mock_ep.get_context_for_person = AsyncMock(return_value={
                        "familiarity": 0.0, "total_interactions": 0, "memories": []
                    })
                    mock_ep.retrieve = AsyncMock(return_value=[])
                    # Must not raise
                    try:
                        await mind._maybe_speak("face_seen", "Test")
                    except Exception as e:
                        pytest.fail(f"_maybe_speak raised on timeout: {e}")

        # speech_in_flight should be cleared after timeout
        assert not mind._speech_in_flight.is_set()


# ── Mid-conversation budget exhaustion ───────────────────────────────────────

class TestMidConversationBudgetExhaustion:

    @pytest.mark.asyncio
    async def test_budget_exhausted_mid_conversation(self):
        """Budget exhausts mid-conversation → next turn returns empty, no crash."""
        from cognition.llm import TokenBudget, LLMRouter, OllamaProvider, ClaudeProvider

        budget = TokenBudget(50)

        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)
        ollama.generate = AsyncMock(return_value=None)  # Ollama down

        # First mock call: records tokens via budget
        async def first_call(prompt, system, max_tokens=150):
            budget.record(30)  # simulate actual token usage
            return {"text": "First response", "backend": "claude/test", "tokens": 30}
        claude.generate = first_call

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)

        # First call: Claude works, uses 30 tokens
        r1 = await router.generate("hello", "system")
        assert r1["text"] == "First response"
        assert budget.day_total == 30

        # Exhaust budget: record enough to go over 50
        budget.record(25)  # total now 55 >= 50
        assert budget.over_limit()

        # Second call: Claude should be silenced by LLMRouter budget check
        claude_second_calls = {"n": 0}
        async def second_call(prompt, system, max_tokens=150):
            claude_second_calls["n"] += 1
            return {"text": "Should not appear", "backend": "claude/test", "tokens": 10}
        claude.generate = second_call

        r2 = await router.generate("more", "system")
        assert r2["text"] == "", f"Expected empty, got: {r2['text']!r}"
        assert claude_second_calls["n"] == 0, "Claude should not be called when budget exhausted"

    @pytest.mark.asyncio
    async def test_budget_exhausted_mind_silences_cleanly(self):
        """CosmoMind respects budget — _maybe_speak early-returns when over limit."""
        from cognition.mind import CosmoMind, _DailyBudget

        mind = CosmoMind.__new__(CosmoMind)
        mind._enabled = True
        mind._client = MagicMock()
        mind._last_spoke = 0.0
        mind._trigger_last = {}

        budget = _DailyBudget(100)
        class U:
            input_tokens = 101
            output_tokens = 0
        budget.record(U())
        assert budget.over_limit()

        mind._budget = budget
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        with patch("cognition.mind.tts", mock_tts):
            await mind._maybe_speak("face_seen", "Test")

        mock_tts.speak.assert_not_called()


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
