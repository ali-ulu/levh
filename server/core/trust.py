"""Provenance / trust scoring — a deterministic, explainable *reliability*
signal for a memory, separate from the H(x,ψ) recall score.

  - H-score answers: "which memory should be recalled?"
  - trust score answers: "how reliable is this memory signal?"

These are NOT merged and the trust score NEVER changes recall ranking.

This is provenance, NOT truth: it does not claim a memory is factually correct.
It summarises where the memory came from, how many *independent* sources
corroborate it (via the entity graph), whether a human has vouched for it
through the review/feedback lifecycle, how fresh it is, and whether it carries
risk flags (redaction, rejected admission, weakening). No LLM, no network.

The corroboration component needs the entity graph + other memories, so it is
computed in the engine; everything else here is a pure function of one memory.
"""

from __future__ import annotations

from typing import Any

# ── Source scoring ────────────────────────────────────────────────────

# Deterministic base reliability by source *type* — defaults, not universal
# truth. A human who typed or pinned something is trusted most; raw imported
# text least.
_SOURCE_TYPE_SCORES = {
    "manual": 0.85,      # dashboard / cli / capture — a human entered it
    "calendar": 0.80,
    "email": 0.75,
    "document": 0.70,    # notion / obsidian / local files
    "code": 0.65,        # git hook / code context
    "transcript": 0.60,
    "summary": 0.55,     # auto-summary / consolidation (derived, not primary)
    "unknown": 0.45,     # imported raw text with no known provenance
}


def source_type(source: str | None) -> str:
    """Normalise a raw ``source`` string to a coarse provenance type."""
    s = (source or "").lower()
    if not s:
        return "unknown"
    if "calendar" in s:
        return "calendar"
    if "email" in s:
        return "email"
    if "transcript" in s:
        return "transcript"
    if any(k in s for k in ("notion", "obsidian", "local_files", "document", "file")):
        return "document"
    if any(k in s for k in ("github", "git", "code", "commit")):
        return "code"
    if any(k in s for k in ("auto-summary", "summary", "consolidation")):
        return "summary"
    if any(k in s for k in ("dashboard", "cli", "manual", "capture", "claude", "cursor")):
        return "manual"
    return "unknown"


def source_score(source: str | None, pinned: bool = False) -> float:
    """Base reliability from where the memory came from. A pinned memory is a
    deliberate human keep, so it floors at the manual level."""
    stype = source_type(source)
    base = _SOURCE_TYPE_SCORES.get(stype, 0.45)
    if pinned:
        base = max(base, _SOURCE_TYPE_SCORES["manual"])
    return round(base, 4)


# ── Review / lifecycle scoring ────────────────────────────────────────

def review_score(memory: Any) -> float:
    """How much the human-in-the-loop lifecycle vouches for this memory:
    pinning, reinforcement/recall, positive review actions raise it; explicit
    weakening lowers it. Starts neutral at 0.5."""
    score = 0.5
    metadata = getattr(memory, "metadata", None) or {}
    pinned = bool(getattr(memory, "pinned", False))
    recall_count = int(getattr(memory, "recall_count", 0) or 0)
    stability = float(getattr(memory, "stability_hours", 168.0) or 168.0)

    if pinned:
        score += 0.2
    if recall_count > 0:
        score += min(0.15, 0.03 * recall_count)

    review = metadata.get("review") or {}
    last_action = review.get("last_action")
    if last_action in ("keep", "reinforce"):
        score += 0.1
    elif last_action == "pin":
        score += 0.15
    elif last_action == "weaken":
        score -= 0.15

    # Stability drifts up on reinforcement, down on negative feedback.
    if stability > 200:
        score += 0.1
    elif stability < 100:
        score -= 0.1

    return round(max(0.0, min(1.0, score)), 4)


# ── Recency scoring ───────────────────────────────────────────────────

def recency_score(memory: Any, now_iso: str) -> float:
    """Freshness of the memory by its own timestamp. Deliberately simple and
    based on ``created_at`` — NOT the H-score's accessed_at decay — so the two
    signals stay independent. Linear falloff to zero at ~180 days."""
    from datetime import datetime

    created = getattr(memory, "created_at", "") or ""
    if not created:
        return 0.5
    try:
        from datetime import timezone

        c = datetime.fromisoformat(created.replace("Z", "+00:00"))
        n = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        if n.tzinfo is None:
            n = n.replace(tzinfo=timezone.utc)
        c = c.astimezone(timezone.utc)
        n = n.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return 0.5
    age_days = max(0.0, (n - c).total_seconds() / 86400.0)
    return round(max(0.0, min(1.0, 1.0 - age_days / 180.0)), 4)


# ── Risk penalty ──────────────────────────────────────────────────────

def risk_penalty(memory: Any) -> float:
    """Higher = more caution warranted. Redaction, rejected/held admission,
    explicit weakening, and unknown provenance each add risk. Not a
    contradiction detector — no LLM."""
    penalty = 0.0
    metadata = getattr(memory, "metadata", None) or {}

    admission = metadata.get("admission") or {}
    if metadata.get("redaction_history") or admission.get("redacted"):
        penalty += 0.4
    if admission.get("action") in ("review", "reject"):
        penalty += 0.3

    review = metadata.get("review") or {}
    if review.get("last_action") == "weaken":
        penalty += 0.2

    if source_type(getattr(memory, "source", None)) == "unknown":
        penalty += 0.1

    return round(min(1.0, penalty), 4)


# ── Aggregation ───────────────────────────────────────────────────────

_WEIGHTS = {
    "source": 0.30,
    "corroboration": 0.25,
    "review": 0.20,
    "recency": 0.15,
    "risk": 0.10,
}


def confidence(
    source: float, corroboration: float, review: float, recency: float, risk: float
) -> float:
    """Combine the components into a clamped [0,1] confidence. NOT truth."""
    raw = (
        _WEIGHTS["source"] * source
        + _WEIGHTS["corroboration"] * corroboration
        + _WEIGHTS["review"] * review
        + _WEIGHTS["recency"] * recency
        - _WEIGHTS["risk"] * risk
    )
    return round(max(0.0, min(1.0, raw)), 4)


def corroboration_from_types(distinct_source_types: int) -> float:
    """Corroboration from how many DISTINCT source types reference the same
    entities. Repeated memories from the SAME source don't add distinct types,
    so same-source duplicates can't inflate this. Alone = 0.2."""
    n = max(1, distinct_source_types)
    return round(max(0.0, min(1.0, 0.2 + 0.2 * (n - 1))), 4)


def label_for(conf: float) -> str:
    if conf >= 0.80:
        return "high"
    if conf >= 0.60:
        return "medium_high"
    if conf >= 0.40:
        return "medium"
    if conf >= 0.20:
        return "low"
    return "very_low"


def build_explanation(
    source_t: str,
    distinct_types: list[str],
    corroborating: int,
    review_val: float,
    risk_flags: list[str],
) -> list[str]:
    lines = [f"Memory came from {source_t} source."]
    if corroborating > 0:
        lines.append(
            f"Corroborated by {corroborating} other memory(ies) across "
            f"{len(distinct_types)} distinct source type(s): {', '.join(distinct_types)}."
        )
    else:
        lines.append("No independent corroboration found yet (single source).")
    if review_val >= 0.65:
        lines.append("Vouched for by the review lifecycle (pinned / reinforced / kept).")
    elif review_val <= 0.4:
        lines.append("Weakened or unreviewed in the lifecycle.")
    if risk_flags:
        lines.append("Risk flags: " + ", ".join(risk_flags) + ".")
    else:
        lines.append("No redaction or admission risk flags found.")
    return lines


def risk_flags(memory: Any) -> list[str]:
    """Human-readable risk labels (for the evidence/explanation payload)."""
    flags: list[str] = []
    metadata = getattr(memory, "metadata", None) or {}
    admission = metadata.get("admission") or {}
    if metadata.get("redaction_history") or admission.get("redacted"):
        flags.append("redacted")
    if admission.get("action") in ("review", "reject"):
        flags.append(f"admission_{admission.get('action')}")
    review = metadata.get("review") or {}
    if review.get("last_action") == "weaken":
        flags.append("weakened")
    if source_type(getattr(memory, "source", None)) == "unknown":
        flags.append("unknown_source")
    return flags
