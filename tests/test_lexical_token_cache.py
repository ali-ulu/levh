"""The token cache behind model-free interference — and the proof it is invisible.

``server.core.lexical`` caches the tokenization of each distinct text, because
the model-free interference scan tokenized the same stored texts again and
again: ``mutual_similarity`` calls ``similarity`` in both directions and each
direction tokenizes both arguments, so admitting one memory into a store of N
rows cost ~4N regex scans and a 4505-row import cost ~9 million of them.

The cache is only safe because it is a pure memoization of a pure function, and
these tests hold it to that:

1. the tokens, and every score derived from them, are identical to an
   independent reference implementation — with the cache cold and warm;
2. a caller that mutates the set ``terms()`` returns cannot poison the cache;
3. the cache is bounded and actually short-circuits the regex work;
4. the write path still records the same supersession decisions, including the
   retirement flag, so no near-identical pair gains or loses a replacement.

Offline and deterministic: ``EMBEDDER_MODE=hash``, no model, no network.
"""

from __future__ import annotations

import os
import re

import pytest
import pytest_asyncio

os.environ["EMBEDDER_MODE"] = "hash"

from server.core import lexical
from server.core.lexical import mutual_similarity
from server.core.memory_engine import MemoryEngine

_REFERENCE_WORD = re.compile(r"\w+", flags=re.UNICODE)


def _reference_terms(text: str) -> set[str]:
    """The tokenization rule, written out again — not imported from the code.

    A cache whose key or body drifted from the rule would still be
    self-consistent, so equality is checked against this copy rather than
    against the function being tested.
    """
    return {
        word
        for word in _REFERENCE_WORD.findall((text or "").lower())
        if len(word) >= 3 and word not in lexical._STOPWORDS
    }


def _reference_similarity(query: str, content: str) -> float:
    query_terms = _reference_terms(query)
    if not query_terms:
        return 0.0
    content_terms = _reference_terms(content)
    if not content_terms:
        return 0.0
    matched = sum(
        1
        for term in query_terms
        if any(lexical._stem_matches(term, word) for word in content_terms)
    )
    return matched / len(query_terms)


TEXTS = (
    "",
    "the of is a an",
    "The production deploy branch is prod, not main",
    "Veritabanı migrasyonu her gece saat ikide çalışır",
    "CONFIG=load_test_123",
    "punctuation... only!!! ???",
    "multi\nline\twhitespace   text",
    "config",
    "confirm the order",
)

PAIRS = [(a, b) for a in TEXTS for b in TEXTS]


@pytest.fixture(autouse=True)
def _cold_cache():
    """Every test starts and ends with an empty cache.

    Otherwise a key warmed by an earlier test makes a later "cold" measurement
    a cache hit, and a monkeypatched pattern could leave proxy-computed entries
    behind.
    """
    lexical._content_terms.cache_clear()
    yield
    lexical._content_terms.cache_clear()


def test_cached_tokens_equal_the_tokenization_rule():
    for text in TEXTS:
        assert lexical.terms(text) == _reference_terms(text)
        assert set(lexical._content_terms(text)) == _reference_terms(text)


def test_none_tokenizes_like_empty_text():
    # get_env-driven callers can hand in None; the rule treats it as empty.
    assert lexical.terms(None) == set()
    assert set(lexical._content_terms(None)) == set()


def test_scores_are_identical_with_a_cold_and_a_warm_cache():
    cold = [mutual_similarity(a, b) for a, b in PAIRS]
    warm = [mutual_similarity(a, b) for a, b in PAIRS]
    assert cold == warm
    assert lexical._content_terms.cache_info().hits > 0


def test_scores_match_the_definitional_minimum_directionally():
    for a, b in PAIRS:
        expected = min(_reference_similarity(a, b), _reference_similarity(b, a))
        assert mutual_similarity(a, b) == expected
        assert lexical.similarity(a, b) == _reference_similarity(a, b)


def test_mutating_a_returned_set_cannot_poison_the_cache():
    text = "The production deploy branch is prod"
    fresh = lexical.terms(text)
    fresh.add("poisoned")
    fresh.discard("production")
    assert "poisoned" not in lexical.terms(text)
    assert "production" in lexical.terms(text)


def test_the_cached_value_is_immutable():
    assert isinstance(lexical._content_terms("immutable probe"), frozenset)


def test_a_repeat_lookup_does_no_regex_work(monkeypatch):
    """The optimisation itself, pinned: the second lookup must not rescan."""
    scans = 0
    original = lexical._WORD

    class _CountingPattern:
        def findall(self, text):
            nonlocal scans
            scans += 1
            return original.findall(text)

    monkeypatch.setattr(lexical, "_WORD", _CountingPattern())
    text = "The production deploy branch is prod, not main"
    lexical.terms(text)
    assert scans == 1
    lexical.terms(text)
    assert scans == 1


def test_the_cache_is_bounded():
    info = lexical._content_terms.cache_info()
    assert info.maxsize == lexical._TERMS_CACHE_SIZE == 8192
    limit = info.maxsize or 0
    for i in range(limit + 16):
        lexical._content_terms(f"eviction probe {i}")
    assert lexical._content_terms.cache_info().currsize <= limit
    lexical._content_terms.cache_clear()
    # A bounded cache still returns the right answer after evictions.
    assert lexical.terms("after eviction") == _reference_terms("after eviction")


# ── the write path: the cache must not move a supersession decision ──────

OLD = "The production deploy branch is main"
NEW = "The production deploy branch is prod"
TOPIC_FRAME = "The staging deploy branch is stage, not prod"
UNRELATED = "Vacation requests go through the HR portal"


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = MemoryEngine(db_path=str(tmp_path / "token-cache.db"), embedder_mode="hash")
    await eng.initialize()
    try:
        yield eng
    finally:
        await eng.shutdown()


@pytest.mark.asyncio
async def test_a_near_identical_write_still_supersedes_under_the_cache(engine):
    old = await engine.store(OLD, project="p", memory_type="episodic")
    new = await engine.store(NEW, project="p", memory_type="episodic")

    stored = await engine.get_memory(old.id)
    assert stored.metadata["superseded_by"] == new.id
    # The decision follows the definitional score, not the cache's warm order.
    assert mutual_similarity(old.content, new.content) >= 0.65
    assert "superseded_by" not in (await engine.get_memory(new.id)).metadata


@pytest.mark.asyncio
async def test_a_topic_frame_still_does_not_supersede_under_the_cache(engine):
    real = await engine.store(OLD, project="p", memory_type="episodic")
    await engine.store(TOPIC_FRAME, project="p", memory_type="episodic")

    assert mutual_similarity(real.content, TOPIC_FRAME) < 0.65
    assert "superseded_by" not in (await engine.get_memory(real.id)).metadata


@pytest.mark.asyncio
async def test_an_unrelated_write_still_leaves_the_corpus_alone(engine):
    old = await engine.store(OLD, project="p", memory_type="episodic")
    stability = old.stability_hours
    await engine.store(UNRELATED, project="p", memory_type="episodic")

    stored = await engine.get_memory(old.id)
    assert stored.stability_hours == stability
    assert "superseded_by" not in stored.metadata


@pytest.mark.asyncio
async def test_the_selected_candidates_agree_with_the_definitional_scores(engine):
    """Every stored memory, scored the definitional way, must predict exactly
    which ones the write path weakens — the cache may not change the set."""
    corpus = [
        "The deploy branch is main",
        "The deploy branch is prod",
        "The deploy branch is stage",
        "The deploy branch is dev",
        TOPIC_FRAME,
        UNRELATED,
    ]
    stored = [
        await engine.store(content, project="p", memory_type="episodic")
        for content in corpus
    ]
    newest = await engine.store(
        "The deploy branch is canary", project="p", memory_type="episodic"
    )

    # Recomputed here without the engine's own candidate path, so this pins the
    # decision rather than restating it.
    expected = {
        memory.id
        for memory in stored
        if mutual_similarity(newest.content, memory.content) >= 0.65
    }
    weakened = {
        memory.id
        for memory in stored
        if (await engine.get_memory(memory.id)).metadata.get("superseded_by")
        == newest.id
    }
    assert weakened == expected
    # The test is only meaningful if the corpus has both outcomes.
    assert expected and expected != {memory.id for memory in stored}


@pytest.mark.asyncio
async def test_retirement_still_happens_under_the_cache(engine, monkeypatch):
    monkeypatch.setenv("LEVH_SUPERSESSION", "1")
    old = await engine.store(OLD, project="p", memory_type="episodic")
    new = await engine.store(NEW, project="p", memory_type="episodic")

    stored = await engine.get_memory(old.id)
    assert stored.valid_to is not None
    assert stored.superseded_by == new.id
