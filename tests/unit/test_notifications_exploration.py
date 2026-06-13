"""
Tests for cognition/notifications.py and behavior/exploration.py.
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys
import os

# ── stub heavy deps before importing robot modules ────────────────────────────
sys.modules.setdefault("aiohttp", MagicMock())
sys.modules.setdefault("structlog", MagicMock())
sys.modules.setdefault("aiosqlite", MagicMock())

# Provide a minimal logger stub
import types
_log_stub = types.ModuleType("utils.logger")
_log_stub.get_logger = lambda *a, **kw: MagicMock()
sys.modules["utils.logger"] = _log_stub


# ── ExplorationMemory tests ───────────────────────────────────────────────────

class TestExplorationMemory:

    def setup_method(self):
        from behavior.exploration import ExplorationMemory
        self.mem = ExplorationMemory()

    def test_familiarity_empty(self):
        assert self.mem.familiarity(100.0, False) == 0.0

    def test_snapshot_recorded(self):
        # First call always records
        recorded = self.mem.maybe_record_snapshot(200.0, False, "straight")
        assert recorded is True
        assert len(self.mem._snapshots) == 1

    def test_snapshot_interval_throttled(self):
        self.mem.maybe_record_snapshot(200.0, False, "straight")
        # Second call within interval should NOT record
        recorded = self.mem.maybe_record_snapshot(200.0, False, "straight")
        assert recorded is False
        assert len(self.mem._snapshots) == 1

    def test_wander_weights_sum_to_one(self):
        fw, lw, rw, pw = self.mem.wander_weights()
        assert abs(fw + lw + rw + pw - 1.0) < 1e-6

    def test_anti_revisit_bias_left(self):
        # Record lots of right turns
        for _ in range(8):
            self.mem.record_turn("right")
        fw, lw, rw, pw = self.mem.wander_weights()
        # Left should be preferred (boost applied)
        assert lw > rw

    def test_anti_revisit_bias_right(self):
        for _ in range(8):
            self.mem.record_turn("left")
        fw, lw, rw, pw = self.mem.wander_weights()
        assert rw > lw

    def test_preferred_direction_empty(self):
        assert self.mem.preferred_direction() == "straight"

    def test_record_discovery(self):
        event = self.mem.record_discovery(18.0, 200.0, False)
        assert event.distance_cm == 18.0
        assert event.person_present is False
        assert event.reported is False

    def test_pending_discovery(self):
        event = self.mem.record_discovery(18.0, 200.0, False)
        assert self.mem.pending_discovery() is event

    def test_mark_reported_clears_pending(self):
        event = self.mem.record_discovery(18.0, 200.0, False)
        self.mem.mark_reported(event)
        assert self.mem.pending_discovery() is None

    def test_tod_bucket_morning(self):
        import datetime
        # 8am
        ts = datetime.datetime(2026, 1, 1, 8, 0).timestamp()
        assert self.mem._tod_bucket(ts) == "morning"

    def test_tod_bucket_night(self):
        import datetime
        ts = datetime.datetime(2026, 1, 1, 23, 0).timestamp()
        assert self.mem._tod_bucket(ts) == "night"

    def test_familiarity_increases_with_matching_snapshots(self):
        """3+ snapshots in window with matching conditions → familiarity ≥ 1."""
        now = time.time()
        from behavior.exploration import RoomSnapshot
        for _ in range(3):
            snap = RoomSnapshot(
                ts=now - 60,
                light_lux=200.0,
                person_present=False,
                tod_bucket=self.mem._tod_bucket(now),
                dominant_dir="straight",
            )
            self.mem._snapshots.append(snap)
        assert self.mem.familiarity(210.0, False) >= 1.0


# ── NotificationManager tests ─────────────────────────────────────────────────

class TestNotificationManager:

    def setup_method(self):
        # Patch DB to avoid real SQLite writes
        sys.modules["aiosqlite"] = MagicMock()
        from cognition.notifications import NotificationManager
        self.nm = NotificationManager()
        self.nm._conn = None  # skip DB checks

    def test_can_send_initial(self):
        assert self.nm.can_send("missing_you") is True

    def test_can_send_respects_cooldown(self):
        self.nm._last_sent["missing_you"] = time.time()
        assert self.nm.can_send("missing_you") is False

    def test_can_send_after_cooldown_expired(self):
        from cognition.notifications import COOLDOWNS
        self.nm._last_sent["missing_you"] = time.time() - COOLDOWNS["missing_you"] - 1
        assert self.nm.can_send("missing_you") is True

    @pytest.mark.asyncio
    async def test_send_cooldown_skip(self):
        self.nm._last_sent["missing_you"] = time.time()
        result = await self.nm.send("missing_you", "test message")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_calls_endpoint(self):
        """When cooldown clear and no DB, send() should POST to notify endpoint."""
        import aiohttp as _aiohttp
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await self.nm.send("discovery", "hello test")
        assert result is True
        assert self.nm._last_sent.get("discovery") is not None

    @pytest.mark.asyncio
    async def test_send_network_error_returns_false(self):
        import aiohttp as _aiohttp
        with patch("aiohttp.ClientSession", side_effect=Exception("network down")):
            result = await self.nm.send("discovery", "hello")
        assert result is False
