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
"""

from __future__ import annotations

import re

_WORD = re.compile(r"\w+", flags=re.UNICODE)

# Function words carry no retrieval signal and are the main source of spurious
# overlap between otherwise unrelated notes ("the", "is", "of", ...).
_STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of to in on at by for with
    from into over under is are was were be been being do does did has have
    had will would can could should may might must not no this that these
    those it its as we you they he she i our your their there here what which
    who how why where all any some each more most other than so just
    """.split()
)


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
    score 1.0 even though the content has words the query does not.
    """
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    return len(query_terms & terms(content)) / len(query_terms)
