"""Word-overlap relevance — the model-free recall signal.

The ``hash`` embedder is positional: its cosine measures character positions,
not meaning, so a memory written as "The production deploy branch is prod" is
barely ranked for the query "which branch do we deploy from" even though every
content word matches. This module supplies the missing signal for that one
mode, with no model, no dependency and no network.

It is deliberately not a general-purpose semantic score. With a real embedder
(``local``/``ollama``/``openai``) the cosine is the better ranking and this is
never consulted — see ``MemoryRecallMixin._recall`` and
``MemoryEngine.evaluate_admission``, both gated on ``Embedder.is_semantic``.

The matching is language-agnostic on purpose. Turkish, where every retrieval
term arrives inflected ("migrasyonu" for "migrasyon"), is a first-class store
language (#78), so a term also matches a longer word it is a clean prefix of —
that is how the inflection is absorbed without a stemmer, a dictionary or a
per-language rule table.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from functools import lru_cache

_WORD = re.compile(r"\w+", flags=re.UNICODE)

# Function words carry no retrieval signal and are the main source of spurious
# overlap between otherwise unrelated notes ("the", "is", "of", ...). English
# and Turkish share the list: this module must not need to detect the language.
_STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of to in on at by for with
    from into over under is are was were be been being do does did has have
    had will would can could should may might must not no this that these
    those it its as we you they he she i our your their there here what which
    who how why where all any some each more most other than so just
    bir ve ile bu su o da de ki mi mu mü için gibi ama fakat veya ancak çok
    daha en ne nasıl neden nerede nereye hangi kim bunu şunu onu bunlar şunlar
    olarak olan olur oldu var yok ise değil her kez zaman üzere göre kadar
    sonra önce çünkü yani eğer
    """.split()
)

# Words shorter than this never take part in prefix (stem) matching: at that
# length a shared opening is as likely coincidence ("siz"/"size") as grammar.
_MIN_STEM_LENGTH = 4
# How far a shared opening may fall short of the shorter word. Turkish suffixes
# diverge the two forms ("migrasyonu" vs "migrasyonlar" share "migrasyon");
# English "-ing"/plural is +3. Beyond this the words stop sharing a stem.
_MAX_SUFFIX_LENGTH = 4


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for a, b in zip(left, right):
        if a != b:
            break
        length += 1
    return length


def _stem_matches(term: str, word: str) -> bool:
    """Whether ``word`` is the same retrieval term as ``term``.

    Exact match, or the two share a long enough opening to be one stem in
    different inflections — ``deploy``/``deploying``, ``migrasyonu``/
    ``migrasyonlar``. ``config`` does not match ``confirm`` (three characters
    is a coincidence, not a stem). Short stems over-cover on purpose: recall
    scores query coverage, so a false positive costs a little precision while a
    false negative hides a memory the user asked for.
    """
    if term == word:
        return True
    prefix = _common_prefix_length(term, word)
    if prefix < _MIN_STEM_LENGTH:
        return False
    return prefix >= min(len(term), len(word)) - _MAX_SUFFIX_LENGTH


# The token cache is what keeps model-free ingestion linear instead of
# quadratic. ``similarity`` tokenizes both of its arguments on every call, and
# ``mutual_similarity`` calls it twice, so the interference scan — which
# compares a new memory against *every* stored memory — used to re-tokenize the
# same texts about four times per pair: a 4505-commit import is ~9 million
# regex scans over ~4505 distinct texts (~4 * N per new row, N grows to 4505).
# Tokenization is a pure function of the text: ``_WORD`` and ``_STOPWORDS`` are
# module constants that nothing mutates, and ``str.lower`` is locale
# independent. Caching it therefore changes no result, only how often it is
# computed.
#
# Size: the working set is one text per memory of the store being written to,
# re-read on every admit. 8192 covers the largest import observed so far (4505
# rows) with headroom, while bounding retention to roughly 30 MB of token sets
# in the worst case. An eviction only ever costs a re-tokenization; it can
# never change a score.
#
# Retention note: an entry holds its source text as the cache key, so the last
# 8192 distinct tokenized texts stay in process memory until evicted or the
# process exits. Every caller that tokenizes stored content or a query passes
# already-redacted text (``admission.evaluate`` and ``recall`` redact first);
# ``action_gate.action_terms`` is the one path that tokenizes a raw proposed
# action, which may carry a secret it is there to detect. That is a bounded
# in-memory retention of a request payload the process already holds, not a new
# durable copy, but it is a deliberate trade rather than an accident.
_TERMS_CACHE_SIZE = 8192


@lru_cache(maxsize=_TERMS_CACHE_SIZE)
def _content_terms(text: str) -> frozenset[str]:
    """The immutable token set of ``text``, computed once per distinct text."""
    return frozenset(
        word
        for word in _WORD.findall((text or "").lower())
        if len(word) >= 3 and word not in _STOPWORDS
    )


def terms(text: str) -> set[str]:
    """Content words of ``text``: lowercased, length >= 3, stopwords removed.

    Returns a fresh set per call. The tokenized form itself is cached (see
    :func:`_content_terms`), but handing the cached object out would let any
    caller that mutates the result corrupt every later lookup, so the mutable
    copy stays the caller's.
    """
    return set(_content_terms(text))


def similarity(query: str, content: str) -> float:
    """Share of the query's content words present in ``content``, in [0, 1].

    Query coverage, not Jaccard: recall asks "does this memory answer the
    words I used", so a short memory that contains every query term should
    score 1.0 even though the content has words the query does not. A query
    term counts as present when the content contains the term itself or an
    inflected form of it (see ``_stem_matches``).

    Synonym expansion is *not* applied here — this is the raw surface-word
    signal. Recall uses :func:`similarity_expanded` so a memory can still score
    on the words it shares even when the query's other words were translated.
    """
    return similarity_expanded(query, content)


def similarity_expanded(
    query: str, content: str, expansions: Mapping[str, frozenset[str]] | None = None
) -> float:
    """Query coverage, where a query term also matches its synonyms.

    ``expansions`` maps a query term to the terms the store considers
    equivalent (see :mod:`server.core.synonyms`). A query term is covered when
    the content contains the term, an inflection of it, *or* any of its
    equivalents. Counting it once keeps this a coverage ratio in [0, 1], so
    expansions broaden what matches without inflating the score of a memory
    that merely shares a thesaurus entry with everything else.
    """
    query_terms = _content_terms(query)
    if not query_terms:
        return 0.0
    content_terms = _content_terms(content)
    if not content_terms:
        return 0.0
    matched = sum(
        1
        for term in query_terms
        if _term_matches(term, content_terms, expansions)
    )
    return matched / len(query_terms)


def _term_matches(
    term: str, content_terms: Set[str], expansions: Mapping[str, frozenset[str]] | None
) -> bool:
    if any(_stem_matches(term, word) for word in content_terms):
        return True
    if not expansions:
        return False
    equivalents = expansions.get(term)
    if not equivalents:
        return False
    return any(
        _stem_matches(equivalent, word)
        for equivalent in equivalents
        for word in content_terms
    )


def mutual_similarity(a: str, b: str) -> float:
    """Symmetric overlap of two texts' content words, in [0, 1].

    The minimum of the two directional coverages, so the score is bounded by
    the *longer* text's share of shared terms: two memories score high only
    when each contains most of the other. Recall's :func:`similarity` is
    deliberately one-directional (does this answer my query?); supersession
    needs the other question — are these two memories about the same thing? —
    and a topical frame that only one direction covers must not count as a
    replacement (see ``server.core.engine.write``).
    """
    return min(similarity(a, b), similarity(b, a))


def expand_terms(
    query: str, expansions: Mapping[str, frozenset[str]] | None
) -> set[str]:
    """Query terms plus the equivalents of those that have any.

    The candidate-retrieval half of expansion: the flat set is what the
    keyword scan and the FTS query look for. Membership is not attribution, so
    flattening is right here — unlike the score, which needs to know *which*
    query term an equivalent answers for.
    """
    query_terms = _content_terms(query)
    if not expansions:
        return set(query_terms)
    expanded = set(query_terms)
    for term in query_terms:
        expanded.update(expansions.get(term, frozenset()))
    return expanded
