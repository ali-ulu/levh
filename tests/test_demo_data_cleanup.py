from __future__ import annotations

import pytest

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_demo_cleanup_removes_only_demo_and_preserves_real_memory(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "cleanup.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        real = await eng.store(
            content="Real customer memory survives cleanup",
            source="manual",
            project="real",
            memory_type="episodic",
        )
        seeded = await eng.seed_demo(force=True)
        assert seeded["seeded"] == 20

        result = await eng.remove_demo_data()
        assert result["removed"] == 20
        assert result["fully_purged"] is True
        assert result["remaining"] == 1
        assert await eng.get_memory(real.id) is not None
        remaining = await eng.episodic.get_all()
        assert all(not (m.metadata or {}).get("demo") for m in remaining)
        assert await eng.list_conflict_candidates(status="open", limit=100) == []
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_demo_cleanup_is_repeat_safe(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "repeat.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        await eng.seed_demo()
        first = await eng.remove_demo_data()
        second = await eng.remove_demo_data()
        assert first["removed"] == 20
        assert second["removed"] == 0
        assert second["remaining"] == 0
        assert second["fully_purged"] is True
    finally:
        await eng.shutdown()
