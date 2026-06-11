"""Unit tests for the personality engine."""
import asyncio
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.personality import PersonalityEngine, EmotionalState


@pytest.fixture
async def engine():
    e = PersonalityEngine()
    await e.start()
    yield e
    await e.stop()


def test_emotional_state_clamp():
    state = EmotionalState(mood=2.0, energy=-0.5, arousal=1.5, attachment=2.0)
    state.clamp()
    assert state.mood == 1.0
    assert state.energy == 0.0
    assert state.arousal == 1.0
    assert state.attachment == 1.0


def test_emotional_state_serialization():
    state = EmotionalState(mood=0.5, energy=0.7, arousal=0.3, attachment=0.6)
    d = state.to_dict()
    restored = EmotionalState.from_dict(d)
    assert restored.mood == 0.5
    assert restored.energy == 0.7


@pytest.mark.asyncio
async def test_event_impacts_positive(engine):
    initial = engine.state.mood
    engine.process_event("person_arrived")
    assert engine.state.mood > initial


@pytest.mark.asyncio
async def test_event_impacts_negative(engine):
    engine.state.mood = 0.5
    engine.process_event("touch_rough")
    assert engine.state.mood < 0.5


@pytest.mark.asyncio
async def test_person_tracking(engine):
    uid = f"test_{id(engine)}"   # unique ID per engine instance
    p = engine.update_person(uid, name="Madhan", interaction=True)
    assert p.name == "Madhan"
    assert p.interaction_count == 1

    p2 = engine.update_person(uid, interaction=True)
    assert p2.interaction_count == 2


@pytest.mark.asyncio
async def test_familiarity_amplifies_positive_events(engine):
    engine.update_person("p001", interaction=True)
    for _ in range(20):
        engine.update_person("p001", interaction=True)

    mood_before = engine.state.mood
    engine.process_event("person_arrived", person_id="p001")
    familiar_boost = engine.state.mood - mood_before

    engine2 = PersonalityEngine()
    await engine2.start()
    mood_before2 = engine2.state.mood
    engine2.process_event("person_arrived", person_id="stranger_001")
    stranger_boost = engine2.state.mood - mood_before2
    await engine2.stop()

    assert familiar_boost >= stranger_boost


@pytest.mark.asyncio
async def test_describe_not_empty(engine):
    desc = engine.describe()
    assert desc.startswith("Cosmo")
    assert len(desc) > 10


@pytest.mark.asyncio
async def test_thresholds(engine):
    engine.state.energy = 0.1   # below boredom threshold
    thresholds = engine.check_thresholds()
    assert thresholds["bored"] is True

    engine.state.energy = 0.9   # restore
    engine.state.arousal = 0.95
    thresholds = engine.check_thresholds()
    assert thresholds["excited"] is True


@pytest.mark.asyncio
async def test_unknown_event_doesnt_crash(engine):
    engine.process_event("completely_made_up_event_xyz")
    # Should not crash, just log and return


# ── Circadian energy modulation (Phase 2.3) ──────────────────────────────────

def _at_hour(hour):
    from unittest.mock import MagicMock, patch
    return patch("core.personality.time.localtime",
                 return_value=MagicMock(tm_hour=hour))


@pytest.mark.asyncio
async def test_circadian_targets_drop_at_late_night(engine):
    with _at_hour(2):
        _, energy_night = engine._circadian_targets()
    with _at_hour(8):
        _, energy_morning = engine._circadian_targets()
    assert energy_night < energy_morning
    # settled overnight energy must reach the sleepy band (< 0.2 thresholds)
    assert energy_night <= 0.2


@pytest.mark.asyncio
async def test_circadian_drift_pulls_energy_down_overnight(engine):
    import time as _time
    engine.state.energy = 0.9
    with _at_hour(2):
        engine._last_update = _time.monotonic() - 3600  # one elapsed hour
        engine._update()
    assert engine.state.energy < 0.9


@pytest.mark.asyncio
async def test_circadian_drift_recovers_energy_in_morning(engine):
    import time as _time
    engine.state.energy = 0.2
    with _at_hour(8):
        engine._last_update = _time.monotonic() - 3600
        engine._update()
    assert engine.state.energy > 0.2
