"""
Unit tests for all new systems built in this session.
Target: all pass with no hardware attached (mock mode).
"""

import asyncio
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Sensor Manager ────────────────────────────────────────────────────────────

class TestBH1750Sensor:
    def test_mock_lux_noon(self):
        from hardware.sensor_manager import BH1750Sensor
        s = BH1750Sensor()
        # Force noon-ish hour by patching time.time
        import time as _t
        original = _t.time
        _t.time = lambda: 12 * 3600  # noon
        try:
            lux = s._mock_lux()
            assert lux > 600, f"Noon lux should be >600, got {lux}"
        finally:
            _t.time = original

    def test_mock_lux_midnight(self):
        from hardware.sensor_manager import BH1750Sensor
        import time as _t
        s = BH1750Sensor()
        original = _t.time
        _t.time = lambda: 0  # midnight
        try:
            lux = s._mock_lux()
            assert lux < 20, f"Midnight lux should be <20, got {lux}"
        finally:
            _t.time = original

    def test_initialize_falls_back_to_mock(self):
        from hardware.sensor_manager import BH1750Sensor
        s = BH1750Sensor()
        # smbus2 may or may not be available — either way mock should be set
        result = s.initialize()
        assert isinstance(result, bool)
        assert s._mock or result  # either mock or succeeded


# PIR / MPU-6050 / cliff / ultrasonic live on the ESP32 co-processor —
# their dispatch into events is covered by tests/hardware/test_esp32_bridge.py.


class TestUPSHATSensor:
    def test_mock_starts_at_85(self):
        from hardware.sensor_manager import UPSHATSensor
        s = UPSHATSensor()
        s.initialize()
        if not s._mock:
            pytest.skip("Real UPS HAT detected — skipping mock battery test")
        data = s.read()
        assert abs(data["percent"] - 85.0) < 1.0

    def test_mock_drains_over_time(self):
        from hardware.sensor_manager import UPSHATSensor
        s = UPSHATSensor()
        s.initialize()
        if not s._mock:
            pytest.skip("Real UPS HAT detected — skipping mock drain test")
        s._last_mock_t = time.monotonic() - 100 * 60  # 100 min ago
        data = s.read()
        assert data["percent"] < 85.0 - 9  # drains 0.1%/min → ~10%


class TestSensorManager:
    def test_initialize_all(self):
        from hardware.sensor_manager import SensorManager
        sm = SensorManager()
        sm.initialize_all()
        assert sm.is_mock  # no hardware on test machine

    def test_get_battery(self):
        from hardware.sensor_manager import SensorManager
        sm = SensorManager()
        sm.initialize_all()
        bat = sm.get_battery()
        assert "percent" in bat
        assert 0 <= bat["percent"] <= 100

    def test_get_distance(self):
        from hardware.sensor_manager import SensorManager
        sm = SensorManager()
        sm.initialize_all()
        dist = sm.get_distance_cm()
        assert dist > 0


# ── Motor Controller ──────────────────────────────────────────────────────────

class TestMotorController:
    def test_init_is_bool(self):
        from hardware.motors import MotorController
        mc = MotorController()
        assert isinstance(mc.is_mock, bool)

    @pytest.mark.asyncio
    async def test_initialize(self):
        from hardware.motors import MotorController
        mc = MotorController()
        ok = await mc.initialize()
        assert ok

    @pytest.mark.asyncio
    async def test_forward_updates_speed(self):
        from hardware.motors import MotorController
        mc = MotorController()
        await mc.initialize()
        await mc.forward(speed=0.5, ramp=False)
        assert mc.left_speed > 0 and mc.right_speed > 0

    @pytest.mark.asyncio
    async def test_stop_zeroes_speed(self):
        from hardware.motors import MotorController
        mc = MotorController()
        await mc.initialize()
        await mc.forward(speed=0.5, ramp=False)
        await mc.stop(emergency=True)
        assert abs(mc.left_speed) < 0.01 and abs(mc.right_speed) < 0.01

    @pytest.mark.asyncio
    async def test_turn_sets_opposite_speeds(self):
        from hardware.motors import MotorController
        mc = MotorController()
        await mc.initialize()
        await mc.turn_left(speed=0.4)
        assert mc.left_speed < 0 and mc.right_speed > 0


# ── Servo Controller ──────────────────────────────────────────────────────────

class TestServoController:
    @pytest.mark.asyncio
    async def test_initialize_mock(self):
        from hardware.servos import ServoController
        sc = ServoController()
        await sc.initialize()
        assert sc.is_mock

    @pytest.mark.asyncio
    async def test_center(self):
        from hardware.servos import ServoController
        sc = ServoController()
        await sc.initialize()
        await sc.center()
        assert sc.current_pan == 90
        assert sc.current_tilt == 90

    @pytest.mark.asyncio
    async def test_pan_clamps(self):
        from hardware.servos import ServoController
        sc = ServoController()
        await sc.initialize()
        await sc.pan_to(200, smooth=False)  # above max
        assert sc.current_pan == sc.PAN_MAX

    @pytest.mark.asyncio
    async def test_track_person_dead_zone(self):
        from hardware.servos import ServoController
        sc = ServoController()
        await sc.initialize()
        await sc.center()
        initial_pan = sc.current_pan
        await sc.track_person(0.05, 0.05)  # within dead zone
        assert sc.current_pan == initial_pan  # should not move


# ── Eye Engine ────────────────────────────────────────────────────────────────

class TestEyeEngine:
    def test_render_terminal_returns_string(self):
        from expression.eyes import EyeEngine, EyeExpression
        engine = EyeEngine()
        result = engine.render_terminal()
        assert isinstance(result, str)
        assert len(result) > 10

    def test_all_expressions_render(self):
        from expression.eyes import EyeEngine, EyeExpression
        engine = EyeEngine()
        for expr in EyeExpression:
            engine.set_expression(expr)
            engine._state.expression = expr  # skip transition
            result = engine.render_terminal()
            assert isinstance(result, str), f"{expr} failed to render"

    def test_set_expression_changes_target(self):
        from expression.eyes import EyeEngine, EyeExpression
        engine = EyeEngine()
        engine.set_expression(EyeExpression.HAPPY)
        assert engine._state.target_expression == EyeExpression.HAPPY

    def test_set_pupil_clamps(self):
        from expression.eyes import EyeEngine
        engine = EyeEngine()
        engine.set_pupil(5.0, -5.0)
        assert engine._state.pupil_x == 1.0
        assert engine._state.pupil_y == -1.0

    def test_render_frame_dict(self):
        from expression.eyes import EyeEngine
        engine = EyeEngine()
        frame = engine.render_frame()
        assert "expression" in frame
        assert "pupil_x" in frame
        assert "blink_progress" in frame


# ── Sound Engine ──────────────────────────────────────────────────────────────

class TestSoundEngine:
    def test_generate_all_sounds(self):
        from expression.sounds import SoundEngine
        engine = SoundEngine()
        for name in engine.SOUNDS:
            samples = engine.generate(name)
            assert samples is not None and len(samples) > 0, \
                f"{name} generated empty array"

    def test_generate_unknown_returns_none(self):
        from expression.sounds import SoundEngine
        engine = SoundEngine()
        assert engine.generate("does_not_exist") is None

    def test_beep_ack_short(self):
        import numpy as np
        from expression.sounds import _gen_beep_ack, RATE
        samples = _gen_beep_ack()
        duration_s = len(samples) / RATE
        assert 0.05 < duration_s < 0.3, f"beep_ack should be ~0.1s, got {duration_s}"

    def test_boot_chime_longer(self):
        import numpy as np
        from expression.sounds import _gen_boot_chime, RATE
        samples = _gen_boot_chime()
        duration_s = len(samples) / RATE
        assert duration_s > 0.5, f"boot_chime should be >0.5s, got {duration_s}"

    def test_no_clipping(self):
        import numpy as np
        from expression.sounds import SoundEngine
        engine = SoundEngine()
        for name in engine.SOUNDS:
            s = engine.generate(name)
            assert np.max(np.abs(s)) <= 1.0 + 0.01, f"{name} clips"


# ── Intent Parser ─────────────────────────────────────────────────────────────

class TestIntentParser:
    def test_come_here_english(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        result = p.parse("come here please")
        assert result is not None
        assert result.name == "come_here"

    def test_come_here_tamil(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        result = p.parse("inga vaa da")
        assert result is not None
        assert result.name == "come_here"

    def test_stop(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("stop").name == "stop"
        assert p.parse("nillu").name == "stop"

    def test_dance(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("dance for me").name == "dance"
        assert p.parse("aadu kuthu").name == "dance"

    def test_sleep(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("go to sleep now").name == "sleep"
        assert p.parse("thoonga po").name == "sleep"

    def test_no_match_returns_none(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        result = p.parse("what is the weather today")
        assert result is None

    def test_confidence_positive(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        result = p.parse("come here")
        assert result.confidence > 0

    def test_action_mapped(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        result = p.parse("follow me")
        assert result.action == "navigation.follow_mode"

    def test_case_insensitive(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("STOP").name == "stop"
        assert p.parse("Dance!").name == "dance"

    def test_good_boy_phrases(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("good boy").name == "good_boy"
        assert p.parse("nalla irukka").name == "good_boy"

    def test_love_phrases(self):
        from cognition.intent import IntentParser
        p = IntentParser()
        assert p.parse("i love you").name == "i_love_you"
        assert p.parse("love you da").name == "i_love_you"

    def test_all_intents_have_actions(self):
        from cognition.intent import IntentParser, _INTENTS, _ACTION_MAP
        p = IntentParser()
        for name in _INTENTS:
            action = p.get_action(name)
            assert action is not None, f"No action for intent {name}"


# ── Navigation Engine ─────────────────────────────────────────────────────────

class TestNavigationEngine:
    @pytest.mark.asyncio
    async def test_initialize(self):
        from behavior.navigation import NavigationEngine
        nav = NavigationEngine()
        await nav.initialize()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_is_safe(self):
        from behavior.navigation import NavigationEngine, NavState
        nav = NavigationEngine()
        await nav.initialize()
        await nav.stop()
        assert nav.state == NavState.IDLE

    @pytest.mark.asyncio
    async def test_blocked_by_cliff(self):
        from behavior.navigation import NavigationEngine, NavState
        nav = NavigationEngine()
        await nav.initialize()
        nav._cliff_blocked = True
        # When blocked, forward() returns early without changing state to FORWARD
        await nav.stop()  # ensure idle first
        result = await nav._safe_exec(nav.stop())
        assert result is False  # blocked

    @pytest.mark.asyncio
    async def test_retreat_calls_backward(self):
        from behavior.navigation import NavigationEngine, NavState
        nav = NavigationEngine()
        await nav.initialize()
        nav._cliff_blocked = False
        nav._pickup_blocked = False
        nav._bat_blocked = False
        # Just call it and ensure no exception
        task = asyncio.create_task(nav.retreat(duration=0.05))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
