"""
B1 — Tier-1 rule engine correctness tests (I1, I2), post Phase-1 refactor.

The rule engine emits Intents via core.action_router only — no direct
actuation. Assertions:
  - Every rule fires with correct conditions (obstacle, dark, alone_long)
  - Zero LLM calls in any pure-rule path
  - Non-verbal reaction intent structurally precedes any LLM speech
  - Cooldown / dedup flags work
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.intents import Intent

# ── Helpers ──────────────────────────────────────────────────────────────────

def make_mind(idle_s: float = 10.0):
    """Build a CosmoMind instance without running __init__ side effects."""
    from cognition.mind import CosmoMind

    mind = CosmoMind.__new__(CosmoMind)
    mind._running       = False
    mind._task          = None
    mind._enabled       = True
    mind._last_spoke    = 0.0
    mind._budget_lock   = asyncio.Lock()
    mind._speech_in_flight = asyncio.Event()
    mind._trigger_last  = {}
    mind._was_dark      = False
    mind._obstacle_warn = False
    mind._morning_day   = -1
    mind._wound_down    = False
    mind._is_busy       = lambda: False
    return mind


def fake_bb(person_visible: bool, alone_s: float = 0.0, name=None):
    """Install a fake core.behavior_tree module with a blackboard."""
    mod = MagicMock()
    mod.bb.person_visible = person_visible
    mod.bb.person_name = name
    mod.bb.alone_since = time.monotonic() - alone_s
    sys.modules["core.behavior_tree"] = mod
    return mod


def sensor_mock(dist_cm: float, lux: float) -> MagicMock:
    m = MagicMock()
    m.get_distance_cm.return_value = dist_cm
    m.get_lux.return_value = lux
    return m


def daytime():
    """Patch the clock to 12:00 — outside sleep/wind-down/morning windows."""
    from cognition.mind import CosmoMind
    return patch.object(CosmoMind, "_hour", staticmethod(lambda: 12))


def fresh_cooldowns(mind) -> None:
    """Suppress curiosity/memory_ref so person-visible ticks stay silent."""
    now = time.monotonic()
    mind._trigger_last["curiosity"] = now
    mind._trigger_last["memory_ref"] = now


def spy_speak(mind) -> list:
    calls: list = []
    async def _spy(trigger, name, cooldown=None):
        calls.append(trigger)
    mind._maybe_speak = _spy
    return calls


# ── Test rule: dark room ──────────────────────────────────────────────────────

class TestDarkRoomRule:

    @pytest.mark.asyncio
    async def test_dark_room_emits_fear_intent(self):
        """lux < 50 → Intent.EXPRESS_FEAR emitted, was_dark set."""
        mind = make_mind()
        fresh_cooldowns(mind)
        fake_bb(person_visible=True)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(100.0, 10.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        assert mind._was_dark is True
        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.EXPRESS_FEAR in emitted, f"Expected EXPRESS_FEAR in {emitted}"

    @pytest.mark.asyncio
    async def test_dark_room_no_llm_call(self):
        """Dark room rule fires zero LLM calls."""
        mind = make_mind()
        calls = spy_speak(mind)
        fake_bb(person_visible=True)

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(100.0, 5.0)),
            patch("cognition.mind.router", MagicMock()),
            daytime(),
        ):
            await mind._rule_tick()

        assert calls == [], "Dark room rule should not call LLM"

    @pytest.mark.asyncio
    async def test_dark_room_only_fires_once(self):
        """was_dark flag prevents repeat EXPRESS_FEAR while still dark."""
        mind = make_mind()
        mind._was_dark = True
        fresh_cooldowns(mind)
        fake_bb(person_visible=True)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(100.0, 5.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.EXPRESS_FEAR not in emitted

    @pytest.mark.asyncio
    async def test_was_dark_resets_when_light_returns(self):
        mind = make_mind()
        mind._was_dark = True
        fresh_cooldowns(mind)
        fake_bb(person_visible=True)

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(100.0, 300.0)),
            patch("cognition.mind.router", MagicMock()),
            daytime(),
        ):
            await mind._rule_tick()

        assert mind._was_dark is False


# ── Test rule: obstacle ───────────────────────────────────────────────────────

class TestObstacleRule:

    @pytest.mark.asyncio
    async def test_obstacle_emits_stop_and_alert(self):
        """dist < 25 → Intent.STOP + Intent.ALERT(reason=obstacle)."""
        mind = make_mind()
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(10.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP in emitted, f"Expected STOP in {emitted}"
        assert Intent.ALERT in emitted, f"Expected ALERT in {emitted}"
        assert mind._obstacle_warn is True

    @pytest.mark.asyncio
    async def test_obstacle_fires_even_when_busy(self):
        """Safety reflex bypasses the busy gate."""
        mind = make_mind()
        mind._is_busy = lambda: True
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(10.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP in emitted

    @pytest.mark.asyncio
    async def test_obstacle_no_stop_when_clear(self):
        """dist > 25 → no STOP emit, warn flag cleared."""
        mind = make_mind()
        mind._obstacle_warn = True
        fresh_cooldowns(mind)
        fake_bb(person_visible=True)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(100.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP not in emitted
        assert mind._obstacle_warn is False

    @pytest.mark.asyncio
    async def test_obstacle_warn_dedup(self):
        """obstacle_warn flag prevents duplicate STOP emits."""
        mind = make_mind()
        mind._obstacle_warn = True
        fresh_cooldowns(mind)
        fake_bb(person_visible=True)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(10.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.STOP not in emitted

    @pytest.mark.asyncio
    async def test_obstacle_no_llm_call(self):
        mind = make_mind()
        calls = spy_speak(mind)

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(5.0, 300.0)),
            patch("cognition.mind.router", MagicMock()),
            daytime(),
        ):
            await mind._rule_tick()

        assert calls == [], "Obstacle rule should not call LLM"


# ── Test: no autonomous movement from the mind (BT owns wander) ───────────────

class TestNoMindMovement:

    @pytest.mark.asyncio
    async def test_idle_tick_emits_no_movement_intents(self):
        """Long-idle tick must NOT emit WANDER/COME/APPROACH — BT owns movement."""
        mind = make_mind(idle_s=500.0)
        spy_speak(mind)
        fake_bb(person_visible=False, alone_s=100.0)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(150.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            daytime(),
        ):
            await mind._rule_tick()

        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        for movement in (Intent.WANDER, Intent.COME, Intent.APPROACH, Intent.FLEE):
            assert movement not in emitted, f"Mind must not emit {movement}"


# ── Test: Non-verbal BEFORE speech (I2) ──────────────────────────────────────

class TestNonVerbalBeforeSpeech:

    @pytest.mark.asyncio
    async def test_nonverbal_intent_fires_before_llm_call(self):
        """Non-verbal router.emit happens before LLMInterface.generate_once."""
        from cognition.mind import CosmoMind

        timeline: List[str] = []
        mind = make_mind()

        mock_router = MagicMock()
        mock_router.emit.side_effect = lambda *a, **kw: timeline.append("nonverbal")

        mock_tts = MagicMock()
        mock_tts.is_speaking = False
        mock_tts.speak = AsyncMock()

        mock_attention = MagicMock()
        mock_attention.state.focused = False

        async def fake_generate_once(prompt, system, max_tokens=80, claude_direct=False):
            timeline.append("llm")
            return {"text": "Hello there!", "backend": "claude"}

        mock_llm = MagicMock()
        mock_llm.generate_once = fake_generate_once
        mock_budget = MagicMock()
        mock_budget.claude_allowed.return_value = True

        with (
            patch("cognition.mind.router", mock_router),
            patch("cognition.mind.tts", mock_tts),
            patch("cognition.mind.attention", mock_attention),
            patch.dict("sys.modules", {}),
            patch("cognition.llm.llm", mock_llm),
            patch("cognition.llm.token_budget", mock_budget),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 12)),
            patch.object(CosmoMind, "_build_rich_system_prompt",
                         AsyncMock(return_value="sys")),
            patch.object(CosmoMind, "_memory_context",
                         AsyncMock(return_value="")),
        ):
            await mind._maybe_speak("face_seen", "Madhan")

        await asyncio.sleep(0.05)

        assert "nonverbal" in timeline, f"Expected non-verbal emit, got {timeline}"
        if "llm" in timeline:
            assert timeline.index("nonverbal") < timeline.index("llm"), (
                f"Non-verbal must precede LLM call: {timeline}"
            )


# ── Test: Trigger cooldowns (I1 correctness) ─────────────────────────────────

class TestTriggerCooldowns:

    def test_cooldown_values_defined_for_all_triggers(self):
        from cognition.mind import _TRIGGER_COOLDOWNS
        expected = {"face_seen", "emotion_happy", "emotion_sad", "emotion_angry",
                    "touched", "alone_long", "obstacle", "dark_room",
                    "curiosity", "memory_ref", "wonder"}
        missing = expected - set(_TRIGGER_COOLDOWNS.keys())
        assert not missing, f"Missing cooldown entries: {missing}"

    def test_all_cooldowns_positive(self):
        from cognition.mind import _TRIGGER_COOLDOWNS
        for key, val in _TRIGGER_COOLDOWNS.items():
            assert val > 0, f"{key} cooldown must be positive"

    def test_alone_long_has_high_cooldown(self):
        from cognition.mind import _TRIGGER_COOLDOWNS
        assert _TRIGGER_COOLDOWNS["alone_long"] >= 120

    def test_ambient_triggers_subset_of_cooldowns(self):
        """Every D4 ambient trigger must have a cooldown entry."""
        from cognition.mind import _AMBIENT_TRIGGERS, _TRIGGER_COOLDOWNS
        missing = _AMBIENT_TRIGGERS - set(_TRIGGER_COOLDOWNS.keys())
        assert not missing, f"Ambient triggers without cooldown: {missing}"

    @pytest.mark.asyncio
    async def test_speak_returns_early_when_disabled(self):
        mind = make_mind()
        mind._enabled = False
        mock_router = MagicMock()

        with patch("cognition.mind.router", mock_router):
            await mind._maybe_speak("face_seen", "test")

        mock_router.emit.assert_not_called()


# ── Test: Rule tick makes zero LLM calls (I1) ────────────────────────────────

class TestI1RuleTick:

    @pytest.mark.asyncio
    async def test_rule_tick_zero_llm_calls_pure_rule_paths(self):
        """Obstacle / dark / clear scenarios never reach _maybe_speak
        (curiosity suppressed via fresh cooldowns)."""
        for dist, lux in [
            (5.0, 300.0),    # obstacle
            (100.0, 5.0),    # dark
            (150.0, 300.0),  # clear, person visible
        ]:
            mind = make_mind()
            calls = spy_speak(mind)
            fresh_cooldowns(mind)
            fake_bb(person_visible=True)

            with (
                patch("cognition.mind.sensor_manager", sensor_mock(dist, lux)),
                patch("cognition.mind.router", MagicMock()),
                daytime(),
            ):
                await mind._rule_tick()

            assert calls == [], (
                f"Rule tick called _maybe_speak for dist={dist}, lux={lux}: {calls}"
            )

    @pytest.mark.asyncio
    async def test_alone_long_fires_when_no_person(self):
        """alone_s > 600 AND person_visible=False → _maybe_speak('alone_long')."""
        mind = make_mind()
        calls = spy_speak(mind)
        fake_bb(person_visible=False, alone_s=700.0)

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(150.0, 300.0)),
            patch("cognition.mind.router", MagicMock()),
            daytime(),
        ):
            await mind._rule_tick()

        assert "alone_long" in calls, f"Expected alone_long trigger, got {calls}"

    @pytest.mark.asyncio
    async def test_curiosity_fires_when_person_visible_and_cooldown_elapsed(self):
        mind = make_mind()
        calls = spy_speak(mind)
        fake_bb(person_visible=True, name="Madhan")

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(150.0, 300.0)),
            patch("cognition.mind.router", MagicMock()),
            daytime(),
        ):
            await mind._rule_tick()

        assert "curiosity" in calls, f"Expected curiosity trigger, got {calls}"

    @pytest.mark.asyncio
    async def test_sleep_hours_silences_rules(self):
        """During 0–7h the tick exits before any non-safety rule."""
        from cognition.mind import CosmoMind
        mind = make_mind()
        calls = spy_speak(mind)
        fake_bb(person_visible=False, alone_s=700.0)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(150.0, 5.0)),
            patch("cognition.mind.router", mock_router),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 3)),
        ):
            await mind._rule_tick()

        assert calls == []
        emitted = [c.args[0] for c in mock_router.emit.call_args_list]
        assert Intent.EXPRESS_FEAR not in emitted

    @pytest.mark.asyncio
    async def test_wind_down_emits_sleep_once(self):
        """hour == 23 → Intent.SLEEP(speak=True), only once per night."""
        from cognition.mind import CosmoMind
        mind = make_mind()
        fake_bb(person_visible=False)
        mock_router = MagicMock()

        with (
            patch("cognition.mind.sensor_manager", sensor_mock(150.0, 300.0)),
            patch("cognition.mind.router", mock_router),
            patch.object(CosmoMind, "_hour", staticmethod(lambda: 23)),
        ):
            await mind._rule_tick()
            await mind._rule_tick()

        sleeps = [c for c in mock_router.emit.call_args_list
                  if c.args[0] == Intent.SLEEP]
        assert len(sleeps) == 1, f"Expected exactly one SLEEP emit, got {len(sleeps)}"
        assert sleeps[0].kwargs.get("speak") is True
