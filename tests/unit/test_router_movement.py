"""
3.3 — Movement intents exercised against simulated LOCOMOTION.

Router is the sole actuator authority. With LOCOMOTION simulated the
movement executors (APPROACH/FLEE/WANDER/FOLLOW/COME) must drive
navigation; without it they must fall back to an expressive substitute —
never a silent no-op (drift guard 3). STOP is the one allowed no-op.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.action_router import ActionRouter
from core.capabilities import Capability, CapState, registry
from core.intents import Intent, IntentRequest


def _req(intent: Intent, **params) -> IntentRequest:
    return IntentRequest(intent=intent, params=params, source="test")


@pytest.fixture
def router():
    return ActionRouter()


@pytest.fixture
def nav():
    nav = MagicMock()
    for m in ("approach_person", "retreat", "follow_mode", "wander", "stop",
              "turn_left", "turn_right", "spin_360"):
        setattr(nav, m, AsyncMock())
    with patch("behavior.navigation.navigation", nav), \
         patch("hardware.sensor_manager.sensor_manager") as sm:
        sm.get_distance_cm.return_value = 100.0  # no obstacle
        yield nav


@pytest.fixture
def _caps():
    """Save/restore the singleton registry around each test."""
    saved = {c: registry.state(c) for c in
             (Capability.LOCOMOTION, Capability.EXPRESSION)}
    yield
    for c, s in saved.items():
        registry.set_state(c, s, "test restore")


@pytest.fixture
def loco_sim(_caps):
    registry.simulate(Capability.LOCOMOTION, "test mock motors")


@pytest.fixture
def loco_absent(_caps):
    registry.set_state(Capability.LOCOMOTION, CapState.ABSENT, "test")
    registry.simulate(Capability.EXPRESSION, "test eyes")


class TestMovementWithSimulatedLocomotion:

    def test_approach_drives_navigation(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.APPROACH)))
        assert ok is True
        nav.approach_person.assert_awaited()

    def test_approach_steers_toward_person(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.APPROACH, person_x=-0.8)))
        assert ok is True
        nav.turn_left.assert_awaited()
        nav.approach_person.assert_awaited()

    def test_flee_retreats(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.FLEE)))
        assert ok is True
        nav.retreat.assert_awaited()

    def test_wander_runs(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.WANDER, duration=5)))
        assert ok is True
        nav.wander.assert_awaited_with(duration=5)

    def test_follow_runs(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.FOLLOW, duration=30)))
        assert ok is True
        nav.follow_mode.assert_awaited_with(duration=30)

    def test_come_routes_through_approach(self, router, nav, loco_sim):
        ok = asyncio.run(router.dispatch(_req(Intent.COME)))
        assert ok is True
        nav.approach_person.assert_awaited()

    def test_approach_obstacle_gate_holds(self, router, nav, loco_sim):
        with patch("hardware.sensor_manager.sensor_manager") as sm:
            sm.get_distance_cm.return_value = 10.0  # obstacle close
            ok = asyncio.run(router.dispatch(_req(Intent.APPROACH)))
        assert ok is True  # handled: deliberately not moving
        nav.approach_person.assert_not_awaited()


class TestMovementWithoutLocomotion:

    def test_approach_falls_back_expressively(self, router, nav, loco_absent):
        eye = MagicMock()
        snd = MagicMock(play=AsyncMock())
        with patch("expression.eyes.eye_engine", eye), \
             patch("expression.sounds.sounds", snd):
            ok = asyncio.run(router.dispatch(_req(Intent.APPROACH)))
        assert ok is True            # never a silent no-op
        nav.approach_person.assert_not_awaited()
        eye.set_expression.assert_called()

    def test_flee_fallback_is_scared(self, router, nav, loco_absent):
        from expression.eyes import EyeExpression
        eye = MagicMock()
        snd = MagicMock(play=AsyncMock())
        with patch("expression.eyes.eye_engine", eye), \
             patch("expression.sounds.sounds", snd):
            ok = asyncio.run(router.dispatch(_req(Intent.FLEE)))
        assert ok is True
        nav.retreat.assert_not_awaited()
        args = eye.set_expression.call_args
        assert args.args[0] == EyeExpression.SCARED

    def test_stop_is_allowed_noop(self, router, nav, loco_absent):
        eye = MagicMock()
        with patch("expression.eyes.eye_engine", eye):
            ok = asyncio.run(router.dispatch(_req(Intent.STOP)))
        assert ok is True
        nav.stop.assert_not_awaited()
        eye.set_expression.assert_not_called()  # no fallback theatrics
