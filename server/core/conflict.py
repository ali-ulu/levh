"""Deterministic conflict-candidate detection — flag memories that MIGHT
disagree, for a human to review. Offline, no LLM, no truth claim.

This never decides that two memories contradict each other. It only surfaces
*candidates*: two memories that (a) share an entity and (b) show an opposing
surface pattern (an antonym, a negation, or the same attribute with different
values). A human then reviews. Signal, not verdict.

Everything here is a pure function of the two memories' text; the engine adds
the entity-overlap and trust context.
"""

from __future__ import annotations

import re

# Bidirectional antonym / opposite pairs (lowercase). Small and curated —
# broad NLP is deliberately avoided.
_ANTONYM_PAIRS: list[frozenset[str]] = [
    frozenset({"approved", "rejected"}),
    frozenset({"accepted", "rejected"}),
    frozenset({"approve", "reject"}),
    frozenset({"enabled", "disabled"}),
    frozenset({"enable", "disable"}),
    frozenset({"allowed", "denied"}),
    frozenset({"allow", "deny"}),
    frozenset({"on", "off"}),
    frozenset({"yes", "no"}),
    frozenset({"true", "false"}),
    frozenset({"valid", "invalid"}),
    frozenset({"active", "inactive"}),
    frozenset({"open", "closed"}),
    frozenset({"start", "stop"}),
    frozenset({"add", "remove"}),
    frozenset({"keep", "drop"}),
    frozenset({"pass", "fail"}),
    frozenset({"success", "failure"}),
    frozenset({"up", "down"}),
    frozenset({"main", "prod"}),
    frozenset({"increase", "decrease"}),
]

# Attribute assertions: key → value. Deliberately narrow patterns.
_IS_RE = re.compile(
    r"\b([a-z][a-z0-9 _'-]{1,28}?)\s+(?:is|are|was|were|=|:)\s+(not\s+)?([a-z0-9.$:@/#-]{1,30})",
    re.IGNORECASE,
)
_USE_RE = re.compile(r"\buse[sd]?\s+([a-z0-9.+_-]{2,20})", re.IGNORECASE)
_AT_RE = re.compile(r"\b(?:at|@)\s*(\d{1,2}:\d{2})", re.IGNORECASE)
_LABELLED_RE = re.compile(
    r"\b(deadline|budget|price|cost|due|version|port|branch)\s+(?:is\s+|=\s*|:\s*)?"
    r"([a-z0-9.$:@/#-]{1,20})",
    re.IGNORECASE,
)

# Keys too generic to treat as a conflicting attribute on their own.
_STOP_KEYS = {"it", "this", "that", "there", "here", "what", "who", "which"}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def extract_assertions(content: str) -> dict[str, set[str]]:
    """Return {normalized_key: {values}} of simple attribute assertions.
    A negated value is prefixed with ``!`` so "is not X" opposes "is X"."""
    out: dict[str, set[str]] = {}

    def _add(key: str, value: str) -> None:
        key = key.strip().lower()
        value = value.strip().lower()
        if not key or not value or key in _STOP_KEYS:
            return
        out.setdefault(key, set()).add(value)

    for m in _IS_RE.finditer(content or ""):
        key, neg, value = m.group(1), m.group(2), m.group(3)
        _add(key, ("!" if neg else "") + value)
    for m in _USE_RE.finditer(content or ""):
        _add("use", m.group(1))
    for m in _AT_RE.finditer(content or ""):
        _add("time", m.group(1))
    for m in _LABELLED_RE.finditer(content or ""):
        _add(m.group(1), m.group(2))
    return out


def _antonym_signal(a: str, b: str) -> str | None:
    wa, wb = _words(a), _words(b)
    for pair in _ANTONYM_PAIRS:
        x, y = tuple(pair)
        if (x in wa and y in wb) or (y in wa and x in wb):
            return f"{x}/{y}"
    return None


def opposing_signal(content_a: str, content_b: str) -> tuple[str, str] | None:
    """Detect an opposing surface pattern between two texts. Returns
    ``(signal_type, detail)`` or None. signal_type ∈
    {"antonym", "negation", "attribute_value"}."""
    ant = _antonym_signal(content_a, content_b)
    if ant:
        return ("antonym", ant)

    aa, bb = extract_assertions(content_a), extract_assertions(content_b)
    for key in set(aa) & set(bb):
        va, vb = aa[key], bb[key]
        if va == vb:
            continue
        # negation: "x" vs "!x" for the same key
        for v in va:
            if v.startswith("!") and v[1:] in vb:
                return ("negation", key)
            if ("!" + v) in vb:
                return ("negation", key)
        # different concrete values for the same attribute
        pos_a = {v for v in va if not v.startswith("!")}
        pos_b = {v for v in vb if not v.startswith("!")}
        if pos_a and pos_b and pos_a.isdisjoint(pos_b):
            return ("attribute_value", key)
    return None


_BASE_CONFIDENCE = {
    "antonym": 0.7,
    "negation": 0.65,
    "attribute_value": 0.55,
}


def candidate_confidence(signal_type: str, distinct_source_types: int) -> float:
    """Confidence that this is a *candidate worth reviewing* — never 1.0, it's
    never a verdict. Different source types raise review priority."""
    base = _BASE_CONFIDENCE.get(signal_type, 0.5)
    if distinct_source_types >= 2:
        base += 0.1
    return round(min(0.9, base), 4)
