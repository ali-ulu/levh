"""Concurrency guard for the P0-1 fix (_sync_with_external_writes).

tests/groundtruth/test_cross_process_coherence.py proves the sequential
contract: a peer's create/update/delete is visible without a restart. This
file guards the concurrent case that sequential tests can't reach: a
recall()-triggered cache refresh (clear + reload from SQLite) running at the
same time as this engine's own store() calls. If the refresh's clear() ever
raced ahead of a concurrent store()'s cache write without a lock, an engine
could transiently lose visibility of a memory it just stored itself, even
though SQLite always had it correctly.

Interference is disabled here (interference_threshold = 1.0): it makes an
extra write per store() to weaken a similar older memory, and at high enough
write concurrency across two connections that trips a separate, pre-existing
SQLite `database is locked` condition unrelated to cache coherence. That is a
real, independently reproducible issue in the interference write path — filed
separately, not papered over here — but it has nothing to do with what this
test guards, so it's disabled to keep this a reliable, non-flaky regression
test for the thing it actually checks.
"""

from __future__ import annotations

import asyncio

import pytest

from server.core.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_own_writes_survive_a_concurrent_external_sync(tmp_path):
    db_path = str(tmp_path / "concurrency.db")
    mine = MemoryEngine(db_path=db_path, embedder_mode="hash")
    peer = MemoryEngine(db_path=db_path, embedder_mode="hash")
    await mine.initialize()
    await peer.initialize()
    mine.interference_threshold = 1.0
    peer.interference_threshold = 1.0
    try:
        stored_ids: list[str] = []

        async def store_mine(i: int) -> None:
            memory = await mine.store(f"mine concurrency canary {i}", memory_type="episodic")
            stored_ids.append(memory.id)

        async def store_peer(n: int) -> None:
            for i in range(n):
                await peer.store(f"peer concurrency canary {i}", memory_type="episodic")

        # Own writes, a peer hammering external writes (forcing repeated
        # data_version changes), and concurrent recalls (each a potential
        # cache-refresh trigger) all racing on the same engine at once.
        await asyncio.gather(
            *[store_mine(i) for i in range(40)],
            store_peer(40),
            *[mine.recall("concurrency canary", reinforce=False) for _ in range(20)],
        )

        result = await mine.recall("mine concurrency canary", top_k=200, reinforce=False)
        found = {m.id for m in result.memories}
        missing = [mid for mid in stored_ids if mid not in found]
        assert not missing, f"{len(missing)}/{len(stored_ids)} own writes vanished from recall"
    finally:
        await mine.shutdown()
        await peer.shutdown()
