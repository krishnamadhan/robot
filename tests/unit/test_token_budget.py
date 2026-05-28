"""
T1.2 — Token budget cutoff verification.

Simulates exceeding the 100K daily limit and asserts:
  - over_limit() returns True
  - cosmo_mind.daily_summary is logged
  - mind silences itself (returns early from _maybe_speak)
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.mind import _DailyBudget, DAILY_TOKEN_LIMIT


class TestDailyBudget:
    def _make_usage(self, n: int):
        u = MagicMock()
        u.input_tokens = n
        u.output_tokens = 0
        return u

    def test_starts_under_limit(self):
        b = _DailyBudget(100_000)
        assert not b.over_limit()

    def test_over_limit_after_exceeding(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(100_001))
        assert b.over_limit()

    def test_exactly_at_limit_is_over(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(100_000))
        assert b.over_limit()

    def test_one_below_limit_is_not_over(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(99_999))
        assert not b.over_limit()

    def test_cumulative_tracking(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(60_000))
        assert not b.over_limit()
        b.record(self._make_usage(40_001))
        assert b.over_limit()

    def test_day_total_property(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(12_345))
        assert b.day_total == 12_345

    def test_daily_summary_logged_on_new_day(self, caplog):
        import logging
        b = _DailyBudget(100_000)
        b.record(self._make_usage(500))
        # Force a "new day" by changing the internal day
        b._day = "1970-01-01"
        with patch("cognition.mind.log") as mock_log:
            b.record(self._make_usage(100))
            calls = [str(c) for c in mock_log.info.call_args_list]
            assert any("daily_summary" in c for c in calls), (
                "Expected cosmo_mind.daily_summary to be logged on day rollover"
            )

    def test_budget_resets_on_new_day(self):
        b = _DailyBudget(100_000)
        b.record(self._make_usage(99_999))
        assert not b.over_limit()
        b._day = "1970-01-01"
        b._reset_if_new_day()
        assert b.day_total == 0
        assert not b.over_limit()


class TestCosmoMindBudgetGate:
    """Verify CosmoMind._maybe_speak returns early when budget exceeded."""

    def test_mind_silences_when_over_budget(self):
        from cognition.mind import CosmoMind
        mind = CosmoMind.__new__(CosmoMind)

        # Build minimal mock state
        budget = _DailyBudget(100_000)
        budget._total_tokens = 100_001
        import datetime
        budget._day = datetime.date.today().isoformat()

        mind._enabled = True
        mind._client = MagicMock()
        mind._budget = budget
        mind._budget_lock = asyncio.Lock()
        mind._speech_in_flight = asyncio.Event()
        mind._last_spoke = 0.0
        mind._trigger_last = {}

        spoken = []

        async def run():
            with patch("cognition.mind.tts") as mock_tts:
                mock_tts.is_speaking = False
                with patch.object(mind, "_is_busy", return_value=False):
                    await mind._maybe_speak("touched", "Madhan")
            return spoken

        asyncio.run(run())

        # If budget gate works, _client.messages.create was never called
        mind._client.messages.create.assert_not_called()
