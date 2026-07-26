"""Desired invariants for live readers sharing one SQLite database.

The process-heavy transport reproduction is retained under
evidence/groundtruth/task-00A1/harness/. These tests use two in-process engine
instances so normal CI records the desired contract without spawning services.
"""

from pathlib import Path

import pytest

from server.core.memory_engine import MemoryEngine


PROJECT = "GT00A5_P0_1_INVARIANT"
async def _engine(db_path: Path) -> MemoryEngine:
    engine = MemoryEngine(db_path=str(db_path), embedder_mode="hash")
    await engine.initialize()
    return engine


async def _seed(db_path: Path, content: str) -> str:
    seed = await _engine(db_path)
    try:
        memory = await seed.store(
            content,
            project=PROJECT,
            memory_type="episodic",
        )
        return memory.id
    finally:
        await seed.shutdown()


@pytest.mark.asyncio
async def test_live_peer_observes_create_without_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "create.db"
    writer = await _engine(db_path)
    observer = await _engine(db_path)
    try:
        memory = await writer.store(
            "GT00A5 cross-reader create canary",
            project=PROJECT,
            memory_type="episodic",
        )
        result = await observer.recall(
            "GT00A5 cross-reader create canary",
            project=PROJECT,
            reinforce=False,
        )
        assert memory.id in {item.id for item in result.memories}
    finally:
        await observer.shutdown()
        await writer.shutdown()


@pytest.mark.asyncio
async def test_live_peer_observes_update_without_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "update.db"
    memory_id = await _seed(db_path, "GT00A5 old update canary")
    writer = await _engine(db_path)
    observer = await _engine(db_path)
    try:
        updated = await writer.update_memory(
            memory_id,
            content="GT00A5 fresh update canary",
        )
        assert updated is not None
        result = await observer.recall(
            "GT00A5 fresh update canary",
            project=PROJECT,
            reinforce=False,
        )
        observed = {item.id: item.content for item in result.memories}
        assert observed.get(memory_id) == "GT00A5 fresh update canary"
    finally:
        await observer.shutdown()
        await writer.shutdown()


@pytest.mark.asyncio
async def test_live_peer_observes_delete_without_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "delete.db"
    memory_id = await _seed(db_path, "GT00A5 delete ghost canary")
    writer = await _engine(db_path)
    observer = await _engine(db_path)
    try:
        assert await writer.forget(memory_id) is True
        result = await observer.recall(
            "GT00A5 delete ghost canary",
            project=PROJECT,
            reinforce=False,
        )
        assert memory_id not in {item.id for item in result.memories}
    finally:
        await observer.shutdown()
        await writer.shutdown()
