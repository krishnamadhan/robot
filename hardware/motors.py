"""
TB6612FNG dual motor driver — 4WD configuration (2 chips, 4 channels).

Hardware layout (from config/hardware.yaml):
  Chip 1 — LEFT side:
    left_front  AIN1/AIN2/PWM  (GPIO17/22/12)
    left_rear   BIN1/BIN2/PWM  (GPIO23/6/13)
  Chip 2 — RIGHT side:
    right_front AIN1/AIN2/PWM  (GPIO20/21/18)
    right_rear  BIN1/BIN2/PWM  (GPIO25/26/19)
  Shared STBY GPIO27

Safety rules (hardcoded — never bypass):
  - STBY LOW on init, HIGH only after self_test passes
  - AIN1 + AIN2 never both HIGH (MotorSafetyError)
  - Watchdog kills motors if heartbeat > 500ms old
  - CLIFF_DETECTED or PICKUP_DETECTED → emergency stop
  - Always clear OFF pin before setting ON pin (no both-HIGH glitch)

Mock mode: no GPIO imported — logs actions instead.
Real mode: set cfg.hardware.simulation.enabled = never
"""

import asyncio
import threading
import time
from typing import Optional, Tuple

from core.event_bus import Event, EventPriority, EventType, bus
from hardware.registry import hw_registry
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)

try:
    from gpiozero import DigitalOutputDevice
    from gpiozero.pins.lgpio import LGPIOFactory
    from gpiozero import Device as _GpioDevice
    _GPIO_OK = True
except ImportError:
    _GPIO_OK = False


class MotorSafetyError(Exception):
    """Raised when a motor command would violate safety constraints."""


def _is_mock() -> bool:
    sim = cfg.hardware.simulation.enabled
    if sim == "always":
        return True
    if sim == "never":
        return False
    return not _GPIO_OK


class _SoftPWM:
    """Software PWM via a daemon thread. Toggles a DigitalOutputDevice at ~100 Hz."""

    _FREQ = 100  # Hz

    def __init__(self, pin: int) -> None:
        self._pin = DigitalOutputDevice(pin)
        self._duty: float = 0.0
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_duty(self, duty: float) -> None:
        with self._lock:
            self._duty = max(0.0, min(1.0, duty))

    def off(self) -> None:
        self.set_duty(0.0)
        self._pin.off()

    def _run(self) -> None:
        period = 1.0 / self._FREQ
        while not self._stop:
            with self._lock:
                duty = self._duty
            if duty <= 0.0:
                self._pin.off()
                time.sleep(period)
            elif duty >= 1.0:
                self._pin.on()
                time.sleep(period)
            else:
                self._pin.on()
                time.sleep(period * duty)
                self._pin.off()
                time.sleep(period * (1.0 - duty))

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=0.5)
        self._pin.off()
        self._pin.close()


class _MotorChannel:
    """Single H-bridge channel. Uses DigitalOutputDevice for direction, _SoftPWM for speed."""

    def __init__(self, in1_pin: int, in2_pin: int, pwm_pin: int,
                 name: str, mock: bool) -> None:
        self._name = name
        self._mock = mock
        self._speed: float = 0.0
        if not mock:
            self._in1 = DigitalOutputDevice(in1_pin)
            self._in2 = DigitalOutputDevice(in2_pin)
            self._pwm = _SoftPWM(pwm_pin)
        else:
            self._in1 = self._in2 = self._pwm = None

    def set(self, speed: float, trim: float = 1.0) -> None:
        speed = max(-1.0, min(1.0, speed)) * trim
        self._speed = speed
        if self._mock:
            return
        duty = abs(speed)
        if speed > 0:
            if self._in1.value and self._in2.value:
                raise MotorSafetyError(f"{self._name}: IN1+IN2 both HIGH")
            self._in2.off(); self._in1.on()  # clear before set — prevents both-HIGH glitch
            self._pwm.set_duty(duty)
        elif speed < 0:
            self._in1.off(); self._in2.on()
            self._pwm.set_duty(duty)
        else:
            self._in1.off(); self._in2.off()
            self._pwm.off()

    def brake(self) -> None:
        self._speed = 0.0
        if not self._mock:
            self._in1.off(); self._in2.off(); self._pwm.off()

    @property
    def speed(self) -> float:
        return self._speed


class MotorController:
    """
    TB6612FNG 4WD motor controller.
    Left side (front+rear) and right side (front+rear) driven in sync.
    Mock mode: logs actions. Real mode: drives GPIO.
    """

    RAMP_MS:    int   = 150
    WATCHDOG_MS: int  = 500

    def __init__(self) -> None:
        self._mock = _is_mock()
        self._enabled = False
        self._left_front:  Optional[_MotorChannel] = None
        self._left_rear:   Optional[_MotorChannel] = None
        self._right_front: Optional[_MotorChannel] = None
        self._right_rear:  Optional[_MotorChannel] = None
        self._stby = None
        self._last_heartbeat: float = time.monotonic()
        self._watchdog_task: Optional[asyncio.Task] = None
        self._safety_stop = False
        self._web_drive: bool = False
        mc = cfg.hardware.motors
        self.LEFT_TRIM  = float(getattr(mc, "left_trim",  0.600))
        self.RIGHT_TRIM = float(getattr(mc, "right_trim", 1.000))

    async def initialize(self) -> bool:
        mc = cfg.hardware.motors
        if self._mock:
            log.info("motors.mock_mode")
            self._left_front  = _MotorChannel(0, 0, 0, "left_front",  mock=True)
            self._left_rear   = _MotorChannel(0, 0, 0, "left_rear",   mock=True)
            self._right_front = _MotorChannel(0, 0, 0, "right_front", mock=True)
            self._right_rear  = _MotorChannel(0, 0, 0, "right_rear",  mock=True)
        else:
            try:
                _GpioDevice.pin_factory = LGPIOFactory()
                self._stby = DigitalOutputDevice(mc.stby)
                self._stby.off()  # SAFETY: motors off on init
                lf = mc.left_front
                lr = mc.left_rear
                rf = mc.right_front
                rr = mc.right_rear
                self._left_front  = _MotorChannel(lf.ain1, lf.ain2, lf.pwm, "left_front",  mock=False)
                self._left_rear   = _MotorChannel(lr.bin1, lr.bin2, lr.pwm, "left_rear",   mock=False)
                self._right_front = _MotorChannel(rf.ain1, rf.ain2, rf.pwm, "right_front", mock=False)
                self._right_rear  = _MotorChannel(rr.bin1, rr.bin2, rr.pwm, "right_rear",  mock=False)
                log.info("motors.real_4wd", stby=mc.stby,
                         pins_lf=(lf.ain1, lf.ain2, lf.pwm),
                         pins_lr=(lr.bin1, lr.bin2, lr.pwm),
                         pins_rf=(rf.ain1, rf.ain2, rf.pwm),
                         pins_rr=(rr.bin1, rr.bin2, rr.pwm))
            except Exception as e:
                log.warning("motors.init_failed", error=str(e))
                self._mock = True
                return await self.initialize()

        # Subscribe to safety events
        @bus.on(EventType.CLIFF_DETECTED)
        async def _on_cliff(event: Event) -> None:
            log.warning("motors.emergency_stop", reason="cliff")
            await self.stop(emergency=True)
            self._safety_stop = True

        @bus.on(EventType.PICKUP_DETECTED)
        async def _on_pickup(event: Event) -> None:
            log.warning("motors.emergency_stop", reason="pickup")
            await self.stop(emergency=True)
            self._safety_stop = True

        @bus.on(EventType.OBSTACLE_CRITICAL)
        async def _on_obstacle(event: Event) -> None:
            if self._web_drive:
                return
            if self._left_front and self._left_front.speed > 0.01 and self._right_front.speed > 0.01:
                cm = event.data.get("distance_cm", 0)
                log.warning("motors.emergency_stop", reason="obstacle", cm=cm)
                await self.stop(emergency=True)
                self._safety_stop = True

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        log.info("motors.initialized", mock=self._mock)
        if self._mock:
            hw_registry.report_mock("motors", reason="GPIO unavailable or sim=always",
                                    mock_behavior="logs intended movement")
        else:
            hw_registry.report_real("motors")
        return True

    async def self_test(self) -> bool:
        if self._mock:
            log.info("motors.self_test_pass", mock=True)
            self._enabled = True
            return True
        try:
            for ch in (self._left_front, self._left_rear,
                       self._right_front, self._right_rear):
                ch.set(0.1)
                await asyncio.sleep(0.05)
                ch.brake()
            if self._stby:
                self._stby.on()
            self._enabled = True
            log.info("motors.self_test_pass")
            return True
        except Exception as e:
            log.error("motors.self_test_fail", error=str(e))
            return False

    async def forward(self, speed: float = 0.55, ramp: bool = True) -> None:
        if not self._enabled and not self._mock:
            log.warning("motors.not_enabled", hint="self_test() must pass first")
            return
        if self._safety_stop:
            log.debug("motors.blocked_by_safety")
            return
        await self.ramp_to(speed, speed, emergency=not ramp)
        if self._mock:
            log.info("motors.forward", speed=speed)

    async def backward(self, speed: float = 0.55, ramp: bool = True) -> None:
        if not self._enabled and not self._mock:
            log.warning("motors.not_enabled", hint="self_test() must pass first")
            return
        if self._safety_stop:
            return
        await self.ramp_to(-speed, -speed, emergency=not ramp)
        if self._mock:
            log.info("motors.backward", speed=speed)

    async def turn_left(self, speed: float = 0.5, duration: float = None) -> None:
        if not self._enabled and not self._mock:
            return
        if self._safety_stop:
            return
        await self.ramp_to(-speed, speed)
        if self._mock:
            log.info("motors.turn_left", speed=speed)
        if duration:
            await asyncio.sleep(duration)
            await self.stop()

    async def turn_right(self, speed: float = 0.5, duration: float = None) -> None:
        if not self._enabled and not self._mock:
            return
        if self._safety_stop:
            return
        await self.ramp_to(speed, -speed)
        if self._mock:
            log.info("motors.turn_right", speed=speed)
        if duration:
            await asyncio.sleep(duration)
            await self.stop()

    async def stop(self, emergency: bool = False) -> None:
        await self.ramp_to(0.0, 0.0, emergency=True)
        if self._mock and emergency:
            log.info("motors.stop", emergency=emergency)

    async def ramp_to(self, left: float, right: float,
                      emergency: bool = False) -> None:
        left  = max(-1.0, min(1.0, left))
        right = max(-1.0, min(1.0, right))

        if emergency or not self._left_front:
            if self._left_front:
                self._left_front.set(left,  self.LEFT_TRIM)
                self._left_rear.set(left,   self.LEFT_TRIM)
                self._right_front.set(right, self.RIGHT_TRIM)
                self._right_rear.set(right,  self.RIGHT_TRIM)
            return

        cur_l = self._left_front.speed
        cur_r = self._right_front.speed
        steps = 10
        for i in range(1, steps + 1):
            t = i / steps
            l = cur_l + (left  - cur_l) * t
            r = cur_r + (right - cur_r) * t
            self._left_front.set(l,  self.LEFT_TRIM)
            self._left_rear.set(l,   self.LEFT_TRIM)
            self._right_front.set(r, self.RIGHT_TRIM)
            self._right_rear.set(r,  self.RIGHT_TRIM)
            await asyncio.sleep(self.RAMP_MS / 1000.0 / steps)

    async def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()
        self._safety_stop = False

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            # Watchdog only applies to web/manual-drive — autonomous navigation
            # manages its own stop logic and never calls heartbeat()
            if self._web_drive and self.is_moving:
                elapsed_ms = (time.monotonic() - self._last_heartbeat) * 1000
                if elapsed_ms > self.WATCHDOG_MS:
                    log.warning("motors.watchdog_stop",
                                elapsed_ms=int(elapsed_ms))
                    await self.stop(emergency=True)

    @property
    def is_moving(self) -> bool:
        if not self._left_front:
            return False
        return abs(self._left_front.speed) > 0.01 or abs(self._right_front.speed) > 0.01

    @property
    def current_speed(self) -> Tuple[float, float]:
        if not self._left_front:
            return (0.0, 0.0)
        return (self._left_front.speed, self._right_front.speed)

    @property
    def is_mock(self) -> bool:
        return self._mock

    # Convenience alias — kept for callers that held a reference to _left/_right
    @property
    def _left(self) -> Optional[_MotorChannel]:
        return self._left_front

    @property
    def _right(self) -> Optional[_MotorChannel]:
        return self._right_front


motor_controller = MotorController()
