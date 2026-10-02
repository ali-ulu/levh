"""Token estimation for context budgeting.

The context window needs to know how much room a memory will take *before* it
commits to including it, and it must do so without a tokenizer: this module is
stdlib-only, because the storage and recall layers must not grow a model or
tokenizer dependency (``docs/ARCHITECTURE.md`` §1 — the engine is offline and
provider-independent).

``estimate_tokens`` replaces the older ``len(text) // 4`` rule of thumb, which
was wrong in both directions on real memory text. Code, identifiers and
punctuation tokenize far denser than prose (roughly one token per 2–3
characters), while repetitive prose tokenizes looser. Counting word runs and
punctuation separately, and weighting them by their measured density, tracks
actual tokenizer output closely enough for budgeting while staying deterministic
and free of any dependency.

The estimate is an approximation by construction. It is used to *rank and pack*
candidates against a soft budget, never to assert an exact count, so a few
percent of drift changes nothing observable.
"""

from __future__ import annotations

import re

# Word runs and single punctuation/symbol characters. `\w` is Unicode-aware so a
# Turkish or CJK memory counts its characters rather than being undercounted.
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Measured densities. English prose sits near 4 characters per token; source code
# and identifiers near 2.5; punctuation usually rides along with the token that
# precedes it, so it is weighted well below a word character.
_CHARS_PER_TOKEN_WORD = 4.0
_PUNCT_TOKENS = 0.5

# Per-memory ceiling. A very long memory would otherwise consume a whole window
# and crowd out every other candidate; callers that want the full text read the
# memory directly instead of through the context window.
MAX_MEMORY_TOKENS = 2000


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text``.

    Returns 0 when there is nothing to tokenize — empty *or* whitespace-only,
    since whitespace carries no tokens on its own. Never raises: a non-string
    input is coerced, because this is called on metadata fields that are
    untyped at the storage boundary.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)

    words = _WORD_RE.findall(text)
    word_chars = sum(len(match) for match in words)
    punct_count = len(_PUNCT_RE.findall(text))
    if not words and not punct_count:
        return 0

    tokens = word_chars / _CHARS_PER_TOKEN_WORD + punct_count * _PUNCT_TOKENS
    # Whitespace and separators between words still cost something; the word
    # term alone undercounts a text that is mostly short words.
    tokens += len(words) * 0.05
    return max(1, round(tokens))


def memory_tokens(content: str) -> int:
    """Tokens one memory's content costs in a context window, capped.

    The cap is what keeps a single long memory from monopolising the budget;
    ``MAX_MEMORY_TOKENS`` is deliberately far below a normal window so the cap
    only ever binds on an outlier.
    """
    return min(estimate_tokens(content), MAX_MEMORY_TOKENS)
