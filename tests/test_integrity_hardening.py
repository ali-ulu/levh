from __future__ import annotations

import copy
import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


async def _opposing_pair(engine: MemoryEngine):
    a = await engine.store(
        "The contract is approved",
        memory_type="episodic",
        source="connector:calendar",
        metadata={"attendees": ["Alice <alice@acme.com>"]},
    )
    b = await engine.store(
        "The contract is rejected",
        memory_type="episodic",
        source="connector:email",
        metadata={"attendees": ["Alice <alice@acme.com>"]},
    )
    return a, b


@pytest.mark.asyncio
async def test_purge_removes_primary_and_all_derived_rows(engine):
    a, _ = await _opposing_pair(engine)
    assert await engine.get_entity("Alice") is not None
    assert await engine.get_trust(a.id) is not None
    assert await engine.list_conflict_candidates(status="open")

    result = await engine.purge_memory(a.id)
    assert result["purged"] is True
    assert all(value is False for value in result["residue"].values())
    assert await engine.db.memory_residue(a.id) == {
        "episodic": 0,
        "entity_links": 0,
        "trust_score": 0,
        "conflict_candidates": 0,
    }


@pytest.mark.asyncio
async def test_replace_restore_validates_before_destructive_clear(engine):
    survivor = await engine.store("Existing local memory", memory_type="episodic")
    snapshot = await engine.backup()
    broken = copy.deepcopy(snapshot)
    broken["memories"].append({"id": "broken-no-content"})

    with pytest.raises(ValueError, match="invalid backup snapshot"):
        await engine.restore(broken, replace=True)

    assert (await engine.get_memory(survivor.id)) is not None
    assert (await engine.get_stats()).total_memories == 1


@pytest.mark.asyncio
async def test_restore_rebuilds_caches_graph_trust_and_conflicts(engine, tmp_path):
    await _opposing_pair(engine)
    snapshot = await engine.backup()

    restored = MemoryEngine(db_path=str(tmp_path / "restored.db"), embedder_mode="hash")
    await restored.initialize()
    try:
        result = await restored.restore(snapshot, replace=True)
        assert result["memories"] == 2
        assert restored.vector_store.size == 2
        assert await restored.get_entity("Alice") is not None
        memories = await restored.list_memories(limit=10)
        assert await restored.get_trust(memories[0].id) is not None
        assert len(await restored.list_conflict_candidates(status="open")) == 1
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_content_edit_prunes_stale_conflict_candidate(engine):
    _, b = await _opposing_pair(engine)
    assert len(await engine.list_conflict_candidates(status="open")) == 1

    await engine.update_memory(b.id, content="The contract is approved")
    assert await engine.list_conflict_candidates(status="open") == []


@pytest.mark.asyncio
async def test_entity_graph_reconciles_after_content_edit(engine):
    memory = await engine.store(
        "I will send the Atlas report",
        memory_type="episodic",
        source="connector:notes",
    )
    tasks = await engine.list_entities_graph(entity_type="task")
    assert any("send the Atlas report" in row["name"] for row in tasks)

    await engine.update_memory(memory.id, content="No action is required")
    tasks_after = await engine.list_entities_graph(entity_type="task")
    assert not any("send the Atlas report" in row["name"] for row in tasks_after)


@pytest.mark.asyncio
async def test_pin_invalidates_cached_trust_breakdown(engine):
    memory = await engine.store(
        "Atlas architecture decision",
        memory_type="episodic",
        source="connector:notes",
    )
    before = await engine.get_trust(memory.id)
    await engine.set_pinned(memory.id, True)
    after = await engine.get_trust(memory.id)
    assert after["components"]["review_score"] > before["components"]["review_score"]
    assert after["confidence"] >= before["confidence"]


@pytest.mark.asyncio
async def test_session_count_refreshes_after_forget(engine):
    session = await engine.create_session("Integrity session")
    memory = await engine.store(
        "Session-bound memory",
        session_id=session.id,
        memory_type="episodic",
    )
    assert (await engine.get_session(session.id)).memory_count == 1
    await engine.forget(memory.id)
    assert (await engine.get_session(session.id)).memory_count == 0
