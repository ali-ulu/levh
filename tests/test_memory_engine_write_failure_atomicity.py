"""Regression coverage for issue #15: DB write failures must not mutate caches."""

from __future__ import annotations

import sqlite3

import pytest

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_failed_store_does_not_leave_ghost_cache_entries(tmp_path, monkeypatch):
    engine = MemoryEngine(
        db_path=str(tmp_path / "store-failure.db"),
        embedder_mode="hash",
    )
    await engine.initialize()

    async def fail_store(_memory):
        raise sqlite3.OperationalError("forced")

    monkeypatch.setattr(engine.episodic, "store", fail_store)

    try:
        with pytest.raises(sqlite3.OperationalError, match="forced"):
            await engine.store("ghost memory")

        assert len(engine.short_term) == 0
        assert engine.vector_store.size == 0
        assert await engine.episodic.count() == 0
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_failed_forget_keeps_persisted_memory_visible_in_caches(tmp_path, monkeypatch):
    engine = MemoryEngine(
        db_path=str(tmp_path / "forget-failure.db"),
        embedder_mode="hash",
    )
    await engine.initialize()

    try:
        memory = await engine.store("keep me after failed delete")

        async def fail_delete(_memory_id):
            raise sqlite3.OperationalError("forced")

        monkeypatch.setattr(engine.db, "delete_memory_cascade", fail_delete)

        with pytest.raises(sqlite3.OperationalError, match="forced"):
            await engine.forget(memory.id)

        assert engine.short_term.find(memory.id) is not None
        assert engine.vector_store.get(memory.id) is not None
        assert await engine.episodic.get(memory.id) is not None

        recalled = await engine.recall(
            "keep me after failed delete",
            top_k=10,
            reinforce=False,
        )
        assert memory.id in {item.id for item in recalled.memories}
    finally:
        await engine.shutdown()
