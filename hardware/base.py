"""
Abstract hardware interface — every hardware module implements this.

Design decision: we use an ABC rather than duck-typing because hardware
modules must guarantee initialize()/shutdown()/self_test() exist. Missing
these in production code causes silent failures that are hard to diagnose.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class HardwareStatus(str, Enum):
    UNINITIALIZED = "uninitialized"
    OK = "ok"
    DEGRADED = "degraded"      # working but with reduced capability
    FAILED = "failed"          # completely unavailable
    SIMULATED = "simulated"    # mock hardware in use
    DISABLED = "disabled"      # intentionally turned off


@dataclass
class SelfTestResult:
    passed: bool
    status: HardwareStatus
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: float = 0.0


class HardwareInterface(ABC):
    """
    Base class for all hardware modules.

    Subclasses must implement initialize(), self_test(), shutdown().
    The is_available property drives mock selection — if False, the
    system automatically falls back to the mock implementation.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._status = HardwareStatus.UNINITIALIZED
        self._error: Optional[str] = None
        self._init_time: Optional[float] = None

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize hardware. Return True on success.
        Must be idempotent — safe to call multiple times.
        """

    @abstractmethod
    async def self_test(self) -> SelfTestResult:
        """
        Verify hardware is working correctly.
        Called after initialize() and periodically during operation.
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources, put hardware in safe state."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """True if real hardware is physically present and responding."""

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> HardwareStatus:
        return self._status

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _set_status(self, status: HardwareStatus, error: Optional[str] = None) -> None:
        self._status = status
        self._error = error
        if status == HardwareStatus.OK:
            self._init_time = time.monotonic()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self._name!r}, status={self._status})"


class SensorInterface(HardwareInterface):
    """Extended base for sensors that produce continuous readings."""

    @abstractmethod
    async def read(self) -> Dict[str, Any]:
        """
        Read current sensor value(s).
        Returns dict with typed values, never raises — returns error dict on failure.
        """

    async def read_safe(self) -> Dict[str, Any]:
        """Read with error handling — never propagates exceptions."""
        try:
            return await self.read()
        except Exception as e:
            return {"error": str(e), "available": False}


class ActuatorInterface(HardwareInterface):
    """Extended base for actuators (motors, servos, displays, speakers)."""

    @abstractmethod
    async def stop(self) -> None:
        """Immediately stop/disable actuator. Must be safe to call anytime."""
