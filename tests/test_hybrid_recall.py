"""Hybrid candidate retrieval: full-text (FTS5) joins the vector store.

The vector store is process-local and only ever holds rows that had an
embedding. Any row without one — imported by a peer, or left without a vector
by a mode switch — was invisible to every candidate path, so recall could
never return it however well its words matched. The database is the source of
truth; FTS indexes content and reaches those rows regardless of vectors.

Offline and deterministic — ``EMBEDDER_MODE=hash``.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

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


async def _drop_embedding(engine, memory_id: str) -> None:
    """Make a row embedding-less, the way a peer import or a cleared vector would.

    Drops it from the process-local vector store too: this connection's own raw
    write does not bump ``PRAGMA data_version``, so the store would otherwise
    keep serving the stale copy.
    """
    await engine.db.conn.execute(
        "UPDATE memories SET embedding = NULL WHERE id = ?", (memory_id,)
    )
    await engine.db.commit()
    engine.vector_store.remove(memory_id)


@pytest.mark.asyncio
async def test_embedding_less_memory_is_recallable(engine):
    target = await engine.store(
        content="migration rollout notes for the database", memory_type="episodic"
    )
    await _drop_embedding(engine, target.id)

    result = await engine.recall("migration rollout", top_k=5, reinforce=False)

    assert [m.id for m in result.memories] == [target.id]


@pytest.mark.asyncio
async def test_embedding_less_memory_survives_a_restart(engine):
    """The reload path is where the row would otherwise be lost: initialize()
    only repopulates the vector store from rows that have an embedding."""
    target = await engine.store(
        content="the deploy checklist lives in the runbook", memory_type="episodic"
    )
    await _drop_embedding(engine, target.id)
    await engine.shutdown()

    reloaded = MemoryEngine(db_path=engine.db.db_path, embedder_mode="hash")
    await reloaded.initialize()
    try:
        assert reloaded.vector_store.size == 0
        result = await reloaded.recall("deploy checklist runbook", top_k=5, reinforce=False)
        assert [m.id for m in result.memories] == [target.id]
    finally:
        await reloaded.shutdown()


@pytest.mark.asyncio
async def test_fts_candidate_is_labelled_keyword(engine):
    target = await engine.store(
        content="migration rollout notes for the database", memory_type="episodic"
    )
    await _drop_embedding(engine, target.id)

    result = await engine.recall(
        "migration rollout", top_k=5, reinforce=False, explain=True
    )

    bd = {b.memory_id: b for b in result.breakdowns}[target.id]
    assert bd.candidate_source == "keyword"
    assert bd.similarity_source == "lexical"
    assert bd.cosine == 0.0  # no vector existed to measure


@pytest.mark.asyncio
async def test_fts_matches_a_prefix_of_the_stored_word(engine):
    """FTS tokenises and prefix-matches, so a query root finds an inflected form
    even though no vector exists for the row — "migrasyon" finds "migrasyonu".
    The in-memory lexical scan absorbs the same inflection, but it can only see
    rows the vector store holds; this is the path for the ones it does not."""
    target = await engine.store(
        content="Veritabanı migrasyonu her gece saat ikide çalışır", memory_type="episodic"
    )
    await _drop_embedding(engine, target.id)

    result = await engine.recall("veritabanı migrasyon", top_k=5, reinforce=False)

    assert target.id in [m.id for m in result.memories]


@pytest.mark.asyncio
async def test_filters_apply_to_fts_candidates(engine):
    """A project filter must gate the FTS source too, or a scoped recall leaks
    another workspace's memories."""
    mine = await engine.store(
        content="migration rollout for alpha", memory_type="episodic", project="alpha"
    )
    theirs = await engine.store(
        content="migration rollout for beta", memory_type="episodic", project="beta"
    )
    await _drop_embedding(engine, mine.id)
    await _drop_embedding(engine, theirs.id)

    result = await engine.recall(
        "migration rollout", top_k=5, project="alpha", reinforce=False
    )

    assert [m.id for m in result.memories] == [mine.id]


@pytest.mark.asyncio
async def test_like_fallback_when_fts5_is_unavailable(engine):
    """A vendor SQLite without FTS5 must not lose recall: the vector path still
    answers, and the FTS source simply contributes nothing."""
    kept = await engine.store(content="migration rollout notes", memory_type="episodic")
    engine.db.fts5_available = False

    result = await engine.recall("migration rollout", top_k=5, reinforce=False)

    assert [m.id for m in result.memories] == [kept.id]


@pytest.mark.asyncio
async def test_fts_or_query_drops_stopwords(engine):
    """The candidate query ORs content words; stopwords would otherwise pull
    every row and swamp the ranking."""
    assert engine.db._fts_or_query("which branch do we deploy from") == "branch* OR deploy*"
    assert engine.db._fts_or_query("the of is") == ""


class _SemanticStub:
    """Reports semantic vectors, so the cosine ranking path runs offline."""

    is_semantic = True
    dimension = 2

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def identity(self) -> dict:
        return {"mode": "stub"}

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_semantic_mode_falls_back_to_coverage_for_fts_candidates(engine):
    """With a real embedder the cosine ranks — but only for rows that have one.
    An embedding-less FTS candidate would score a measured-looking 0.0 and be
    buried; it must instead be scored on coverage and labelled lexical."""
    engine._embedder = _SemanticStub()
    target = await engine.store(
        content="migration rollout notes for the database", memory_type="episodic"
    )
    await _drop_embedding(engine, target.id)

    result = await engine.recall(
        "migration rollout", top_k=5, reinforce=False, explain=True
    )

    assert target.id in [m.id for m in result.memories]
    bd = {b.memory_id: b for b in result.breakdowns}[target.id]
    assert bd.similarity_source == "lexical"
    assert bd.similarity == 1.0
    assert bd.cosine == 0.0
