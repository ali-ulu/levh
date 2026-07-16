"""Regression tests for the code-review fixes.

Focus: the vector-store cache staleness bug (recall scores off the cached
Memory objects, so mutators that only wrote to SQLite were invisible to
ranking), and the read-only recall option.
"""

import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine


@pytest_asyncio.fixture
async def engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    eng = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=10)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_set_pinned_refreshes_vector_cache(engine):
    mem = await engine.store(content="deploy branch is prod", memory_type="episodic")
    assert engine.vector_store.get(mem.id).pinned is False

    await engine.set_pinned(mem.id, True)
    # The object recall() ranks from must reflect the pin, not just SQLite.
    assert engine.vector_store.get(mem.id).pinned is True


@pytest.mark.asyncio
async def test_set_importance_refreshes_vector_cache(engine):
    mem = await engine.store(content="minor note", importance=0.2, memory_type="episodic")
    await engine.set_importance(mem.id, 0.9)
    assert engine.vector_store.get(mem.id).importance == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_negative_feedback_refreshes_vector_cache(engine):
    mem = await engine.store(content="stale fact", memory_type="episodic")
    before = engine.vector_store.get(mem.id).stability_hours
    await engine.memory_feedback(mem.id, helpful=False)
    after = engine.vector_store.get(mem.id).stability_hours
    assert after < before


@pytest.mark.asyncio
async def test_reinforce_memory_refreshes_vector_cache(engine):
    mem = await engine.store(content="useful fact", importance=0.8, memory_type="episodic")
    before = engine.vector_store.get(mem.id).stability_hours
    await engine.reinforce_memory(mem.id)
    after = engine.vector_store.get(mem.id).stability_hours
    assert after > before


@pytest.mark.asyncio
async def test_readonly_recall_does_not_reinforce(engine):
    mem = await engine.store(content="searchable content here", memory_type="episodic")
    freq_before = mem.frequency
    stability_before = mem.stability_hours

    await engine.recall(query="searchable content", top_k=5, reinforce=False)

    fresh = await engine.get_memory(mem.id)
    assert fresh.frequency == freq_before
    assert fresh.stability_hours == stability_before
    assert fresh.recall_count == 0


@pytest.mark.asyncio
async def test_default_recall_still_reinforces(engine):
    mem = await engine.store(content="another searchable thing", memory_type="episodic")
    freq_before = mem.frequency  # capture int before recall mutates in place
    await engine.recall(query="another searchable", top_k=5)
    fresh = await engine.get_memory(mem.id)
    assert fresh.frequency > freq_before
    assert fresh.recall_count >= 1


@pytest.mark.asyncio
async def test_store_invalid_memory_type_raises(engine):
    with pytest.raises(ValueError):
        await engine.store(content="x", memory_type="bogus")


@pytest.mark.asyncio
async def test_get_related_excludes_self_and_ranks_neighbours(engine):
    anchor = await engine.store(content="JWT auth token expiry handling", memory_type="episodic")
    await engine.store(content="JWT auth token expiry handling detail", memory_type="episodic")
    await engine.store(content="completely unrelated coffee machine", memory_type="episodic")

    related = await engine.get_related(anchor.id, top_k=5)
    ids = [m.id for m, _ in related]
    assert anchor.id not in ids  # never relate a memory to itself
    assert len(ids) >= 1
    # similarities are sorted descending
    sims = [s for _, s in related]
    assert sims == sorted(sims, reverse=True)


@pytest.mark.asyncio
async def test_get_related_respects_project_scope(engine):
    a = await engine.store(content="shared topic text", project="p1", memory_type="episodic")
    await engine.store(content="shared topic text", project="p2", memory_type="episodic")
    related = await engine.get_related(a.id, top_k=5, project_scoped=True)
    assert all(m.project == "p1" for m, _ in related)


@pytest.mark.asyncio
async def test_summarize_session_extractive_fallback(engine):
    # No OPENAI_API_KEY in tests → deterministic extractive summary.
    sess = await engine.create_session(name="work")
    await engine.store(content="decided to use Zustand for state", session_id=sess.id, memory_type="episodic")
    await engine.store(content="deploy branch is prod", session_id=sess.id, memory_type="episodic")

    summary = await engine.summarize_session(sess.id)
    assert summary is not None
    assert "session-summary" in summary.tags
    assert summary.source == "auto-summary"
    assert "Zustand" in summary.content or "prod" in summary.content


@pytest.mark.asyncio
async def test_summarize_empty_session_returns_none(engine):
    sess = await engine.create_session(name="empty")
    assert await engine.summarize_session(sess.id) is None


@pytest.mark.asyncio
async def test_recall_benchmark_harness_runs():
    from server.core.benchmark import run_benchmark

    metrics = await run_benchmark(embedder_mode="hash", top_k=5)
    for key in ("hit@1", "hit@3", "hit@5", "mrr", "queries"):
        assert key in metrics
    assert 0.0 <= metrics["mrr"] <= 1.0
    assert metrics["queries"] > 0


@pytest.mark.asyncio
async def test_import_reports_skipped(engine):
    events = []
    engine.subscribe(lambda ev, payload: events.append((ev, payload)))
    # one valid record, one malformed (missing required content)
    good = (await engine.store(content="valid one", memory_type="episodic")).model_dump()
    imported = await engine.import_memories([good, {"not": "a memory"}])
    assert imported == 1
    imported_events = [p for ev, p in events if ev == "imported"]
    assert imported_events and imported_events[-1]["skipped"] == 1
