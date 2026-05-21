"""
PCA9685 servo controller — pan/tilt camera + ultrasonic sweep.

Channels (from config/hardware.yaml):
  0 → CAMERA_PAN   MG90S, 30-150° range
  1 → CAMERA_TILT  MG90S, 60-120° range
  2 → ULTRASONIC   MG90S, 0-180° sweep

Mock mode: logs angles. Real: adafruit-servokit via PCA9685 I2C 0x40.
"""

import asyncio
import time
from typing import Dict, Optional

from hardware.registry import hw_registry
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)

try:
    from adafruit_servokit import ServoKit
    _SERVOKIT_OK = True
except ImportError:
    _SERVOKIT_OK = False


class ServoController:
    """Pan/tilt camera + ultrasonic sweep controller."""

    CAMERA_PAN  = 0
    CAMERA_TILT = 1
    ULTRASONIC  = 2

    PAN_MIN,  PAN_MAX  = 30, 150
    TILT_MIN, TILT_MAX = 60, 120
    CENTER_PAN  = 90
    CENTER_TILT = 90

    SMOOTH_STEPS = 5
    SMOOTH_MS    = 200

    def __init__(self) -> None:
        self._mock = True
        self._kit = None
        self._current_pan:  float = self.CENTER_PAN
        self._current_tilt: float = self.CENTER_TILT
        self._us_angle:     float = 90.0

    async def initialize(self) -> bool:
        if not _SERVOKIT_OK:
            log.info("servos.mock", reason="adafruit-servokit not installed")
            hw_registry.report_mock("servos", reason="adafruit-servokit not installed",
                                    mock_behavior="logs angles only")
            return False
        try:
            i2c_addr = cfg.hardware.servos.i2c_address
            self._kit = ServoKit(channels=16, address=i2c_addr)
            self._mock = False
            await self.center()
            log.info("servos.real", i2c_addr=hex(i2c_addr))
            hw_registry.report_real("servos")
            return True
        except Exception as e:
            log.info("servos.mock", reason=str(e)[:60])
            hw_registry.report_mock("servos", reason=str(e)[:80],
                                    mock_behavior="logs angles only")
            self._kit = None
            return False

    def _set_angle(self, channel: int, angle: float) -> None:
        if not self._mock and self._kit:
            self._kit.servo[channel].angle = angle

    async def pan_to(self, angle: float, smooth: bool = True) -> None:
        angle = max(self.PAN_MIN, min(self.PAN_MAX, angle))
        if smooth:
            await self._smooth_move(self.CAMERA_PAN, self._current_pan, angle,
                                    lambda a: setattr(self, "_current_pan", a))
        else:
            self._current_pan = angle
            self._set_angle(self.CAMERA_PAN, angle)
        if self._mock:
            log.debug("servo.pan", angle=round(angle, 1))

    async def tilt_to(self, angle: float, smooth: bool = True) -> None:
        angle = max(self.TILT_MIN, min(self.TILT_MAX, angle))
        if smooth:
            await self._smooth_move(self.CAMERA_TILT, self._current_tilt, angle,
                                    lambda a: setattr(self, "_current_tilt", a))
        else:
            self._current_tilt = angle
            self._set_angle(self.CAMERA_TILT, angle)
        if self._mock:
            log.debug("servo.tilt", angle=round(angle, 1))

    async def _smooth_move(self, channel: int, start: float, end: float,
                           update_fn) -> None:
        for i in range(1, self.SMOOTH_STEPS + 1):
            t = i / self.SMOOTH_STEPS
            angle = start + (end - start) * t
            update_fn(angle)
            self._set_angle(channel, angle)
            await asyncio.sleep(self.SMOOTH_MS / 1000.0 / self.SMOOTH_STEPS)

    async def center(self) -> None:
        await self.pan_to(self.CENTER_PAN,  smooth=False)
        await self.tilt_to(self.CENTER_TILT, smooth=False)
        log.debug("servo.centered")

    async def track_person(self, person_x: float, person_y: float) -> None:
        """person_x/y: normalized offset from center, -1.0 to 1.0."""
        DEAD_ZONE = 0.15
        if abs(person_x) > DEAD_ZONE:
            new_pan = self._current_pan + person_x * 15
            await self.pan_to(new_pan, smooth=True)
        if abs(person_y) > DEAD_ZONE:
            new_tilt = self._current_tilt + (-person_y * 10)
            await self.tilt_to(new_tilt, smooth=True)

    async def sweep_ultrasonic(self) -> Dict[int, float]:
        """Sweep HC-SR04 0→180°, return {angle: distance_cm}."""
        from hardware.sensor_manager import sensor_manager
        results: Dict[int, float] = {}
        for angle in range(0, 181, 15):
            if not self._mock and self._kit:
                self._kit.servo[self.ULTRASONIC].angle = angle
                self._us_angle = angle
                await asyncio.sleep(0.06)  # settle
            dist_cm = sensor_manager.get_distance_cm()
            results[angle] = round(dist_cm, 1)
        await self._smooth_move(self.ULTRASONIC, 180, 90,
                                lambda a: setattr(self, "_us_angle", a))
        if self._mock:
            log.debug("servo.sweep_mock", angles=len(results))
        return results

    @property
    def current_pan(self) -> float:
        return self._current_pan

    @property
    def current_tilt(self) -> float:
        return self._current_tilt

    @property
    def is_mock(self) -> bool:
        return self._mock


servo_controller = ServoController()
