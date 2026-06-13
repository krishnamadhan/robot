"""
Ambient activity inference + CO_PRESENCE behavior branch.

Drives ActivityMonitor._step() synchronously with injected signals and
ticks the behavior tree to verify the co-presence behaviors fire.
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.activity import (
    ActivityMonitor, TV_LEVEL, QUIET_LEVEL, SUSTAIN_S, INTERACTION_GAP_S,
)
import core.behavior_tree as bt_mod
from core.behavior_tree import bb


@pytest.fixture(autouse=True)
def _clean_bb():
    bb.activity = "none"
    bb.activity_since = 0.0
    bb.settled = False
    bb.tv_moment = 0.0
    bb.person_visible = False
    bb.person_name = ""
    bb.person_id = ""
    yield
    bb.activity = "none"
    bb.settled = False
    bb.person_visible = False


def make_monitor(person_around=True, interacting=False, avg60=0.0, peak5=0.0):
    m = ActivityMonitor()
    now = time.monotonic()
    if person_around:
        m._person_last_seen = now
    if interacting:
        m._last_interaction = now
    m._gather = lambda: {
        "now": time.monotonic(),
        "person_around": person_around,
        "interacting": interacting,
        "avg60": avg60,
        "peak5": peak5,
    }
    return m


class TestClassifier:

    def test_no_person_means_none(self):
        m = make_monitor(person_around=False, avg60=0.5)
        m._step(); m._step()
        assert m.activity == "none"

    def test_recent_interaction_means_hangout(self):
        m = make_monitor(interacting=True, avg60=0.1)
        m._step(); m._step()
        assert m.activity == "hangout"

    def test_sustained_loud_audio_becomes_watching_tv(self):
        m = make_monitor(avg60=TV_LEVEL + 0.02)
        m._loud_since = time.monotonic() - SUSTAIN_S - 1
        m._step(); m._step()
        assert m.activity == "watching_tv"
        assert bb.activity == "watching_tv"

    def test_brief_loudness_is_not_tv(self):
        m = make_monitor(avg60=TV_LEVEL + 0.02)
        m._step(); m._step()   # loud_since just started — sustain not met
        assert m.activity != "watching_tv"

    def test_quiet_room_with_person_is_quiet_company(self):
        m = make_monitor(avg60=QUIET_LEVEL - 0.01)
        m._step(); m._step()
        assert m.activity == "quiet_company"

    def test_hysteresis_requires_two_steps(self):
        m = make_monitor(avg60=QUIET_LEVEL - 0.01)
        m._step()
        assert m.activity == "none"   # first vote only pending
        m._step()
        assert m.activity == "quiet_company"

    def test_tv_spike_sets_tv_moment(self):
        m = make_monitor(avg60=TV_LEVEL + 0.02, peak5=0.5)
        m._activity = "watching_tv"
        bb.activity = "watching_tv"
        m._step()
        assert bb.tv_moment > 0

    def test_no_spike_below_absolute_floor(self):
        m = make_monitor(avg60=0.01, peak5=0.05)   # ratio huge but tiny levels
        m._activity = "watching_tv"
        m._step()
        assert bb.tv_moment == 0.0

    def test_activity_change_resets_settled(self):
        bb.settled = True
        m = make_monitor(avg60=QUIET_LEVEL - 0.01)
        m._step(); m._step()
        assert bb.settled is False


class TestCoPresenceBranch:

    def _tree(self):
        from core.behavior_tree import BehaviorTree
        t = BehaviorTree()
        t.setup()
        return t

    def test_settles_and_approaches_on_first_tick(self):
        bb.activity = "watching_tv"
        bb.person_name = "Madhan"
        with patch("core.action_router.router") as mock_router:
            t = self._tree()
            t.tick_once()
        assert bb.settled is True
        intents = [c.args[0].value for c in mock_router.emit.call_args_list]
        # On settle: stop wander first, then approach (async via _fire)
        assert "stop" in intents

    def test_tv_moment_triggers_surprise(self):
        bb.activity = "watching_tv"
        bb.settled = True
        bt_mod.DoCoPresence._last_tv_react = 0.0
        bt_mod.DoCoPresence._tv_react_for = 0.0
        bb.tv_moment = time.monotonic()
        with patch("expression.eyes.eye_engine") as mock_eyes:
            t = self._tree()
            t.tick_once()
        from expression.eyes import EyeExpression
        exprs = [c.args[0] for c in mock_eyes.set_expression.call_args_list]
        assert EyeExpression.SURPRISED in exprs

    def test_no_co_presence_without_activity(self):
        bb.activity = "none"
        t = self._tree()
        t.tick_once()
        assert bb.settled is False

    def test_quiet_company_settles_without_purr(self):
        bb.activity = "quiet_company"
        with patch("core.action_router.router"), \
             patch("expression.sounds.sounds") as mock_sounds:
            t = self._tree()
            t.tick_once()
        assert bb.settled is True
        played = [c.args[0] for c in mock_sounds.play.call_args_list]
        assert "purr" not in played


class TestAmbientStats:

    def test_pipeline_ambient_stats_window(self):
        from perception.audio.pipeline import ListeningPipeline
        p = ListeningPipeline.__new__(ListeningPipeline)
        from collections import deque
        now = time.monotonic()
        p._amb_buckets = deque([(now - 90, 0.9), (now - 30, 0.2), (now - 5, 0.4)])
        s60 = p.ambient_stats(60.0)
        assert s60["n"] == 2
        assert s60["peak"] == 0.4
        assert abs(s60["avg"] - 0.3) < 1e-9
        assert p.ambient_stats(10.0)["n"] == 1

    def test_note_ambient_buckets(self):
        from perception.audio.pipeline import ListeningPipeline
        import numpy as np
        p = ListeningPipeline.__new__(ListeningPipeline)
        from collections import deque
        p._amb_buckets = deque(maxlen=120)
        p._amb_acc = 0.0
        p._amb_n = 0
        p._amb_bucket_start = time.monotonic() - 2.0   # force bucket rollover
        loud = (np.ones(512, dtype=np.int16) * 8000).tobytes()
        p._note_ambient(loud)   # rolls empty bucket, accumulates
        p._amb_bucket_start = time.monotonic() - 2.0
        p._note_ambient(loud)   # rolls the accumulated bucket out
        assert len(p._amb_buckets) == 1
        assert p._amb_buckets[0][1] == pytest.approx(1.0)
