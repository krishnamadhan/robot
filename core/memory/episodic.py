"""
Episodic memory — SQLite-backed long-term memory of significant interactions.

Schema design decisions:
- importance field drives retrieval priority (not just recency)
- emotional_valence allows "recall happy memories" queries
- linked_episodes captures narrative chains (part 1 of a conversation)
- raw_data JSON is flexible for future schema evolution

Forgetting curve: importance decays over time so old trivial memories
naturally become less retrievable without being deleted.

KI-016: backed by aiosqlite — one async connection serializes all access,
so concurrent recognition + conversation writes can't hit "database is
locked" the way thread-pool executor calls on a shared sync conn did.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

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
    """Long-term episodic memory store (async, aiosqlite)."""

    # Importance decays at this rate per day — old trivial memories fade
    IMPORTANCE_DECAY_PER_DAY = 0.05

    def __init__(self) -> None:
        self._db_path = DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Call once at startup. Creates DB and tables."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._create_schema()
        log.info("episodic_memory.initialized", path=str(self._db_path))

    async def _create_schema(self) -> None:
        await self._conn.executescript("""
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
        await self._conn.commit()

    # ── Episode CRUD ─────────────────────────────────────────────────────────

    async def store(self, episode: Episode) -> str:
        """Store an episode. Returns episode ID."""
        await self._conn.execute("""
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
        await self._conn.commit()
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

        cur = await self._conn.execute(
            f"SELECT * FROM episodes WHERE {where} ORDER BY importance DESC, timestamp DESC LIMIT ?",
            params
        )
        rows = await cur.fetchall()
        return [self._row_to_episode(r) for r in rows]

    def _row_to_episode(self, row: aiosqlite.Row) -> Episode:
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
        cur = await self._conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        row = await cur.fetchone()
        return self._row_to_episode(row) if row else None

    # ── Person management ────────────────────────────────────────────────────

    async def upsert_person(self, person_id: str, name: Optional[str] = None,
                            relationship_delta: float = 0.0,
                            face_encoding: Optional[bytes] = None,
                            notes: Optional[Dict[str, Any]] = None) -> None:
        cur = await self._conn.execute(
            "SELECT * FROM persons WHERE id = ?", (person_id,)
        )
        existing = await cur.fetchone()

        if existing:
            rq = min(1.0, max(0.0, existing["relationship_quality"] + relationship_delta))
            await self._conn.execute("""
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
            await self._conn.execute("""
                INSERT INTO persons
                (id, name, relationship_quality, interaction_count, last_seen,
                 personality_notes, face_encoding)
                VALUES (?, ?, ?, 1, ?, ?, ?)
            """, (person_id, name, 0.5 + relationship_delta, time.time(),
                  json.dumps(notes or {}), face_encoding))
        await self._conn.commit()

    async def get_person(self, person_id: str) -> Optional[Dict[str, Any]]:
        cur = await self._conn.execute(
            "SELECT * FROM persons WHERE id = ?", (person_id,)
        )
        row = await cur.fetchone()
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
        cur = await self._conn.execute(
            "SELECT id, name, relationship_quality, interaction_count, last_seen FROM persons ORDER BY last_seen DESC"
        )
        rows = await cur.fetchall()
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
        cutoff = time.time() - 30 * 86400  # last 30 days
        cur = await self._conn.execute(
            """SELECT summary, timestamp, emotional_valence, episode_type
               FROM episodes
               WHERE person_id = ? AND timestamp > ?
               ORDER BY importance DESC, timestamp DESC LIMIT ?""",
            (person_id, cutoff, limit),
        )
        rows = await cur.fetchall()

        cur = await self._conn.execute(
            "SELECT emotional_valence FROM episodes WHERE person_id = ? ORDER BY timestamp DESC LIMIT 1",
            (person_id,),
        )
        last_row = await cur.fetchone()

        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE person_id = ?",
            (person_id,),
        )
        total = (await cur.fetchone())[0]

        cur = await self._conn.execute(
            "SELECT relationship_quality, last_seen FROM persons WHERE id = ?",
            (person_id,),
        )
        prow = await cur.fetchone()

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
            "relationship_quality": prow["relationship_quality"] if prow else 0.5,
            "away_s": (time.time() - prow["last_seen"])
                      if prow and prow["last_seen"] else None,
        }

    async def apply_forgetting_curve(self) -> int:
        """Decay importance of old memories. Run daily."""
        cutoff = time.time() - 86400   # older than 1 day
        decay = self.IMPORTANCE_DECAY_PER_DAY
        cur = await self._conn.execute("""
            UPDATE episodes
            SET importance = MAX(0.05, importance - ?)
            WHERE timestamp < ? AND importance > 0.05
        """, (decay, cutoff))
        await self._conn.commit()
        return cur.rowcount

    async def stats(self) -> Dict[str, Any]:
        cur = await self._conn.execute("SELECT COUNT(*) FROM episodes")
        total = (await cur.fetchone())[0]
        cur = await self._conn.execute("SELECT COUNT(*) FROM persons")
        persons = (await cur.fetchone())[0]
        cur = await self._conn.execute("SELECT MIN(timestamp) FROM episodes")
        oldest = (await cur.fetchone())[0]
        return {
            "total_episodes": total,
            "total_persons": persons,
            "oldest_memory_ts": oldest,
            "db_path": str(self._db_path),
        }

    # ── Memory recall for LLM prompts (I5) ───────────────────────────────────

    async def recall_for_prompt(
        self,
        person_id: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        limit: int = 5,
        max_chars: int = 800,
    ) -> str:
        """
        Retrieve recent + relevant memories and summarize to a hard token cap.

        Strategy:
        1. Fetch recent high-importance episodes (recency + importance mix).
        2. If keywords provided, also run LIKE-based keyword search and merge.
        3. Deduplicate by episode ID.
        4. Trim to max_chars (~200 tokens / 800 chars).

        Returns a formatted string ready for LLM injection, or "" if no memories.
        """
        if not self._conn:
            return ""
        try:
            # Base query: recency + importance weighted
            base_clause = "importance >= 0.0"
            base_params: List[Any] = []
            if person_id:
                base_clause += " AND person_id = ?"
                base_params.append(person_id)

            cur = await self._conn.execute(
                f"""SELECT id, summary, timestamp, emotional_valence, importance
                    FROM episodes
                    WHERE {base_clause}
                    ORDER BY (importance * 0.4 + 0.6 * (timestamp / (SELECT MAX(timestamp) + 1 FROM episodes)))
                    DESC LIMIT ?""",
                base_params + [limit],
            )
            rows = list(await cur.fetchall())

            seen_ids: set = {r[0] for r in rows}

            # Keyword search (LIKE) — union with above
            if keywords:
                for kw in keywords[:3]:   # limit to 3 keywords to avoid query bloat
                    cur = await self._conn.execute(
                        f"""SELECT id, summary, timestamp, emotional_valence, importance
                            FROM episodes
                            WHERE summary LIKE ? {'AND person_id = ?' if person_id else ''}
                            ORDER BY timestamp DESC LIMIT ?""",
                        ([f"%{kw}%", person_id, limit // 2] if person_id
                         else [f"%{kw}%", limit // 2]),
                    )
                    kw_rows = await cur.fetchall()
                    for r in kw_rows:
                        if r[0] not in seen_ids:
                            rows.append(r)
                            seen_ids.add(r[0])

            if not rows:
                return ""

            # Sort by timestamp DESC for most-recent-first in prompt
            rows.sort(key=lambda r: r[2], reverse=True)

            # Format and apply hard char cap
            lines: List[str] = []
            total_chars = 0
            for row_id, summary, ts, valence, importance in rows:
                age_s = time.time() - ts
                if age_s < 3600:
                    when = "recently"
                elif age_s < 86400:
                    when = f"{int(age_s / 3600)}h ago"
                elif age_s < 7 * 86400:
                    when = f"{int(age_s / 86400)}d ago"
                else:
                    when = time.strftime("%b %d", time.localtime(ts))
                mood = ("happy" if valence > 0.3 else "sad" if valence < -0.3 else "neutral")
                line = f"[{when}, {mood}] {summary}"
                if total_chars + len(line) + 1 > max_chars:
                    break
                lines.append(line)
                total_chars += len(line) + 1

            if not lines:
                return ""
            return "\n".join(lines)
        except Exception as e:
            log.warning("episodic.recall_failed", error=str(e)[:80])
            return ""

    async def store_fact(
        self,
        person_id: str,
        fact: str,
        importance: float = 0.7,
        valence: float = 0.0,
    ) -> str:
        """
        Convenience method to store a single salient fact about a person.
        Used after conversation turns to write back key details.
        """
        ep = Episode(
            episode_type="conversation_fact",
            summary=fact,
            person_id=person_id,
            importance=importance,
            emotional_valence=valence,
        )
        return await self.store(ep)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None


episodic = EpisodicMemory()
