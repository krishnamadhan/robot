"""
Hardware registry — single source of truth for what's real vs mocked.

Every hardware component calls hw_registry.report() after init.
Query at runtime via hw_registry.all / hw_registry.summary().
Exposed at GET /hardware in the debug API.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from utils.logger import get_logger

log = get_logger(__name__)


class HWStatus(str, Enum):
    REAL    = "real"
    MOCK    = "mock"
    ERROR   = "error"
    UNKNOWN = "unknown"


@dataclass
class ComponentInfo:
    status: HWStatus
    reason: str = ""
    mock_behavior: str = ""
    reported_at: float = field(default_factory=time.monotonic)


class HardwareRegistry:
    """
    Central registry. Components call report() after probing themselves.
    Read by the debug API and logged at startup summary.
    """

    def __init__(self) -> None:
        self._components: Dict[str, ComponentInfo] = {}

    def report(
        self,
        name: str,
        status: HWStatus,
        reason: str = "",
        mock_behavior: str = "",
    ) -> None:
        self._components[name] = ComponentInfo(
            status=status,
            reason=reason,
            mock_behavior=mock_behavior,
        )
        if status == HWStatus.REAL:
            log.info("hw.probe", component=name, status=status.value)
        else:
            log.info("hw.probe", component=name, status=status.value,
                     reason=reason or "—", mock=mock_behavior or "—")

    def report_real(self, name: str, reason: str = "") -> None:
        self.report(name, HWStatus.REAL, reason=reason)

    def report_mock(self, name: str, reason: str, mock_behavior: str = "") -> None:
        self.report(name, HWStatus.MOCK, reason=reason, mock_behavior=mock_behavior)

    def report_error(self, name: str, reason: str) -> None:
        self.report(name, HWStatus.ERROR, reason=reason)

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_real(self, name: str) -> bool:
        return self._components.get(name, ComponentInfo(HWStatus.UNKNOWN)).status == HWStatus.REAL

    def is_mock(self, name: str) -> bool:
        info = self._components.get(name, ComponentInfo(HWStatus.UNKNOWN))
        return info.status in (HWStatus.MOCK, HWStatus.UNKNOWN)

    @property
    def all(self) -> Dict[str, ComponentInfo]:
        return dict(self._components)

    @property
    def real(self) -> list:
        return [n for n, c in self._components.items() if c.status == HWStatus.REAL]

    @property
    def mocked(self) -> list:
        return [n for n, c in self._components.items() if c.status == HWStatus.MOCK]

    @property
    def errors(self) -> list:
        return [n for n, c in self._components.items() if c.status == HWStatus.ERROR]

    def as_dict(self) -> dict:
        return {
            name: {
                "status":        info.status.value,
                "reason":        info.reason,
                "mock_behavior": info.mock_behavior,
            }
            for name, info in self._components.items()
        }

    def log_summary(self) -> None:
        log.info(
            "hw.summary",
            real=self.real,
            mocked=self.mocked,
            errors=self.errors,
        )


hw_registry = HardwareRegistry()
