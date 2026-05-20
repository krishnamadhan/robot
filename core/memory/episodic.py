"""
Episodic memory — SQLite-backed long-term memory of significant interactions.

Schema design decisions:
- importance field drives retrieval priority (not just recency)
- emotional_valence allows "recall happy memories" queries
- linked_episodes captures narrative chains (part 1 of a conversation)
- raw_data JSON is flexible for future schema evolution

Forgetting curve: importance decays over time so old trivial memories
naturally become less retrievable without being deleted.
"""

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

log = get_logger(__name__)

DB_PATH = Path.home() / ".robot" / "memory" / "episodic.db"


@dataclass
class Episode:
    episode_type: str
    summary: str
    emotional_valence: float = 0.0      # -1.0 (bad) to 1.0 (good)
    importance: float = 0.5             # 0.0 to 1.0
    person_id: Optional[str] = None
    room_id: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    linked_episodes: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)


class EpisodicMemory:
    """
    Long-term episodic memory store.

    Async API wraps synchronous SQLite via run_in_executor to avoid
    blocking the event loop on disk I/O.
    """

    # Importance decays at this rate per day — old trivial memories fade
    IMPORTANCE_DECAY_PER_DAY = 0.05

    def __init__(self) -> None:
        self._db_path = DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def initialize(self) -> None:
        """Call once at startup (sync). Creates DB and tables."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()
        log.info("episodic_memory.initialized", path=str(self._db_path))

    def _create_schema(self) -> None:
        c = self._conn.cursor()
        c.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            episode_type TEXT NOT NULL,
            person_id TEXT,
            room_id TEXT,
            emotional_valence REAL DEFAULT 0.0,
            importance REAL DEFAULT 0.5,
            summary TEXT NOT NULL,
            raw_data TEXT DEFAULT '{}',
            linked_episodes TEXT DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_episodes_timestamp ON episodes(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_episodes_person ON episodes(person_id);
        CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type);
        CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes(importance DESC);

        CREATE TABLE IF NOT EXISTS persons (
            id TEXT PRIMARY KEY,
            face_encoding BLOB,
            name TEXT,
            relationship_quality REAL DEFAULT 0.5,
            interaction_count INTEGER DEFAULT 0,
            last_seen REAL,
            personality_notes TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        self._conn.commit()

    # ── Episode CRUD ─────────────────────────────────────────────────────────

    async def store(self, episode: Episode) -> str:
        """Store an episode. Returns episode ID."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._store_sync, episode)

    def _store_sync(self, episode: Episode) -> str:
        c = self._conn.cursor()
        c.execute("""
            INSERT INTO episodes
            (id, timestamp, episode_type, person_id, room_id,
             emotional_valence, importance, summary, raw_data, linked_episodes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            episode.id,
            episode.timestamp,
            episode.episode_type,
            episode.person_id,
            episode.room_id,
            episode.emotional_valence,
            episode.importance,
            episode.summary,
            json.dumps(episode.raw_data),
            json.dumps(episode.linked_episodes),
        ))
        self._conn.commit()
        return episode.id

    async def retrieve(
        self,
        limit: int = 20,
        person_id: Optional[str] = None,
        episode_type: Optional[str] = None,
        min_importance: float = 0.0,
        min_valence: Optional[float] = None,
        max_valence: Optional[float] = None,
        since_ts: Optional[float] = None,
        room_id: Optional[str] = None,
    ) -> List[Episode]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._retrieve_sync,
            limit, person_id, episode_type,
            min_importance, min_valence, max_valence, since_ts, room_id
        )

    def _retrieve_sync(
        self,
        limit: int,
        person_id: Optional[str],
        episode_type: Optional[str],
        min_importance: float,
        min_valence: Optional[float],
        max_valence: Optional[float],
        since_ts: Optional[float],
        room_id: Optional[str],
    ) -> List[Episode]:
        clauses = ["importance >= ?"]
        params: List[Any] = [min_importance]

        if person_id:
            clauses.append("person_id = ?")
            params.append(person_id)
        if episode_type:
            clauses.append("episode_type = ?")
            params.append(episode_type)
        if min_valence is not None:
            clauses.append("emotional_valence >= ?")
            params.append(min_valence)
        if max_valence is not None:
            clauses.append("emotional_valence <= ?")
            params.append(max_valence)
        if since_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(since_ts)
        if room_id:
            clauses.append("room_id = ?")
            params.append(room_id)

        where = " AND ".join(clauses)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM episodes WHERE {where} ORDER BY importance DESC, timestamp DESC LIMIT ?",
            params
        ).fetchall()

        return [self._row_to_episode(r) for r in rows]

    def _row_to_episode(self, row: sqlite3.Row) -> Episode:
        return Episode(
            id=row["id"],
            timestamp=row["timestamp"],
            episode_type=row["episode_type"],
            person_id=row["person_id"],
            room_id=row["room_id"],
            emotional_valence=row["emotional_valence"],
            importance=row["importance"],
            summary=row["summary"],
            raw_data=json.loads(row["raw_data"] or "{}"),
            linked_episodes=json.loads(row["linked_episodes"] or "[]"),
        )

    async def get_by_id(self, episode_id: str) -> Optional[Episode]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_by_id_sync, episode_id)

    def _get_by_id_sync(self, episode_id: str) -> Optional[Episode]:
        row = self._conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        return self._row_to_episode(row) if row else None

    # ── Person management ────────────────────────────────────────────────────

    async def upsert_person(self, person_id: str, name: Optional[str] = None,
                            relationship_delta: float = 0.0,
                            face_encoding: Optional[bytes] = None,
                            notes: Optional[Dict[str, Any]] = None) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._upsert_person_sync,
            person_id, name, relationship_delta, face_encoding, notes
        )

    def _upsert_person_sync(self, person_id: str, name: Optional[str],
                             relationship_delta: float,
                             face_encoding: Optional[bytes],
                             notes: Optional[Dict[str, Any]]) -> None:
        existing = self._conn.execute(
            "SELECT * FROM persons WHERE id = ?", (person_id,)
        ).fetchone()

        if existing:
            rq = min(1.0, max(0.0, existing["relationship_quality"] + relationship_delta))
            self._conn.execute("""
                UPDATE persons SET
                    name = COALESCE(?, name),
                    relationship_quality = ?,
                    interaction_count = interaction_count + 1,
                    last_seen = ?,
                    personality_notes = COALESCE(?, personality_notes),
                    face_encoding = COALESCE(?, face_encoding)
                WHERE id = ?
            """, (name, rq, time.time(), json.dumps(notes) if notes else None,
                  face_encoding, person_id))
        else:
            self._conn.execute("""
                INSERT INTO persons
                (id, name, relationship_quality, interaction_count, last_seen,
                 personality_notes, face_encoding)
                VALUES (?, ?, ?, 1, ?, ?, ?)
            """, (person_id, name, 0.5 + relationship_delta, time.time(),
                  json.dumps(notes or {}), face_encoding))
        self._conn.commit()

    async def get_person(self, person_id: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_person_sync, person_id)

    def _get_person_sync(self, person_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM persons WHERE id = ?", (person_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "relationship_quality": row["relationship_quality"],
            "interaction_count": row["interaction_count"],
            "last_seen": row["last_seen"],
            "personality_notes": json.loads(row["personality_notes"] or "{}"),
        }

    async def list_persons(self) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: self._conn.execute(
                "SELECT id, name, relationship_quality, interaction_count, last_seen FROM persons ORDER BY last_seen DESC"
            ).fetchall()
        )
        return [dict(r) for r in rows]

    # ── Maintenance ──────────────────────────────────────────────────────────

    async def get_context_for_person(
        self,
        person_id: str,
        limit: int = 5,
    ) -> dict:
        """
        Structured memory context for LLM injection.
        Returns familiarity, recent memories, last mood, total interactions.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._get_context_sync, person_id, limit
        )

    def _get_context_sync(self, person_id: str, limit: int) -> dict:
        cutoff = time.time() - 30 * 86400  # last 30 days
        rows = self._conn.execute(
            """SELECT summary, timestamp, emotional_valence, episode_type
               FROM episodes
               WHERE person_id = ? AND timestamp > ?
               ORDER BY importance DESC, timestamp DESC LIMIT ?""",
            (person_id, cutoff, limit),
        ).fetchall()

        last_row = self._conn.execute(
            "SELECT emotional_valence FROM episodes WHERE person_id = ? ORDER BY timestamp DESC LIMIT 1",
            (person_id,),
        ).fetchone()

        total = self._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE person_id = ?",
            (person_id,),
        ).fetchone()[0]

        memories = []
        for summary, ts, valence, ep_type in rows:
            age_s = time.time() - ts
            if age_s < 3600:
                when = "just now"
            elif age_s < 86400:
                when = f"{int(age_s / 3600)}h ago"
            else:
                when = f"{int(age_s / 86400)}d ago"
            mood_word = ("happy" if valence > 0.3 else
                         "sad" if valence < -0.3 else "neutral")
            memories.append(f"[{when}, {mood_word}] {summary}")

        return {
            "memories": memories,
            "last_mood": last_row[0] if last_row else 0.0,
            "total_interactions": total,
            "familiarity": min(1.0, total / 20.0),
        }

    async def apply_forgetting_curve(self) -> int:
        """Decay importance of old memories. Run daily."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._apply_forgetting_sync)

    def _apply_forgetting_sync(self) -> int:
        cutoff = time.time() - 86400   # older than 1 day
        decay = self.IMPORTANCE_DECAY_PER_DAY
        c = self._conn.cursor()
        c.execute("""
            UPDATE episodes
            SET importance = MAX(0.05, importance - ?)
            WHERE timestamp < ? AND importance > 0.05
        """, (decay, cutoff))
        self._conn.commit()
        return c.rowcount

    async def stats(self) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._stats_sync)

    def _stats_sync(self) -> Dict[str, Any]:
        total = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        persons = self._conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        oldest = self._conn.execute("SELECT MIN(timestamp) FROM episodes").fetchone()[0]
        return {
            "total_episodes": total,
            "total_persons": persons,
            "oldest_memory_ts": oldest,
            "db_path": str(self._db_path),
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()


episodic = EpisodicMemory()
