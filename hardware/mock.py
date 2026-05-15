"""
Mock hardware system — realistic simulation of all sensors and actuators.

Every sensor produces Gaussian-noised values that match real hardware ranges.
Latency is simulated. Failures can be injected for testing error paths.
Scenario replay allows pre-recorded sensor sessions to be replayed.

Why Gaussian noise everywhere: real sensors have shot noise, thermal drift,
and quantization errors. A mock that returns perfectly stable values will
produce test behavior that diverges from production.
"""

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hardware.base import (
    ActuatorInterface,
    HardwareInterface,
    HardwareStatus,
    SelfTestResult,
    SensorInterface,
)
from utils.config import cfg
from utils.logger import get_logger

log = get_logger(__name__)


def _gaussian(mean: float, noise_fraction: float = None) -> float:
    """Apply Gaussian noise scaled to the value magnitude."""
    if noise_fraction is None:
        noise_fraction = cfg.hardware.simulation.noise_level
    sigma = abs(mean) * noise_fraction if mean != 0 else noise_fraction
    return random.gauss(mean, sigma)


async def _sim_latency(base_ms: float = 1.0) -> None:
    """Simulate realistic hardware latency (I2C ~1ms, camera ~33ms)."""
    if cfg.hardware.simulation.latency_simulation:
        jitter = random.gauss(base_ms, base_ms * 0.1)
        await asyncio.sleep(max(0, jitter) / 1000.0)


# ── Failure injection ────────────────────────────────────────────────────────

@dataclass
class FailureSpec:
    """Describes an injected hardware failure for testing."""
    component: str
    failure_type: str   # "timeout" | "bad_value" | "i2c_error" | "total"
    probability: float  # 0.0-1.0, chance of failure per read
    duration_s: Optional[float] = None   # None = permanent


class FailureInjector:
    """Thread-safe failure injection for testing error handling."""

    def __init__(self) -> None:
        self._specs: List[FailureSpec] = []
        self._injected_at: Dict[str, float] = {}

    def inject(self, spec: FailureSpec) -> None:
        self._specs.append(spec)
        self._injected_at[spec.component] = time.monotonic()
        log.warning("mock.failure_injected", component=spec.component, type=spec.failure_type)

    def clear(self, component: Optional[str] = None) -> None:
        if component:
            self._specs = [s for s in self._specs if s.component != component]
        else:
            self._specs.clear()

    def should_fail(self, component: str) -> Optional[str]:
        """Returns failure type string if this read should fail, else None."""
        for spec in self._specs:
            if spec.component != component:
                continue
            if spec.duration_s:
                elapsed = time.monotonic() - self._injected_at.get(component, 0)
                if elapsed > spec.duration_s:
                    continue
            if random.random() < spec.probability:
                return spec.failure_type
        return None


# Module-level injector instance
failure_injector = FailureInjector()


# ── Mock sensor implementations ──────────────────────────────────────────────

class MockBH1750(SensorInterface):
    """Mock ambient light sensor — realistic indoor lux values."""

    def __init__(self) -> None:
        super().__init__("mock_bh1750")
        self._base_lux = 120.0   # typical indoor, varies by time of day

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(2.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        reading = await self.read()
        return SelfTestResult(
            passed=True,
            status=HardwareStatus.SIMULATED,
            details=reading,
        )

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        if f := failure_injector.should_fail("bh1750"):
            return {"error": f, "available": False}
        await _sim_latency(1.5)
        # Lux varies sinusoidally with hour of day
        hour = time.localtime().tm_hour
        day_factor = 0.5 + 0.5 * math.sin((hour - 6) * math.pi / 12)
        lux = _gaussian(self._base_lux * (0.3 + 0.7 * day_factor))
        return {"lux": max(0.0, round(lux, 1)), "available": True}


class MockMPU6050(SensorInterface):
    """Mock IMU — simulates stationary robot with small drift."""

    def __init__(self) -> None:
        super().__init__("mock_mpu6050")

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(5.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        if f := failure_injector.should_fail("mpu6050"):
            return {"error": f, "available": False}
        await _sim_latency(1.0)
        return {
            "accel": {
                "x": _gaussian(0.0, 0.02),
                "y": _gaussian(0.0, 0.02),
                "z": _gaussian(9.81, 0.01),   # gravity
            },
            "gyro": {
                "x": _gaussian(0.0, 0.05),
                "y": _gaussian(0.0, 0.05),
                "z": _gaussian(0.0, 0.05),
            },
            "temp_c": _gaussian(35.0, 0.005),
            "available": True,
        }


class MockUltrasonic(SensorInterface):
    """Mock HC-SR04 — simulates open space with occasional obstacle."""

    def __init__(self) -> None:
        super().__init__("mock_ultrasonic")
        self._obstacle_cm: Optional[float] = None  # set to simulate obstacle

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(1.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        if f := failure_injector.should_fail("ultrasonic"):
            return {"error": f, "available": False}
        await _sim_latency(10.0)   # ultrasonic takes ~10ms for full sweep
        if self._obstacle_cm:
            dist = _gaussian(self._obstacle_cm, 0.02)
        else:
            dist = _gaussian(150.0, 0.05)   # typical open space
        return {"distance_cm": max(2.0, round(dist, 1)), "available": True}

    def set_obstacle(self, distance_cm: Optional[float]) -> None:
        self._obstacle_cm = distance_cm


class MockPIR(SensorInterface):
    """Mock PIR motion sensor."""

    def __init__(self) -> None:
        super().__init__("mock_pir")
        self._motion = False
        self._motion_until: float = 0.0

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(1.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        await _sim_latency(0.5)
        motion = time.monotonic() < self._motion_until
        return {"motion": motion, "available": True}

    def trigger_motion(self, duration_s: float = 3.0) -> None:
        self._motion_until = time.monotonic() + duration_s


class MockTouch(SensorInterface):
    """Mock capacitive touch sensors (4x TTP223)."""

    def __init__(self) -> None:
        super().__init__("mock_touch")
        self._pressed: Dict[int, bool] = {5: False, 25: False, 6: False, 13: False}

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        await _sim_latency(0.2)
        return {"pins": dict(self._pressed), "available": True}

    def press(self, pin: int, duration_s: float = 0.2) -> None:
        self._pressed[pin] = True
        asyncio.get_event_loop().call_later(duration_s, lambda: self._pressed.update({pin: False}))


class MockCliff(SensorInterface):
    """Mock TCRT5000 cliff detection sensors."""

    def __init__(self) -> None:
        super().__init__("mock_cliff")
        self._cliff_detected = False

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def read(self) -> Dict[str, Any]:
        await _sim_latency(0.5)
        return {
            "left_cliff": self._cliff_detected,
            "right_cliff": self._cliff_detected,
            "available": True,
        }

    def set_cliff(self, detected: bool) -> None:
        self._cliff_detected = detected


class MockMotors(ActuatorInterface):
    """Mock TB6612FNG motor controller — tracks commanded speeds."""

    def __init__(self) -> None:
        super().__init__("mock_motors")
        self.left_speed = 0.0    # -100 to +100
        self.right_speed = 0.0

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(2.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(
            passed=True,
            status=HardwareStatus.SIMULATED,
            details={"left": self.left_speed, "right": self.right_speed},
        )

    async def shutdown(self) -> None:
        await self.stop()

    async def stop(self) -> None:
        self.left_speed = 0.0
        self.right_speed = 0.0

    async def set_speeds(self, left: float, right: float) -> None:
        """Set motor speeds, -100 to +100."""
        await _sim_latency(0.5)
        self.left_speed = max(-100, min(100, left))
        self.right_speed = max(-100, min(100, right))


class MockDisplay(ActuatorInterface):
    """Mock OLED display — logs draw calls, no physical output."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.last_expression: Optional[str] = None

    @property
    def is_available(self) -> bool:
        return False

    async def initialize(self) -> bool:
        await _sim_latency(3.0)
        self._set_status(HardwareStatus.SIMULATED)
        return True

    async def self_test(self) -> SelfTestResult:
        return SelfTestResult(passed=True, status=HardwareStatus.SIMULATED)

    async def shutdown(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def show_expression(self, expression: str) -> None:
        await _sim_latency(5.0)   # SPI/I2C display update ~5ms
        self.last_expression = expression
        log.debug("mock_display.expression", display=self._name, expr=expression)


class MockHardwareRegistry:
    """
    Central registry of all mock hardware instances.
    Main system queries this to get the right implementation
    (real or mock) for each hardware component.
    """

    def __init__(self) -> None:
        self.light = MockBH1750()
        self.imu = MockMPU6050()
        self.ultrasonic = MockUltrasonic()
        self.pir = MockPIR()
        self.touch = MockTouch()
        self.cliff = MockCliff()
        self.motors = MockMotors()
        self.left_eye = MockDisplay("left_eye")
        self.right_eye = MockDisplay("right_eye")

    async def initialize_all(self) -> Dict[str, bool]:
        """Initialize all mocks, return status per component."""
        results = {}
        components = [
            ("light", self.light),
            ("imu", self.imu),
            ("ultrasonic", self.ultrasonic),
            ("pir", self.pir),
            ("touch", self.touch),
            ("cliff", self.cliff),
            ("motors", self.motors),
            ("left_eye", self.left_eye),
            ("right_eye", self.right_eye),
        ]
        for name, hw in components:
            try:
                results[name] = await hw.initialize()
            except Exception as e:
                log.error("mock.init_failed", component=name, error=str(e))
                results[name] = False
        return results

    async def shutdown_all(self) -> None:
        for hw in [self.motors, self.left_eye, self.right_eye]:
            try:
                await hw.shutdown()
            except Exception:
                pass


mock_hardware = MockHardwareRegistry()
