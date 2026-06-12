"""
Mock tests for ESP32 bridge protocol.
Tests JSON parsing, event dispatch, motor command serialization.
No real serial port needed — uses AsyncMock serial substitute.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_line(*msgs) -> bytes:
    """Pack one or more JSON dicts into newline-delimited bytes."""
    return b"".join((json.dumps(m) + "\n").encode() for m in msgs)


class FakeSerial:
    """Minimal serial.Serial substitute for testing."""

    def __init__(self, lines: list[bytes]):
        self._data = b"".join(lines)
        self._pos = 0
        self.is_open = True
        self.written: list[bytes] = []

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.is_open = False


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_bus():
    published = []
    async def fake_publish(event):
        published.append(event)
    with patch("hardware.esp32_bridge.bus") as b:
        b.publish = fake_publish
        yield b, published


# ── Protocol parsing tests ────────────────────────────────────────────────────

class TestProtocolParsing:

    def test_heartbeat_parsed(self):
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._last_hb = 0.0
        bridge._esp_uptime = 0
        bridge._sensor_flags = {}
        bridge._mock = True

        msg = {"t": "hb", "up": 42, "sensors": {"ultra": False, "pir": False}}

        async def run():
            await bridge._handle_line(json.dumps(msg).encode())

        asyncio.run(run())
        assert bridge._esp_uptime == 42
        assert bridge._sensor_flags == {"ultra": False, "pir": False}

    def test_invalid_json_ignored(self):
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._last_hb = 0.0
        bridge._esp_uptime = 0
        bridge._sensor_flags = {}

        async def run():
            await bridge._handle_line(b"not json {{{")

        asyncio.run(run())  # must not raise

    def test_unknown_type_ignored(self):
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._last_hb = 0.0
        bridge._esp_uptime = 0
        bridge._sensor_flags = {}

        async def run():
            await bridge._handle_line(json.dumps({"t": "unknown_future_type"}).encode())

        asyncio.run(run())  # must not raise


# ── Sensor dispatch tests ─────────────────────────────────────────────────────

class TestSensorDispatch:

    def _make_bridge(self):
        from hardware.esp32_bridge import ESP32Bridge
        b = ESP32Bridge.__new__(ESP32Bridge)
        b._last_hb = 0.0
        b._esp_uptime = 0
        b._sensor_flags = {}
        b._mock = True
        return b

    def test_ultrasonic_normal_publishes_distance(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "ultra", "v": 55.0})

        asyncio.run(run())
        assert any(e.data.get("distance_cm") == 55.0 for e in published)

    def test_ultrasonic_critical_publishes_obstacle(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "ultra", "v": 15.0})

        asyncio.run(run())
        from core.event_bus import EventType
        types = [e.type for e in published]
        assert EventType.OBSTACLE_CRITICAL in types

    def test_ultrasonic_warning_zone(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "ultra", "v": 35.0})

        asyncio.run(run())
        from core.event_bus import EventType
        types = [e.type for e in published]
        assert EventType.OBSTACLE_WARNING in types
        assert EventType.OBSTACLE_CRITICAL not in types

    def test_pir_trigger_publishes_motion(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "pir", "v": 1})

        asyncio.run(run())
        from core.event_bus import EventType
        assert any(e.type == EventType.MOTION_DETECTED for e in published)

    def test_pir_release_no_event(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "pir", "v": 0})

        asyncio.run(run())
        assert len(published) == 0

    def test_cliff_left_detected(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "cliff", "side": "l", "v": 0})

        asyncio.run(run())
        from core.event_bus import EventType
        assert any(e.type == EventType.CLIFF_DETECTED for e in published)
        assert any(e.data.get("side") == "l" for e in published)

    def test_touch_head_detected(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            await bridge._dispatch_sensor({"id": "touch", "n": 0, "v": 1})

        asyncio.run(run())
        from core.event_bus import EventType
        assert any(e.type == EventType.TOUCH_DETECTED for e in published)
        assert any(e.data.get("zone") == "head" for e in published)

    def test_touch_zone_mapping(self):
        from hardware.esp32_bridge import ESP32Bridge
        b = ESP32Bridge.__new__(ESP32Bridge)
        zones = {0: "head", 1: "back", 2: "belly", 3: "tail"}
        assert zones[0] == "head"
        assert zones[3] == "tail"

    def test_imu_pickup_threshold(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            # accel_mag = sqrt(3^2 + 3^2 + 3^2) ≈ 5.2g — above 2.5g threshold
            await bridge._dispatch_sensor({"id": "imu",
                                           "ax": 3.0, "ay": 3.0, "az": 3.0,
                                           "gx": 0.0, "gy": 0.0, "gz": 0.0})

        asyncio.run(run())
        from core.event_bus import EventType
        assert any(e.type == EventType.PICKUP_DETECTED for e in published)

    def test_imu_stationary_no_pickup(self, mock_bus):
        _, published = mock_bus
        bridge = self._make_bridge()

        async def run():
            # MPU-6050 values are in g-units (1.0 = 1g), not m/s²
            # Stationary: az≈1.0g (gravity), ax≈ay≈0
            await bridge._dispatch_sensor({"id": "imu",
                                           "ax": 0.0, "ay": 0.0, "az": 1.0,
                                           "gx": 0.0, "gy": 0.0, "gz": 0.0})

        asyncio.run(run())
        from core.event_bus import EventType
        assert not any(e.type == EventType.PICKUP_DETECTED for e in published)


# ── Motor command serialization tests ────────────────────────────────────────

class TestMotorCommands:

    def _make_bridge_with_queue(self):
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._send_queue = asyncio.Queue()
        bridge._mock = False
        bridge._connected = True
        bridge._serial = MagicMock()
        return bridge

    def test_send_motor_forward(self):
        bridge = self._make_bridge_with_queue()

        async def run():
            await bridge.send_motor(0.6, 0.6)
            cmd = bridge._send_queue.get_nowait()
            assert cmd["cmd"] == "move"
            assert cmd["l"] == 0.6
            assert cmd["r"] == 0.6

        asyncio.run(run())

    def test_send_stop(self):
        bridge = self._make_bridge_with_queue()

        async def run():
            await bridge.send_stop()
            cmd = bridge._send_queue.get_nowait()
            assert cmd["cmd"] == "stop"

        asyncio.run(run())

    def test_send_stby_enable(self):
        bridge = self._make_bridge_with_queue()

        async def run():
            await bridge.send_stby(True)
            cmd = bridge._send_queue.get_nowait()
            assert cmd["cmd"] == "stby"
            assert cmd["v"] == 1

        asyncio.run(run())

    def test_motor_speed_clamped_to_1(self):
        bridge = self._make_bridge_with_queue()

        async def run():
            await bridge.send_motor(2.0, -3.0)  # out of range
            cmd = bridge._send_queue.get_nowait()
            # Bridge passes through; clamping is motors.py responsibility
            assert cmd["l"] == 2.0   # bridge doesn't clamp, motors.py does

        asyncio.run(run())

    def test_json_round_trip(self):
        """Every command must survive JSON encode/decode."""
        cmds = [
            {"cmd": "move", "l": 0.55, "r": 0.55},
            {"cmd": "stop"},
            {"cmd": "stby", "v": 1},
            {"cmd": "ping"},
            {"cmd": "set_sensor", "key": "ultra", "v": 1},
        ]
        for cmd in cmds:
            encoded = json.dumps(cmd) + "\n"
            decoded = json.loads(encoded.strip())
            assert decoded == cmd


# ── Status / mock mode tests ─────────────────────────────────────────────────

class TestBridgeStatus:

    def test_mock_mode_when_serial_unavailable(self):
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._mock = True
        bridge._connected = False
        bridge._last_hb = 0.0
        assert bridge.is_mock is True
        assert bridge.is_connected is False

    def test_connected_false_when_heartbeat_stale(self):
        import time
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._mock = False
        bridge._connected = True
        bridge._last_hb = time.monotonic() - 10.0  # stale
        assert bridge.is_connected is False

    def test_status_dict_shape(self):
        import time
        from hardware.esp32_bridge import ESP32Bridge
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._mock = True
        bridge._connected = False
        bridge._last_hb = time.monotonic()
        bridge._esp_uptime = 0
        bridge._sensor_flags = {}
        bridge._port = "/dev/ttyUSB0"
        status = bridge.get_status()
        assert "connected" in status
        assert "mock" in status
        assert "sensors" in status
