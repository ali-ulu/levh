"""StackMemory Type Definitions — Pydantic models for all data structures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


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
    reinforce: bool = Field(
        default=True,
        description=(
            "Whether this recall reinforces the returned memories "
            "(resets decay clock, bumps frequency). Set false for read-only "
            "dashboard/search previews so browsing doesn't inflate the signal."
        ),
    )


class RecallResult(BaseModel):
    memories: list[Memory]
    scores: list[float]


class ScoreBreakdown(BaseModel):
    """Individual H(x,ψ) components for visualization."""

    memory_id: str
    content_snippet: str
    total_hscore: float
    alpha_component: float  # α·(1-similarity)
    beta_component: float   # β·decay
    gamma_component: float  # γ·(1-importance)
    delta_component: float  # δ·(1-freq_norm)
