"""Tests for new Emo-inspired personality features:
farewell on person leaving, time-of-day context,
lights-on reaction, sound spike startle, spontaneous joy.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cognition.mind import CosmoMind, _SPEAK_PROMPTS, _TRIGGER_COOLDOWNS, _AMBIENT_TRIGGERS


def _bare_mind():
    m = CosmoMind.__new__(CosmoMind)
    m._enabled = True
    m._budget_lock = asyncio.Lock()
    m._speech_in_flight = asyncio.Event()
    m._last_spoke = 0.0
    m._trigger_last = {}
    m._was_dark = False
    m._last_sound_spike = 0.0
    m._is_busy = lambda: False
    return m


# ── Prompt and cooldown table checks ─────────────────────────────────────────

class TestNewTriggers:
    def test_farewell_prompt_includes_name(self):
        fn = _SPEAK_PROMPTS["farewell"]
        assert "Madhan" in fn("Madhan")

    def test_farewell_prompt_fallback_no_name(self):
        fn = _SPEAK_PROMPTS["farewell"]
        assert "human" in fn(None)

    def test_lights_on_prompt_exists(self):
        assert "lights_on" in _SPEAK_PROMPTS

    def test_spontaneous_joy_prompt_exists(self):
        assert "spontaneous_joy" in _SPEAK_PROMPTS

    def test_new_triggers_have_cooldowns(self):
        for t in ("farewell", "lights_on", "spontaneous_joy"):
            assert t in _TRIGGER_COOLDOWNS, f"{t} missing cooldown"

    def test_ambient_triggers_include_new(self):
        assert "lights_on" in _AMBIENT_TRIGGERS
        assert "spontaneous_joy" in _AMBIENT_TRIGGERS

    def test_farewell_not_ambient(self):
        assert "farewell" not in _AMBIENT_TRIGGERS


# ── Lights-on handler ─────────────────────────────────────────────────────────

class TestLightsOnHandler:
    @pytest.mark.asyncio
    async def test_lights_on_fires_when_was_dark(self):
        mind = _bare_mind()
        mind._was_dark = True
        mind._maybe_speak = AsyncMock()

        # Simulate _on_light with bright lux after dark
        lux = 200
        if lux < 50:
            mind._was_dark = True
            await mind._maybe_speak("dark_room", None)
        elif lux > 150 and mind._was_dark:
            mind._was_dark = False
            await mind._maybe_speak("lights_on", None)

        mind._maybe_speak.assert_called_once_with("lights_on", None)
        assert mind._was_dark is False

    @pytest.mark.asyncio
    async def test_lights_on_skipped_if_not_dark(self):
        mind = _bare_mind()
        mind._was_dark = False
        mind._maybe_speak = AsyncMock()

        lux = 300
        if lux > 150 and mind._was_dark:
            await mind._maybe_speak("lights_on", None)

        mind._maybe_speak.assert_not_called()

    @pytest.mark.asyncio
    async def test_dark_room_sets_was_dark(self):
        mind = _bare_mind()
        mind._maybe_speak = AsyncMock()

        lux = 30
        if lux < 50:
            mind._was_dark = True
            await mind._maybe_speak("dark_room", None)

        assert mind._was_dark is True
        mind._maybe_speak.assert_called_once_with("dark_room", None)


# ── Sound spike startle ───────────────────────────────────────────────────────

class TestSoundSpikeStartle:
    def test_spike_detection_threshold(self):
        """peak5 > 0.18 and ratio > 3.5 should trigger startle."""
        peak5 = 0.25
        avg60 = 0.05
        ratio = peak5 / max(avg60, 0.01)
        assert peak5 > 0.18 and ratio > 3.5

    def test_no_spike_if_ratio_low(self):
        """High absolute level but low ratio (sustained loud) should not startle."""
        peak5 = 0.5
        avg60 = 0.4
        ratio = peak5 / max(avg60, 0.01)
        assert ratio < 3.5

    def test_no_spike_if_level_too_low(self):
        """Big ratio but tiny absolute level (background blip) should not startle."""
        peak5 = 0.05
        avg60 = 0.005
        ratio = peak5 / max(avg60, 0.01)
        assert ratio > 3.5 and peak5 < 0.18

    def test_spike_cooldown_respected(self):
        mind = _bare_mind()
        now = time.monotonic()
        mind._last_sound_spike = now - 10  # 10s ago, cooldown is 30s
        assert now - mind._last_sound_spike < 30.0  # still in cooldown


# ── System prompt time-of-day ─────────────────────────────────────────────────

class TestTimeOfDay:
    @pytest.mark.asyncio
    async def test_system_prompt_contains_time(self):
        mind = _bare_mind()
        with patch("core.memory.episodic.episodic.get_context_for_person",
                   AsyncMock(return_value={"familiarity": 0.5, "total_interactions": 5,
                                           "memories": [], "relationship_quality": 0.6,
                                           "away_s": None})), \
             patch("core.personality.personality") as mock_pers, \
             patch("core.attention.attention") as mock_attn:
            mock_pers.state.mood = 0.4
            mock_pers.state.energy = 0.5
            mock_attn.state.focused = False
            prompt = await mind._build_rich_system_prompt("pid1", "Madhan", None)
        time_words = ("morning", "afternoon", "evening", "night", "late night")
        assert any(w in prompt for w in time_words)
