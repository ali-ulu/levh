"""Re-embedding stored vectors after the embedder mode changes.

Switching ``EMBEDDER_MODE`` leaves every stored vector in the old embedder's
space; recall compares dimension-matched vectors only and skips the rest, so
the pre-existing memories silently stop being reachable. ``levh reembed`` is
the repair ``levh doctor`` points at. These tests pin the repair: which
memories count as stale, that a run rewrites exactly those, and that the live
vector store — not just the DB row — ends up holding the new vector.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

from server.core.memory_engine import MemoryEngine


class _StubEmbedder:
    """A deterministic embedder whose identity and vectors can be swapped.

    Stands in for a real ``local``/``openai`` provider so the re-embed path can
    be exercised offline and without a model, which is exactly the situation
    the command exists for.
    """

    is_semantic = True

    def __init__(self, tag: str, dimension: int = 4) -> None:
        self._tag = tag
        self.dimension = dimension

    async def embed(self, text: str) -> list[float]:
        # Distinct per tag, so "was this re-embedded" is observable from the
        # vector itself, not only from provenance.
        base = float(sum(ord(c) for c in self._tag) % 97)
        vector = [base, float(len(text)), 1.0, 0.0][: self.dimension]
        return vector

    def identity(self) -> dict:
        return {"provider": "stub", "model": self._tag, "dimension": self.dimension}

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    eng._embedder = _StubEmbedder("v1")
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_every_memory_is_stale_once_the_embedder_changes(engine):
    await engine.store(content="The deploy branch is prod", memory_type="episodic")
    await engine.store(content="Rate limits are per API key", memory_type="episodic")

    engine._embedder = _StubEmbedder("v2")
    report = await engine.reembed_memories(dry_run=True)
    assert report["scanned"] == 2
    assert report["stale"] == 2
    assert report["reembedded"] == 0


@pytest.mark.asyncio
async def test_a_matching_provenance_is_not_stale(engine):
    await engine.store(content="On v1", memory_type="episodic")

    report = await engine.reembed_memories(dry_run=True)
    assert report["scanned"] == 1
    assert report["stale"] == 0


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(engine):
    memory = await engine.store(content="The deploy branch is prod", memory_type="episodic")
    before = list(memory.embedding or [])
    engine._embedder = _StubEmbedder("v2")

    report = await engine.reembed_memories(dry_run=True)

    assert report["dry_run"] is True
    stored = await engine.episodic.get(memory.id)
    assert stored.embedding == before


@pytest.mark.asyncio
async def test_a_run_rewrites_only_the_stale_memories(engine):
    memory = await engine.store(content="On v1", memory_type="episodic")
    assert engine._provenance_of(memory) == engine.embedder.identity()

    # Swap the embedder underneath: the stored vector is now in the old space.
    engine._embedder = _StubEmbedder("v2")
    report = await engine.reembed_memories(dry_run=True)
    assert report["stale"] == 1

    report = await engine.reembed_memories()
    assert report["reembedded"] == 1
    refreshed = await engine.episodic.get(memory.id)
    assert engine._provenance_of(refreshed) == engine.embedder.identity()
    assert refreshed.embedding != memory.embedding

    # Second run is a no-op: nothing is stale any more.
    assert (await engine.reembed_memories())["stale"] == 0


@pytest.mark.asyncio
async def test_the_live_vector_store_is_refreshed_not_just_the_row(engine):
    memory = await engine.store(content="The deploy branch is prod", memory_type="episodic")
    engine._embedder = _StubEmbedder("v2")
    await engine.reembed_memories()

    live = engine.vector_store.get(memory.id)
    assert live is not None
    assert live.embedding == (await engine.episodic.get(memory.id)).embedding


@pytest.mark.asyncio
async def test_project_filter_and_breakdown(engine):
    await engine.store(content="alpha one", project="a", memory_type="episodic")
    await engine.store(content="beta two", project="b", memory_type="episodic")
    engine._embedder = _StubEmbedder("v2")

    report = await engine.reembed_memories(project="a", dry_run=True)
    assert report["scanned"] == 1
    assert report["stale"] == 1
    assert report["by_project"] == {"a": 1}

    everything = await engine.reembed_memories(dry_run=True)
    assert everything["by_project"] == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_progress_callback_reports_each_memory(engine):
    for i in range(3):
        await engine.store(content=f"fact number {i}", memory_type="episodic")
    engine._embedder = _StubEmbedder("v2")

    seen: list[tuple[int, int]] = []
    report = await engine.reembed_memories(on_progress=lambda d, t: seen.append((d, t)))

    assert report["reembedded"] == 3
    assert seen == [(1, 3), (2, 3), (3, 3)]
