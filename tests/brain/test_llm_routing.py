"""
B2 — LLM routing + budget guard tests (I3, I4, I6).

Tests:
  - TokenBudget: limit enforcement, daily reset, per-call tracking
  - OllamaProvider: happy path, failure path
  - ClaudeProvider: budget gate, happy path
  - LLMRouter: Ollama-first, Claude fallback, both-down degradation
  - I3: budget exhausted → Claude silenced, Ollama continues
  - I4: Ollama succeeds → Claude never called
  - I6: Ollama down → Claude fallback; both down → empty (no crash)
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.llm import TokenBudget, OllamaProvider, ClaudeProvider, LLMRouter


# ── TokenBudget ───────────────────────────────────────────────────────────────

class TestTokenBudget:

    def test_starts_under_limit(self):
        b = TokenBudget(100_000)
        assert b.claude_allowed()
        assert not b.over_limit()

    def test_over_limit_after_exceeding(self):
        b = TokenBudget(100)
        b.record(101)
        assert not b.claude_allowed()
        assert b.over_limit()

    def test_exactly_at_limit_is_over(self):
        b = TokenBudget(100)
        b.record(100)
        assert b.over_limit()

    def test_one_below_is_not_over(self):
        b = TokenBudget(100)
        b.record(99)
        assert b.claude_allowed()

    def test_cumulative_tracking(self):
        b = TokenBudget(100)
        b.record(40)
        b.record(40)
        assert b.day_total == 80
        assert b.claude_allowed()
        b.record(30)
        assert b.over_limit()

    def test_remaining_decreases(self):
        b = TokenBudget(100)
        assert b.remaining == 100
        b.record(30)
        assert b.remaining == 70

    def test_remaining_never_below_zero(self):
        b = TokenBudget(100)
        b.record(200)
        assert b.remaining == 0

    def test_day_reset(self):
        import datetime
        b = TokenBudget(100)
        b.record(90)
        assert b.day_total == 90
        # Simulate day change by patching
        b._day = "1999-01-01"
        # Next call resets
        b._reset_if_new_day()
        assert b.day_total == 0
        assert b.claude_allowed()


# ── OllamaProvider ────────────────────────────────────────────────────────────

class TestOllamaProvider:

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        provider = OllamaProvider()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=mock_response)))
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_cm
            result = await provider.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        provider = OllamaProvider()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_cm = AsyncMock()
            cm_instance = MagicMock()
            cm_instance.get = AsyncMock(side_effect=ConnectionError("refused"))
            mock_cm.__aenter__ = AsyncMock(return_value=cm_instance)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_cm
            result = await provider.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_generate_success(self):
        provider = OllamaProvider()
        fake_data = {"message": {"content": "Hello I am Cosmo"}, "eval_count": 5}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = fake_data

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_cm = AsyncMock()
            cm_instance = MagicMock()
            cm_instance.post = AsyncMock(return_value=mock_response)
            mock_cm.__aenter__ = AsyncMock(return_value=cm_instance)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_cm

            result = await provider.generate("hi", "you are cosmo")

        assert result is not None
        assert result["text"] == "Hello I am Cosmo"
        assert result["backend"].startswith("ollama/")

    @pytest.mark.asyncio
    async def test_generate_returns_none_on_failure(self):
        provider = OllamaProvider()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_cm = AsyncMock()
            cm_instance = MagicMock()
            cm_instance.post = AsyncMock(side_effect=ConnectionError("no ollama"))
            mock_cm.__aenter__ = AsyncMock(return_value=cm_instance)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_cm

            result = await provider.generate("hi", "system")

        assert result is None
        assert provider._healthy is False


# ── ClaudeProvider ────────────────────────────────────────────────────────────

class TestClaudeProvider:

    @pytest.mark.asyncio
    async def test_returns_none_when_budget_exhausted(self):
        budget = TokenBudget(100)
        budget.record(100)  # exhaust
        provider = ClaudeProvider(budget=budget)

        result = await provider.generate("hi", "system")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_api_call_when_no_key(self):
        budget = TokenBudget(100_000)
        provider = ClaudeProvider(budget=budget)
        api_calls = {"n": 0}

        # Patch _get_client to raise
        def _fail_client():
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        provider._get_client = _fail_client

        result = await provider.generate("hi", "system")
        assert result is None

    @pytest.mark.asyncio
    async def test_records_tokens_to_budget(self):
        budget = TokenBudget(100_000)
        provider = ClaudeProvider(budget=budget)

        # Mock the Anthropic client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello")]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 10

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        provider._client = mock_client

        result = await provider.generate("hi", "system")

        assert result is not None
        assert result["text"] == "Hello"
        assert budget.day_total == 60  # 50 + 10


# ── LLMRouter ─────────────────────────────────────────────────────────────────

class TestLLMRouter:

    @pytest.mark.asyncio
    async def test_i4_ollama_first_when_available(self):
        """I4: Ollama available → uses Ollama, never calls Claude."""
        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        claude_calls = {"n": 0}
        original_generate = claude.generate
        async def spy_claude(*args, **kwargs):
            claude_calls["n"] += 1
            return await original_generate(*args, **kwargs)
        claude.generate = spy_claude

        # Mock Ollama to succeed
        ollama.generate = AsyncMock(return_value={
            "text": "Hi from Ollama!",
            "backend": "ollama/llama3.2:1b",
            "tokens": 5,
        })

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system prompt")

        assert result["text"] == "Hi from Ollama!"
        assert "ollama" in result["backend"]
        assert claude_calls["n"] == 0, "Claude should not be called when Ollama succeeds"

    @pytest.mark.asyncio
    async def test_i4_i6_claude_fallback_when_ollama_down(self):
        """I4+I6: Ollama fails → Claude is called as fallback."""
        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        # Ollama fails
        ollama.generate = AsyncMock(return_value=None)

        # Claude succeeds
        mock_response_data = {
            "text": "Hi from Claude!",
            "backend": "claude/claude-haiku-4-5-20251001",
            "tokens": 20,
        }
        claude.generate = AsyncMock(return_value=mock_response_data)

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system prompt")

        assert result["text"] == "Hi from Claude!"
        assert "claude" in result["backend"]
        claude.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_i6_both_down_returns_empty_no_crash(self):
        """I6: Both Ollama and Claude fail → returns empty dict, no exception."""
        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value=None)
        claude.generate = AsyncMock(return_value=None)

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system prompt")

        assert result["text"] == ""
        assert result["backend"] == "unavailable"

    @pytest.mark.asyncio
    async def test_i3_budget_exhausted_silences_claude_not_ollama(self):
        """I3: Budget exhausted → Claude silenced, but Ollama still tried."""
        budget = TokenBudget(100)
        budget.record(100)  # exhaust Claude
        assert not budget.claude_allowed()

        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        claude_calls = {"n": 0}
        async def spy_claude(*args, **kwargs):
            claude_calls["n"] += 1
            return {"text": "Claude!", "backend": "claude/test", "tokens": 10}
        claude.generate = spy_claude

        # Ollama succeeds
        ollama.generate = AsyncMock(return_value={
            "text": "Ollama still works!",
            "backend": "ollama/llama",
            "tokens": 5,
        })

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system")

        assert result["text"] == "Ollama still works!"
        assert claude_calls["n"] == 0, "Claude must be silenced when budget exhausted"

    @pytest.mark.asyncio
    async def test_i3_budget_exhausted_both_down_is_non_verbal(self):
        """I3+I6: Budget exhausted AND Ollama down → non-verbal only (empty text, no crash)."""
        budget = TokenBudget(100)
        budget.record(100)  # exhaust

        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        ollama.generate = AsyncMock(return_value=None)
        # Claude would fail anyway due to budget, but shouldn't even be called
        claude_calls = {"n": 0}
        async def spy_claude(*a, **kw):
            claude_calls["n"] += 1
            return None
        claude.generate = spy_claude

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)
        result = await router.generate("hello", "system")

        assert result["text"] == ""
        assert claude_calls["n"] == 0, "Budget-exhausted Claude should never be called"

    @pytest.mark.asyncio
    async def test_router_no_exception_on_all_failures(self):
        """Router must never raise exceptions regardless of backend failures."""
        budget = TokenBudget(100_000)
        ollama = OllamaProvider()
        claude = ClaudeProvider(budget=budget)

        # Both raise unexpected exceptions
        ollama.generate = AsyncMock(side_effect=RuntimeError("Ollama crashed"))
        claude.generate = AsyncMock(side_effect=RuntimeError("Claude crashed"))

        router = LLMRouter(ollama=ollama, claude=claude, budget=budget)

        # Should not raise
        try:
            result = await router.generate("hello", "system")
            # If exceptions propagate, the router is broken
        except RuntimeError as e:
            pytest.fail(f"Router raised RuntimeError: {e}")
