"""
Cosmo's personality engine — continuous emotional state dynamics.

This is NOT a lookup table. Emotional state evolves continuously:
- Decay toward baselines when nothing is happening
- Events shift state with configured deltas
- Time-of-day modulates energy and mood
- Person-specific relationship quality influences reactions
- Quirks fire probabilistically to add unpredictability

Emotional dimensions (all -1.0 to 1.0 or 0.0 to 1.0 as specified):
  mood:       -1.0 (sad) to 1.0 (happy)
  energy:      0.0 (exhausted) to 1.0 (hyper)
  arousal:     0.0 (calm) to 1.0 (excited)
  attachment:  0.0 (detached) to 1.0 (strongly bonded)
"""

import asyncio
import json
import math
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TRAITS_PATH = Path.home() / ".robot" / "personality_traits.json"

from utils.config import cfg
from utils.logger import get_logger
from utils.telemetry import telemetry

log = get_logger(__name__)

STATE_PATH = Path.home() / ".robot" / "personality_state.json"


@dataclass
class EmotionalState:
    mood: float = 0.6
    energy: float = 0.7
    arousal: float = 0.5
    attachment: float = 0.6

    def clamp(self) -> "EmotionalState":
        self.mood = max(-1.0, min(1.0, self.mood))
        self.energy = max(0.0, min(1.0, self.energy))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.attachment = max(0.0, min(1.0, self.attachment))
        return self

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "EmotionalState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PersonRecord:
    """Cosmo's model of a specific person."""
    person_id: str
    name: Optional[str] = None
    relationship_quality: float = 0.5    # 0 = stranger/negative, 1 = beloved
    interaction_count: int = 0
    last_seen: float = field(default_factory=time.monotonic)
    notes: Dict[str, Any] = field(default_factory=dict)

    def familiarity(self) -> float:
        """0-1, grows with interaction count, caps quickly for family."""
        return min(1.0, self.interaction_count / 20.0)


class PersonalityEngine:
    """
    Drives Cosmo's continuous emotional life.

    Call update() on a regular tick (every 1-5 seconds).
    Call process_event() when something significant happens.
    Query describe() for LLM system prompt injection.
    """

    TICK_INTERVAL_S = 2.0       # update frequency
    PERSIST_INTERVAL_S = 60.0   # save emotional state to disk

    def __init__(self) -> None:
        self._pc = cfg.personality
        self._state = EmotionalState(
            mood=self._pc.emotional_state.mood,
            energy=self._pc.emotional_state.energy,
            arousal=self._pc.emotional_state.arousal,
            attachment=self._pc.emotional_state.attachment,
        )
        self._baselines = self._pc.baselines
        self._persons: Dict[str, PersonRecord] = {}
        self._last_update = time.monotonic()
        self._last_persist = time.monotonic()
        self._mood_history: List[Tuple[float, float]] = []   # (timestamp, mood)
        self._active_person_id: Optional[str] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._load_state()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._tick_loop(), name="personality")
        log.info("personality.started", state=self._state.to_dict())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        self._save_state()

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def state(self) -> EmotionalState:
        return self._state

    @property
    def name(self) -> str:
        return self._pc.name

    def process_event(self, event_key: str, person_id: Optional[str] = None) -> None:
        """
        Apply an event's emotional impact.
        event_key matches keys in personality.yaml event_impacts.
        """
        impacts = self._pc.event_impacts.get(event_key, {})
        if not impacts:
            log.debug("personality.unknown_event", evt=event_key)
            return

        person = self._persons.get(person_id) if person_id else None
        familiarity_boost = person.familiarity() * 0.3 if person else 0.0

        for dim, delta in impacts.items():
            old = getattr(self._state, dim, None)
            if old is None:
                continue
            # Positive events are amplified by familiarity; negative events are dampened
            if delta > 0:
                delta *= (1.0 + familiarity_boost)
            else:
                delta *= (1.0 - familiarity_boost * 0.5)
            setattr(self._state, dim, old + delta)

        self._state.clamp()
        self._mood_history.append((time.monotonic(), self._state.mood))
        telemetry.increment(f"personality.event.{event_key}")
        log.debug("personality.event_applied", evt=event_key,
                   state=self._state.to_dict())

    def update_person(self, person_id: str, name: Optional[str] = None,
                      interaction: bool = True) -> PersonRecord:
        """Record an interaction with a person."""
        if person_id not in self._persons:
            self._persons[person_id] = PersonRecord(person_id=person_id, name=name)
        p = self._persons[person_id]
        if name:
            p.name = name
        if interaction:
            p.interaction_count += 1
        p.last_seen = time.monotonic()
        self._active_person_id = person_id
        return p

    def get_person(self, person_id: str) -> Optional[PersonRecord]:
        return self._persons.get(person_id)

    def introspect(self) -> Dict[str, Any]:
        """Why is Cosmo in this emotional state? For debugging and LLM prompting."""
        reasons = []
        s = self._state

        if s.mood > 0.7:
            reasons.append("feeling happy and content")
        elif s.mood < -0.3:
            reasons.append("feeling a bit sad or down")

        if s.energy > 0.8:
            reasons.append("very energetic and ready to play")
        elif s.energy < 0.3:
            reasons.append("tired and low on energy")

        if s.arousal > 0.75:
            reasons.append("excited and alert")
        elif s.arousal < 0.2:
            reasons.append("calm and relaxed")

        if s.attachment < self._pc.thresholds.get("loneliness", 0.15):
            reasons.append("feeling a bit lonely")

        if self._active_person_id:
            p = self._persons.get(self._active_person_id)
            if p:
                pname = p.name or "someone"
                reasons.append(f"happy to be with {pname}")

        return {
            "state": s.to_dict(),
            "reasons": reasons,
            "active_person": self._active_person_id,
            "known_persons": len(self._persons),
            "mood_trend": self._mood_trend(),
        }

    def describe(self) -> str:
        """Short human-readable description for LLM system prompt injection."""
        s = self._state
        parts = []

        if s.mood > 0.7:
            parts.append("in a great mood")
        elif s.mood > 0.4:
            parts.append("feeling pretty good")
        elif s.mood > 0.0:
            parts.append("feeling okay")
        elif s.mood > -0.3:
            parts.append("a little down")
        else:
            parts.append("feeling sad")

        if s.energy > 0.7:
            parts.append("energetic")
        elif s.energy < 0.3:
            parts.append("tired")

        if s.arousal > 0.75:
            parts.append("excited")
        elif s.arousal < 0.2:
            parts.append("calm")

        p = self._persons.get(self._active_person_id) if self._active_person_id else None
        if p:
            parts.append(f"with {p.name or 'someone familiar'}")

        return "Cosmo is " + ", ".join(parts) + "."

    def check_thresholds(self) -> Dict[str, bool]:
        """Which personality thresholds are currently triggered?"""
        t = self._pc.thresholds
        s = self._state
        return {
            "bored": s.energy < t.get("boredom", 0.2),
            "lonely": s.attachment < t.get("loneliness", 0.15),
            "excited": s.arousal > t.get("excitement", 0.82),
            "tired": s.energy < t.get("tiredness", 0.25),
            "euphoric": s.mood > t.get("euphoria", 0.9),
        }

    def pick_quirk(self) -> Optional[Dict[str, Any]]:
        """
        Return a random quirk to fire, or None.
        Called by the behavior engine periodically.
        """
        eligible = []
        for quirk in self._pc.quirks:
            emin = quirk.get("energy_min", 0.0)
            emax = quirk.get("energy_max", 1.0)
            mmin = quirk.get("mood_min", -1.0)
            if (self._state.energy >= emin
                    and self._state.energy <= emax
                    and self._state.mood >= mmin):
                eligible.append(quirk)

        if not eligible:
            return None

        for quirk in eligible:
            prob_per_min = quirk.get("probability_per_min", 0.1)
            # Convert to per-tick probability
            prob_per_tick = prob_per_min * (self.TICK_INTERVAL_S / 60.0)
            if random.random() < prob_per_tick:
                return quirk

        return None

    # ── Internal update ──────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.TICK_INTERVAL_S)
            try:
                self._update()
            except Exception as e:
                log.error("personality.tick_error", error=str(e))

    def _update(self) -> None:
        now = time.monotonic()
        elapsed_h = (now - self._last_update) / 3600.0
        self._last_update = now

        dr = self._pc.decay_rates
        bl = self._baselines
        s = self._state

        # Drift each dimension toward its baseline
        s.mood += (bl.get("mood", 0.55) - s.mood) * dr.get("mood_decay_per_hour", 0.02) * elapsed_h
        s.energy += (bl.get("energy", 0.65) - s.energy) * dr.get("energy_recovery_per_hour", 0.15) * elapsed_h
        s.arousal += (bl.get("arousal", 0.4) - s.arousal) * dr.get("arousal_decay_per_sec", 0.003) * (elapsed_h * 3600)
        s.attachment += (bl.get("attachment", 0.5) - s.attachment) * dr.get("attention_decay_per_min", 0.1) * (elapsed_h * 60)

        # Time-of-day modulation (small nudge each tick)
        tod_mod = self._time_of_day_modifiers()
        s.energy += tod_mod["energy_mod"] * elapsed_h * 0.5   # gentle push
        s.mood += tod_mod["mood_mod"] * elapsed_h * 0.5

        s.clamp()

        # Record mood history (keep last 2 hours)
        self._mood_history.append((now, s.mood))
        cutoff = now - 7200
        self._mood_history = [(t, m) for t, m in self._mood_history if t > cutoff]

        telemetry.gauge("personality.mood", s.mood)
        telemetry.gauge("personality.energy", s.energy)
        telemetry.gauge("personality.arousal", s.arousal)

        # Persist occasionally
        if now - self._last_persist > self.PERSIST_INTERVAL_S:
            self._save_state()
            self._last_persist = now

    def _time_of_day_modifiers(self) -> Dict[str, float]:
        hour = time.localtime().tm_hour
        for period_name, period in self._pc.time_of_day.items():
            if hour in period.get("hours", []):
                return {
                    "energy_mod": period.get("energy_mod", 0.0),
                    "mood_mod": period.get("mood_mod", 0.0),
                }
        return {"energy_mod": 0.0, "mood_mod": 0.0}

    def _mood_trend(self) -> str:
        """Is mood going up, down, or stable over last 10 minutes?"""
        cutoff = time.monotonic() - 600
        recent = [(t, m) for t, m in self._mood_history if t > cutoff]
        if len(recent) < 2:
            return "stable"
        first = recent[0][1]
        last = recent[-1][1]
        delta = last - first
        if delta > 0.05:
            return "improving"
        if delta < -0.05:
            return "declining"
        return "stable"

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "emotional_state": self._state.to_dict(),
                "persons": {
                    pid: {
                        "person_id": p.person_id,
                        "name": p.name,
                        "relationship_quality": p.relationship_quality,
                        "interaction_count": p.interaction_count,
                        "last_seen": p.last_seen,
                        "notes": p.notes,
                    }
                    for pid, p in self._persons.items()
                },
            }
            STATE_PATH.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.error("personality.save_failed", error=str(e))

    def _load_state(self) -> None:
        try:
            if not STATE_PATH.exists():
                return
            data = json.loads(STATE_PATH.read_text())
            if "emotional_state" in data:
                self._state = EmotionalState.from_dict(data["emotional_state"])
                self._state.clamp()
            for pid, pd in data.get("persons", {}).items():
                self._persons[pid] = PersonRecord(**pd)
            log.info("personality.loaded", state=self._state.to_dict())
        except Exception as e:
            log.warning("personality.load_failed", error=str(e))


class PersonalityLearning:
    """
    Slow trait evolution based on interaction outcomes.

    Changes are tiny (0.005 per interaction) — visible over weeks, not hours.
    Traits persist across restarts in ~/.robot/personality_traits.json.

    Traits:
      expressiveness — how much Cosmo volunteers speech (0.3–0.95)
      curiosity      — how often Cosmo asks questions (0.3–0.95)
      affection      — warmth toward people (0.4–0.99)
      caution        — how cautious with strangers (0.1–0.7)
    """

    LEARNING_RATE = 0.005

    _BOUNDS: Dict[str, tuple] = {
        "expressiveness": (0.3, 0.95),
        "curiosity":      (0.3, 0.95),
        "affection":      (0.4, 0.99),
        "caution":        (0.1, 0.70),
    }

    def __init__(self) -> None:
        self.traits: Dict[str, float] = {
            "expressiveness": 0.65,
            "curiosity":      0.70,
            "affection":      0.75,
            "caution":        0.40,
        }
        self._interaction_count = 0
        self._load()

    def _load(self) -> None:
        try:
            if TRAITS_PATH.exists():
                data = json.loads(TRAITS_PATH.read_text())
                loaded = data.get("traits", {})
                for k in self.traits:
                    if k in loaded:
                        self.traits[k] = float(loaded[k])
                self._interaction_count = data.get("interactions", 0)
                log.info("personality_traits.loaded",
                          traits={k: round(v, 3) for k, v in self.traits.items()})
        except Exception as e:
            log.warning("personality_traits.load_failed", error=str(e))

    def _save(self) -> None:
        try:
            TRAITS_PATH.parent.mkdir(parents=True, exist_ok=True)
            TRAITS_PATH.write_text(json.dumps({
                "traits":       self.traits,
                "interactions": self._interaction_count,
                "updated":      time.time(),
            }, indent=2))
        except Exception as e:
            log.warning("personality_traits.save_failed", error=str(e))

    def record_outcome(
        self,
        interaction_type: str,
        person_responded: bool,
        mood_delta: float = 0.0,
    ) -> None:
        """
        Drift personality traits based on how an interaction went.
        Call after every significant interaction.
        """
        self._interaction_count += 1
        outcome = (0.5 if person_responded else -0.2) + (mood_delta * 0.3)
        outcome = max(-1.0, min(1.0, outcome))

        changes: Dict[str, float] = {}

        if interaction_type == "proactive_speech":
            if outcome > 0.3:
                changes["expressiveness"] = +self.LEARNING_RATE
            elif outcome < -0.1:
                changes["expressiveness"] = -self.LEARNING_RATE

        elif interaction_type == "conversation":
            if outcome > 0.3:
                changes["affection"]      = +self.LEARNING_RATE * 0.5
                changes["expressiveness"] = +self.LEARNING_RATE * 0.3
            changes["curiosity"] = +self.LEARNING_RATE * 0.2 * outcome

        elif interaction_type == "touch":
            changes["affection"] = +self.LEARNING_RATE
            changes["caution"]   = -self.LEARNING_RATE * 0.3

        elif interaction_type == "stranger":
            changes["caution"] = +self.LEARNING_RATE * 0.5

        elif interaction_type == "wander":
            changes["curiosity"] = +self.LEARNING_RATE * 0.3

        for trait, delta in changes.items():
            old = self.traits.get(trait, 0.5)
            lo, hi = self._BOUNDS.get(trait, (0.0, 1.0))
            new_val = max(lo, min(hi, old + delta))
            self.traits[trait] = new_val
            if abs(delta) >= 0.001:
                log.debug("personality_trait.drift",
                           trait=trait, before=round(old, 3),
                           after=round(new_val, 3), delta=round(delta, 4))

        # Save every 10 interactions to avoid disk churn
        if self._interaction_count % 10 == 0:
            self._save()

    def get(self, trait: str, default: float = 0.5) -> float:
        return self.traits.get(trait, default)


# Module-level singletons
personality = PersonalityEngine()
personality_learning = PersonalityLearning()
