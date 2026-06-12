"""
KI-019 — shared I2C bus mutex.

Every smbus transaction (sensor_manager) and OLED frame display (eyes.py)
must hold hardware.i2c_bus.i2c_lock, so the executor-thread OLED render
cannot interleave with loop-thread sensor reads.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hardware.i2c_bus import i2c_lock
from hardware.sensor_manager import BH1750Sensor, UPSHATSensor
from expression.eyes import EyeEngine


class _LockAssertingBus:
    """Fake smbus that records whether i2c_lock was held during each call."""

    def __init__(self):
        self.held_during_calls = []

    def write_byte(self, *a):
        self.held_during_calls.append(i2c_lock.locked())

    def read_i2c_block_data(self, *a):
        self.held_during_calls.append(i2c_lock.locked())
        return [0x12, 0x34]


class TestSensorReadsHoldLock:

    def test_bh1750_read_lux_holds_lock(self):
        bus = _LockAssertingBus()
        s = BH1750Sensor()
        s._mock = False
        with patch("hardware.sensor_manager._i2c_bus", return_value=bus):
            s.read_lux()
        assert bus.held_during_calls and all(bus.held_during_calls)

    def test_ups_read_holds_lock(self):
        bus = _LockAssertingBus()
        s = UPSHATSensor()
        s._mock = False
        with patch("hardware.sensor_manager._i2c_bus", return_value=bus):
            s.read()
        assert len(bus.held_during_calls) == 2
        assert all(bus.held_during_calls)


class TestOledRenderHoldsLock:

    def test_render_oled_holds_lock_per_frame(self):
        held = []
        oled = MagicMock()
        oled.display.side_effect = lambda img: held.append(i2c_lock.locked())

        engine = EyeEngine()
        engine._oled_left = oled
        engine._oled_right = oled
        engine._render_oled()

        assert len(held) == 2 and all(held)
        # Lock released between frames so sensor reads can interleave
        assert not i2c_lock.locked()


class TestLockIsProcessWide:

    def test_same_lock_object_everywhere(self):
        import hardware.sensor_manager as sm
        assert sm.i2c_lock is i2c_lock
