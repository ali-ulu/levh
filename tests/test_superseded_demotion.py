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
REAL_FACT = "The production deploy branch is prod, not main"
QUESTION = "which branch do we deploy to production from"


@pytest_asyncio.fixture
async def engine(monkeypatch):
    # Retirement (#335) is opt-in; these tests exercise it, so turn it on for
    # the duration of each test rather than inheriting the off-by-default flag.
    monkeypatch.setenv("LEVH_SUPERSESSION", "1")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


@pytest_asyncio.fixture
async def default_engine(monkeypatch):
    """An engine with the supersession flag left at its default (off)."""
    monkeypatch.delenv("LEVH_SUPERSESSION", raising=False)
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
async def test_superseded_memory_is_not_current_and_its_replacement_is(engine):
    """The #335 contract: a superseded fact is *retired*, not merely demoted.

    The old test pinned "the replaced fact still appears, ranked below its
    replacement". That is exactly the behaviour #335 removes: a fact that is
    no longer believed must not surface as current at all, however it would
    rank. What must survive is the replacement being current and the old fact
    staying reachable through an audit read.
    """
    old, new = await _supersede(engine)

    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    ranked = [m.id for m in result.memories]
    assert new.id in ranked
    assert old.id not in ranked


@pytest.mark.asyncio
async def test_retired_fact_stays_reachable_and_auditable_via_as_of(engine):
    """Retirement is belief revision, not deletion: the old row is still in
    the store, still carries its supersession pointer, and a point-in-time
    read that asks about before the replacement still returns it."""
    old, new = await _supersede(engine)

    stored = await engine.get_memory(old.id)
    assert stored.valid_to is not None
    assert stored.superseded_by == new.id
    assert stored.valid_from is not None

    # An as_of read taken *before* the supersession sees the old fact.
    before = await engine.recall(
        QUESTION, top_k=5, reinforce=False, as_of=stored.valid_from
    )
    assert old.id in [m.id for m in before.memories]

    # An audit read that opts the retired rows back in also sees it, and the
    # explicit penalty still demotes it below its replacement there.
    audit = await engine.recall(
        QUESTION, top_k=5, reinforce=False, include_superseded=True
    )
    ranked = [m.id for m in audit.memories]
    assert ranked.index(new.id) < ranked.index(old.id)
    scores = dict(zip(ranked, audit.scores))
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

    # A retired row is out of the current ranking, so the penalty is only
    # observable on an audit read that opts it back in (#335).
    result = await engine.recall(
        QUESTION, top_k=5, reinforce=False, explain=True, include_superseded=True
    )
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
async def test_a_differently_scoped_fact_does_not_supersede(engine):
    """A near-miss that shares the topic frame but answers a different question
    is not a replacement. Scoring overlap one-way let this mark the real fact
    superseded: the near-miss contains every word of the older memory even
    though the older memory does not contain every word of the near-miss."""
    real = await engine.store(content=REAL_FACT, memory_type="episodic")
    await engine.store(
        content="The staging deploy branch is stage, not prod", memory_type="episodic"
    )
    assert "superseded_by" not in (await engine.get_memory(real.id)).metadata


@pytest.mark.asyncio
async def test_a_topic_miss_does_not_outrank_the_real_fact(engine):
    """With the real fact no longer wrongly demoted, the near-miss cannot score
    strictly above it. (Equal scores are a legitimate tie; the point is that the
    near-miss gains no supersession advantage.)"""
    real = await engine.store(content=REAL_FACT, memory_type="episodic")
    await engine.store(
        content="The staging deploy branch is stage, not prod", memory_type="episodic"
    )
    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    ranked = [m.id for m in result.memories]
    scores = dict(zip(ranked, result.scores))
    miss_id = next(i for i in ranked if i != real.id)
    assert scores[real.id] >= scores[miss_id] - 1e-9


@pytest.mark.asyncio
async def test_a_genuine_replacement_still_supersedes_after_the_tightening(engine):
    """The symmetric floor must not stop a real one-value edit from being
    recorded — over-tightening would defeat the whole feature."""
    old, new = await _supersede(engine)
    assert (await engine.get_memory(old.id)).metadata["superseded_by"] == new.id


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
    # While retired, the old fact is out of a current read entirely (#335).
    before = await engine.recall(QUESTION, top_k=5, reinforce=False)
    assert old.id not in [m.id for m in before.memories]

    assert await engine.forget(new.id) is True

    # Deleting the replacement reopens the predecessor's validity window, so
    # it becomes current again and is recallable without any penalty.
    reloaded = await engine.get_memory(old.id)
    assert reloaded.valid_to is None
    assert reloaded.superseded_by is None
    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    assert old.id in [m.id for m in result.memories]


@pytest.mark.asyncio
async def test_retirement_is_off_by_default(default_engine):
    """Without the flag, a near-identical write still weakens but never
    retires: the old fact keeps a NULL valid_to and stays in the current
    read. This is what keeps dedupe/consolidation and every existing caller
    unaffected by #335 until an operator opts in."""
    old = await default_engine.store(content=OLD, memory_type="episodic")
    await default_engine.store(content=NEW, memory_type="episodic")

    stored = await default_engine.get_memory(old.id)
    assert stored.valid_to is None
    assert stored.superseded_by is None
    # Weakening still happened — the interference metadata pointer is set.
    assert stored.metadata["superseded_by"]

    result = await default_engine.recall(QUESTION, top_k=5, reinforce=False)
    assert old.id in [m.id for m in result.memories]


@pytest.mark.asyncio
async def test_a_retired_fact_is_hidden_from_the_list_surface(engine):
    """The list surface shares the validity filter, so a retired fact does not
    leak back in through ``GET /api/memories`` while it is hidden from recall."""
    old, _new = await _supersede(engine)

    listed = await engine.list_memories(limit=50)
    assert old.id not in [m.id for m in listed]

    # ...and a point-in-time list read still finds it.
    as_of = (await engine.get_memory(old.id)).valid_from
    historical = await engine.list_memories(limit=50, as_of=as_of)
    assert old.id in [m.id for m in historical]


@pytest.mark.asyncio
async def test_as_of_read_does_not_reinforce(engine):
    """A point-in-time question must not strengthen a current belief."""
    old, _new = await _supersede(engine)
    as_of = (await engine.get_memory(old.id)).valid_from
    before = await engine.get_memory(old.id)

    await engine.recall(QUESTION, top_k=5, as_of=as_of)

    after = await engine.get_memory(old.id)
    assert after.frequency == before.frequency
    assert after.recall_count == before.recall_count


@pytest.mark.asyncio
async def test_valid_from_is_set_on_write(engine):
    """A fresh write opens its validity window at creation time (#335)."""
    mem = await engine.store(content="A brand new fact", memory_type="episodic")
    assert mem.valid_from is not None
    assert mem.valid_to is None
    assert mem.superseded_by is None
    stored = await engine.get_memory(mem.id)
    assert stored.valid_from == mem.created_at

