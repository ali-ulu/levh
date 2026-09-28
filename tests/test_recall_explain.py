"""``recall(explain=True)``: the score split that says *why* a memory ranked.

The H(x,ψ) total is a weighted sum of four penalties. A memory that matched the
query well but decayed, and one that is perfectly fresh but barely matched,
produce similar totals from opposite causes — and they call for opposite
responses (re-read/reinforce vs. rephrase the query). The breakdown is what
tells the two apart, so these tests pin the fields that carry that signal on
both ranking paths.

Offline and deterministic — ``EMBEDDER_MODE=hash`` plus a semantic stub.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core.memory_engine import MemoryEngine

FACT = "The production deploy branch is prod, not main"
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


class _SemanticStub:
    """Reports semantic vectors so the cosine ranking path runs offline."""

    is_semantic = True
    dimension = 2

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    async def embed(self, text: str) -> list[float]:
        for marker, vector in self._table.items():
            if marker in text:
                return list(vector)
        return [0.0, 1.0]

    def identity(self) -> dict:
        return {"mode": "stub"}

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_explain_is_off_by_default(engine):
    await engine.store(content=FACT, memory_type="episodic")

    result = await engine.recall(QUESTION, top_k=5, reinforce=False)

    assert result.breakdowns == []


@pytest.mark.asyncio
async def test_breakdown_fields_are_populated_and_sum_to_the_score(engine):
    await engine.store(content=FACT, memory_type="episodic")

    result = await engine.recall(QUESTION, top_k=5, reinforce=False, explain=True)

    assert len(result.breakdowns) == len(result.memories) == 1
    bd = result.breakdowns[0]
    assert bd.memory_id == result.memories[0].id
    assert bd.total_hscore == result.scores[0]
    # The four components are the weighted penalties; they add up to the total
    # by construction, and a drift here means the two code paths diverged.
    components = (
        bd.alpha_component + bd.beta_component + bd.gamma_component + bd.delta_component
    )
    assert abs(components - bd.total_hscore) < 1e-6
    assert bd.importance == result.memories[0].importance
    assert bd.frequency == result.memories[0].frequency
    assert 0.0 <= bd.cosine <= 1.0
    assert 0.0 <= bd.decay_factor <= 1.0


@pytest.mark.asyncio
async def test_model_free_breakdown_names_the_lexical_signal(engine):
    await engine.store(content=FACT, memory_type="episodic")

    result = await engine.recall(QUESTION, top_k=5, reinforce=False, explain=True)

    bd = result.breakdowns[0]
    # Hash embeddings are positional, so the ranking cannot have used the
    # cosine — the breakdown must not claim it did.
    assert bd.similarity_source == "lexical"
    assert bd.similarity == 1.0  # every query term covered by the fact


@pytest.mark.asyncio
async def test_semantic_breakdown_names_the_cosine_signal(engine):
    engine._embedder = _SemanticStub({"alpha": [1.0, 0.0], "deploy": [0.9, 0.43589]})
    await engine.store(content="alpha memory about deploys", memory_type="episodic")

    result = await engine.recall("alpha deploy", top_k=5, reinforce=False, explain=True)

    assert result.breakdowns
    bd = result.breakdowns[0]
    assert bd.similarity_source == "cosine"
    assert bd.similarity == bd.cosine


@pytest.mark.asyncio
async def test_breakdown_points_at_the_stale_component_when_decay_is_the_cause(engine):
    """The split is only useful if it attributes the penalty to the right term.

    Two identical-content memories are ranked; the one whose clock is older must
    carry the larger decay penalty, even though their match penalty is equal.
    """
    fresh = await engine.store(content=FACT, memory_type="episodic")
    stale = await engine.store(content=FACT + " (archived copy)", memory_type="episodic")

    # Backdate the second memory's access clock far past its one-week half-life.
    old = "2020-01-01T00:00:00+00:00"
    await engine.db.update_memory(stale.id, {"accessed_at": old})
    stale.accessed_at = old

    result = await engine.recall(QUESTION, top_k=5, reinforce=False, explain=True)

    by_id = {bd.memory_id: bd for bd in result.breakdowns}
    assert fresh.id in by_id and stale.id in by_id
    assert by_id[stale.id].beta_component > by_id[fresh.id].beta_component


@pytest.mark.asyncio
async def test_breakdown_marks_keyword_only_candidates(engine):
    """A memory the vector path could not serve but the terms pulled is labelled.

    This is the stale-vector case: after an embedder switch the stored vector's
    dimension no longer matches the query, so ``vector_store.search`` skips it.
    The lexical pass still finds it by content, and the breakdown must say the
    candidate came from there — that label is what distinguishes "recall is
    working and this was found another way" from "recall never saw it".
    """
    target = await engine.store(
        content="migration notes for the database rollout", memory_type="episodic"
    )
    stale_vector = [0.0] * (engine.vector_store.dimension + 1)
    target.embedding = stale_vector
    engine.vector_store.add(target)

    result = await engine.recall("rollout", top_k=5, reinforce=False, explain=True)

    by_id = {bd.memory_id: bd for bd in result.breakdowns}
    assert target.id in by_id
    assert by_id[target.id].candidate_source == "keyword"
    # The stale vector was skipped entirely, so no cosine was available.
    assert by_id[target.id].cosine == 0.0


@pytest.mark.asyncio
async def test_breakdown_candidates_from_the_vector_store_are_labelled_vector(engine):
    await engine.store(content=FACT, memory_type="episodic")

    result = await engine.recall(QUESTION, top_k=5, reinforce=False, explain=True)

    assert result.breakdowns[0].candidate_source == "vector"
