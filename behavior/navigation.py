"""
Navigation engine — movement with full safety stack.

Mock mode: logs intended movement. Real mode: calls MotorController.
Safety stack enforced regardless of mock/real.

Safety priority (checked in order):
  1. cliff_detected        → emergency_stop
  2. obstacle < 5cm        → emergency_stop
  3. pickup_detected        → motors_off
  4. battery_critical       → stop_and_stay
  5. obstacle < 15cm       → slow_and_avoid
  6. user_command           → execute_if_safe
  7. autonomous             → execute
"""

import asyncio
import random
import time
from enum import Enum
from typing import Optional

from core.event_bus import Event, EventPriority, EventType, bus
from hardware.motors import motor_controller
from utils.logger import get_logger

log = get_logger(__name__)


class NavState(str, Enum):
    IDLE     = "idle"
    FORWARD  = "forward"
    BACKWARD = "backward"
    TURNING  = "turning"
    WANDERING = "wandering"
    FOLLOWING = "following"
    AVOIDING  = "avoiding"


class NavigationEngine:
    """
    High-level navigation — wraps MotorController with safety + autonomy.
    """

    OBSTACLE_STOP_CM  = 5.0
    OBSTACLE_SLOW_CM  = 15.0
    WANDER_SPEED      = 0.25
    APPROACH_SPEED    = 0.35
    RETREAT_SPEED     = 0.35

    def __init__(self) -> None:
        self._state          = NavState.IDLE
        self._cliff_blocked  = False
        self._pickup_blocked = False
        self._bat_blocked    = False
        self._obstacle_cm    = 100.0
        self._wander_task: Optional[asyncio.Task] = None
        self._follow_task:  Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        await motor_controller.initialize()

        @bus.on(EventType.CLIFF_DETECTED)
        async def _on_cliff(e: Event) -> None:
            self._cliff_blocked = True
            await self.stop(emergency=True)

        @bus.on(EventType.PICKUP_DETECTED)
        async def _on_pickup(e: Event) -> None:
            self._pickup_blocked = True
            await self.stop(emergency=True)

        @bus.on(EventType.BATTERY_CRITICAL)
        async def _on_bat(e: Event) -> None:
            self._bat_blocked = True
            await self.stop()

        @bus.on(EventType.DISTANCE_UPDATED)
        async def _on_dist(e: Event) -> None:
            self._obstacle_cm = e.data.get("distance_cm", 100.0)
            if self._obstacle_cm < self.OBSTACLE_STOP_CM:
                await self.stop(emergency=True)

        @bus.on(EventType.PERSON_DETECTED)
        async def _on_person(e: Event) -> None:
            # Resume from pickup block if person present (person set us down)
            if self._pickup_blocked:
                self._pickup_blocked = False
                log.info("nav.pickup_block_cleared")

        log.info("nav.initialized", mock=motor_controller.is_mock)

    def _is_blocked(self) -> bool:
        return self._cliff_blocked or self._pickup_blocked or self._bat_blocked

    def _is_path_clear(self) -> bool:
        return self._obstacle_cm > self.OBSTACLE_SLOW_CM

    def _speed_limit(self) -> float:
        if self._obstacle_cm < self.OBSTACLE_SLOW_CM:
            return 0.2
        return 1.0

    async def _safe_exec(self, coro) -> bool:
        if self._is_blocked():
            coro.close()  # prevent "coroutine was never awaited" warning
            log.debug("nav.blocked", cliff=self._cliff_blocked,
                      pickup=self._pickup_blocked, bat=self._bat_blocked)
            return False
        await motor_controller.heartbeat()
        await coro
        return True

    async def _sleep_with_heartbeat(self, duration: float) -> None:
        """Sleep for duration, sending motor heartbeats every 200ms."""
        end = time.monotonic() + duration
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.2, remaining))
            await motor_controller.heartbeat()

    # ── Movement primitives ───────────────────────────────────────────────────

    async def forward(self, speed: float = None, duration: float = None) -> None:
        speed = min(speed or self.APPROACH_SPEED, self._speed_limit())
        self._state = NavState.FORWARD
        ok = await self._safe_exec(motor_controller.forward(speed))
        if ok and motor_controller.is_mock:
            log.info("nav.forward", speed=round(speed, 2))
        if ok and duration:
            await self._sleep_with_heartbeat(duration)
            await self.stop(_cancel_wander=False)

    async def backward(self, speed: float = None, duration: float = None) -> None:
        speed = speed or self.RETREAT_SPEED
        self._state = NavState.BACKWARD
        ok = await self._safe_exec(motor_controller.backward(speed))
        if ok and motor_controller.is_mock:
            log.info("nav.backward", speed=round(speed, 2))
        if ok and duration:
            await self._sleep_with_heartbeat(duration)
            await self.stop(_cancel_wander=False)

    async def turn_left(self, speed: float = 0.5, degrees: float = None,
                        duration: float = None) -> None:
        self._state = NavState.TURNING
        if degrees:
            duration = degrees / 360 * 2.2  # ~2.2s for full turn at speed 0.5
        # Pass duration=None to motor; handle sleep+heartbeat here
        ok = await self._safe_exec(motor_controller.turn_left(speed))
        if motor_controller.is_mock:
            log.info("nav.turn_left", speed=speed, degrees=degrees, duration=duration)
        if ok and duration:
            await self._sleep_with_heartbeat(duration)
            await self.stop(_cancel_wander=False)

    async def turn_right(self, speed: float = 0.5, degrees: float = None,
                         duration: float = None) -> None:
        self._state = NavState.TURNING
        if degrees:
            duration = degrees / 360 * 2.2
        ok = await self._safe_exec(motor_controller.turn_right(speed))
        if motor_controller.is_mock:
            log.info("nav.turn_right", speed=speed, degrees=degrees, duration=duration)
        if ok and duration:
            await self._sleep_with_heartbeat(duration)
            await self.stop(_cancel_wander=False)

    async def stop(self, emergency: bool = False, _cancel_wander: bool = True) -> None:
        self._state = NavState.IDLE
        await motor_controller.stop(emergency=emergency)
        if _cancel_wander:
            if self._wander_task and not self._wander_task.done():
                self._wander_task.cancel()
            if self._follow_task and not self._follow_task.done():
                self._follow_task.cancel()

    async def retreat(self, duration: float = 2.0) -> None:
        log.info("nav.retreat")
        await self.backward(self.RETREAT_SPEED, duration=duration)

    async def spin_360(self) -> None:
        log.info("nav.spin_360")
        await self.turn_right(speed=0.5, degrees=360)

    # ── Wander ────────────────────────────────────────────────────────────────

    async def wander(self, duration: float = 30) -> None:
        if self._wander_task and not self._wander_task.done():
            return  # already wandering
        self._wander_task = asyncio.create_task(self._wander_loop(duration))
        log.info("nav.wander_start", duration=duration)

    async def _wander_loop(self, duration: float) -> None:
        end_time = time.monotonic() + duration
        self._state = NavState.WANDERING
        try:
            while time.monotonic() < end_time:
                if self._is_blocked() or not self._is_path_clear():
                    await self.stop(_cancel_wander=False)
                    await asyncio.sleep(1.0)
                    continue

                action = random.choices(
                    ["forward", "slight_left", "slight_right", "pause"],
                    weights=[0.50, 0.20, 0.20, 0.10],
                )[0]

                if action == "forward":
                    await self.forward(self.WANDER_SPEED,
                                       duration=random.uniform(1, 3))
                elif action == "slight_left":
                    await self.turn_left(speed=0.3,
                                         duration=random.uniform(0.3, 0.8))
                elif action == "slight_right":
                    await self.turn_right(speed=0.3,
                                          duration=random.uniform(0.3, 0.8))
                elif action == "pause":
                    await asyncio.sleep(random.uniform(1, 3))

                await motor_controller.heartbeat()
        finally:
            await self.stop()
            self._state = NavState.IDLE
            log.info("nav.wander_end")

    # ── Person approach ───────────────────────────────────────────────────────

    async def approach_person(self) -> None:
        log.info("nav.approach_person")
        await self.forward(self.APPROACH_SPEED, duration=1.5)

    async def face_person(self, person_x: float) -> None:
        """person_x: normalized -1 (left) to 1 (right)."""
        DEAD_ZONE = 0.2
        if person_x < -DEAD_ZONE:
            await self.turn_left(speed=0.3, duration=abs(person_x) * 0.5)
        elif person_x > DEAD_ZONE:
            await self.turn_right(speed=0.3, duration=person_x * 0.5)

    async def follow_mode(self, duration: float = 60) -> None:
        if self._follow_task and not self._follow_task.done():
            return
        self._follow_task = asyncio.create_task(self._follow_loop(duration))
        log.info("nav.follow_mode_start")

    async def _follow_loop(self, duration: float) -> None:
        end_time = time.monotonic() + duration
        self._state = NavState.FOLLOWING
        last_person_x: float = 0.0

        @bus.on(EventType.PERSON_DETECTED)
        async def _on_person(e: Event) -> None:
            nonlocal last_person_x
            bbox = e.data.get("bbox", [0, 0, 0, 0])
            if bbox:
                cx = (bbox[0] + bbox[2]) / 2 / 640 - 0.5
                last_person_x = cx * 2

        try:
            while time.monotonic() < end_time:
                if self._is_blocked():
                    await asyncio.sleep(0.5)
                    continue
                await self.face_person(last_person_x)
                await asyncio.sleep(0.3)
                await motor_controller.heartbeat()
        finally:
            bus.unsubscribe(_on_person)
            await self.stop()
            self._state = NavState.IDLE
            log.info("nav.follow_mode_end")

    @property
    def state(self) -> NavState:
        return self._state

    @property
    def is_blocked(self) -> bool:
        return self._is_blocked()


navigation = NavigationEngine()
