"""_apply_interference's write must never fail a store() that already
succeeded (discovered while stress-testing the P0-1 cross-process fix:
concurrent multi-engine writes can trip a transient
`sqlite3.OperationalError: database is locked` here).

store() commits the new memory via episodic.store(mem) BEFORE
_apply_interference runs, so by the time interference's own write happens,
the caller has already durably succeeded. Interference is a best-effort
enhancement -- like the embedder/summarizer fallbacks in
docs/ARCHITECTURE.md invariant #5 -- so a transient failure here must degrade
(skip weakening that one candidate) rather than propagate and make a
successful store() look like it failed.
"""

from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "interference.db"), embedder_mode="hash")
    await eng.initialize()
    eng.interference_threshold = 0.0  # every same-project candidate interferes
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_store_succeeds_despite_a_locked_interference_write(engine, monkeypatch):
    old = await engine.store("The deploy branch is main", project="p", memory_type="episodic")
    original_update = engine.db.update_memory

    async def flaky_update(memory_id, fields):
        if memory_id == old.id:
            raise sqlite3.OperationalError("database is locked")
        return await original_update(memory_id, fields)

    monkeypatch.setattr(engine.db, "update_memory", flaky_update)

    # Must not raise: the new memory's own store() has nothing to do with the
    # failing interference write on a DIFFERENT (older) memory.
    new = await engine.store("The deploy branch is prod", project="p", memory_type="episodic")

    assert new is not None
    assert (await engine.get_memory(new.id)) is not None


@pytest.mark.asyncio
async def test_locked_candidate_keeps_its_prior_stability_in_both_db_and_cache(engine, monkeypatch):
    """A failed weaken must not desync the in-memory cache from SQLite --
    the old code mutated old.stability_hours before the write, so a failure
    left the cache weakened while the DB stayed at the original value."""
    old = await engine.store("The deploy branch is main", project="p", memory_type="episodic")
    original_stability = old.stability_hours
    original_update = engine.db.update_memory

    async def flaky_update(memory_id, fields):
        if memory_id == old.id:
            raise sqlite3.OperationalError("database is locked")
        return await original_update(memory_id, fields)

    monkeypatch.setattr(engine.db, "update_memory", flaky_update)
    await engine.store("The deploy branch is prod", project="p", memory_type="episodic")

    cached = engine.vector_store.get(old.id)
    assert cached.stability_hours == original_stability

    row = await engine.db.get_memory(old.id)
    assert row["stability_hours"] == original_stability


@pytest.mark.asyncio
async def test_one_locked_candidate_does_not_block_the_others(engine, monkeypatch):
    """Five candidates can interfere per call; one transient failure must not
    stop the loop from weakening the rest."""
    locked = await engine.store("The deploy branch is main", project="p", memory_type="episodic")
    healthy = await engine.store("The deploy branch was staging", project="p", memory_type="episodic")
    original_healthy_stability = healthy.stability_hours
    original_update = engine.db.update_memory

    async def flaky_update(memory_id, fields):
        if memory_id == locked.id:
            raise sqlite3.OperationalError("database is locked")
        return await original_update(memory_id, fields)

    monkeypatch.setattr(engine.db, "update_memory", flaky_update)
    await engine.store("The deploy branch is prod", project="p", memory_type="episodic")

    # `healthy` is the same object instance vector_store holds, so its
    # attribute already reflects the mutation -- the DB row must agree with
    # it, and both must be weaker than the pre-interference original.
    healthy_row = await engine.db.get_memory(healthy.id)
    assert healthy_row["stability_hours"] == healthy.stability_hours
    assert healthy.stability_hours < original_healthy_stability


@pytest.mark.asyncio
async def test_a_genuinely_different_operational_error_is_still_swallowed_here(engine, monkeypatch):
    """The catch is intentionally not narrowed to a 'locked' message match --
    any OperationalError on this best-effort write degrades the same way."""
    # Not referenced further; storing it is what gives the next store() an
    # interference candidate to (fail to) weaken.
    await engine.store("The deploy branch is main", project="p", memory_type="episodic")

    async def broken_update(memory_id, fields):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(engine.db, "update_memory", broken_update)

    new = await engine.store("The deploy branch is prod", project="p", memory_type="episodic")
    assert new is not None
