"""
Spatial memory — room fingerprinting and landmark tracking.
JSON-persisted, no database needed for this small data set.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger

log = get_logger(__name__)

SPATIAL_PATH = Path.home() / ".robot" / "memory" / "spatial.json"


@dataclass
class RoomFingerprint:
    room_id: str
    name: str
    avg_lux: float = 200.0
    lux_variance: float = 50.0
    typical_activity_hours: List[int] = field(default_factory=list)
    color_histogram: List[float] = field(default_factory=list)
    visit_count: int = 0
    last_visited: float = field(default_factory=time.time)
    notes: Dict[str, Any] = field(default_factory=dict)

    def lux_matches(self, lux: float, tolerance: float = 2.0) -> bool:
        """Does this lux reading plausibly come from this room?"""
        if self.lux_variance == 0:
            return abs(lux - self.avg_lux) < 50
        z = abs(lux - self.avg_lux) / self.lux_variance
        return z < tolerance


@dataclass
class Landmark:
    landmark_id: str
    name: str
    room_id: str
    description: str
    visual_features: Dict[str, Any] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class RobotPosition:
    """Estimated robot position — not GPS-precise, coarse room-level."""
    room_id: Optional[str] = None
    room_confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    last_obstacle_positions: List[Tuple[float, float]] = field(default_factory=list)


class SpatialMemory:
    """
    Room-level spatial awareness for Cosmo.

    Room identification works by comparing current sensor fingerprint
    (lux, time of day, color histogram if available) against stored profiles.
    Confidence is reported — "probably the bedroom" not "the bedroom".
    """

    def __init__(self) -> None:
        self._rooms: Dict[str, RoomFingerprint] = {}
        self._landmarks: Dict[str, Landmark] = {}
        self._position = RobotPosition()
        self._obstacle_map: List[Dict[str, Any]] = []
        self._load()

    # ── Room management ──────────────────────────────────────────────────────

    def add_room(self, room_id: str, name: str, lux: float) -> RoomFingerprint:
        rf = RoomFingerprint(room_id=room_id, name=name, avg_lux=lux)
        self._rooms[room_id] = rf
        self._save()
        log.info("spatial.room_added", room_id=room_id, name=name)
        return rf

    def update_room(self, room_id: str, lux: float) -> None:
        """Update room fingerprint with new observation (running average)."""
        room = self._rooms.get(room_id)
        if not room:
            return
        alpha = 0.1   # learning rate
        room.avg_lux = (1 - alpha) * room.avg_lux + alpha * lux
        room.lux_variance = (1 - alpha) * room.lux_variance + alpha * abs(lux - room.avg_lux)
        room.visit_count += 1
        room.last_visited = time.time()
        hour = time.localtime().tm_hour
        if hour not in room.typical_activity_hours:
            room.typical_activity_hours.append(hour)
        self._save()

    def identify_room(self, lux: float,
                      color_histogram: Optional[List[float]] = None) -> Tuple[Optional[str], float]:
        """
        Identify current room from sensor fingerprint.
        Returns (room_id, confidence) or (None, 0.0).
        """
        if not self._rooms:
            return None, 0.0

        lux_vals = [r.avg_lux for r in self._rooms.values()]
        lux_range = max(lux_vals) - min(lux_vals)

        scores: Dict[str, float] = {}
        for room_id, room in self._rooms.items():
            score = 0.0
            # Lux match (primary signal)
            if lux_range > 10:
                lux_diff = abs(lux - room.avg_lux)
                score = max(0.0, 1.0 - lux_diff / (lux_range / 2))
            else:
                score = 0.5  # rooms look the same, no confidence

            # Time-of-day match
            hour = time.localtime().tm_hour
            if hour in room.typical_activity_hours:
                score += 0.1

            scores[room_id] = score

        if not scores:
            return None, 0.0

        best_id = max(scores, key=scores.__getitem__)
        best_score = scores[best_id]  # noqa: F841

        # Normalize to [0, 1]
        total = sum(scores.values())
        if total > 0:
            confidence = scores[best_id] / total
        else:
            confidence = 0.0

        return best_id, round(confidence, 2)

    def get_room(self, room_id: str) -> Optional[RoomFingerprint]:
        return self._rooms.get(room_id)

    def list_rooms(self) -> List[RoomFingerprint]:
        return list(self._rooms.values())

    # ── Position ─────────────────────────────────────────────────────────────

    def update_position(self, room_id: Optional[str], confidence: float) -> None:
        self._position.room_id = room_id
        self._position.room_confidence = confidence
        self._position.timestamp = time.time()

    @property
    def current_room(self) -> Optional[str]:
        return self._position.room_id

    @property
    def room_confidence(self) -> float:
        return self._position.room_confidence

    # ── Obstacles ────────────────────────────────────────────────────────────

    def record_obstacle(self, x: float, y: float, persistent: bool = False) -> None:
        self._obstacle_map.append({
            "x": x, "y": y,
            "persistent": persistent,
            "ts": time.time(),
        })
        # Keep only last 500 obstacle records
        if len(self._obstacle_map) > 500:
            self._obstacle_map = self._obstacle_map[-500:]

    def clear_temporary_obstacles(self) -> None:
        cutoff = time.time() - 300   # 5-minute TTL for temporary obstacles
        self._obstacle_map = [
            o for o in self._obstacle_map
            if o["persistent"] or o["ts"] > cutoff
        ]

    # ── Landmarks ────────────────────────────────────────────────────────────

    def add_landmark(self, name: str, room_id: str, description: str) -> Landmark:
        import uuid
        lm = Landmark(
            landmark_id=str(uuid.uuid4())[:8],
            name=name,
            room_id=room_id,
            description=description,
        )
        self._landmarks[lm.landmark_id] = lm
        self._save()
        return lm

    # ── Persistence ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            SPATIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rooms": {rid: asdict(r) for rid, r in self._rooms.items()},
                "landmarks": {lid: asdict(l) for lid, l in self._landmarks.items()},
            }
            from utils.atomic_write import atomic_write_json

            atomic_write_json(SPATIAL_PATH, data, indent=2)  # tmp + os.replace + fsync
        except Exception as e:
            log.error("spatial.save_failed", error=str(e))

    def _load(self) -> None:
        try:
            if not SPATIAL_PATH.exists():
                return
            data = json.loads(SPATIAL_PATH.read_text())
            for rid, rd in data.get("rooms", {}).items():
                self._rooms[rid] = RoomFingerprint(**rd)
            for lid, ld in data.get("landmarks", {}).items():
                self._landmarks[lid] = Landmark(**ld)
            log.info("spatial.loaded", rooms=len(self._rooms), landmarks=len(self._landmarks))
        except Exception as e:
            log.warning("spatial.load_failed", error=str(e))

    def stats(self) -> Dict[str, Any]:
        return {
            "rooms": len(self._rooms),
            "landmarks": len(self._landmarks),
            "obstacles": len(self._obstacle_map),
            "current_room": self._position.room_id,
            "room_confidence": self._position.room_confidence,
        }


spatial = SpatialMemory()
