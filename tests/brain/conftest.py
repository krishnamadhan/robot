"""
Brain test harness — shared fixtures for all tests/brain/ tests.

Provides:
  - FakeLLM: records all calls, returns scripted responses, zero real API calls
  - BudgetSpy: wraps TokenBudget to track counts per-call
  - Mock HAL: sensor_manager, motor_controller, tts, sounds, eye_engine
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make sure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── FakeLLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    """
    Drop-in replacement for LLMInterface.

    - Records every call (generate / generate_streaming / _call_claude / _call_ollama).
    - Returns scripted responses in order; cycles to last entry when exhausted.
    - Zero real network calls.
    """

    def __init__(self, responses: Optional[List[str]] = None) -> None:
        self.responses: List[str] = responses or ["I am Cosmo."]
        self._call_count = 0
        self.calls: List[Dict[str, Any]] = []   # record of every call
        # Mirror LLMInterface attributes used by conversation.py
        self._ollama_available: Optional[bool] = False
        self._anthropic_client = None

    def _next_response(self) -> str:
        idx = min(self._call_count, len(self.responses) - 1)
        self._call_count += 1
        return self.responses[idx]

    async def generate(
        self,
        user_message: str,
        conversation_history=None,
        context=None,
    ) -> Dict[str, Any]:
        text = self._next_response()
        self.calls.append({"method": "generate", "message": user_message, "context": context})
        return {"text": text, "backend": "fake/llm", "latency_ms": 1, "tokens": len(text.split())}

    async def generate_streaming(self, user_message: str, conversation_history=None, context=None):
        text = self._next_response()
        self.calls.append({"method": "generate_streaming", "message": user_message})
        yield text

    async def is_ollama_ready(self) -> bool:
        return False

    def reset(self) -> None:
        self._call_count = 0
        self.calls.clear()


# ── BudgetSpy ────────────────────────────────────────────────────────────────

class BudgetSpy:
    """Tracks token usage injected via record() calls."""

    def __init__(self, limit: int = 100_000) -> None:
        self._limit = limit
        self._total = 0
        self._calls: List[int] = []
        self._day = None

    def _reset_if_new_day(self) -> None:
        import datetime
        today = datetime.date.today().isoformat()
        if self._day != today:
            self._day = today
            self._total = 0
            self._calls.clear()

    def record(self, usage: Any) -> None:
        self._reset_if_new_day()
        n = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
        self._total += n
        self._calls.append(n)

    def add_tokens(self, n: int) -> None:
        """Direct token injection for tests."""
        self._reset_if_new_day()
        self._total += n
        self._calls.append(n)

    def over_limit(self) -> bool:
        self._reset_if_new_day()
        return self._total >= self._limit

    @property
    def day_total(self) -> int:
        return self._total

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def reset(self) -> None:
        self._total = 0
        self._calls.clear()
        self._day = None


# ── Mock HAL modules ──────────────────────────────────────────────────────────

def make_mock_sensor_manager(dist_cm: float = 100.0, lux: float = 300.0):
    m = MagicMock()
    m.get_distance_cm.return_value = dist_cm
    m.get_lux.return_value = lux
    m.is_moving = False
    return m


def make_mock_motor_controller():
    m = MagicMock()
    m.is_moving = False
    m.stop = AsyncMock()
    m.forward = AsyncMock()
    m.backward = AsyncMock()
    m.turn_left = AsyncMock()
    m.turn_right = AsyncMock()
    return m


def make_mock_tts():
    m = MagicMock()
    m.is_speaking = False
    m.speak = AsyncMock()
    return m


def make_mock_sounds():
    m = MagicMock()
    m.play = AsyncMock()
    return m


def make_mock_eye_engine():
    m = MagicMock()
    m.set_expression = MagicMock()
    m.expression_calls: List[Any] = []
    # Track calls for I2 ordering assertion
    original_set = m.set_expression.side_effect
    def _track(expr, **kwargs):
        m.expression_calls.append((time.monotonic(), expr))
    m.set_expression.side_effect = _track
    return m


# ── Pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def budget_spy():
    return BudgetSpy()


@pytest.fixture
def mock_sensor():
    return make_mock_sensor_manager()


@pytest.fixture
def mock_motors():
    return make_mock_motor_controller()


@pytest.fixture
def mock_tts():
    return make_mock_tts()


@pytest.fixture
def mock_sounds():
    return make_mock_sounds()


@pytest.fixture
def mock_eye():
    return make_mock_eye_engine()


@pytest.fixture(autouse=False)
def patch_hal(mock_sensor, mock_motors, mock_tts, mock_sounds, mock_eye):
    """Patch all hardware objects so no GPIO/audio is touched."""
    with (
        patch("hardware.sensor_manager.sensor_manager", mock_sensor),
        patch("hardware.motors.motor_controller", mock_motors),
        patch("expression.speech.tts", mock_tts),
        patch("expression.sounds.sounds", mock_sounds),
        patch("expression.eyes.eye_engine", mock_eye),
    ):
        yield {
            "sensor": mock_sensor,
            "motors": mock_motors,
            "tts": mock_tts,
            "sounds": mock_sounds,
            "eye": mock_eye,
        }


@pytest.fixture
def event_loop():
    """Fresh event loop per test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
