"""LEVH Type Definitions — Pydantic models for all data structures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# Tag carried by every rule the mistake guard records. It lives here rather
# than in `guard.py` because both the guard and the context-file builder in
# `memory_engine` need it, and the guard imports the engine — defining it in
# either of those would make the import cycle.
RULE_TAG = "levh-rule"
DECISION_TAG = "levh-decision"
BLOCKER_TAG = "levh-blocker"


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware UTC datetime, or ``None``.

    Accepts the ``Z`` suffix, treats a naive value as UTC, and converts any
    offset to UTC — so a caller can pass any well-formed instant and get a
    comparable one back. Raises ``ValueError`` on a malformed value.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_as_of(value: str | None) -> str | None:
    """Canonicalise a point-in-time read parameter (#335).

    Returns fixed-precision UTC ISO-8601 so every temporal comparison sees the
    same shape regardless of what offset or precision the caller used, or
    ``None`` for an absent/empty value. A malformed value raises ``ValueError``
    so the caller rejects it instead of silently falling back to the current
    view — "I asked about the past and got today" is the one answer a
    point-in-time read must never give by accident.
    """
    parsed = parse_iso(value)
    if parsed is None:
        return None
    # ``isoformat()`` rather than an explicit strftime format: it is exactly
    # what the writers use for ``created_at``/``valid_from``/``valid_to``
    # (``datetime.now(timezone.utc).isoformat()``), so the string comparison
    # in the SQL and predicate paths stays consistent on both sides.
    return parsed.isoformat()


# `Enum.__str__`/`__format__` print "ClassName.MEMBER" even for a `(str, Enum)`
# mixin — the `str` base is not enough to make f-string formatting return the
# plain value. Left alone, that leaks straight into MCP tool output text (the
# model's context, and what a user reads) as e.g. "Type: MemoryType.EPISODIC",
# and into `Memory.model_dump()` (Python mode preserves the enum instance;
# only `mode="json"` converts it). Overriding `__str__` fixes both call shapes
# everywhere they're formatted, current and future, instead of patching each
# f-string site with `.value`. Equality and `isinstance(x, str)` are unaffected.
class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"

    def __str__(self) -> str:
        return self.value


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"

    def __str__(self) -> str:
        return self.value


# ── Data Models ──────────────────────────────────────────────────────


class Memory(BaseModel):
    """A single memory record."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    content: str
    memory_type: MemoryType = MemoryType.SHORT_TERM
    embedding: Optional[list[float]] = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    frequency: int = Field(default=1, ge=1)
    tags: list[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    hscore: Optional[float] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    accessed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decay_factor: float = Field(default=1.0, ge=0.0, le=1.0)
    stability_hours: float = Field(
        default=168.0,
        gt=0.0,
        description=(
            "This memory's own half-life in hours — how long until it decays "
            "to 50% relevance since last access. Grows every time the memory "
            "is recalled or explicitly reinforced (spaced-repetition style), "
            "so frequently-used memories become durable while unused ones fade."
        ),
    )
    recall_count: int = Field(
        default=0, ge=0, description="Times this memory has been reinforced by recall."
    )
    valid_from: Optional[str] = Field(
        default=None,
        description=(
            "World time from which this fact was believed true (issue #335). "
            "Defaults to created_at at write; NULL only for a row that predates "
            "the column and has not been backfilled."
        ),
    )
    valid_to: Optional[str] = Field(
        default=None,
        description=(
            "World time at which this fact stopped being current. NULL means "
            "'still believed'. Set when a newer, near-identical memory "
            "supersedes this one. A retired row is never deleted and is "
            "reachable through an as_of read."
        ),
    )
    superseded_by: Optional[str] = Field(
        default=None,
        description="Id of the memory that replaced this one, if any (issue #335).",
    )

    def touch(self) -> None:
        """Update accessed_at to now."""
        self.accessed_at = datetime.now(timezone.utc).isoformat()


class Session(BaseModel):
    """A memory session (e.g. one coding session)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Untitled Session"
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)
    memory_count: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: Optional[str] = None


class MemoryStats(BaseModel):
    """Aggregate statistics about the memory system."""

    total_memories: int = 0
    short_term_count: int = 0
    episodic_count: int = 0
    avg_hscore: float = 0.0
    avg_importance: float = 0.0
    sessions_count: int = 0
    pinned_count: int = 0
    projects_count: int = 0


class RecallRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    memory_types: list[MemoryType] = Field(default_factory=list)
    session_id: Optional[str] = None
    project: Optional[str] = None
    min_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    explain: bool = Field(
        default=False,
        description=(
            "Return a per-result score breakdown: which signal drove the "
            "ranking, where each candidate came from, and the four H(x,ψ) "
            "penalties that sum to the score. Off by default; the ranking is "
            "identical either way."
        ),
    )
    reinforce: bool = Field(
        default=True,
        description=(
            "Whether this recall reinforces the returned memories "
            "(resets decay clock, bumps frequency). Set false for read-only "
            "dashboard/search previews so browsing doesn't inflate the signal."
        ),
    )
    as_of: Optional[str] = Field(
        default=None,
        description=(
            "Point-in-time read (issue #335): return what the store believed "
            "on this ISO-8601 instant, including facts retired since. Always "
            "read-only — a question about the past never reinforces a current "
            "belief."
        ),
    )
    include_superseded: bool = Field(
        default=False,
        description=(
            "Opt retired facts back into the candidate set (issue #335). Off "
            "by default: a superseded fact is not current and must not surface "
            "in an ordinary read. Set true to audit what was replaced."
        ),
    )

    @field_validator("as_of")
    @classmethod
    def _normalize_as_of(cls, value: Optional[str]) -> Optional[str]:
        # Canonicalise at the request boundary so a malformed instant is a
        # 422 the caller sees, not a silent fallback to the current view.
        return normalize_as_of(value)


class ScoreBreakdown(BaseModel):
    """Individual H(x,ψ) components for visualization.

    The four ``*_component`` values sum to ``total_hscore``. ``similarity_source``
    and ``cosine`` are only meaningful on a recall result (which signal actually
    drove the ranking, and the raw cosine even when it did not); the single-memory
    ``/score-breakdown`` route leaves them at their defaults.
    """

    memory_id: str
    content_snippet: str
    total_hscore: float
    alpha_component: float  # α·(1-similarity)
    beta_component: float   # β·decay
    gamma_component: float  # γ·(1-importance)
    delta_component: float  # δ·(1-freq_norm)
    superseded_penalty: float = 0.0  # added when a newer memory replaced this one
    similarity_source: str = "cosine"
    similarity: float = 0.0
    cosine: float = 0.0
    candidate_source: str = "vector"
    decay_factor: float = 1.0
    importance: float = 0.0
    frequency: int = 0


class RecallDiagnosis(BaseModel):
    """Why an empty recall returned nothing.

    Attached to a ``RecallResult`` only when no memory was returned, so the
    "why is nothing coming back" question — the one a user asks exactly when
    recall is broken — has an answer that is a function of the store, not a
    guess. Computed from counts over the same table the candidate pipeline
    reads; deterministic and model-free.
    """

    query: str
    stored_total: int
    in_scope_total: int
    excluded_by_project: int = 0
    excluded_by_session: int = 0
    excluded_by_importance: int = 0
    query_terms: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def empty_store(self) -> bool:
        return self.stored_total == 0


class RecallResult(BaseModel):
    memories: list[Memory]
    scores: list[float]
    breakdowns: list[ScoreBreakdown] = Field(default_factory=list)
    diagnosis: Optional[RecallDiagnosis] = None
