"""
T1.2 — Token budget cutoff verification (unified TokenBudget, OQ-5).

Simulates exceeding the 100K daily limit and asserts:
  - claude_allowed() returns False / over_limit() returns True
  - daily_summary is logged on day rollover
  - CosmoMind._maybe_speak skips the LLM for claude-direct triggers when exhausted
"""
import asyncio
import datetime
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.llm import TokenBudget


class TestTokenBudget:

    def test_starts_under_limit(self):
        b = TokenBudget(100_000)
        assert b.claude_allowed()
        assert not b.over_limit()

    def test_over_limit_after_exceeding(self):
        b = TokenBudget(100_000)
        b.record(100_001)
        assert b.over_limit()

    def test_exactly_at_limit_is_over(self):
        b = TokenBudget(100_000)
        b.record(100_000)
        assert b.over_limit()

    def test_one_below_limit_is_not_over(self):
        b = TokenBudget(100_000)
        b.record(99_999)
        assert not b.over_limit()

    def test_cumulative_tracking(self):
        b = TokenBudget(100_000)
        b.record(60_000)
        assert not b.over_limit()
        b.record(40_001)
        assert b.over_limit()

    def test_day_total_property(self):
        b = TokenBudget(100_000)
        b.record(12_345)
        assert b.day_total == 12_345

    def test_daily_summary_logged_on_new_day(self):
        b = TokenBudget(100_000)
        b.record(500)
        b._day = "1970-01-01"
        with patch("cognition.llm.log") as mock_log:
            b.record(100)
            calls = [str(c) for c in mock_log.info.call_args_list]
            assert any("daily_summary" in c for c in calls), (
                "Expected token_budget.daily_summary on day rollover"
            )

    def test_budget_resets_on_new_day(self):
        b = TokenBudget(100_000)
        b.record(99_999)
        assert not b.over_limit()
        b._day = "1970-01-01"
        b._reset_if_new_day()
        assert b.day_total == 0
        assert not b.over_limit()


class TestBudgetReservation:
    """KI-017 — atomic try_reserve/release closes the concurrent double-spend
    window between claude_allowed() and record()."""

    def test_try_reserve_succeeds_with_headroom(self):
        b = TokenBudget(100_000)
        assert b.try_reserve()
        assert b._reserved == TokenBudget.EST_CALL_TOKENS

    def test_try_reserve_fails_at_limit(self):
        b = TokenBudget(100_000)
        b.record(100_000)
        assert not b.try_reserve()

    def test_try_reserve_fails_when_estimate_exceeds_headroom(self):
        b = TokenBudget(100_000)
        b.record(99_000)  # 1000 left < EST_CALL_TOKENS
        assert not b.try_reserve()

    def test_double_spend_closed(self):
        # Remaining 3000 with est 2000: only one concurrent call may pass.
        b = TokenBudget(100_000)
        b.record(97_000)
        assert b.try_reserve()
        assert not b.try_reserve()

    def test_release_restores_headroom(self):
        b = TokenBudget(100_000)
        b.record(97_000)
        assert b.try_reserve()
        assert not b.try_reserve()
        b.release()
        assert b.try_reserve()

    def test_release_never_goes_negative(self):
        b = TokenBudget(100_000)
        b.release()
        assert b._reserved == 0

    def test_claude_allowed_accounts_for_reservations(self):
        b = TokenBudget(100_000)
        b.record(99_000)
        assert b.claude_allowed()
        assert b.try_reserve(1000)
        assert not b.claude_allowed()

    def test_record_after_release_accounting(self):
        b = TokenBudget(100_000)
        assert b.try_reserve()
        b.release()
        b.record(1500)
        assert b._reserved == 0
        assert b.day_total == 1500


class TestCosmoMindBudgetGate:
    """CosmoMind._maybe_speak must skip the LLM for claude-direct triggers
    when the shared budget is exhausted."""

    def test_mind_silences_when_over_budget(self):
        from cognition.mind import CosmoMind

        mind = CosmoMind.__new__(CosmoMind)
        mind._enabled = True
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._last_spoke = 0.0
        mind._trigger_last = {}
        mind._is_busy = lambda: False

        exhausted = TokenBudget(100_000)
        exhausted._day = datetime.date.today().isoformat()
        exhausted._total = 100_001

        mock_llm = MagicMock()
        mock_llm.generate_once = AsyncMock(
            return_value={"text": "should not happen", "backend": "claude"})

        async def run():
            with (
                patch("cognition.mind.tts") as mock_tts,
                patch("cognition.mind.router", MagicMock()),
                patch("cognition.llm.token_budget", exhausted),
                patch("cognition.llm.llm", mock_llm),
                patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
            ):
                mock_tts.is_speaking = False
                # "touched" is claude-direct (not ambient) → budget gate applies
                await mind._maybe_speak("touched", "Madhan")

        asyncio.run(run())
        mock_llm.generate_once.assert_not_awaited()
