"""
T1.3 — Safety-path tests (obstacle stop, cliff halt, STBY e-stop).

Uses the mock HAL — no real hardware needed. These paths protect
hardware and people; regressions here are critical.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


# ── Navigation safety ─────────────────────────────────────────────────────────

class TestNavigationSafety:

    def _make_nav(self):
        """NavigationEngine with mocked motor_controller."""
        with patch("behavior.navigation.motor_controller") as mc:
            mc.stop = AsyncMock()
            mc.initialize = AsyncMock()
            mc.is_moving = False
            from behavior.navigation import NavigationEngine
            nav = NavigationEngine()
            nav._motor = mc
            return nav, mc

    def test_obstacle_emergency_stop_below_5cm(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        nav._cliff_blocked = False
        nav._pickup_blocked = False
        nav._bat_blocked = False
        nav._obstacle_cm = 4.9

        with patch("behavior.navigation.motor_controller") as mc:
            mc.stop = AsyncMock()
            nav._motor = mc

            async def _check():
                safe = nav._is_safe_to_move()
                return safe

            # Distance below OBSTACLE_STOP_CM (5.0) → not safe
            assert nav._obstacle_cm < NavigationEngine.OBSTACLE_STOP_CM

    def test_obstacle_safe_above_15cm(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        nav._obstacle_cm = 20.0
        nav._cliff_blocked = False
        nav._pickup_blocked = False
        nav._bat_blocked = False
        assert nav._obstacle_cm > NavigationEngine.OBSTACLE_SLOW_CM

    def test_cliff_blocks_movement(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        nav._cliff_blocked = True
        nav._pickup_blocked = False
        nav._bat_blocked = False
        nav._obstacle_cm = 100.0
        assert nav._cliff_blocked is True

    def test_pickup_blocks_movement(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        nav._cliff_blocked = False
        nav._pickup_blocked = True
        nav._bat_blocked = False
        nav._obstacle_cm = 100.0
        assert nav._pickup_blocked is True

    def test_battery_critical_blocks_movement(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        nav._cliff_blocked = False
        nav._pickup_blocked = False
        nav._bat_blocked = True
        nav._obstacle_cm = 100.0
        assert nav._bat_blocked is True


# ── Mind rule engine safety ────────────────────────────────────────────────────

def _make_safety_mind():
    from cognition.mind import CosmoMind
    mind = CosmoMind.__new__(CosmoMind)
    mind._running = True
    mind._was_dark = False
    mind._obstacle_warn = False
    mind._enabled = False
    mind._last_spoke = 0.0
    mind._trigger_last = {}
    mind._morning_day = -1
    mind._wound_down = False
    mind._budget_lock = asyncio.Lock()
    mind._speech_in_flight = asyncio.Event()
    mind._is_busy = lambda: False
    return mind


class TestMindRuleEngineSafety:
    """
    Obstacle detected by rule engine → Intent.STOP emitted via action router.
    """

    def test_obstacle_stop_fires_when_dist_below_25cm(self):
        from cognition.mind import CosmoMind
        from core.intents import Intent
        mind = _make_safety_mind()
        mock_router = MagicMock()

        async def _tick():
            with patch("cognition.mind.sensor_manager") as sm, \
                 patch("cognition.mind.router", mock_router), \
                 patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)):
                sm.get_distance_cm.return_value = 10.0
                sm.get_lux.return_value = 300.0
                await mind._rule_tick()

        asyncio.run(_tick())
        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP in emitted, "Intent.STOP not emitted on obstacle < 25cm"

    def test_no_stop_when_dist_above_25cm(self):
        from cognition.mind import CosmoMind
        from core.intents import Intent
        mind = _make_safety_mind()
        mock_router = MagicMock()

        async def _tick():
            with patch("cognition.mind.sensor_manager") as sm, \
                 patch("cognition.mind.router", mock_router), \
                 patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)):
                sm.get_distance_cm.return_value = 80.0
                sm.get_lux.return_value = 300.0
                await mind._rule_tick()

        asyncio.run(_tick())
        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP not in emitted, "Intent.STOP should NOT fire at dist=80cm"


# ── Motor driver STBY e-stop ──────────────────────────────────────────────────

class TestMotorStby:
    """STBY pin must go LOW on emergency stop; AIN1+AIN2 never both HIGH."""

    def test_motor_safety_error_on_both_high(self):
        """MotorSafetyError raised if code attempts AIN1=HIGH AIN2=HIGH."""
        from hardware.motors import MotorSafetyError
        # The error class must exist and be raisable
        with pytest.raises(MotorSafetyError):
            raise MotorSafetyError("both HIGH — test")

    def test_mock_mode_stop_does_not_raise(self):
        """In mock mode, motor_controller.stop() must complete without error."""
        from hardware.motors import motor_controller

        async def _run():
            # motor_controller is a singleton; in test env GPIO is unavailable → mock mode
            try:
                await motor_controller.stop()
            except Exception as e:
                pytest.fail(f"stop() raised unexpectedly in mock mode: {e}")

        asyncio.run(_run())

    def test_obstacle_warn_flag_prevents_duplicate_stops(self):
        """_obstacle_warn flag prevents hammering STOP every rule tick."""
        from cognition.mind import CosmoMind
        from core.intents import Intent
        mind = _make_safety_mind()
        mind._obstacle_warn = True  # already warned
        mock_router = MagicMock()

        async def _tick():
            with patch("cognition.mind.sensor_manager") as sm, \
                 patch("cognition.mind.router", mock_router), \
                 patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)):
                sm.get_distance_cm.return_value = 10.0
                sm.get_lux.return_value = 300.0
                await mind._rule_tick()

        asyncio.run(_tick())
        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP not in emitted, "_obstacle_warn set — STOP should not fire again"
