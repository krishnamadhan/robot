"""
Unified sensor manager — all sensors with mock fallback.

Every sensor tries real hardware first. If unavailable (ImportError, IOError),
it silently falls back to mock. When hardware arrives: set available=true in
config/hardware.yaml and the real driver activates automatically.

Mock behaviors:
  BH1750     — time-of-day lux curve
  PIR        — random trigger every 2-5 min  (GPIO8)
  Touch      — never triggers (needs physical touch)  (GPIO5,4,7; belly TBD)
  APDS9960   — random gesture every 10 min
  MPU6050    — slight drift, random pickup spike
  Cliff      — always safe  (GPIO14,15 via LLC)
  Ultrasonic — 100cm open space  (TRIG=GPIO16, ECHO=GPIO24 via LLC)
  Sound      — never triggers (needs audio input)  (GPIO11 via divider)
  Vibration  — never triggers  (pin reassigned to motor — see hardware.yaml)
  UPS HAT    — drains 0.1%/min from 85%
"""

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.event_bus import Event, EventPriority, EventType, bus
from core.personality import personality
from hardware.registry import hw_registry, HWStatus
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)

try:
    import smbus2 as smbus
    _SMBUS_OK = True
except ImportError:
    _SMBUS_OK = False

# ── Shared I2C bus singleton ──────────────────────────────────────────────────
# Pi 5 RP1 driver returns EAGAIN on concurrent ioctl from different FDs.
# All sensors share ONE open handle; an asyncio.Lock serializes access.
_i2c_bus_1: Optional["smbus.SMBus"] = None
_i2c_lock_1: Optional[asyncio.Lock] = None


def _i2c_bus() -> "smbus.SMBus":
    global _i2c_bus_1
    if _i2c_bus_1 is None:
        _i2c_bus_1 = smbus.SMBus(1)
    return _i2c_bus_1


def _i2c_lock() -> asyncio.Lock:
    global _i2c_lock_1
    if _i2c_lock_1 is None:
        _i2c_lock_1 = asyncio.Lock()
    return _i2c_lock_1

try:
    from gpiozero import DigitalInputDevice
    _GPIO_OK = True
except ImportError:
    _GPIO_OK = False


# ── Personality event keys (must match personality.yaml) ─────────────────────
_P_TOUCH    = "touch_detected"
_P_PICKUP   = "pickup_detected"
_P_GESTURE  = "gesture_detected"
_P_MOTION   = "motion_detected"
_P_BAT_LOW  = "battery_low"
_P_BAT_CRIT = "battery_critical"


# ── BH1750 Light Sensor ───────────────────────────────────────────────────────

class BH1750Sensor:
    """Ambient light sensor — I2C 0x23."""

    ADDR = 0x23
    CMD_CONT_HIGH = 0x10

    def __init__(self) -> None:
        self._mock = True
        self._bus = None
        self._last_lux: float = 0.0
        self._last_published: float = 0.0

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            log.info("bh1750.mock", reason="smbus2 not installed")
            return False
        try:
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
            data = _i2c_bus().read_i2c_block_data(self.ADDR, self.CMD_CONT_HIGH, 2)
            raw = (data[0] << 8) | data[1]
            return raw / 1.2
        except Exception:
            return self._mock_lux()

    def _mock_lux(self) -> float:
        hour = (time.time() % 86400) / 3600  # 0-24
        # Sunrise 6am, peak noon 800 lux, sunset 6pm, dark night 5 lux
        lux = 5 + 795 * max(0, math.sin(math.pi * (hour - 6) / 12)) ** 2
        return lux + random.gauss(0, lux * 0.03)


# ── PIR Motion Sensor ─────────────────────────────────────────────────────────

class PIRSensor:
    """HC-SR501 motion sensor — GPIO8 (CORRECTED: was 16, conflicts with HC-SR04 TRIG)."""

    GPIO_PIN = 8

    def __init__(self) -> None:
        self._mock = True
        self._device = None
        self._last_trigger: float = 0.0
        self._next_mock_trigger: float = time.monotonic() + random.uniform(120, 300)

    def initialize(self) -> bool:
        pir_cfg = cfg.hardware.sensors.get("pir", {})
        if not pir_cfg.get("available", False):
            log.info("pir.mock", reason="disabled in hardware.yaml")
            return False
        if not _GPIO_OK:
            log.info("pir.mock", reason="gpiozero not available")
            return False
        try:
            self._device = DigitalInputDevice(self.GPIO_PIN)
            self._mock = False
            log.info("pir.real", pin=self.GPIO_PIN)
            return True
        except Exception as e:
            log.info("pir.mock", reason=str(e)[:60])
            return False

    def check(self) -> bool:
        if self._mock:
            now = time.monotonic()
            if now >= self._next_mock_trigger:
                self._next_mock_trigger = now + random.uniform(120, 300)
                self._last_trigger = now
                return True
            return False
        return bool(self._device and self._device.is_active)


# ── Touch Sensor Array ────────────────────────────────────────────────────────

class TouchSensorArray:
    """TTP223 capacitive touch array — pins from hardware.yaml.
    Note: belly (GPIO25) reassigned to right_rear motor BIN1 — not available.
    Active zones: head=GPIO5, left=GPIO4, right=GPIO7.
    """

    # Default zones and pins — overridden by hardware.yaml touch.pins list
    _DEFAULT_ZONES = ["head", "left", "right"]

    def __init__(self) -> None:
        self._mock = True
        self._devices: Dict[str, Any] = {}
        self._press_start: Dict[str, float] = {}
        # Load pins from config; yaml has list matching zone order
        cfg_t = cfg.hardware.sensors.get("touch", {})
        raw_pins = cfg_t.get("pins", [5, 4, 7])
        self.PINS: Dict[str, int] = {
            self._DEFAULT_ZONES[i]: p
            for i, p in enumerate(raw_pins)
            if i < len(self._DEFAULT_ZONES)
        }

    def initialize(self) -> bool:
        if not _GPIO_OK:
            log.info("touch.mock", reason="gpiozero not available")
            return False
        try:
            for zone, pin in self.PINS.items():
                self._devices[zone] = DigitalInputDevice(pin)
            self._mock = False
            log.info("touch.real", pins=list(self.PINS.values()))
            return True
        except Exception as e:
            log.info("touch.mock", reason=str(e)[:60])
            self._devices = {}
            return False

    def check(self) -> List[str]:
        if self._mock:
            return []  # mock never triggers — needs physical touch
        active = []
        for zone, dev in self._devices.items():
            if dev.is_active:
                active.append(zone)
        return active


# ── APDS9960 Gesture Sensor ───────────────────────────────────────────────────

class APDS9960Sensor:
    """Gesture + proximity sensor — I2C 0x39."""

    ADDR = 0x39
    GESTURES = ["up", "down", "left", "right", "wave"]

    def __init__(self) -> None:
        self._mock = True
        self._next_mock_gesture: float = time.monotonic() + random.uniform(300, 600)

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            log.info("apds9960.mock", reason="smbus2 not installed")
            return False
        try:
            _i2c_bus().read_byte_data(self.ADDR, 0x92)  # WHO_AM_I check
            self._mock = False
            log.info("apds9960.real")
            return True
        except Exception as e:
            log.info("apds9960.mock", reason=str(e)[:60])
            return False

    def check_gesture(self) -> Optional[str]:
        if self._mock:
            now = time.monotonic()
            if now >= self._next_mock_gesture:
                self._next_mock_gesture = now + random.uniform(300, 600)
                return random.choice(self.GESTURES)
            return None
        return None  # real impl: read gesture register


# ── MPU6050 IMU ───────────────────────────────────────────────────────────────

class MPU6050Sensor:
    """6-axis IMU — I2C 0x68."""

    ADDR = 0x68
    PWR_MGMT = 0x6B
    ACCEL_XOUT = 0x3B

    def __init__(self) -> None:
        self._mock = True
        self._bus = None
        self._last_data: Dict[str, Any] = {}

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            log.info("mpu6050.mock", reason="smbus2 not installed")
            return False
        try:
            _i2c_bus().write_byte_data(self.ADDR, self.PWR_MGMT, 0)
            self._mock = False
            log.info("mpu6050.real")
            return True
        except Exception as e:
            log.info("mpu6050.mock", reason=str(e)[:60])
            return False

    def read(self) -> Dict[str, Any]:
        if self._mock:
            return self._mock_imu()
        try:
            raw = _i2c_bus().read_i2c_block_data(self.ADDR, self.ACCEL_XOUT, 14)
            def s(hi, lo): return ((hi << 8) | lo) - (65536 if ((hi << 8) | lo) > 32767 else 0)
            ax, ay, az = s(raw[0], raw[1]) / 16384, s(raw[2], raw[3]) / 16384, s(raw[4], raw[5]) / 16384
            gx, gy, gz = s(raw[8], raw[9]) / 131, s(raw[10], raw[11]) / 131, s(raw[12], raw[13]) / 131
            temp = (s(raw[6], raw[7]) / 340) + 36.53
            return {"accel": {"x": ax, "y": ay, "z": az},
                    "gyro":  {"x": gx, "y": gy, "z": gz},
                    "temp_c": temp}
        except Exception:
            return self._mock_imu()

    def _mock_imu(self) -> Dict[str, Any]:
        # Slight drift + rare pickup spike
        noise = lambda: random.gauss(0, 0.02)
        spike = random.random() < 0.0002  # 0.02% chance per read
        az = 1.0 + noise() + (random.uniform(3, 5) if spike else 0)
        return {
            "accel": {"x": noise(), "y": noise(), "z": az},
            "gyro":  {"x": noise() * 0.5, "y": noise() * 0.5, "z": noise() * 0.5},
            "temp_c": 28.0 + random.gauss(0, 0.1),
            "_spike": spike,
        }


# ── Cliff Sensor Array ────────────────────────────────────────────────────────

class CliffSensorArray:
    """2× TCRT5000 IR cliff sensors — pins and availability from hardware.yaml."""

    def __init__(self) -> None:
        self._mock = True
        self._devices = []
        self._pins: list = []

    def initialize(self) -> bool:
        cliff_cfg = cfg.hardware.sensors.get("cliff", {})
        if not cliff_cfg.get("available", False):
            log.info("cliff.mock", reason="disabled in hardware.yaml")
            return False
        self._pins = cliff_cfg.get("pins", [14, 15])
        if not _GPIO_OK:
            log.info("cliff.mock", reason="gpiozero not installed")
            return False
        try:
            self._devices = [DigitalInputDevice(p) for p in self._pins]
            self._mock = False
            log.info("cliff.real", pins=self._pins)
            return True
        except Exception as e:
            log.info("cliff.mock", reason=str(e)[:60])
            return False

    def is_cliff(self) -> bool:
        if self._mock:
            return False  # always safe in mock
        return any(d.is_active for d in self._devices)


# ── Ultrasonic Sensor ─────────────────────────────────────────────────────────

class UltrasonicSensor:
    """HC-SR04 — TRIG=GPIO16 direct, ECHO=GPIO24 via LLC. Raw lgpio (Pi 5 compatible)."""

    TRIG_PIN = 16
    ECHO_PIN = 24
    TIMEOUT_S = 0.03   # ~5m max range

    def __init__(self) -> None:
        self._mock = True
        self._h = None
        self._last_cm: float = 100.0

    def initialize(self) -> bool:
        if not _GPIO_OK:
            log.info("ultrasonic.mock")
            return False
        try:
            import lgpio
            self._lgpio = lgpio
            self._h = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self._h, self.TRIG_PIN, 0)
            lgpio.gpio_claim_input(self._h, self.ECHO_PIN, lgpio.SET_PULL_NONE)
            self._mock = False
            log.info("ultrasonic.real", trig=self.TRIG_PIN, echo=self.ECHO_PIN)
            return True
        except Exception as e:
            log.info("ultrasonic.mock", reason=str(e)[:60])
            return False

    def read_cm(self) -> float:
        if self._mock:
            return 100.0 + random.gauss(0, 2)
        try:
            lg = self._lgpio
            h = self._h
            # ensure echo is LOW before triggering (skip stale pulse)
            t0 = time.monotonic()
            while lg.gpio_read(h, self.ECHO_PIN) == 1:
                if time.monotonic() - t0 > 0.01:
                    return self._last_cm
            # 10µs trigger pulse + 600µs settle before measuring
            lg.gpio_write(h, self.TRIG_PIN, 1)
            time.sleep(0.00001)
            lg.gpio_write(h, self.TRIG_PIN, 0)
            time.sleep(0.0006)
            # wait for echo HIGH
            t0 = time.monotonic()
            while lg.gpio_read(h, self.ECHO_PIN) == 0:
                if time.monotonic() - t0 > self.TIMEOUT_S:
                    return self._last_cm
            start = time.monotonic()
            # wait for echo LOW
            while lg.gpio_read(h, self.ECHO_PIN) == 1:
                if time.monotonic() - start > self.TIMEOUT_S:
                    return self._last_cm
            elapsed = time.monotonic() - start
            cm = (elapsed * 34300) / 2.0
            if cm < 2 or cm > 400:   # reject out-of-range readings
                return self._last_cm
            self._last_cm = cm
            return cm
        except Exception:
            return self._last_cm

    def __del__(self):
        if self._h is not None:
            try:
                self._lgpio.gpiochip_close(self._h)
            except Exception:
                pass


# ── UPS HAT Battery ───────────────────────────────────────────────────────────

class UPSHATSensor:
    """DFRobot FIT0992 / MAX17043 — I2C 0x36."""

    ADDR = 0x36
    SOC_REG = 0x04
    VOLTAGE_REG = 0x02

    def __init__(self) -> None:
        self._mock = True
        self._bus = None
        self._mock_start_pct = 85.0
        self._mock_start_time = time.monotonic()

    def initialize(self) -> bool:
        if not _SMBUS_OK:
            log.info("ups.mock", reason="smbus2 not installed")
            return False
        try:
            _i2c_bus().read_i2c_block_data(self.ADDR, self.SOC_REG, 2)
            self._mock = False
            log.info("ups.real")
            return True
        except Exception as e:
            log.info("ups.mock", reason=str(e)[:60])
            return False

    def read(self) -> Dict[str, Any]:
        if self._mock:
            elapsed_min = (time.monotonic() - self._mock_start_time) / 60
            pct = max(0.0, self._mock_start_pct - elapsed_min * 0.1)
            return {"percent": pct, "voltage": 7.4 + (pct / 100) * 1.0, "charging": False}
        try:
            raw_soc  = _i2c_bus().read_i2c_block_data(self.ADDR, self.SOC_REG,     2)
            raw_volt = _i2c_bus().read_i2c_block_data(self.ADDR, self.VOLTAGE_REG, 2)
            # MAX17040: big-endian 16-bit words
            # VCELL (0x02): bits[15:4] * 1.25mV → divide by 16 then * 0.00125
            raw_v = (raw_volt[0] << 8) | raw_volt[1]
            volt  = (raw_v >> 4) * 1.25 / 1000
            # SOC (0x04): byte[0] = integer %, byte[1] = 1/256 fraction
            pct   = min(100.0, raw_soc[0] + raw_soc[1] / 256.0)
            return {"percent": round(pct, 1), "voltage": round(volt, 3), "charging": volt > 4.1}
        except Exception:
            return {"percent": None, "voltage": None, "charging": False}


# ── Sound Sensor ─────────────────────────────────────────────────────────────

class SoundSensor:
    """KY-038 analog sound sensor — pin from hardware.yaml (GPIO11 via 10kΩ+10kΩ divider)."""

    def __init__(self) -> None:
        self._mock = True
        self._device = None
        cfg_s = cfg.hardware.sensors.get("sound", {})
        self._pin = cfg_s.get("pin")  # None means pin not assigned

    def initialize(self) -> bool:
        if self._pin is None:
            log.info("sound.mock", reason="pin not assigned in hardware.yaml")
            return False
        if not _GPIO_OK:
            log.info("sound.mock")
            return False
        try:
            self._device = DigitalInputDevice(self._pin)
            self._mock = False
            log.info("sound.real", pin=self._pin)
            return True
        except Exception as e:
            log.info("sound.mock", reason=str(e)[:60])
            return False

    def check(self) -> bool:
        if self._mock:
            return False  # mock never triggers — needs real audio input
        try:
            return self._device.is_active
        except Exception:
            return False


# ── Vibration Sensor ──────────────────────────────────────────────────────────

class VibrationSensor:
    """SW-420 vibration sensor — pin from hardware.yaml.
    Note: GPIO26 reassigned to right_rear motor BIN2 — pin is null until sensor rewired.
    """

    def __init__(self) -> None:
        self._mock = True
        self._device = None
        self._next_mock_trigger: float = time.monotonic() + random.uniform(600, 1200)
        cfg_v = cfg.hardware.sensors.get("vibration", {})
        self._pin = cfg_v.get("pin")  # None means pin not assigned

    def initialize(self) -> bool:
        if self._pin is None:
            log.info("vibration.mock", reason="pin not assigned in hardware.yaml — GPIO26 taken by motor")
            return False
        if not _GPIO_OK:
            log.info("vibration.mock")
            return False
        try:
            self._device = DigitalInputDevice(self._pin)
            self._mock = False
            log.info("vibration.real", pin=self._pin)
            return True
        except Exception as e:
            log.info("vibration.mock", reason=str(e)[:60])
            return False

    def check(self) -> bool:
        if self._mock:
            now = time.monotonic()
            if now >= self._next_mock_trigger:
                self._next_mock_trigger = now + random.uniform(600, 1200)
                return True
            return False
        try:
            return self._device.is_active
        except Exception:
            return False


# ── Sensor Manager ────────────────────────────────────────────────────────────

class SensorManager:
    """
    Unified sensor manager — starts all sensors and runs polling loops.
    All events publish to the event bus. Personality effects applied automatically.
    """

    def __init__(self) -> None:
        self.bh1750     = BH1750Sensor()
        self.pir        = PIRSensor()
        self.touch      = TouchSensorArray()
        self.apds9960   = APDS9960Sensor()
        self.mpu6050    = MPU6050Sensor()
        self.cliff      = CliffSensorArray()
        self.ultrasonic = UltrasonicSensor()
        self.sound      = SoundSensor()
        self.vibration  = VibrationSensor()
        self.ups        = UPSHATSensor()

        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._last_lux: float = 0.0
        self._last_bat_pct: float = 100.0
        self._bat_warned_low = False
        self._bat_warned_crit = False
        self._touch_press_times: Dict[str, float] = {}
        self._long_fired: set = set()
        self._pickup_count: int = 0

    def initialize_all(self) -> None:
        sensors = [
            (self.bh1750,     "sensor.bh1750",     "time-of-day lux curve"),
            (self.pir,        "sensor.pir",        "random trigger every 2-5 min"),
            (self.touch,      "sensor.touch",      "never triggers"),
            (self.apds9960,   "sensor.apds9960",   "random gesture every 10 min"),
            (self.mpu6050,    "sensor.mpu6050",    "slight drift, random pickup spike"),
            (self.cliff,      "sensor.cliff",      "always safe"),
            (self.ultrasonic, "sensor.ultrasonic", "100 cm open space"),
            (self.sound,      "sensor.sound",      "never triggers"),
            (self.vibration,  "sensor.vibration",  "random trigger every 15 min"),
            (self.ups,        "sensor.ups",        "drains 0.1%/min from 85%"),
        ]
        for sensor, name, mock_behavior in sensors:
            sensor.initialize()
            if sensor._mock:
                hw_registry.report_mock(name, reason="hardware not detected",
                                        mock_behavior=mock_behavior)
            else:
                hw_registry.report_real(name)
        real  = [n for s, n, _ in sensors if not s._mock]
        mocks = [n for s, n, _ in sensors if s._mock]
        log.info("sensor_manager.initialized", real=real, mock=mocks)
        hw_registry.log_summary()

    async def start(self) -> None:
        self.initialize_all()
        self._running = True
        self._tasks = [
            asyncio.create_task(self._poll_light()),
            asyncio.create_task(self._poll_pir()),
            asyncio.create_task(self._poll_touch()),
            asyncio.create_task(self._poll_gesture()),
            asyncio.create_task(self._poll_imu()),
            asyncio.create_task(self._poll_cliff()),
            asyncio.create_task(self._poll_ultrasonic()),
            asyncio.create_task(self._poll_sound()),
            asyncio.create_task(self._poll_vibration()),
            asyncio.create_task(self._poll_battery()),
        ]
        log.info("sensor_manager.started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        log.info("sensor_manager.stopped")

    # ── Poll loops ────────────────────────────────────────────────────────────

    async def _poll_light(self) -> None:
        while self._running:
            lux = self.bh1750.read_lux()
            if abs(lux - self._last_lux) > 50:
                self._last_lux = lux
                await bus.publish(Event(
                    type=EventType.LIGHT_CHANGED,
                    data={"lux": round(lux, 1)},
                    priority=EventPriority.NORMAL,
                ))
            await asyncio.sleep(30)

    _PIR_COOLDOWN_S = 10.0  # suppress re-fire while PIR hold-time is active

    async def _poll_pir(self) -> None:
        if self.pir._mock:
            return  # PIR not wired — skip entirely, mock fires are just noise
        _last_fired: float = 0.0
        while self._running:
            if self.pir.check():
                now = time.monotonic()
                if now - _last_fired >= self._PIR_COOLDOWN_S:
                    _last_fired = now
                    await bus.publish(Event(
                        type=EventType.MOTION_DETECTED,
                        data={"source": "pir"},
                        priority=EventPriority.HIGH,
                    ))
                    personality.process_event(_P_MOTION)
                    log.info("sensor.pir_motion")
            await asyncio.sleep(0.5)

    async def _poll_touch(self) -> None:
        prev_active: set = set()
        while self._running:
            active = set(self.touch.check())
            # New touches
            for zone in active - prev_active:
                self._touch_press_times[zone] = time.monotonic()
                await bus.publish(Event(
                    type=EventType.TOUCH_DETECTED,
                    data={"zone": zone},
                    priority=EventPriority.HIGH,
                ))
                personality.process_event(_P_TOUCH)
                log.info("sensor.touch", zone=zone)
            # Long press check
            now = time.monotonic()
            for zone in active:
                start = self._touch_press_times.get(zone, now)
                if now - start > 2.0 and zone not in self._long_fired:
                    self._long_fired.add(zone)
                    await bus.publish(Event(
                        type=EventType.TOUCH_LONG,
                        data={"zone": zone, "duration_s": round(now - start, 1)},
                        priority=EventPriority.HIGH,
                    ))
            # Released
            for zone in prev_active - active:
                self._touch_press_times.pop(zone, None)
                self._long_fired.discard(zone)
            prev_active = active
            await asyncio.sleep(0.1)

    async def _poll_gesture(self) -> None:
        while self._running:
            gesture = self.apds9960.check_gesture()
            if gesture:
                await bus.publish(Event(
                    type=EventType.GESTURE_DETECTED,
                    data={"gesture": gesture},
                    priority=EventPriority.HIGH,
                ))
                personality.process_event(_P_GESTURE)
                log.info("sensor.gesture", gesture=gesture)
            await asyncio.sleep(5)

    async def _poll_imu(self) -> None:
        while self._running:
            data = self.mpu6050.read()
            a = data["accel"]
            magnitude = (a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2) ** 0.5
            if magnitude > 2.5:
                self._pickup_count += 1
                if self._pickup_count >= 3:
                    self._pickup_count = 0
                    await bus.publish(Event(
                        type=EventType.PICKUP_DETECTED,
                        data={"accel_g": round(magnitude, 2)},
                        priority=EventPriority.SAFETY,
                    ))
                    personality.process_event(_P_PICKUP)
                    log.info("sensor.pickup_detected", accel_g=round(magnitude, 2))
                    await asyncio.sleep(2)  # debounce
            else:
                self._pickup_count = 0
            await asyncio.sleep(0.2)

    async def _poll_cliff(self) -> None:
        while self._running:
            if self.cliff.is_cliff():
                await bus.publish(Event(
                    type=EventType.CLIFF_DETECTED,
                    data={"source": "cliff_ir"},
                    priority=EventPriority.SAFETY,
                ))
                log.warning("sensor.cliff_detected")
                await asyncio.sleep(1)  # debounce
            await asyncio.sleep(0.1)

    async def _poll_ultrasonic(self) -> None:
        while self._running:
            dist_cm = self.ultrasonic.read_cm()
            await bus.publish(Event(
                type=EventType.DISTANCE_UPDATED,
                data={"distance_cm": round(dist_cm, 1)},
                priority=EventPriority.LOW,
            ))
            if dist_cm < 20:
                await bus.publish(Event(
                    type=EventType.OBSTACLE_CRITICAL,
                    data={"distance_cm": round(dist_cm, 1)},
                    priority=EventPriority.SAFETY,
                ))
            elif dist_cm < 40:
                await bus.publish(Event(
                    type=EventType.OBSTACLE_WARNING,
                    data={"distance_cm": round(dist_cm, 1)},
                    priority=EventPriority.HIGH,
                ))
            await asyncio.sleep(0.1)

    async def _poll_sound(self) -> None:
        while self._running:
            if self.sound.check():
                await bus.publish(Event(
                    type=EventType.SOUND_DETECTED,
                    data={"source": "ky038"},
                    priority=EventPriority.NORMAL,
                ))
                log.info("sensor.sound_detected")
                await asyncio.sleep(0.5)  # debounce
            await asyncio.sleep(0.05)

    async def _poll_vibration(self) -> None:
        while self._running:
            if self.vibration.check():
                await bus.publish(Event(
                    type=EventType.MOTION_DETECTED,
                    data={"source": "vibration"},
                    priority=EventPriority.HIGH,
                ))
                personality.process_event(_P_MOTION)
                log.info("sensor.vibration_detected")
                await asyncio.sleep(1.0)  # debounce
            await asyncio.sleep(0.1)

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
        return self.ultrasonic.read_cm()

    def get_imu(self) -> Dict[str, Any]:
        return self.mpu6050.read()

    @property
    def is_mock(self) -> bool:
        return all(s._mock for s in [
            self.bh1750, self.pir, self.mpu6050, self.cliff, self.ultrasonic
        ])


sensor_manager = SensorManager()
