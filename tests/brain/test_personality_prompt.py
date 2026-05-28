"""
B3 — PersonalityPromptBuilder tests (I7).

Tests:
  - Deterministic: same state → same string
  - Tone shifts visible across emotional extremes
  - Compact output (< 300 chars for preamble)
  - All EmotionalState dimensions represented
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.personality_prompt import PersonalityPromptBuilder
from core.personality import EmotionalState


class TestPersonalityPromptDeterminism:

    def test_same_state_same_string(self):
        """I7: Same state produces identical string on multiple calls."""
        state = EmotionalState(mood=0.5, energy=0.6, arousal=0.4, attachment=0.7)
        s1 = PersonalityPromptBuilder.build(state)
        s2 = PersonalityPromptBuilder.build(state)
        assert s1 == s2

    def test_same_state_full_same_string(self):
        """I7: build_full is also deterministic."""
        state = EmotionalState(mood=0.2, energy=0.4, arousal=0.3, attachment=0.6)
        s1 = PersonalityPromptBuilder.build_full(state)
        s2 = PersonalityPromptBuilder.build_full(state)
        assert s1 == s2

    def test_different_states_different_strings(self):
        """Extreme happy vs extreme sad produce different preambles."""
        happy = EmotionalState(mood=0.9, energy=0.9, arousal=0.9, attachment=0.9)
        sad   = EmotionalState(mood=-0.9, energy=0.1, arousal=0.1, attachment=0.1)
        s1 = PersonalityPromptBuilder.build(happy)
        s2 = PersonalityPromptBuilder.build(sad)
        assert s1 != s2

    def test_exact_boundary_determinism(self):
        """Values exactly at bucket boundaries produce same output."""
        state = EmotionalState(mood=0.7, energy=0.7, arousal=0.7, attachment=0.8)
        s1 = PersonalityPromptBuilder.build(state)
        s2 = PersonalityPromptBuilder.build(state)
        assert s1 == s2


class TestPersonalityPromptContent:

    def test_happy_mood_reflected(self):
        """High mood → 'happy' in output."""
        state = EmotionalState(mood=0.8, energy=0.5, arousal=0.5, attachment=0.5)
        result = PersonalityPromptBuilder.build(state)
        assert "happy" in result.lower(), f"Expected 'happy' in: {result}"

    def test_sad_mood_reflected(self):
        """Very low mood → 'sad' in output."""
        state = EmotionalState(mood=-0.8, energy=0.3, arousal=0.2, attachment=0.3)
        result = PersonalityPromptBuilder.build(state)
        assert "sad" in result.lower(), f"Expected 'sad' in: {result}"

    def test_high_energy_reflected(self):
        """High energy → 'energy' in output."""
        state = EmotionalState(mood=0.5, energy=0.9, arousal=0.5, attachment=0.5)
        result = PersonalityPromptBuilder.build(state)
        assert "energy" in result.lower(), f"Expected 'energy' in: {result}"

    def test_tired_reflected(self):
        """Low energy → 'tired' in output."""
        state = EmotionalState(mood=0.0, energy=0.1, arousal=0.2, attachment=0.5)
        result = PersonalityPromptBuilder.build(state)
        assert "tired" in result.lower() or "exhausted" in result.lower(), f"Got: {result}"

    def test_excited_arousal_reflected(self):
        """High arousal → 'excited' in output."""
        state = EmotionalState(mood=0.5, energy=0.8, arousal=0.9, attachment=0.5)
        result = PersonalityPromptBuilder.build(state)
        assert "excited" in result.lower(), f"Expected 'excited' in: {result}"

    def test_attachment_reflected(self):
        """High attachment → 'warm' or 'bonded' in output."""
        state = EmotionalState(mood=0.5, energy=0.5, arousal=0.5, attachment=0.9)
        result = PersonalityPromptBuilder.build(state)
        assert "bond" in result.lower() or "warm" in result.lower(), f"Got: {result}"


class TestPersonalityPromptCompactness:

    def test_build_under_300_chars(self):
        """Compact preamble fits in ~100 chars."""
        state = EmotionalState(mood=0.5, energy=0.6, arousal=0.4, attachment=0.7)
        result = PersonalityPromptBuilder.build(state)
        assert len(result) < 300, f"Preamble too long ({len(result)} chars): {result}"

    def test_build_full_under_600_chars(self):
        """Full preamble fits in ~500 chars."""
        state = EmotionalState(mood=0.5, energy=0.6, arousal=0.4, attachment=0.7)
        result = PersonalityPromptBuilder.build_full(state)
        assert len(result) < 600, f"Full preamble too long ({len(result)} chars): {result}"


class TestPersonalityPromptToneShifts:

    def test_extreme_happy_vs_sad_tone_guidance(self):
        """Extreme happy gives warm/enthusiastic guidance; extreme sad gives subdued."""
        very_happy = EmotionalState(mood=0.9, energy=0.9, arousal=0.9, attachment=0.9)
        very_sad   = EmotionalState(mood=-0.9, energy=0.1, arousal=0.1, attachment=0.1)

        full_happy = PersonalityPromptBuilder.build_full(very_happy)
        full_sad   = PersonalityPromptBuilder.build_full(very_sad)

        # Happy should mention enthusiasm/warmth
        assert any(w in full_happy.lower() for w in ["warm", "enthusiasm", "chatty", "exclamation"]), \
            f"Happy tone not visible in: {full_happy}"

        # Sad should mention subdued/quiet/brief
        assert any(w in full_sad.lower() for w in ["subdued", "quiet", "brief", "tired", "reserved"]), \
            f"Sad tone not visible in: {full_sad}"

    def test_family_attachment_in_full(self):
        """Very high attachment → 'family' tone guidance."""
        state = EmotionalState(mood=0.5, energy=0.5, arousal=0.5, attachment=0.9)
        result = PersonalityPromptBuilder.build_full(state)
        assert "family" in result.lower(), f"Expected 'family' tone in: {result}"
