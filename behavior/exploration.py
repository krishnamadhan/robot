"""
Exploration memory — lightweight room state tracking.

No occupancy grid (too CPU-heavy on Pi 5). Instead:
- Room snapshots every 5 min during wander (light level, person present, time-of-day)
- Turn history to bias wander away from recently visited directions (anti-revisit)
- Familiarity score for current conditions (0–1)
- Discovery log: when a new obstacle appears during wander

Strategy inspired by Vector/Cozmo: the robot doesn't need a map to *feel* curious.
It needs to remember "I've been this way before" and prefer the unknown.
"""

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from utils.logger import get_logger

log = get_logger(__name__)

_SNAPSHOT_PATH  = Path.home() / ".robot" / "memory" / "room_snapshots.json"
_SNAPSHOT_INTERVAL_S = 300   # 5 min between snapshots
_MAX_SNAPSHOTS       = 288   # 24 h × 12 per hour
_FAMILIARITY_WINDOW  = 600   # snapshots within 10 min count for familiarity
_TURN_HISTORY_LEN    = 10    # track last N turn directions for anti-revisit


Direction = Literal["left", "right", "straight"]


@dataclass
class RoomSnapshot:
    ts:             float
    light_lux:      float   # from BH1750; -1 if unavailable
    person_present: bool
    tod_bucket:     str     # morning / afternoon / evening / night
    dominant_dir:   str     # last wander direction before snapshot


@dataclass
class DiscoveryEvent:
    ts:             float
    distance_cm:    float
    light_lux:      float
    person_present: bool
    reported:       bool = False  # True once WhatsApp notification sent


class ExplorationMemory:
    """Tracks where Cosmo has been and biases future exploration away from stale areas."""

    def __init__(self) -> None:
        self._snapshots: deque[RoomSnapshot] = deque(maxlen=_MAX_SNAPSHOTS)
        self._discoveries: list[DiscoveryEvent] = []
        self._turn_history: deque[Direction] = deque(maxlen=_TURN_HISTORY_LEN)
        self._last_snapshot_ts: float = 0.0
        self._loaded = False

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not _SNAPSHOT_PATH.exists():
            self._loaded = True
            return
        try:
            data = json.loads(_SNAPSHOT_PATH.read_text())
            for s in data.get("snapshots", []):
                self._snapshots.append(RoomSnapshot(**s))
            log.info("exploration.loaded", count=len(self._snapshots))
        except Exception as exc:
            log.warning("exploration.load_error", error=str(exc))
        self._loaded = True

    def save(self) -> None:
        _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"snapshots": [asdict(s) for s in self._snapshots]}
        _SNAPSHOT_PATH.write_text(json.dumps(data))

    # ── Snapshot recording ────────────────────────────────────────────────────

    def maybe_record_snapshot(
        self,
        light_lux: float,
        person_present: bool,
        dominant_dir: Direction = "straight",
    ) -> bool:
        """Call during wander. Returns True if a snapshot was recorded."""
        now = time.time()
        if now - self._last_snapshot_ts < _SNAPSHOT_INTERVAL_S:
            return False
        self._last_snapshot_ts = now
        snap = RoomSnapshot(
            ts=now,
            light_lux=light_lux,
            person_present=person_present,
            tod_bucket=self._tod_bucket(now),
            dominant_dir=dominant_dir,
        )
        self._snapshots.append(snap)
        self.save()
        log.info("exploration.snapshot", light=light_lux, person=person_present,
                 tod=snap.tod_bucket, dir=dominant_dir)
        return True

    # ── Familiarity ───────────────────────────────────────────────────────────

    def familiarity(self, light_lux: float, person_present: bool) -> float:
        """0 = never been here like this; 1 = very familiar conditions."""
        if not self._snapshots:
            return 0.0
        now = time.time()
        cutoff = now - _FAMILIARITY_WINDOW
        tod = self._tod_bucket(now)
        matches = sum(
            1 for s in self._snapshots
            if s.ts > cutoff
            and s.tod_bucket == tod
            and abs(s.light_lux - light_lux) < 50
            and s.person_present == person_present
        )
        return min(1.0, matches / 3.0)   # 3+ matches → fully familiar

    # ── Anti-revisit wander bias ──────────────────────────────────────────────

    def record_turn(self, direction: Direction) -> None:
        self._turn_history.append(direction)

    def preferred_direction(self) -> Direction:
        """Return the direction least taken recently."""
        if not self._turn_history:
            return "straight"
        counts: dict[Direction, int] = {"left": 0, "right": 0, "straight": 0}
        for d in self._turn_history:
            counts[d] += 1
        return min(counts, key=lambda k: counts[k])

    def wander_weights(self) -> tuple[float, float, float, float]:
        """Return (forward, left, right, pause) weights biased toward unexplored direction."""
        pref = self.preferred_direction()
        base = {"forward": 0.50, "left": 0.20, "right": 0.20, "pause": 0.10}
        boost = 0.12
        if pref == "left":
            base["left"] += boost
            base["right"] -= boost / 2
            base["forward"] -= boost / 2
        elif pref == "right":
            base["right"] += boost
            base["left"] -= boost / 2
            base["forward"] -= boost / 2
        return (base["forward"], base["left"], base["right"], base["pause"])

    # ── Discovery ─────────────────────────────────────────────────────────────

    def record_discovery(
        self, distance_cm: float, light_lux: float, person_present: bool
    ) -> DiscoveryEvent:
        event = DiscoveryEvent(
            ts=time.time(),
            distance_cm=distance_cm,
            light_lux=light_lux,
            person_present=person_present,
        )
        self._discoveries.append(event)
        log.info("exploration.discovery", dist_cm=distance_cm, person=person_present)
        return event

    def pending_discovery(self) -> DiscoveryEvent | None:
        """Return oldest unreported discovery, or None."""
        for d in self._discoveries:
            if not d.reported:
                return d
        return None

    def mark_reported(self, event: DiscoveryEvent) -> None:
        event.reported = True

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _tod_bucket(ts: float) -> str:
        import datetime
        hour = datetime.datetime.fromtimestamp(ts).hour
        if 5  <= hour < 12: return "morning"
        if 12 <= hour < 17: return "afternoon"
        if 17 <= hour < 21: return "evening"
        return "night"


exploration_memory = ExplorationMemory()
