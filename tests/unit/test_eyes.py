"""Phase 2.1 — personality-driven eye baseline state machine."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from expression.eyes import (EyeEngine, EyeExpression, PRIORITY_EMOTION,
                             baseline_expression)


class TestBaselineExpression:

    def test_exhausted_is_sleepy(self):
        assert baseline_expression(0.5, 0.1, 0.5, 0.5) == EyeExpression.SLEEPY

    def test_low_mood_high_arousal_is_scared(self):
        assert baseline_expression(-0.6, 0.5, 0.8, 0.5) == EyeExpression.SCARED

    def test_low_mood_is_sad(self):
        assert baseline_expression(-0.5, 0.5, 0.3, 0.5) == EyeExpression.SAD

    def test_high_mood_high_arousal_is_excited(self):
        assert baseline_expression(0.7, 0.8, 0.8, 0.5) == EyeExpression.EXCITED

    def test_high_mood_bonded_is_loving(self):
        assert baseline_expression(0.6, 0.6, 0.4, 0.9) == EyeExpression.LOVING

    def test_high_mood_is_happy(self):
        assert baseline_expression(0.6, 0.6, 0.4, 0.5) == EyeExpression.HAPPY

    def test_high_arousal_alone_is_curious(self):
        assert baseline_expression(0.2, 0.6, 0.8, 0.5) == EyeExpression.CURIOUS

    def test_default_is_neutral(self):
        assert baseline_expression(0.2, 0.6, 0.4, 0.5) == EyeExpression.NEUTRAL

    def test_exhaustion_wins_over_joy(self):
        assert baseline_expression(0.9, 0.1, 0.9, 0.9) == EyeExpression.SLEEPY


class _FakeState:
    def __init__(self, mood=0.0, energy=0.7, arousal=0.4, attachment=0.5):
        self.mood, self.energy = mood, energy
        self.arousal, self.attachment = arousal, attachment


def _tick_engine(engine, now):
    engine._tick(now)


class TestBaselineDrift:

    def test_idle_eyes_drift_to_baseline(self):
        eng = EyeEngine()
        with patch("expression.eyes.personality") as p:
            p.state = _FakeState(mood=0.7, arousal=0.2)
            _tick_engine(eng, time.monotonic())
        assert eng._state.target_expression == EyeExpression.HAPPY

    def test_timed_expression_blocks_drift(self):
        eng = EyeEngine()
        now = time.monotonic()
        eng.set_expression(EyeExpression.SCARED, duration=5.0,
                           priority=PRIORITY_EMOTION)
        with patch("expression.eyes.personality") as p:
            p.state = _FakeState(mood=0.7)
            _tick_engine(eng, now)
        assert eng._state.target_expression == EyeExpression.SCARED

    def test_timed_expression_reverts_to_baseline(self):
        eng = EyeEngine()
        now = time.monotonic()
        eng.set_expression(EyeExpression.SCARED, duration=0.1,
                           priority=PRIORITY_EMOTION)
        with patch("expression.eyes.personality") as p:
            p.state = _FakeState(mood=-0.6, arousal=0.2)
            _tick_engine(eng, now + 0.2)
        assert eng._state.target_expression == EyeExpression.SAD

    def test_energy_modulates_blink_cadence(self):
        eng = EyeEngine()
        with patch("expression.eyes.personality") as p:
            p.state = _FakeState(energy=0.1)
            _tick_engine(eng, time.monotonic())
            tired_scale = eng._blink_scale
            p.state = _FakeState(energy=1.0)
            eng._next_baseline = 0.0
            _tick_engine(eng, time.monotonic())
            hyper_scale = eng._blink_scale
        assert tired_scale > hyper_scale

    def test_arousal_modulates_transition_speed(self):
        eng = EyeEngine()
        with patch("expression.eyes.personality") as p:
            p.state = _FakeState(arousal=1.0)
            _tick_engine(eng, time.monotonic())
        assert eng._transition_speed < EyeEngine.TRANSITION_SPEED


# ── Reactive expressions off existing events (Phase 2.4) ─────────────────────

import asyncio

from core.event_bus import Event, EventType, bus
from expression.eyes import (_EVENT_EXPR, PRIORITY_SAFETY, PRIORITY_TOUCH,
                             PRIORITY_IDLE)


class TestReactiveExpressions:

    def test_mapping_covers_live_events(self):
        for evt in (EventType.WAKE_WORD, EventType.FACE_UNKNOWN,
                    EventType.PERSON_LOST, EventType.CONVERSATION_START,
                    EventType.CLIFF_DETECTED, EventType.BATTERY_LOW,
                    EventType.OBSTACLE_WARNING):
            assert evt in _EVENT_EXPR

    def test_safety_events_have_safety_priority(self):
        for evt in (EventType.CLIFF_DETECTED, EventType.OBSTACLE_CRITICAL,
                    EventType.BATTERY_CRITICAL):
            assert _EVENT_EXPR[evt][2] == PRIORITY_SAFETY

    def test_wake_word_pops_attention_via_bus(self):
        async def run():
            engine = EyeEngine()
            engine._running = True
            b = bus
            await b.start()
            try:
                await engine.start()
                await b.publish(Event(type=EventType.WAKE_WORD))
                await asyncio.sleep(0.1)
            finally:
                await engine.stop()
                await b.stop()
            return engine._state.target_expression

        assert asyncio.run(run()) == EyeExpression.SURPRISED

    def test_safety_expression_blocks_idle_request(self):
        engine = EyeEngine()
        engine.set_expression(EyeExpression.SCARED, duration=5.0,
                              priority=PRIORITY_SAFETY)
        engine.set_expression(EyeExpression.SAD, duration=2.0,
                              priority=PRIORITY_IDLE)
        assert engine._state.target_expression == EyeExpression.SCARED
