"""
B2 — LLM routing + budget guard tests (I3, I4, I6) — D3: LLMInterface is the
single LLM call path (LLMRouter/OllamaProvider/ClaudeProvider deleted).

Tests:
  - TokenBudget: limit enforcement, daily reset, per-call tracking
  - LLMInterface.generate_once: Ollama-first, Claude fallback, claude_direct,
    budget gating ("budget_exhausted"), both-down degradation ("unavailable")
  - _system_to_blocks: static block carries cache_control, dynamic tail does not
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.llm import (
    LLMInterface,
    TokenBudget,
    _PROMPT_STATIC,
    _system_to_blocks,
)
import cognition.llm as llm_mod


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
        b = TokenBudget(100)
        b.record(90)
        assert b.day_total == 90
        # Simulate day change by patching
        b._day = "1999-01-01"
        # Next call resets
        b._reset_if_new_day()
        assert b.day_total == 0
        assert b.claude_allowed()


# ── Fixtures ──────────────────────────────────────────────────────────────────

OLLAMA_RESULT = {"text": "Hi from Ollama!", "backend": "ollama/llama3.2:1b", "tokens": 5}
CLAUDE_RESULT = {"text": "Hi from Claude!", "backend": "claude/test", "tokens": 20}


@pytest.fixture
def iface(monkeypatch):
    """Fresh LLMInterface with a fresh module-level token_budget and an API key
    set, so the Claude path is reachable (mocked — never hits the network)."""
    monkeypatch.setattr(llm_mod, "token_budget", TokenBudget(100_000))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return LLMInterface()


def exhaust_budget(monkeypatch, limit: int = 100) -> TokenBudget:
    b = TokenBudget(limit)
    b.record(limit)
    assert b.over_limit()
    monkeypatch.setattr(llm_mod, "token_budget", b)
    return b


# ── LLMInterface.generate_once routing ────────────────────────────────────────

class TestGenerateOnceRouting:

    async def test_i4_ollama_first_when_available(self, iface):
        """I4: Ollama succeeds → result is Ollama's, Claude never called."""
        iface._call_ollama = AsyncMock(return_value=dict(OLLAMA_RESULT))
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == "Hi from Ollama!"
        assert "ollama" in result["backend"]
        assert "latency_ms" in result
        iface._call_ollama.assert_awaited_once()
        iface._call_claude.assert_not_awaited()

    async def test_i4_i6_claude_fallback_when_ollama_returns_none(self, iface):
        """I4+I6: Ollama returns None → Claude fallback used."""
        iface._call_ollama = AsyncMock(return_value=None)
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == "Hi from Claude!"
        assert "claude" in result["backend"]
        iface._call_claude.assert_awaited_once()

    async def test_i6_claude_fallback_when_ollama_raises(self, iface):
        """I6: Ollama raises → marked unavailable, Claude fallback used."""
        iface._call_ollama = AsyncMock(side_effect=ConnectionError("no ollama"))
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == "Hi from Claude!"
        assert iface._ollama_available is False

    async def test_ollama_skipped_when_known_down(self, iface):
        """_ollama_available=False → Ollama not retried, straight to Claude."""
        iface._ollama_available = False
        iface._call_ollama = AsyncMock(return_value=dict(OLLAMA_RESULT))
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert "claude" in result["backend"]
        iface._call_ollama.assert_not_awaited()

    async def test_claude_direct_never_calls_ollama(self, iface):
        """D4: claude_direct=True skips Ollama entirely."""
        iface._call_ollama = AsyncMock(return_value=dict(OLLAMA_RESULT))
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once(
            "hello", "system prompt", claude_direct=True)

        assert "claude" in result["backend"]
        iface._call_ollama.assert_not_awaited()
        iface._call_claude.assert_awaited_once()

    async def test_i3_budget_exhausted_silences_claude(self, monkeypatch):
        """I3: budget exhausted → backend 'budget_exhausted', Claude not called."""
        exhaust_budget(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        iface = LLMInterface()
        iface._call_ollama = AsyncMock(return_value=None)  # Ollama down too
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == ""
        assert result["backend"] == "budget_exhausted"
        iface._call_claude.assert_not_awaited()

    async def test_i3_budget_exhausted_ollama_still_answers(self, monkeypatch):
        """I3: budget exhausted but Ollama up → Ollama answers, Claude silent."""
        exhaust_budget(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        iface = LLMInterface()
        iface._call_ollama = AsyncMock(return_value=dict(OLLAMA_RESULT))
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == "Hi from Ollama!"
        iface._call_claude.assert_not_awaited()

    async def test_i6_both_down_returns_unavailable_no_crash(self, iface):
        """I6: Ollama None + Claude raises → backend 'unavailable', no exception."""
        iface._call_ollama = AsyncMock(return_value=None)
        iface._call_claude = AsyncMock(side_effect=RuntimeError("Claude crashed"))

        result = await iface.generate_once("hello", "system prompt")

        assert result["text"] == ""
        assert result["backend"] == "unavailable"

    async def test_no_api_key_returns_unavailable(self, monkeypatch):
        """No ANTHROPIC_API_KEY → Claude path skipped, backend 'unavailable'."""
        monkeypatch.setattr(llm_mod, "token_budget", TokenBudget(100_000))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        iface = LLMInterface()
        iface._call_ollama = AsyncMock(return_value=None)
        iface._call_claude = AsyncMock(return_value=dict(CLAUDE_RESULT))

        result = await iface.generate_once("hello", "system prompt")

        assert result["backend"] == "unavailable"
        iface._call_claude.assert_not_awaited()

    async def test_no_exception_on_all_failures(self, iface):
        """generate_once must never raise regardless of backend failures."""
        iface._call_ollama = AsyncMock(side_effect=RuntimeError("Ollama crashed"))
        iface._call_claude = AsyncMock(side_effect=RuntimeError("Claude crashed"))

        try:
            result = await iface.generate_once("hello", "system prompt")
        except RuntimeError as e:
            pytest.fail(f"generate_once raised RuntimeError: {e}")
        assert result["text"] == ""


# ── _system_to_blocks (prompt caching) ────────────────────────────────────────

class TestSystemToBlocks:

    def test_static_block_cached_dynamic_tail_not(self):
        system = _PROMPT_STATIC + "\n\nCurrent state: happy\nTime: evening"
        blocks = _system_to_blocks(system)

        assert len(blocks) == 2
        assert blocks[0]["text"] == _PROMPT_STATIC
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "Current state: happy" in blocks[1]["text"]
        assert "cache_control" not in blocks[1]

    def test_no_dynamic_tail_single_block(self):
        blocks = _system_to_blocks(_PROMPT_STATIC)
        assert len(blocks) == 1
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_custom_system_prompt_whole_block_cached(self):
        """Non-Cosmo system prompt → one block, still cacheable."""
        blocks = _system_to_blocks("You are a one-shot commentary generator.")
        assert len(blocks) == 1
        assert blocks[0]["text"] == "You are a one-shot commentary generator."
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
