"""Tests for the onboarding demo seed (2.23B).

Covers the pure dataset (`demo_data`), the engine `seed_demo` orchestration
(backdating, reinforcement, derived pipeline), and the idempotency guard.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from server.core.demo_data import demo_memories
from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(
        db_path=str(tmp_path / "seed.db"), embedder_mode="hash", short_term_max=50
    )
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


# ── dataset (pure) ────────────────────────────────────────────────
def test_demo_dataset_is_wellformed():
    mems = demo_memories()
    assert len(mems) >= 15
    for m in mems:
        assert m["content"].strip()
        assert 0.0 <= m["importance"] <= 1.0
        assert m["source"]
        assert isinstance(m["age_days"], int) and m["age_days"] >= 0
        assert isinstance(m.get("metadata", {}), dict)


def test_demo_dataset_is_deterministic():
    a = demo_memories()
    b = demo_memories()
    assert [m["content"] for m in a] == [m["content"] for m in b]


# ── seeding ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_seed_populates_all_layers(engine):
    result = await engine.seed_demo()
    assert result["skipped"] is False
    assert result["seeded"] == len(demo_memories())
    # derived pipeline ran
    assert result["entities"] > 0
    assert result["entity_links"] > 0
    assert result["trust_scored"] == result["seeded"]
    # exactly the one designed, meaningful conflict (deadline) — not noise
    assert result["conflict_candidates"] == 1

    stats = await engine.get_stats()
    assert stats.total_memories == result["seeded"]
    assert stats.pinned_count >= 1


@pytest.mark.asyncio
async def test_seed_backdates_created_at(engine):
    await engine.seed_demo()
    mems = await engine.episodic.search(limit=1000)
    now = datetime.now(timezone.utc)
    ages = []
    for m in mems:
        created = datetime.fromisoformat(m.created_at)
        age_days = (now - created).total_seconds() / 86400
        ages.append(age_days)
        assert age_days >= -0.01  # never in the future
    # A real spread of ages, not all "now" — the point of backdating.
    assert max(ages) - min(ages) > 10


@pytest.mark.asyncio
async def test_seed_produces_people_and_orgs(engine):
    await engine.seed_demo()
    entities = await engine.list_entities_graph(limit=200)
    by_type: dict[str, int] = {}
    for e in entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    assert by_type.get("person", 0) >= 3
    assert by_type.get("organization", 0) >= 2
    assert by_type.get("task", 0) >= 1


@pytest.mark.asyncio
async def test_seed_conflict_is_the_deadline(engine):
    await engine.seed_demo()
    conflicts = await engine.list_conflict_candidates(status="open", limit=10)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["signal_type"] == "attribute_value"
    assert "deadline" in c["explanation"]["detail"]
    # a person entity is shared between the two conflicting memories
    assert any(e.startswith("person:") for e in c["explanation"]["shared_entities"])


@pytest.mark.asyncio
async def test_seed_reinforced_memories_are_more_durable(engine):
    await engine.seed_demo()
    mems = await engine.episodic.search(limit=1000)
    strong = [m for m in mems if m.stability_hours >= 2000]
    faint = [m for m in mems if m.stability_hours <= 100]
    assert strong, "expected some reinforced (durable) demo memories"
    assert faint, "expected some fading demo memories"


@pytest.mark.asyncio
async def test_seed_counts_are_deterministic(engine):
    """Lock the demo's derived shape so a change to the corpus is a conscious,
    reviewed change (and the 5-minute demo stays predictable)."""
    result = await engine.seed_demo()
    assert result["seeded"] == 20
    # 22, not 21: free-text extraction adds the prose-named "Zephyr Labs"
    # organization, which has no e-mail domain to key off.
    assert result["entities"] == 22
    assert result["entity_links"] == 53
    assert result["trust_scored"] == 20
    assert result["conflict_candidates"] == 1


@pytest.mark.asyncio
async def test_seed_is_idempotent_by_default(engine):
    first = await engine.seed_demo()
    assert first["seeded"] > 0
    second = await engine.seed_demo()
    assert second["skipped"] is True
    assert second["seeded"] == 0
    # count unchanged — no duplication
    stats = await engine.get_stats()
    assert stats.total_memories == first["seeded"]


@pytest.mark.asyncio
async def test_seed_force_appends(engine):
    await engine.seed_demo()
    forced = await engine.seed_demo(force=True)
    assert forced["skipped"] is False
    stats = await engine.get_stats()
    assert stats.total_memories == 2 * len(demo_memories())
