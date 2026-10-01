"""Request bodies for the REST routes.

One module so a router never has to import another router just to reach a
shape, and so the HTTP contract is readable in one place.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from server.core.types import Memory


class AttachmentOut(BaseModel):
    """A file attached to a memory (the ``attachments`` table row)."""

    id: str
    memory_id: str
    path: str
    sha256: str
    mime: Optional[str] = None
    size: int
    derived_text: Optional[str] = None
    derived_by: str = "none"
    status: str = "ok"
    created_at: str
    verified_at: Optional[str] = None


class MemoryOut(Memory):
    """A memory as the HTTP surface returns it: embedding dropped (large and
    useless to a client — similarity is computed server-side) and attachments
    joined where the route does that."""

    attachments: list[AttachmentOut] = Field(default_factory=list)


class FadingMemoryOut(BaseModel):
    """A fading memory plus its predicted retention (the review queue input)."""

    retention: float
    # The remaining fields mirror Memory minus embedding; declared explicitly
    # because the fading route re-keys the dict.
    id: str
    content: str
    memory_type: str
    importance: float
    frequency: int
    tags: list[str]
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool
    metadata: dict[str, Any]
    hscore: Optional[float] = None
    created_at: str
    accessed_at: str
    decay_factor: float
    stability_hours: float
    recall_count: int


class ReviewQueueItem(BaseModel):
    """One spaced-repetition review decision (from ``engine.review_queue``)."""

    id: str
    content: str
    project: Optional[str] = None
    source: Optional[str] = None
    importance: float
    hscore: Optional[float] = None
    retention: float
    stability_hours: float
    last_accessed: str
    recall_count: int
    review_count: int
    reason: str


class ReviewQueueResponse(BaseModel):
    review: list[ReviewQueueItem] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """``/api/health`` — the unauthenticated boundary and liveness report."""

    status: str
    service: str
    auth_required: bool
    unauthenticated_remote_access: bool
    api_host: str


class ConsolidateResponse(BaseModel):
    consolidated: int


class StoreDecision(BaseModel):
    """The admission gate's verdict over one write (from ``admission.evaluate``)."""

    action: str
    reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    redacted_content: Optional[str] = None
    redacted: bool
    secrets: list[Any] = Field(default_factory=list)
    max_similarity: float


class AdmitMemoryResponse(BaseModel):
    """``POST /api/memories`` internal admission result shape."""

    stored: bool
    decision: StoreDecision
    memory: Optional[MemoryOut] = None
    held_id: Optional[str] = None


class AskSource(BaseModel):
    n: int
    id: str
    content: str
    created_at: str
    project: Optional[str] = None
    score: float


class AskResponse(BaseModel):
    """``POST /api/ask`` — the cited answer plus the evidence behind it."""

    question: str
    answer: str
    sources: list[AskSource] = Field(default_factory=list)


class StoreRequest(BaseModel):
    content: str
    importance: float = 0.5
    tags: list[str] = []
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool = False
    memory_type: str = "short_term"
    metadata: dict = {}
    force: bool = False
    min_length: int = 3


class UpdateRequest(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    tags: Optional[list[str]] = None
    project: Optional[str] = None
    pinned: Optional[bool] = None


class CreateSessionRequest(BaseModel):
    name: str = "Untitled Session"
    metadata: dict = {}


class ImportRequest(BaseModel):
    data: list[dict]


class OnboardingMCPConfigRequest(BaseModel):
    client: str = "claude"
    profile: str = "work"


class DemoCleanupRequest(BaseModel):
    confirm: bool = False


class BackupRequest(BaseModel):
    passphrase: str = ""


class FileImportRequest(BaseModel):
    filename: str
    content_b64: str
    project: Optional[str] = None
    tags: list[str] = []


class AttachmentUploadRequest(BaseModel):
    filename: str
    content_b64: str


class AttachFileRequest(BaseModel):
    path: str
    derived_text: Optional[str] = None
    derived_by: str = "manual"


class RestoreRequest(BaseModel):
    content_b64: str
    passphrase: str = ""
    replace: bool = False


class PinRequest(BaseModel):
    pinned: bool = True


class ContextFileRequest(BaseModel):
    project: Optional[str] = None
    style: str = "claude"  # "claude" | "cursor"


class DedupeRequest(BaseModel):
    similarity_threshold: float = 0.95
    project: Optional[str] = None
    dry_run: bool = True


class ConsolidateRequest(BaseModel):
    similarity_threshold: float = 0.82
    min_age_days: int = 7
    min_cluster_size: int = 2
    project: Optional[str] = None
    dry_run: bool = True


class ReviewRequest(BaseModel):
    action: str
    snooze_days: int = 7
    reason: str = ""


class RedactAllRequest(BaseModel):
    dry_run: bool = True


class FindingReportRequest(BaseModel):
    title: str
    detail: str = ""
    category: str = "other"
    severity: str = "medium"
    source: str = "librarian"


class FindingDecisionRequest(BaseModel):
    status: str
    note: str = ""


class AskRequest(BaseModel):
    question: str
    top_k: int = 6
    project: Optional[str] = None
    session_id: Optional[str] = None
    min_importance: float = 0.0


class AdmissionEvalRequest(BaseModel):
    content: str
    project: Optional[str] = None
    min_length: int = 3


class AdmitRequest(BaseModel):
    content: str
    importance: float = 0.5
    tags: list[str] = []
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    pinned: bool = False
    memory_type: str = "short_term"
    metadata: dict = {}
    force: bool = False
    min_length: int = 3


class FeedbackRequest(BaseModel):
    helpful: bool


class ReviewMemoryResponse(BaseModel):
    """The result of a spaced-repetition review decision."""

    ok: bool
    action: Optional[str] = None
    memory_id: Optional[str] = None
    review_count: Optional[int] = None
    review_due_at: Optional[str] = None
    pinned: Optional[bool] = None
    error: Optional[str] = None


class ConnectorRequest(BaseModel):
    connector: str  # "local_files", "obsidian", "notion", "github"
    config: dict = {}
    params: dict = {}
    project: Optional[str] = None
    use_gate: bool = True


class ConnectorUploadRequest(BaseModel):
    filename: str
    content_b64: str


class MistakeRequest(BaseModel):
    task: str = ""
    wrong_action: str
    correct_action: str
    root_cause: str = ""
    tool_name: str = ""
    severity: str = "medium"
    source: str = "user"
    project: Optional[str] = None


class ConflictReviewRequest(BaseModel):
    action: str
