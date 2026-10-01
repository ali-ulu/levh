"""Procedural memory — promote repeatedly-reused memories to *proposed* skills.

``MemoryType`` has two values, ``short_term`` and ``episodic``, and the standard
taxonomy's third and fourth — semantic and procedural — are missing. This module
starts on procedural, and it starts the way the research the issue cites starts:
**a skill is not stored the first time it is seen, it is verified by repeated
successful reuse before it is promoted.**

The raw material is already in the store. ``memories.recall_count`` and
``memories.frequency`` count reuse, and nothing reads them as a promotion
signal; ``violations`` records a rule that failed. This module reads those three
counters and nothing else — no model, no network, no new schema — which is what
lets the whole rule be a pure function and the whole path stay offline.

Two decisions worth stating up front, because both were open questions on the
issue:

**A tag, not an enum value.** Promotion marks a memory with
:data:`PROCEDURE_TAG`. Adding a ``MemoryType`` member would touch the model, the
schema's CHECK constraint, the docs gate and the MCP surface for a distinction
the store does not need to enforce — and ``RULE_TAG`` is the precedent for
carrying exactly this kind of meaning on a tag.

**Proposed, never promoted.** Nothing here pins a memory, clears its decay
clock, or changes its type. A candidate becomes a row in the findings inbox —
the same "signal, not verdict" surface ``conflict.py`` and the admission gate's
``review`` verdict already use — and a person promotes it. That is the PPO-Gate
idea (verify a candidate before keeping it) expressed in LEVH's own idiom, and
it is the reason this file can be pure: the *proposal* is a function of the
counters, while the *promotion* is a decision this layer never makes.

Non-goals, so a reader does not go looking: no RL, no training, no LLM; and no
tool-call sequences — every memory LEVH holds is text, so the first version only
promotes text procedures.

One open decision from the issue is answered by construction rather than by a
mechanism: **demotion needs no separate rule.** A violation recorded against a
promoted procedure already fails :func:`promotion_evidence`, and the guard's
``record_mistake`` files one. What is *not* handled is a procedure that keeps
being recalled and keeps being right — it stays promoted, which is the intended
answer for the first version. A success/failure ratio is a follow-up, not a
prerequisite: it needs a denominator this module deliberately does not invent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .types import RULE_TAG

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph acyclic
    from .memory_engine import MemoryEngine
    from .types import Memory

#: Marks a memory a human has promoted to a procedure. Written by the decision,
#: read here so an already-promoted memory is not proposed again.
PROCEDURE_TAG = "levh-procedure"

#: Reuse evidence a candidate must show. All three, not any: a memory recalled
#: three times in one session (high recall, low frequency) is a hot query, not a
#: skill, and one touched many times at low importance is background noise.
MIN_RECALLS = 3
MIN_FREQUENCY = 3
MIN_IMPORTANCE = 0.5

#: The finding these proposals land in. ``memory`` is the inbox category the
#: held-queue signal already uses; ``low`` because nothing is broken.
PROPOSAL_CATEGORY = "memory"
PROPOSAL_SEVERITY = "low"
PROPOSAL_SOURCE = "procedure"

#: How much of a memory's text a proposal quotes. Enough to recognise the
#: memory in the inbox without pasting an essay into a title.
_EXCERPT_CHARS = 80


def promotion_evidence(
    memory: Memory, violations: int = 0
) -> dict[str, float | int] | None:
    """Whether one memory has earned a procedure proposal. Pure.

    Returns the evidence that promoted it, or ``None``. The three exclusions
    come first and each has a reason:

    - **pinned** — a pinned memory is already exempt from decay; promoting it
      again says nothing new.
    - **already tagged** — a human has been here. Re-proposing a decision is
      how an inbox stops being read.
    - **a recorded violation** — ``guard.py`` files one when a rule was
      *broken*, so a memory with one is not a success story. This is the
      "no violation recorded against it" clause, and it is the only clause that
      can fail a memory the counters alone would pass.
    """
    if memory.pinned:
        return None
    if PROCEDURE_TAG in (memory.tags or []):
        return None
    if violations > 0:
        return None
    if memory.recall_count < MIN_RECALLS:
        return None
    if memory.frequency < MIN_FREQUENCY:
        return None
    if memory.importance < MIN_IMPORTANCE:
        return None

    return {
        "recall_count": memory.recall_count,
        "frequency": memory.frequency,
        "importance": round(float(memory.importance), 4),
    }


def is_rule(memory: Memory) -> bool:
    """A guard rule, which is pinned and therefore never a candidate. Kept as a
    named predicate rather than an inline check because the two paths are
    deliberately separate: rules come from *mistakes*, procedures from
    *successes*."""
    return RULE_TAG in (memory.tags or [])


def build_proposal(memory: Memory, evidence: Mapping) -> dict:
    """Turn a promoted memory into a findings-inbox row. Pure.

    The row is shaped for ``findings.build_row``. The title carries no count on
    purpose: the finding fingerprint is built from the title plus the *shape* of
    the detail with numbers and ids scrubbed, so a title that moved with the
    counters would file the same candidate as a new row on every scan. The
    detail carries the proposed procedure text plus the evidence, so the human
    can decide without going back to the store.
    """
    excerpt = " ".join((memory.content or "").split())
    if len(excerpt) > _EXCERPT_CHARS:
        excerpt = excerpt[: _EXCERPT_CHARS - 1].rstrip() + "…"
    return {
        "title": f"Procedure candidate: {excerpt}",
        "detail": (
            f"Memory {memory.id} has been reused often enough to be a "
            f"procedure candidate (recalled {evidence['recall_count']} times, "
            f"touched {evidence['frequency']} times, importance "
            f"{evidence['importance']}).\n\n"
            f"Proposed procedure:\n{memory.content}\n\n"
            f"No violation is recorded against it, so the reuse has been "
            f"successful so far.\n\n"
            f"To promote it, tag it {PROCEDURE_TAG} and pin it — a procedure is "
            f"exempt from ordinary decay like a pinned rule. To decline, resolve "
            f"or ignore this finding."
        ),
        "category": PROPOSAL_CATEGORY,
        "severity": PROPOSAL_SEVERITY,
        "source": PROPOSAL_SOURCE,
    }


def _violations_by_rule(violations: Iterable[Mapping]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for violation in violations:
        rule_id = violation.get("rule_id")
        if rule_id:
            counts[str(rule_id)] = counts.get(str(rule_id), 0) + 1
    return counts


async def propose_procedures(
    engine: MemoryEngine, *, limit: int = 500
) -> list[dict]:
    """Scan the store and file a proposal for every memory that earned one.

    Read-only with respect to memories: it lists them and their violations, then
    writes findings. The one write is the proposal itself — the promotion it
    suggests stays a human's call.

    Returns the proposals filed, each carrying ``memory_id`` so a caller (the
    evaluation harness, the librarian loop) can map them back without parsing
    the finding text.
    """
    from . import findings as findings_core

    memories = await engine.list_memories(limit=limit)
    violations = await engine.db.list_violations(limit=1000)
    by_rule = _violations_by_rule(violations)

    proposals: list[dict] = []
    for memory in memories:
        if is_rule(memory):
            continue
        evidence = promotion_evidence(memory, by_rule.get(memory.id, 0))
        if evidence is None:
            continue
        row = findings_core.build_row(**build_proposal(memory, evidence))
        stored = await engine.db.record_finding(row)
        proposals.append({"memory_id": memory.id, "finding_id": stored["id"]})

    # Sorted so the returned order is a property of the store, not of the
    # listing order — the determinism contract the evaluation harness holds.
    proposals.sort(key=lambda p: p["memory_id"])
    return proposals
