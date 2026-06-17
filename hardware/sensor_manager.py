"""
Sensor manager — Pi-side sensors only.

Architecture (post-ESP32 rework):
  Pi I2C  → BH1750 light sensor (0x23), UPS HAT battery (0x36)
  ESP32   → all GPIO sensors (PIR, cliff, touch, sound, vibration, IMU, ultrasonic)
             events arrive via esp32_bridge → event bus (already published)

This module:
  - Polls BH1750 and publishes LIGHT_CHANGED
  - Polls UPS HAT and publishes BATTERY_LOW / BATTERY_CRITICAL
  - Subscribes to bridge-originated events → runs personality.process_event()
  - Provides get_lux(), get_battery(), get_distance_cm(), get_imu() getters
"""

import asyncio
import math
import random
import time
from typing import Any, Dict, List, Optional

from core.event_bus import Event, EventPriority, EventType, bus
from core.personality import personality
from hardware.registry import hw_registry
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)

try:
    import smbus2 as smbus
    _SMBUS_OK = True
except ImportError:
    _SMBUS_OK = False

from hardware.i2c_bus import i2c_lock

_i2c_bus_1: Optional["smbus.SMBus"] = None

def _i2c_bus() -> "smbus.SMBus":
    global _i2c_bus_1
    if _i2c_bus_1 is None:
        _i2c_bus_1 = smbus.SMBus(1)
    return _i2c_bus_1

# Personality event keys
_P_TOUCH    = "touch_detected"
_P_PICKUP   = "pickup_detected"
_P_GESTURE  = "gesture_detected"
_P_MOTION   = "motion_detected"
_P_BAT_LOW  = "battery_low"
_P_BAT_CRIT = "battery_critical"


# ── BH1750 Light Sensor ───────────────────────────────────────────────────────

class BH1750Sensor:
    ADDR = 0x23
    CMD_CONT_HIGH = 0x10

    def __init__(self) -> None:
        self._mock = True

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            return False
        try:
            with i2c_lock:
                _i2c_bus().write_byte(self.ADDR, self.CMD_CONT_HIGH)
            time.sleep(0.18)
            self._mock = False
            log.info("bh1750.real")
            return True
        except Exception as e:
            log.info("bh1750.mock", reason=str(e)[:60])
            return False

    def read_lux(self) -> float:
        if self._mock:
            return self._mock_lux()
        try:
            with i2c_lock:
                data = _i2c_bus().read_i2c_block_data(self.ADDR, self.CMD_CONT_HIGH, 2)
            return ((data[0] << 8) | data[1]) / 1.2
        except Exception:
            return self._mock_lux()

    def _mock_lux(self) -> float:
        hour = (time.time() % 86400) / 3600
        lux = 5 + 795 * max(0, math.sin(math.pi * (hour - 6) / 12)) ** 2
        return lux + random.gauss(0, max(1, lux * 0.03))


# ── UPS HAT Battery Sensor ────────────────────────────────────────────────────

class UPSHATSensor:
    ADDR = 0x36

    def __init__(self) -> None:
        self._mock = True
        self._mock_pct = 85.0
        self._last_mock_t = time.monotonic()

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            return False
        try:
            with i2c_lock:
                _i2c_bus().read_i2c_block_data(self.ADDR, 0x04, 2)
            self._mock = False
            log.info("ups_hat.real")
            return True
        except Exception as e:
            log.info("ups_hat.mock", reason=str(e)[:60])
            return False

    def read(self) -> Dict[str, Any]:
        if self._mock:
            return {"percent": 100.0, "voltage": 0.0, "charging": False, "mock": True}
        try:
            with i2c_lock:
                raw = _i2c_bus().read_i2c_block_data(self.ADDR, 0x04, 2)
                raw_v = _i2c_bus().read_i2c_block_data(self.ADDR, 0x02, 2)
            pct = min(100.0, ((raw[0] << 8) | raw[1]) >> 4) * 0.02441
            volts = ((raw_v[0] << 8) | raw_v[1]) * 1.25 / 1000 / 16
            return {"percent": round(pct, 1), "voltage": round(volts, 2), "charging": False, "mock": False}
        except Exception:
            return {"percent": 0.0, "voltage": 0.0, "charging": False, "mock": False}


# ── Sensor Manager ────────────────────────────────────────────────────────────

class SensorManager:
    """
    Manages Pi-local sensors (BH1750, UPS HAT) and wires bridge events
    to personality engine. ESP32 bridge publishes all GPIO sensor events.
    """

    def __init__(self) -> None:
        self.bh1750 = BH1750Sensor()
        self.ups    = UPSHATSensor()

        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._last_lux: float = 0.0
        self._last_bat_pct: float = 100.0
        self._bat_warned_low = False
        self._bat_warned_crit = False

        # ESP32 proxied state (updated from bridge events)
        self._last_distance_cm: float = 100.0
        self._last_imu: Dict[str, Any] = {}
        self._touch_press_times: Dict[str, float] = {}
        self._long_fired: set = set()

    def initialize_all(self) -> None:
        time.sleep(0.3)
        for sensor, name, mock_desc in [
            (self.bh1750, "sensor.bh1750", "time-of-day lux curve"),
            (self.ups,    "sensor.ups",    "drains 0.1%/min from 85%"),
        ]:
            sensor.initialize()
            time.sleep(0.05)
            if sensor._mock:
                hw_registry.report_mock(name, reason="hardware not detected",
                                        mock_behavior=mock_desc)
            else:
                hw_registry.report_real(name)

        hw_registry.report_mock("sensor.pir",        reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.ultrasonic", reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.imu",        reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.cliff",      reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.touch",      reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.sound",      reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.report_mock("sensor.vibration",  reason="on ESP32",
                                mock_behavior="via esp32_bridge")
        hw_registry.log_summary()

    async def start(self) -> None:
        self.initialize_all()
        self._running = True
        self._wire_bridge_events()
        self._tasks = [
            asyncio.create_task(self._poll_light()),
            asyncio.create_task(self._poll_battery()),
        ]
        log.info("sensor_manager.started", mode="pi_i2c+esp32_bridge")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        log.info("sensor_manager.stopped")

    # ── Wire bridge → personality ─────────────────────────────────────────────

    def _wire_bridge_events(self) -> None:
        """Subscribe to events the bridge publishes and apply personality effects."""

        @bus.on(EventType.MOTION_DETECTED)
        async def _on_motion(event: Event) -> None:
            personality.process_event(_P_MOTION)

        @bus.on(EventType.TOUCH_DETECTED)
        async def _on_touch(event: Event) -> None:
            personality.process_event(_P_TOUCH)
            zone = event.data.get("zone", "head")
            self._touch_press_times[zone] = time.monotonic()
            self._long_fired.discard(zone)

        @bus.on(EventType.PICKUP_DETECTED)
        async def _on_pickup(event: Event) -> None:
            personality.process_event(_P_PICKUP)

        @bus.on(EventType.GESTURE_DETECTED)
        async def _on_gesture(event: Event) -> None:
            personality.process_event(_P_GESTURE)

        @bus.on(EventType.DISTANCE_UPDATED)
        async def _on_distance(event: Event) -> None:
            d = event.data.get("distance_cm")
            if d is not None:
                self._last_distance_cm = float(d)
            imu = event.data.get("imu")
            if imu:
                self._last_imu = imu

        log.info("sensor_manager.bridge_events_wired")

    # ── Pi I2C poll loops ─────────────────────────────────────────────────────

    async def _poll_light(self) -> None:
        from core.capabilities import Capability, registry as cap_registry
        while self._running:
            lux = self.bh1750.read_lux()
            if self.bh1750._mock:
                cap_registry.simulate(Capability.AMBIENT_LIGHT)
            else:
                cap_registry.mark_seen(Capability.AMBIENT_LIGHT, "bh1750 poll")
            if abs(lux - self._last_lux) > 50:
                self._last_lux = lux
                await bus.publish(Event(
                    type=EventType.LIGHT_CHANGED,
                    data={"lux": round(lux, 1)},
                    priority=EventPriority.NORMAL,
                ))
            await asyncio.sleep(30)

    async def _poll_battery(self) -> None:
        while self._running:
            data = self.ups.read()
            pct = data["percent"]
            self._last_bat_pct = pct
            if pct <= 5 and not self._bat_warned_crit:
                self._bat_warned_crit = True
                await bus.publish(Event(
                    type=EventType.BATTERY_CRITICAL,
                    data=data, priority=EventPriority.SAFETY,
                ))
                personality.process_event(_P_BAT_CRIT)
                log.warning("sensor.battery_critical", pct=pct)
            elif pct <= 20 and not self._bat_warned_low:
                self._bat_warned_low = True
                await bus.publish(Event(
                    type=EventType.BATTERY_LOW,
                    data=data, priority=EventPriority.NORMAL,
                ))
                personality.process_event(_P_BAT_LOW)
                log.warning("sensor.battery_low", pct=pct)
            elif pct > 25:
                self._bat_warned_low = False
            if pct > 10:
                self._bat_warned_crit = False
            await asyncio.sleep(60)

    # ── Public getters ────────────────────────────────────────────────────────

    def get_lux(self) -> float:
        return self._last_lux

    def get_battery(self) -> Dict[str, Any]:
        return self.ups.read()

    def get_distance_cm(self) -> float:
        return self._last_distance_cm

    def get_imu(self) -> Dict[str, Any]:
        return self._last_imu

    @property
    def is_mock(self) -> bool:
        return self.bh1750._mock


sensor_manager = SensorManager()
