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


def terms(text: str) -> set[str]:
    """Content words of ``text``: lowercased, length >= 3, stopwords removed."""
    return {
        word
        for word in _WORD.findall((text or "").lower())
        if len(word) >= 3 and word not in _STOPWORDS
    }


def similarity(query: str, content: str) -> float:
    """Share of the query's content words present in ``content``, in [0, 1].

    Query coverage, not Jaccard: recall asks "does this memory answer the
    words I used", so a short memory that contains every query term should
    score 1.0 even though the content has words the query does not. A query
    term counts as present when the content contains the term itself or an
    inflected form of it (see ``_stem_matches``).
    """
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    content_terms = terms(content)
    if not content_terms:
        return 0.0
    matched = sum(
        1 for term in query_terms if any(_stem_matches(term, word) for word in content_terms)
    )
    return matched / len(query_terms)
