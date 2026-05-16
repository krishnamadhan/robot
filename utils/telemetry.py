"""
Lightweight telemetry — CPU, RAM, temperature, event rates.
No external prometheus dependency; exposes simple dict snapshot
that the sensor_monitor tool can consume.
"""

import asyncio
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from utils.logger import get_logger

log = get_logger(__name__)

_start_time = time.monotonic()


def _read_cpu_temp() -> Optional[float]:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return int(p.read_text().strip()) / 1000.0
    except Exception:
        return None


def _read_cpu_percent() -> float:
    try:
        import psutil
        return psutil.cpu_percent(interval=None)
    except ImportError:
        # Fallback: parse /proc/stat
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            fields = [float(x) for x in line.split()[1:]]
            idle = fields[3]
            total = sum(fields)
            return round((1 - idle / total) * 100, 1)
        except Exception:
            return 0.0


def _read_memory_mb() -> Dict[str, float]:
    try:
        import psutil
        m = psutil.virtual_memory()
        return {"total": m.total / 1e6, "used": m.used / 1e6, "percent": m.percent}
    except ImportError:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            used = total - avail
            pct = used / total * 100 if total else 0
            return {"total": total / 1024, "used": used / 1024, "percent": round(pct, 1)}
        except Exception:
            return {}


class Telemetry:
    """
    Singleton telemetry collector.
    Call snapshot() to get current system stats.
    update_metric() for robot-specific counters (events/sec etc).
    """

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {}
        self._rates: Dict[str, Deque] = {}
        self._gauges: Dict[str, float] = {}

    def increment(self, key: str, n: int = 1) -> None:
        self._counters[key] = self._counters.get(key, 0) + n
        if key not in self._rates:
            self._rates[key] = deque(maxlen=60)  # last 60 timestamps
        now = time.monotonic()
        self._rates[key].append(now)

    def gauge(self, key: str, value: float) -> None:
        self._gauges[key] = value

    def rate(self, key: str, window_s: float = 10.0) -> float:
        """Events per second over the last window_s seconds."""
        if key not in self._rates:
            return 0.0
        now = time.monotonic()
        cutoff = now - window_s
        recent = [t for t in self._rates[key] if t > cutoff]
        return len(recent) / window_s

    def snapshot(self) -> Dict[str, Any]:
        return {
            "uptime_s": round(time.monotonic() - _start_time, 1),
            "cpu_percent": _read_cpu_percent(),
            "cpu_temp_c": _read_cpu_temp(),
            "memory": _read_memory_mb(),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "rates": {k: round(self.rate(k), 2) for k in self._rates},
        }


# Module-level singleton
telemetry = Telemetry()


class LatencyTracker:
    """
    End-to-end pipeline latency tracking.

    Usage:
        LatencyTracker.start("wake_to_stt")
        # ... work ...
        ms = LatencyTracker.end("wake_to_stt")
    """

    _timers:  Dict[str, float]         = {}
    _history: Dict[str, deque]         = {}
    _MAXLEN = 20

    @classmethod
    def start(cls, name: str) -> None:
        cls._timers[name] = time.perf_counter()

    @classmethod
    def end(cls, name: str) -> float:
        t0 = cls._timers.pop(name, None)
        if t0 is None:
            return 0.0
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if name not in cls._history:
            cls._history[name] = deque(maxlen=cls._MAXLEN)
        cls._history[name].append(elapsed_ms)
        log.info("latency", stage=name, ms=round(elapsed_ms, 1),
                  avg_ms=round(cls.avg(name), 1))
        return elapsed_ms

    @classmethod
    def avg(cls, name: str) -> float:
        h = cls._history.get(name)
        return sum(h) / len(h) if h else 0.0

    @classmethod
    def max_ms(cls, name: str) -> float:
        h = cls._history.get(name)
        return max(h) if h else 0.0

    @classmethod
    def report(cls) -> str:
        if not cls._history:
            return "No latency data yet."
        lines = ["=== LATENCY REPORT ==="]
        for name, history in sorted(cls._history.items()):
            if history:
                avg = sum(history) / len(history)
                mx  = max(history)
                lines.append(f"  {name:<28} avg:{avg:6.0f}ms  max:{mx:6.0f}ms  n={len(history)}")
        return "\n".join(lines)

    @classmethod
    def snapshot(cls) -> Dict[str, Any]:
        return {
            name: {
                "avg_ms": round(cls.avg(name), 1),
                "max_ms": round(cls.max_ms(name), 1),
                "samples": len(h),
            }
            for name, h in cls._history.items() if h
        }
