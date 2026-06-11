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
