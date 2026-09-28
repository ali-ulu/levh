"""Ranking a superseded fact below the one that replaced it.

Writing a near-identical memory weakens the older one (retroactive
interference), but weakening only changes how fast the old memory will decay
from then on — it leaves today's ranking untouched, so the replaced fact can
tie with, or outrank, its replacement. The write path records the supersession
and recall demotes the old memory explicitly, which is what these tests pin.

Offline and deterministic: ``EMBEDDER_MODE=hash``, no model, no network.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.hscore import SUPERSEDED_PENALTY
from server.core.memory_engine import MemoryEngine

OLD = "The production deploy branch is main"
NEW = "The production deploy branch is prod"
QUESTION = "which branch do we deploy to production from"


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


async def _supersede(engine):
    """Store OLD, then NEW (near-identical) so OLD is marked superseded."""
    old = await engine.store(content=OLD, memory_type="episodic")
    new = await engine.store(content=NEW, memory_type="episodic")
    return old, new


@pytest.mark.asyncio
async def test_supersession_is_recorded_on_the_older_memory(engine):
    old, new = await _supersede(engine)

    stored = await engine.get_memory(old.id)
    assert stored.metadata["superseded_by"] == new.id
    assert "superseded_at" in stored.metadata
    # The replacement is not itself marked.
    assert "superseded_by" not in (await engine.get_memory(new.id)).metadata


@pytest.mark.asyncio
async def test_unrelated_memory_is_not_marked_superseded(engine):
    old = await engine.store(content=OLD, memory_type="episodic")
    await engine.store(
        content="Vacation requests go through the HR portal", memory_type="episodic"
    )
    assert "superseded_by" not in (await engine.get_memory(old.id)).metadata


@pytest.mark.asyncio
async def test_superseded_memory_ranks_below_its_replacement(engine):
    old, new = await _supersede(engine)

    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    ranked = [m.id for m in result.memories]
    assert ranked.index(new.id) < ranked.index(old.id)
    # The demotion is the explicit penalty, not a side effect of anything else.
    scores = dict(zip(ranked, result.scores))
    assert scores[old.id] - scores[new.id] >= SUPERSEDED_PENALTY - 1e-9


@pytest.mark.asyncio
async def test_superseded_penalty_stays_within_the_score_range(engine):
    # A memory whose four weighted terms already approach 1.0 must not exceed
    # it once the penalty is added.
    old, _new = await _supersede(engine)
    await engine.db.update_memory(old.id, {"importance": 0.0})
    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    for score in result.scores:
        assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_explain_exposes_the_penalty_and_components_still_sum(engine):
    old, _new = await _supersede(engine)

    result = await engine.recall(QUESTION, top_k=5, reinforce=False, explain=True)
    by_id = {b.memory_id: b for b in result.breakdowns}
    superseded = by_id[old.id]
    assert superseded.superseded_penalty == pytest.approx(SUPERSEDED_PENALTY)
    assert by_id[
        next(m.id for m in result.memories if m.id != old.id)
    ].superseded_penalty == 0.0

    # The four weighted components plus the penalty reconstruct the total.
    components = (
        superseded.alpha_component
        + superseded.beta_component
        + superseded.gamma_component
        + superseded.delta_component
        + superseded.superseded_penalty
    )
    assert abs(min(1.0, components) - superseded.total_hscore) < 1e-6
    assert superseded.total_hscore == pytest.approx(
        dict(zip([m.id for m in result.memories], result.scores))[old.id]
    )


@pytest.mark.asyncio
async def test_score_breakdown_route_agrees_with_the_ranked_score(engine):
    old, _new = await _supersede(engine)

    breakdown = await engine.score_breakdown(old.id, QUESTION)
    assert breakdown.superseded_penalty == pytest.approx(SUPERSEDED_PENALTY)
    assert abs(
        min(
            1.0,
            breakdown.alpha_component
            + breakdown.beta_component
            + breakdown.gamma_component
            + breakdown.delta_component
            + breakdown.superseded_penalty,
        )
        - breakdown.total_hscore
    ) < 1e-6


@pytest.mark.asyncio
async def test_pinned_memory_is_never_superseded(engine):
    old = await engine.store(content=OLD, memory_type="episodic", pinned=True)
    await engine.store(content=NEW, memory_type="episodic")
    assert "superseded_by" not in (await engine.get_memory(old.id)).metadata


@pytest.mark.asyncio
async def test_deleting_the_replacement_clears_the_pointer(engine):
    old, new = await _supersede(engine)
    assert (await engine.get_memory(old.id)).metadata.get("superseded_by") == new.id

    assert await engine.forget(new.id) is True

    reloaded = await engine.get_memory(old.id)
    assert "superseded_by" not in reloaded.metadata
    assert "superseded_at" not in reloaded.metadata


@pytest.mark.asyncio
async def test_recall_stops_demoting_after_the_replacement_is_deleted(engine):
    old, new = await _supersede(engine)
    before = await engine.recall(QUESTION, top_k=5, reinforce=False)
    demoted = dict(zip([m.id for m in before.memories], before.scores))[old.id]

    assert await engine.forget(new.id) is True

    # recall scores the vector store's cached copies; the cached predecessor
    # must not keep a pointer the delete already cleared in SQLite.
    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    rank = [m.id for m in result.memories].index(old.id)
    assert result.scores[rank] < demoted
