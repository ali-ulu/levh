"""The model-free path: recall and the admission gate without an embedding model.

LEVH's purpose is to work with no model at all — ``EMBEDDER_MODE=hash`` (and the
``auto``/``local`` fallback to it) is a first-class mode, not a degraded one.
The hash vectors are positional, so cosine is not a relevance or duplicate
signal; these tests pin the word-overlap path that stands in for it, on the
benchmark shapes and on the store/recall/admission round trip.

Offline and deterministic — no model, no network.
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import lexical
from server.core.embedder import Embedder
from server.core.memory_engine import MemoryEngine

FACT = "The production deploy branch is prod, not main"
QUESTION = "which branch do we deploy to production from"
UNRELATED = "The office coffee machine is on the third floor"


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
    """A stand-in that reports semantic vectors, so the cosine branches run.

    Lets the semantic halves of recall/admission/interference be exercised
    offline: a real embedder needs a model or the network, which the test suite
    must not require.
    """

    is_semantic = True
    dimension = 384

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


@pytest_asyncio.fixture
async def semantic_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = MemoryEngine(db_path=path, embedder_mode="hash", short_term_max=50)
    await eng.initialize()
    eng._embedder = _SemanticStub(
        {"alpha": [1.0, 0.0], "gamma": [0.99, 0.14107], "delta": [0.95, 0.31225]}
    )
    yield eng
    await eng.shutdown()
    if os.path.exists(path):
        os.unlink(path)


# ── lexical.similarity ──────────────────────────────────────────────


def test_lexical_similarity_is_query_coverage():
    assert lexical.similarity(QUESTION, FACT) == 1.0
    assert lexical.similarity(QUESTION, UNRELATED) < 0.34


def test_lexical_similarity_ignores_stopwords_and_short_words():
    # "the", "is", "of" carry no signal and must not create overlap.
    assert lexical.similarity("the of is a an", UNRELATED) == 0.0
    assert lexical.similarity("", FACT) == 0.0


def test_is_semantic_only_false_for_hash():
    assert Embedder(mode="hash").is_semantic is False
    assert Embedder(mode="openai").is_semantic is True


# ── lexical.mutual_similarity ───────────────────────────────────────


def test_mutual_similarity_is_bounded_by_the_longer_text():
    short = "The deploy branch is prod"
    long = "The production deploy branch is prod, not main"
    # One direction covers fully, the other does not; the symmetric score is
    # the smaller, so the longer text's extra words are not free.
    assert lexical.mutual_similarity(short, long) == lexical.similarity(long, short)
    assert lexical.mutual_similarity(short, long) < 1.0


def test_mutual_similarity_separates_a_replacement_from_a_topic_frame():
    """The write path's whole calibration in one assertion: a one-value edit is
    a replacement, a differently scoped fact that merely shares the frame is
    not."""
    replacement = lexical.mutual_similarity(
        "The production deploy branch is main",
        "The production deploy branch is prod",
    )
    topic_frame = lexical.mutual_similarity(
        "The production deploy branch is prod, not main",
        "The staging deploy branch is stage, not prod",
    )
    assert topic_frame < 0.65 <= replacement


def test_mutual_similarity_is_symmetric():
    a, b = "Postgres pool is 20", "The Postgres connection pool max size is 20"
    assert lexical.mutual_similarity(a, b) == lexical.mutual_similarity(b, a)


# ── lexical.similarity across inflections and languages ─────────────


def test_inflected_query_matches_its_stored_form():
    # The stored term is the stem; the query arrives inflected.
    assert lexical.similarity(
        "migrasyon nasıl çalışır", "Veritabanı migrasyonu her gece çalışır"
    ) == 1.0
    assert lexical.similarity("deploy nerede", "Üretim deployu prod dalında") == 1.0


def test_two_inflections_of_one_stem_match_each_other():
    # Turkish plural and possessive suffixes diverge the two surface forms; the
    # shared opening is what the match keys on.
    assert lexical.similarity("migrasyonlar", "migrasyonu saat ikide") == 1.0
    assert lexical.similarity("deploylar", "deployu çalıştır") == 1.0


def test_stem_matching_trades_precision_for_recall():
    # A short shared opening over-matches on purpose: "config"/"confirm" is
    # treated as one stem. Recall scores query coverage, so this costs a little
    # precision in exchange for never hiding an inflected memory.
    assert lexical.similarity("config", "confirm the order") == 1.0
    # Below the four-character floor the opening is a coincidence, not a stem.
    assert lexical.similarity("code style", "coding standards") == 0.0


def test_turkish_stopwords_carry_no_signal():
    # "bir", "ve", "ile", "bu" are the Turkish function words; alone they must
    # not manufacture overlap between unrelated notes.
    assert lexical.similarity("bir ve ile bu", UNRELATED) == 0.0


# ── recall ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_finds_the_matching_memory_without_a_model(engine):
    stored = await engine.store(content=FACT, memory_type="episodic")
    await engine.store(content=UNRELATED, memory_type="episodic")

    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    assert result.memories[0].id == stored.id


@pytest.mark.asyncio
async def test_recall_ranks_keyword_match_above_positional_noise(engine):
    # The distractor shares a prefix with the query, so the positional hash
    # scores it highly, but shares none of its content words. The real memory
    # shares all of them; word overlap must win.
    await engine.store(
        content="whichever brand do we display to produce formally",
        memory_type="episodic",
    )
    real = await engine.store(content=FACT, memory_type="episodic")
    result = await engine.recall(QUESTION, top_k=5, reinforce=False)
    assert result.memories[0].id == real.id


@pytest.mark.asyncio
async def test_recall_still_scoped_by_project(engine):
    await engine.store(content=FACT, project="a", memory_type="episodic")
    other = await engine.store(content=FACT, project="b", memory_type="episodic")
    result = await engine.recall(QUESTION, project="b", top_k=5, reinforce=False)
    assert [m.id for m in result.memories] == [other.id]


@pytest.mark.asyncio
async def test_recall_finds_a_turkish_memory_from_an_inflected_query(engine):
    stored = await engine.store(
        content="Veritabanı migrasyonu her gece saat ikide çalışır",
        memory_type="episodic",
    )
    await engine.store(
        content="Ofis kahve makinesi üçüncü katta duruyor", memory_type="episodic"
    )
    result = await engine.recall("migrasyonlar ne zaman çalışıyor", top_k=5, reinforce=False)
    assert result.memories[0].id == stored.id


# ── admission gate ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_distinct_facts_are_all_stored_without_a_model(engine):
    facts = [
        "The production deploy branch is prod, not main",
        "The production database password rotates every 30 days",
        "Customer Acme prefers invoices in euros, not dollars",
        "The mobile app releases every second Tuesday",
        "Our support SLA is a four-hour first response",
    ]
    for fact in facts:
        result = await engine.admit_memory(fact, memory_type="episodic")
        assert result["stored"] is True, result["decision"]["reasons"]
    assert await engine.episodic.count() == len(facts)
    assert await engine.db.count_held_memories() == 0
    for fact in facts:
        result = await engine.recall(fact, top_k=10, reinforce=False)
        assert fact in {m.content for m in result.memories}


@pytest.mark.asyncio
async def test_exact_duplicate_is_still_rejected_without_a_model(engine):
    first = await engine.admit_memory(FACT, memory_type="episodic")
    assert first["stored"] is True
    second = await engine.admit_memory(FACT, memory_type="episodic")
    assert second["stored"] is False
    assert second["decision"]["reason_codes"] == ["duplicate_exact"]


# ── interference ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_superseding_memory_weakens_the_older_one_without_a_model(engine):
    old = await engine.store(content="The deploy branch is main", memory_type="episodic")
    initial = old.stability_hours
    await engine.store(content="The deploy branch is prod", memory_type="episodic")
    assert (await engine.get_memory(old.id)).stability_hours < initial


@pytest.mark.asyncio
async def test_unrelated_memory_does_not_weaken_others_without_a_model(engine):
    old = await engine.store(
        content="The deploy branch is main", memory_type="episodic"
    )
    initial = old.stability_hours
    await engine.store(
        content="Vacation requests go through the HR portal", memory_type="episodic"
    )
    assert (await engine.get_memory(old.id)).stability_hours == initial


# ── the semantic path is unchanged ──────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_gate_still_uses_the_near_duplicate_band(semantic_engine):
    stored = await semantic_engine.store(
        content="alpha fact", memory_type="episodic"
    )
    # Same stub vector as `alpha` → cosine 1.0, the gate must hold it.
    result = await semantic_engine.admit_memory("gamma fact", memory_type="episodic")
    assert result["stored"] is False
    assert result["decision"]["reason_codes"] == ["duplicate_exact"]
    assert await semantic_engine.get_memory(stored.id) is not None

    # A 0.98 cosine lands in the review band, not the reject band.
    review = await semantic_engine.admit_memory("delta fact", memory_type="episodic")
    assert review["stored"] is False
    assert review["decision"]["reason_codes"] == ["duplicate_near"]


@pytest.mark.asyncio
async def test_semantic_recall_still_ranks_on_cosine(semantic_engine):
    want = await semantic_engine.store(
        content="alpha tuned deploy branch", memory_type="episodic"
    )
    # No lexical overlap with the query, but the stub gives it the same vector as
    # the query, so cosine must still rank it first under a semantic embedder.
    await semantic_engine.store(content="zzz", memory_type="episodic")
    result = await semantic_engine.recall("alpha branch", top_k=5, reinforce=False)
    assert result.memories[0].id == want.id


@pytest.mark.asyncio
async def test_semantic_interference_still_uses_the_cosine_threshold(semantic_engine):
    old = await semantic_engine.store(
        content="alpha old superseded fact", memory_type="episodic"
    )
    initial = old.stability_hours
    # Same stub vector as `alpha` → cosine 1.0, above INTERFERENCE_THRESHOLD.
    await semantic_engine.store(
        content="alpha new superseding fact", memory_type="episodic"
    )
    assert (await semantic_engine.get_memory(old.id)).stability_hours < initial

