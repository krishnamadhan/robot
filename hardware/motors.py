"""
Motor controller — delegates all GPIO to ESP32 via serial bridge.
Pi side only handles: ramp timing, safety logic, event subscriptions.
Physical TB6612FNG pins now on ESP32 GPIO 15-21.

Same public API as before — callers (navigation, behavior, etc.) unchanged.
Mock mode: bridge.is_mock=True → log actions only.
"""

import asyncio
import time
from typing import Optional

from core.event_bus import Event, EventPriority, EventType, bus
from hardware.registry import hw_registry
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)

RAMP_MS     = 150
WATCHDOG_MS = 500
IDLE_STBY_S = 3.0
MAX_MOVE_S  = 5.0
MAX_DUTY    = 0.75


class MotorController:
    """
    Sends motor commands to ESP32 over serial bridge.
    Ramp + safety logic runs on Pi. GPIO runs on ESP32.
    """

    def __init__(self) -> None:
        self._bridge = None          # set in initialize()
        self._enabled = False
        self._left_speed  = 0.0
        self._right_speed = 0.0
        self._last_heartbeat = time.monotonic()
        self._watchdog_task: Optional[asyncio.Task] = None
        self._safety_stop = False
        self._web_drive = False
        self._last_move_start = 0.0
        self._last_stop_time  = 0.0
        mc = cfg.hardware.motors
        self.LEFT_TRIM  = float(getattr(mc, "left_trim",  1.0))
        self.RIGHT_TRIM = float(getattr(mc, "right_trim", 1.0))

    # ── Init ──────────────────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        from hardware.esp32_bridge import bridge
        self._bridge = bridge

        if self._bridge.is_mock:
            log.info("motor_controller.mock_mode")
            hw_registry.report_mock("motors", "ESP32 bridge not connected")
        else:
            log.info("motor_controller.real_mode", port="/dev/ttyUSB0")
            hw_registry.report_real("motors")

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._register_safety_handlers()
        return True

    async def self_test(self) -> bool:
        if self._bridge and self._bridge.is_mock:
            log.info("motor_controller.self_test_skipped_mock")
            return True
        log.info("motor_controller.self_test")
        await self.forward(speed=0.15)
        await asyncio.sleep(0.1)
        await self.stop()
        await asyncio.sleep(0.05)
        await self.backward(speed=0.15)
        await asyncio.sleep(0.1)
        await self.stop()
        return True

    # ── Movement API ──────────────────────────────────────────────────────────

    async def forward(self, speed: float = 0.55, ramp: bool = True) -> None:
        if self._safety_stop:
            return
        s = min(speed, MAX_DUTY)
        await self.ramp_to(s * self.LEFT_TRIM, s * self.RIGHT_TRIM, ramp=ramp)

    async def backward(self, speed: float = 0.55, ramp: bool = True) -> None:
        if self._safety_stop:
            return
        s = min(speed, MAX_DUTY)
        await self.ramp_to(-s * self.LEFT_TRIM, -s * self.RIGHT_TRIM, ramp=ramp)

    async def turn_left(self, speed: float = 0.5, duration: Optional[float] = None) -> None:
        if self._safety_stop:
            return
        s = min(speed, MAX_DUTY)
        await self.ramp_to(-s * self.LEFT_TRIM, s * self.RIGHT_TRIM)
        if duration:
            await asyncio.sleep(duration)
            await self.stop()

    async def turn_right(self, speed: float = 0.5, duration: Optional[float] = None) -> None:
        if self._safety_stop:
            return
        s = min(speed, MAX_DUTY)
        await self.ramp_to(s * self.LEFT_TRIM, -s * self.RIGHT_TRIM)
        if duration:
            await asyncio.sleep(duration)
            await self.stop()

    async def stop(self, emergency: bool = False) -> None:
        self._left_speed  = 0.0
        self._right_speed = 0.0
        self._last_stop_time = time.monotonic()
        if self._bridge:
            await self._bridge.send_stop()
        if emergency:
            self._safety_stop = True
            log.warning("motor_controller.emergency_stop")
        else:
            log.debug("motor_controller.stop")

    async def ramp_to(self, left: float, right: float,
                      ramp: bool = True, emergency: bool = False) -> None:
        if self._safety_stop and not emergency:
            return

        left  = max(-MAX_DUTY, min(MAX_DUTY, left))
        right = max(-MAX_DUTY, min(MAX_DUTY, right))

        if not ramp:
            self._left_speed  = left
            self._right_speed = right
            if self._bridge:
                await self._bridge.send_motor(left, right)
            return

        steps = 10
        step_ms = RAMP_MS / steps
        start_l, start_r = self._left_speed, self._right_speed

        for i in range(1, steps + 1):
            t = i / steps
            cur_l = start_l + (left  - start_l) * t
            cur_r = start_r + (right - start_r) * t
            if self._bridge:
                await self._bridge.send_motor(cur_l, cur_r)
            await asyncio.sleep(step_ms / 1000.0)

        self._left_speed  = left
        self._right_speed = right
        self._last_move_start = time.monotonic()

    async def heartbeat(self) -> None:
        """Called by web_drive to reset watchdog."""
        self._last_heartbeat = time.monotonic()
        self._safety_stop = False

    async def stop_and_release(self) -> None:
        await self.stop()
        if self._bridge:
            await self._bridge.send_stby(False)
        self._enabled = False

    # ── Safety ────────────────────────────────────────────────────────────────

    def clear_safety_stop(self) -> None:
        self._safety_stop = False

    def _register_safety_handlers(self) -> None:
        @bus.on(EventType.CLIFF_DETECTED)
        async def _on_cliff(event: Event) -> None:
            log.warning("motor_controller.cliff_stop")
            await self.stop(emergency=True)

        @bus.on(EventType.PICKUP_DETECTED)
        async def _on_pickup(event: Event) -> None:
            log.warning("motor_controller.pickup_stop")
            await self.stop(emergency=True)

        @bus.on(EventType.OBSTACLE_CRITICAL)
        async def _on_obstacle(event: Event) -> None:
            if not self._web_drive:
                log.warning("motor_controller.obstacle_stop",
                            dist=event.data.get("distance_cm"))
                await self.stop(emergency=True)

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            if self._web_drive:
                age_ms = (time.monotonic() - self._last_heartbeat) * 1000
                if age_ms > WATCHDOG_MS:
                    if self._left_speed != 0 or self._right_speed != 0:
                        log.warning("motor_controller.watchdog_stop")
                        await self.stop()

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        return self._bridge is None or self._bridge.is_mock

    @property
    def left_speed(self) -> float:
        return self._left_speed

    @property
    def right_speed(self) -> float:
        return self._right_speed

    @property
    def web_drive(self) -> bool:
        return self._web_drive

    @web_drive.setter
    def web_drive(self, v: bool) -> None:
        self._web_drive = v

    async def get_status(self) -> dict:
        return {
            "mock": self.is_mock,
            "left": self._left_speed,
            "right": self._right_speed,
            "safety_stop": self._safety_stop,
            "web_drive": self._web_drive,
        }


motor_controller = MotorController()
