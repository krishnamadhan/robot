"""
B4 — Episodic memory recall tests (I5).

Tests:
  - recall_for_prompt: returns facts, respects token cap
  - store_fact: convenience write-back
  - Fact taught early → present in later prompt context
  - Memory token cap ≤ 800 chars
  - Keyword search finds relevant memories
  - Empty DB returns ""
"""

import sys
import time
from pathlib import Path

import aiosqlite
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.memory.episodic import EpisodicMemory, Episode


@pytest.fixture
async def mem_db():
    """Fresh in-memory EpisodicMemory for each test (KI-016: aiosqlite)."""
    db = EpisodicMemory.__new__(EpisodicMemory)
    db._db_path = Path(":memory:")
    db._conn = await aiosqlite.connect(":memory:")
    db._conn.row_factory = aiosqlite.Row
    await db._create_schema()
    yield db
    await db.close()


# ── Basic read/write ──────────────────────────────────────────────────────────

class TestEpisodicStoreAndRetrieve:

    async def test_store_and_retrieve(self, mem_db):
        """Store an episode, retrieve it back."""
        ep = Episode(
            episode_type="test",
            summary="Madhan said he loves idli",
            person_id="madhan",
            importance=0.8,
            emotional_valence=0.5,
        )
        await mem_db.store(ep)

        episodes = await mem_db.retrieve(limit=10, person_id="madhan")
        assert len(episodes) == 1
        assert "idli" in episodes[0].summary

    async def test_empty_db_returns_empty_list(self, mem_db):
        episodes = await mem_db.retrieve(limit=10)
        assert episodes == []


# ── recall_for_prompt ────────────────────────────────────────────────────────

class TestRecallForPrompt:

    async def test_empty_db_returns_empty_string(self, mem_db):
        result = await mem_db.recall_for_prompt("madhan", None, 5, 800)
        assert result == ""

    async def test_stores_and_recalls_fact(self, mem_db):
        """I5: Fact stored → retrieved in recall."""
        ep = Episode(
            episode_type="conversation_fact",
            summary="Madhan told me his favorite food is idli",
            person_id="madhan",
            importance=0.8,
        )
        await mem_db.store(ep)

        result = await mem_db.recall_for_prompt("madhan", None, 5, 800)
        assert "idli" in result, f"Expected 'idli' in recall result: {result!r}"

    async def test_caps_output_to_max_chars(self, mem_db):
        """Memory recall respects hard char cap."""
        for i in range(20):
            ep = Episode(
                episode_type="test",
                summary=f"Memory entry {i} with some content about the robot and person",
                person_id="madhan",
                importance=0.5,
            )
            await mem_db.store(ep)

        result = await mem_db.recall_for_prompt("madhan", None, 20, 800)
        assert len(result) <= 800, f"Recall exceeded cap: {len(result)} chars"

    async def test_keyword_search_finds_relevant(self, mem_db):
        """Keyword search surfaces relevant memories not in top-N by recency."""
        ep_old = Episode(
            episode_type="conversation",
            summary="Madhan once mentioned he hates broccoli",
            person_id="madhan",
            importance=0.3,
            timestamp=time.time() - 7 * 86400,  # 7 days ago
        )
        await mem_db.store(ep_old)

        # Store many newer entries to push old one down by recency
        for i in range(10):
            ep = Episode(
                episode_type="test",
                summary=f"Generic interaction {i}",
                person_id="madhan",
                importance=0.5,
            )
            await mem_db.store(ep)

        # Search with keyword "broccoli" should find it
        result = await mem_db.recall_for_prompt("madhan", ["broccoli"], 5, 800)
        assert "broccoli" in result, f"Keyword search failed: {result!r}"

    async def test_person_filter_isolation(self, mem_db):
        """Recall for person A should not include person B's memories."""
        ep_a = Episode(
            episode_type="test",
            summary="Madhan loves Tamil movies",
            person_id="madhan",
            importance=0.8,
        )
        ep_b = Episode(
            episode_type="test",
            summary="Indhu loves dancing",
            person_id="indhu",
            importance=0.8,
        )
        await mem_db.store(ep_a)
        await mem_db.store(ep_b)

        result_a = await mem_db.recall_for_prompt("madhan", None, 5, 800)
        result_b = await mem_db.recall_for_prompt("indhu", None, 5, 800)

        assert "Tamil movies" in result_a
        assert "dancing" not in result_a
        assert "dancing" in result_b
        assert "Tamil movies" not in result_b


# ── store_fact ────────────────────────────────────────────────────────────────

class TestStoreFact:

    async def test_store_fact_persists(self, mem_db):
        """store_fact writes a retrievable episode."""
        await mem_db.store_fact("madhan", "Madhan's favorite color is blue")

        result = await mem_db.recall_for_prompt("madhan", None, 5, 800)
        assert "blue" in result, f"Expected fact in recall: {result!r}"

    async def test_store_fact_high_importance(self, mem_db):
        """Facts stored with importance=0.7 appear before low-importance ones."""
        ep_low = Episode(
            episode_type="test",
            summary="Generic chat happened",
            person_id="madhan",
            importance=0.2,
        )
        await mem_db.store(ep_low)
        await mem_db.store_fact("madhan", "Madhan is a software developer")

        result = await mem_db.recall_for_prompt("madhan", None, 5, 800)
        assert "software developer" in result


# ── I5: End-to-end fact injection ─────────────────────────────────────────────

class TestI5FactInjection:
    """
    I5: A fact established in voice_conversation scenario
    must appear in a later prompt's memory context.
    """

    async def test_fact_survives_and_appears_in_prompt(self, mem_db):
        """Simulate: teach fact early, recall in later conversation."""
        ep = Episode(
            episode_type="conversation_fact",
            summary="Madhan told Cosmo his favorite food is idli with sambar",
            person_id="madhan",
            importance=0.8,
            emotional_valence=0.4,
        )
        await mem_db.store(ep)

        memories = await mem_db.recall_for_prompt("madhan", None, 5, 800)

        from cognition.mind import _SYSTEM
        system_prompt = _SYSTEM + f"\n\nMemories:\n{memories}"

        assert "idli" in system_prompt, (
            f"I5 FAILED: fact 'idli' not found in prompt.\n"
            f"Memories block:\n{memories}\n"
        )

    async def test_multiple_facts_all_recalled(self, mem_db):
        """Multiple important facts all appear in recall (within cap)."""
        facts = [
            ("Madhan loves cricket", 0.8),
            ("Madhan works in tech", 0.7),
            ("Madhan is from Tamil Nadu", 0.7),
        ]
        for summary, importance in facts:
            ep = Episode(
                episode_type="conversation_fact",
                summary=summary,
                person_id="madhan",
                importance=importance,
            )
            await mem_db.store(ep)

        memories = await mem_db.recall_for_prompt("madhan", None, 5, 800)

        for summary, _ in facts:
            key = summary.split()[-1]  # last word as check
            assert key in memories, f"Fact '{summary}' not in recall: {memories!r}"

    async def test_memory_block_capped_at_800_chars(self, mem_db):
        """Hard cap: memory block injected into prompt never exceeds 800 chars."""
        for i in range(30):
            ep = Episode(
                episode_type="test",
                summary=f"This is memory entry number {i} with lots of details that pad out the text significantly",
                person_id="madhan",
                importance=0.6,
            )
            await mem_db.store(ep)

        memories = await mem_db.recall_for_prompt("madhan", None, 30, 800)
        assert len(memories) <= 800, f"Memory block too long: {len(memories)}"
