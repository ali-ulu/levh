"""Request bodies for the REST routes.

One module so a router never has to import another router just to reach a
shape, and so the HTTP contract is readable in one place.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from server.core.types import Memory, Session


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


class AttachmentListResponse(BaseModel):
    """``GET /api/memories/{id}/attachments`` - every file attached to one
    memory."""

    attachments: list[AttachmentOut]


class MemoryOut(Memory):
    """A memory as the HTTP surface returns it: embedding dropped (large and
    useless to a client - similarity is computed server-side) and attachments
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
    """``/api/health`` - the unauthenticated boundary and liveness report."""

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
    """``POST /api/ask`` - the cited answer plus the evidence behind it."""

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


class TrustBreakdownResponse(BaseModel):
    """``GET /api/memories/{id}/trust`` — the explainable provenance verdict."""

    memory_id: str
    confidence: float
    label: str
    components: dict[str, float]
    evidence: dict[str, Any]
    explanation: list[str]


# ── Response models: recall / config / findings-decision ─────────────


class RecallResponse(BaseModel):
    """``POST /api/memories/recall`` — ranked memories plus scoring context."""

    memories: list[MemoryOut] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    breakdowns: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis: Optional[dict[str, Any]] = None


class ServerConfigResponse(BaseModel):
    """``GET /api/config`` — the Settings page payload. Free-form in shape by
    design (it mirrors runtime internals), so the envelope is typed but its
    leaves stay open."""

    db_path: str
    embedder_mode: str
    requested_embedder_mode: str
    embedder_fallback_reason: Optional[str] = None
    embedder_dimension: Optional[int] = None
    short_term_max: int
    weights: dict[str, float]
    decay_half_life_hours: float
    reinforcement_gain: float
    max_stability_hours: float
    auto_summarize_sessions: bool
    outbound: dict[str, Any]
    recall_log: dict[str, Any]
    version: str


# ── Response models: sessions ─────────────────────────────────────────


class SessionOut(Session):
    pass


class SessionDeleteResponse(BaseModel):
    """``DELETE /api/sessions/{id}`` - what happened to the session and its
    memories. ``ok=False`` bodies carry ``error`` (and ``memory_count`` when
    the store refused because the session still holds memories)."""

    ok: bool
    session_id: Optional[str] = None
    error: Optional[str] = None
    memory_count: Optional[int] = None
    memories_detached: Optional[int] = None
    memories_deleted: Optional[int] = None


class SummarizeSessionResponse(BaseModel):
    """``POST /api/sessions/{id}/summarize``."""

    summarized: bool
    reason: Optional[str] = None
    summary: Optional[MemoryOut] = None


# ── Response models: entities ─────────────────────────────────────────


class EntityOut(BaseModel):
    """A persisted entity with its memory mention count."""

    id: str
    type: str
    ekey: Optional[str] = None
    name: str
    updated_at: Optional[str] = None
    mentions: int


class EntityListResponse(BaseModel):
    entities: list[EntityOut] = Field(default_factory=list)


class EntityReindexResponse(BaseModel):
    """``POST /api/entities/reindex`` - the rebuild report."""

    memories: int
    entities: int
    links: int
    by_type: dict[str, int]


class EntityGraphStatsResponse(BaseModel):
    by_type: dict[str, int]


class EntityNeighborOut(BaseModel):
    """A co-occurring entity: the join groups by neighbour and counts shared
    memories, so this row carries ``shared`` instead of ``mentions``."""

    id: str
    type: str
    name: str
    shared: int


class EntityProfileResponse(BaseModel):
    """``GET /api/entities/{id}`` - the entity, its memories, its neighbours."""

    entity: EntityOut
    memories: list[MemoryOut] = Field(default_factory=list)
    related: list[EntityNeighborOut] = Field(default_factory=list)


# ── Response models: findings ─────────────────────────────────────────


class FindingOut(BaseModel):
    """One findings-inbox row (the ``findings`` table)."""

    id: str
    title: str
    detail: str
    category: str
    severity: str
    source: str
    status: str
    occurrences: int
    first_seen_at: str
    last_seen_at: str
    decided_at: Optional[str] = None
    note: Optional[str] = None
    external_ref: Optional[str] = None


class FindingListResponse(BaseModel):
    findings: list[FindingOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class FindingReportResponse(FindingOut):
    """``POST /api/findings`` - the stored row plus repeat bookkeeping."""

    repeat: bool
    reopened: bool


class FindingDecisionResponse(FindingOut):
    """``POST /api/findings/{id}/decide`` — the updated row."""

    pass


class FindingDeleteResponse(BaseModel):
    ok: bool
    deleted: str


# ── Response models: agents / checkpoints ─────────────────────────────


class AgentConnectResponse(BaseModel):
    """``POST /api/agents/connect`` - the created agent-session record."""

    agent_session_id: str
    agent_name: str
    display: str
    icon: str
    session_id: Optional[str] = None
    project: Optional[str] = None
    connected_at: str


class AgentHeartbeatResponse(BaseModel):
    ok: bool
    agent_session_id: str
    last_heartbeat: str


class AgentDisconnectResponse(BaseModel):
    ok: bool
    agent_session_id: str
    disconnected_at: str


class AgentSessionOut(BaseModel):
    """An ``agent_sessions`` row as the API returns it: the raw row (``metadata_json`` stays the stored
    JSON string) and ``online`` computed per request from the in-process
    presence table."""

    id: str
    agent_name: str
    agent_display: str
    session_id: Optional[str] = None
    project: Optional[str] = None
    status: str
    connected_at: str
    last_heartbeat_at: str
    disconnected_at: Optional[str] = None
    metadata_json: str = "{}"
    online: Optional[bool] = None


class AgentStatsResponse(BaseModel):
    """``GET /api/agents/stats`` - aggregate usage statistics."""

    total_connections: int
    currently_online: int
    online_agents: list[dict[str, str]] = Field(default_factory=list)
    by_agent: list[dict[str, Any]] = Field(default_factory=list)


class AgentCheckpointOut(BaseModel):
    """An ``agent_checkpoints`` row (``memory_ids_json`` parsed client-side
    stays out of the way; the raw column is what the handler returns)."""

    id: str
    agent_name: str
    session_id: Optional[str] = None
    project: Optional[str] = None
    checkpoint_type: str
    title: Optional[str] = None
    summary: Optional[str] = None
    memory_ids_json: Optional[str] = None
    created_at: str


class CheckpointCreateResponse(BaseModel):
    """``POST /api/checkpoints`` - the created checkpoint's identity."""

    checkpoint_id: str
    agent_name: str
    title: str
    created_at: str


class AgentMetricsResponse(BaseModel):
    """``GET /api/agents/{name}/metrics`` - one agent's performance summary."""

    agent_name: str
    display: str
    connections: int
    sessions: int
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    checkpoints: dict[str, dict[str, Any]] = Field(default_factory=dict)
    currently_online: bool


class UsageBillingResponse(BaseModel):
    """``GET /api/agents/metrics/usage`` - cross-agent usage summary."""

    summary: dict[str, int]
    by_agent: list[dict[str, Any]] = Field(default_factory=list)


class CollaborationResponse(BaseModel):
    """``GET /api/agents/collaboration/{project}``."""

    project: str
    agents: list[dict[str, Any]] = Field(default_factory=list)
    shared_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    collaboration_score: int


# ── Response models: attachments ──────────────────────────────────────


class UploadedFileResponse(BaseModel):
    """Shared by the upload endpoints: where the bytes landed on this
    machine, and the display name the caller chose (never the stored path)."""

    path: str
    filename: str
    bytes: int


class AttachmentVerifyResponse(AttachmentOut):
    """``POST /api/attachments/{id}/verify`` - the row with its post-check
    status (``ok`` / ``missing`` / ``changed``)."""

    pass


class VerifyAllAttachmentsResponse(BaseModel):
    ok: int
    missing: int
    changed: int


class AttachmentDeleteResponse(BaseModel):
    deleted: bool


# ── Response models: conflicts ────────────────────────────────────────


class ConflictOut(BaseModel):
    """A conflict-candidate row, JSON columns parsed."""

    id: str
    memory_id_a: str
    memory_id_b: str
    signal_type: str
    confidence: float
    status: str
    shared_entities: list[str] = Field(default_factory=list)
    explanation: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    reviewed_at: Optional[str] = None


class ConflictListResponse(BaseModel):
    conflicts: list[ConflictOut] = Field(default_factory=list)


class ConflictDetectResponse(BaseModel):
    """``POST /api/conflicts/detect`` - the idempotent scan report."""

    new_candidates: int
    pairs_examined: int
    open_total: int
    stale_pruned: int


class ConflictReviewResponse(BaseModel):
    ok: bool
    action: str
    conflict: ConflictOut


# ── Response models: connectors ───────────────────────────────────────


class ConnectorSyncResponse(BaseModel):
    """The shared ingest report of ``/api/connectors/import`` and
    ``/api/connectors/sync``."""

    connector: str
    fetched: int
    stored: int
    redacted: int
    duplicates: int
    held: int
    errors: int
    source_key: str
    last_synced_at: str


class ConnectorSyncStateOut(BaseModel):
    """A ``connector_sync`` bookkeeping row."""

    source_key: str
    connector: str
    project: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_fetched: Optional[int] = None
    last_stored: Optional[int] = None
    total_stored: Optional[int] = None
    runs: Optional[int] = None


class ConnectorSyncStateResponse(BaseModel):
    sync_state: list[ConnectorSyncStateOut] = Field(default_factory=list)


class ConnectorOut(BaseModel):
    name: str
    description: str
    required_config_keys: list[str] = Field(default_factory=list)


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorOut] = Field(default_factory=list)


class ConnectorConfigResponse(ConnectorOut):
    """``GET /api/connectors/{name}/config`` adds the help text."""

    help: str


# ── Response models: knowledge / workspace ────────────────────────────


class NameCountOut(BaseModel):
    """Shared row shape of the project/source/tag aggregates."""

    name: str
    memory_count: Optional[int] = None
    count: Optional[int] = None
    last_used: Optional[str] = None


class ProjectListResponse(BaseModel):
    projects: list[NameCountOut] = Field(default_factory=list)


class SourceListResponse(BaseModel):
    sources: list[NameCountOut] = Field(default_factory=list)


class TagListResponse(BaseModel):
    tags: list[NameCountOut] = Field(default_factory=list)


class PersonOut(BaseModel):
    """An aggregated person (``memory_ids`` dropped for the summary view)."""

    key: str
    name: str
    email: Optional[str] = None
    memory_count: int
    sources: list[str] = Field(default_factory=list)
    last_seen: str


class PersonListResponse(BaseModel):
    people: list[PersonOut] = Field(default_factory=list)


class PersonProfileResponse(BaseModel):
    person: PersonOut
    memories: list[MemoryOut] = Field(default_factory=list)


class OrganizationOut(BaseModel):
    """An aggregated organization (people capped at 50 in the summary)."""

    key: str
    name: str
    domain: str
    memory_count: int
    people: list[str] = Field(default_factory=list)
    person_count: int
    sources: list[str] = Field(default_factory=list)
    last_seen: str


class OrganizationListResponse(BaseModel):
    organizations: list[OrganizationOut] = Field(default_factory=list)


class OrganizationProfileResponse(BaseModel):
    organization: OrganizationOut
    memories: list[MemoryOut] = Field(default_factory=list)


class TimelineItemOut(BaseModel):
    id: str
    summary: str
    source: Optional[str] = None
    memory_type: str


class TimelineGroupOut(BaseModel):
    date: str
    count: int
    items: list[TimelineItemOut] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    timeline: list[TimelineGroupOut] = Field(default_factory=list)


class DecisionOut(BaseModel):
    """One detected decision statement."""

    id: str
    text: str
    source: Optional[str] = None
    date: Optional[str] = None
    project: Optional[str] = None


class DecisionListResponse(BaseModel):
    decisions: list[DecisionOut] = Field(default_factory=list)


class BriefingItemOut(BaseModel):
    id: str
    summary: str
    source: Optional[str] = None
    time: str


class BriefingCommitmentOut(BaseModel):
    id: str
    text: str
    source: Optional[str] = None
    date: Optional[str] = None
    project: Optional[str] = None


class BriefingFadingOut(BaseModel):
    id: str
    summary: str
    retention: float


class BriefingBody(BaseModel):
    generated_at: str
    today: list[BriefingItemOut] = Field(default_factory=list)
    commitments: list[BriefingCommitmentOut] = Field(default_factory=list)
    fading: list[BriefingFadingOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class BriefingResponse(BaseModel):
    """``GET /api/briefing`` - the deterministic daily briefing."""

    briefing: BriefingBody


class MeetingAttendeeOut(BaseModel):
    name: str
    email: Optional[str] = None
    last_seen: str
    interaction_count: int
    recent: list[dict[str, Any]] = Field(default_factory=list)


class MeetingPrepBody(BaseModel):
    generated_at: str
    meeting: Optional[dict[str, Any]] = None
    reason: Optional[str] = None
    people: list[MeetingAttendeeOut] = Field(default_factory=list)
    open_commitments: list[BriefingCommitmentOut] = Field(default_factory=list)


class MeetingPrepResponse(BaseModel):
    """``GET /api/meeting-prep`` - the pre-meeting brief."""

    meeting_prep: MeetingPrepBody
    recent_decisions: list[DecisionOut] = Field(default_factory=list)


# ── Response models: guard ────────────────────────────────────────────


class GuardViolationOut(BaseModel):
    """A ``violations`` row - one recorded mistake."""

    id: str
    rule_id: str
    task: Optional[str] = None
    wrong_action: str
    root_cause: Optional[str] = None
    tool_name: Optional[str] = None
    severity: str
    source: str
    occurred_at: str
    resolved: Optional[int] = None
    resolution: Optional[str] = None


class GuardViolationListResponse(BaseModel):
    violations: list[GuardViolationOut] = Field(default_factory=list)


class GuardRuleOut(BaseModel):
    """A pinned rule as the guard reshapes it for the dashboard."""

    id: str
    statement: str
    importance: float
    severity: str
    task: str
    correct_action: str
    root_cause: str
    project: Optional[str] = None
    created_at: str


class GuardRuleListResponse(BaseModel):
    rules: list[GuardRuleOut] = Field(default_factory=list)


class GuardMistakeResponse(BaseModel):
    """``POST /api/guard/mistakes`` - the pinned rule and the logged incident."""

    rule_id: str
    violation_id: str
    statement: str
    pinned: bool
    severity: str
    total_violations: int


# ── Response models: memory extras ────────────────────────────────────


class ForgettingCurveResponse(BaseModel):
    """``GET /api/memories/{id}/forgetting-curve``."""

    memory_id: str
    pinned: bool
    stability_hours: float
    recall_count: int
    current_retention: float
    curve: list[dict[str, float]] = Field(default_factory=list)


class ScoreBreakdownResponse(BaseModel):
    """``GET /api/memories/{id}/score-breakdown`` - the public H(x,ψ) schema."""

    score: float
    components: dict[str, float]
    weights: dict[str, float]


class RelatedMemoriesResponse(BaseModel):
    """``GET /api/memories/{id}/related`` - nearest neighbours by embedding."""

    memory_id: str
    related: list[MemoryOut] = Field(default_factory=list)


class MemoryDeleteResponse(BaseModel):
    deleted: bool


class PurgeMemoryResponse(BaseModel):
    """``POST /api/memories/{id}/purge`` - the purge postcondition audit."""

    memory_id: str
    existed: bool
    purged: bool
    residue: dict[str, bool]


class RedactMemoryResponse(BaseModel):
    """``POST /api/memories/{id}/redact``."""

    ok: bool
    redacted: bool
    secrets: list[Any] = Field(default_factory=list)
    memory_id: str
    error: Optional[str] = None


class SecretAuditItemOut(BaseModel):
    id: str
    secret_types: list[str] = Field(default_factory=list)
    preview: str
    project: Optional[str] = None
    source: Optional[str] = None


class SecretAuditResponse(BaseModel):
    """``GET /api/memories/audit-secrets``."""

    audit: dict[str, Any]


class RedactAllResponse(BaseModel):
    """``POST /api/memories/redact-all``."""

    dry_run: bool
    scanned: int
    flagged: int
    redacted: int
    items: list[SecretAuditItemOut] = Field(default_factory=list)


class HeldMemoryOut(BaseModel):
    """A ``held_memories`` row - the admission gate's parked candidate."""

    id: str
    content: str
    importance: float
    tags_json: str
    session_id: Optional[str] = None
    project: Optional[str] = None
    source: Optional[str] = None
    memory_type: str
    pinned: Optional[int] = None
    metadata_json: str
    reasons_json: str
    max_similarity: float
    status: str
    created_at: str
    decided_at: Optional[str] = None
    admitted_memory_id: Optional[str] = None


class HeldMemoryListResponse(BaseModel):
    held: list[HeldMemoryOut] = Field(default_factory=list)
    waiting: int


class HeldAdmitResponse(BaseModel):
    """``POST /api/memories/held/{id}/admit`` and ``.../discard``.

    ``ok=False`` bodies carry ``error`` (and ``status`` when the candidate was
    already decided).
    """

    ok: bool
    error: Optional[str] = None
    status: Optional[str] = None
    memory: Optional[MemoryOut] = None
    held_id: Optional[str] = None


class DedupeResponse(BaseModel):
    """``POST /api/memories/dedupe`` - dry-run groups or applied removals."""

    dry_run: bool
    groups: list[list[MemoryOut]] = Field(default_factory=list)
    duplicates: Optional[int] = None
    removed: Optional[int] = None


class ConsolidateSimilarResponse(BaseModel):
    """``POST /api/memories/consolidate-similar``."""

    dry_run: bool
    clusters_found: int
    consolidated: int
    archived: int
    clusters: list[dict[str, Any]] = Field(default_factory=list)


class EvaluateAdmissionResponse(BaseModel):
    """``POST /api/memories/evaluate-admission``."""

    decision: StoreDecision


class ExportMemoriesResponse(BaseModel):
    """``POST /api/memories/export`` - full records including embeddings."""

    count: int
    data: list[dict[str, Any]]


class ImportMemoriesResponse(BaseModel):
    """``POST /api/memories/import`` - the gated import breakdown."""

    imported: int
    redacted: int
    duplicates: int
    held: int
    errors: int
    gated: bool


class TrustRecomputeResponse(BaseModel):
    """``POST /api/memories/trust/recompute``."""

    scored: int
    by_label: dict[str, int]


class LowTrustItemOut(BaseModel):
    memory_id: str
    confidence: float
    label: str


class LowTrustResponse(BaseModel):
    low_trust: list[LowTrustItemOut] = Field(default_factory=list)


class RestoreResponse(BaseModel):
    """``POST /api/restore`` - what the restore wrote."""

    memories: int
    sessions: int
    attachments: int
    replace: bool
    safety_backup_path: Optional[str] = None
    attachment_files_written: Optional[int] = None
    attachment_files_failed: Optional[int] = None
    attachments_by_reference: Optional[int] = None


class FileImportResponse(BaseModel):
    """``POST /api/import/file``."""

    filename: str
    bytes: int
    parts: int
    memories_created: int
    chars_extracted: int
    warnings: list[str] = Field(default_factory=list)


# ── Response models: system / librarian / onboarding ──────────────────


class ReadyzResponse(BaseModel):
    """``GET /api/readyz`` - the same body for 200 and 503."""

    status: str
    service: str
    database: str
    embedder_mode: str
    embedder_ready: bool
    derived_state_current: bool
    reasons: list[str] = Field(default_factory=list)


class BenchmarkResponse(BaseModel):
    """``POST /api/benchmark/recall`` - recall-quality metrics."""

    embedder_mode: str
    requested_embedder_mode: str
    queries: int
    hit_at_1: float = Field(alias="hit@1")
    hit_at_3: float = Field(alias="hit@3")
    hit_at_5: float = Field(alias="hit@5")
    mrr: float

    model_config = {"populate_by_name": True}


class ContextResponse(BaseModel):
    context: str
    chars: int


class ContextFileResponse(BaseModel):
    filename: str
    content: str


class LibrarianConfigOut(BaseModel):
    config: str
    levh_connected: bool


class LibrarianAgentOut(BaseModel):
    """One discovered agent's config scan row (see ``discover_agents``)."""

    agent: str
    levh_connected: bool
    configs: list[LibrarianConfigOut] = Field(default_factory=list)


class LibrarianStatusResponse(BaseModel):
    """``GET /api/librarian/status`` - the read-only scan report."""

    at: str
    agents: list[LibrarianAgentOut] = Field(default_factory=list)
    activity: dict[str, Any]


class LibrarianScanResponse(LibrarianStatusResponse):
    """``POST /api/librarian/scan`` adds how many findings were recorded."""

    findings_recorded: int


class LibrarianActionOut(BaseModel):
    action: dict[str, Any]
    result: dict[str, Any]


class LibrarianChatResponse(BaseModel):
    """``POST /api/librarian/chat``."""

    answer: str
    backend: str
    actions: list[LibrarianActionOut] = Field(default_factory=list)


class OnboardingCheckOut(BaseModel):
    id: str
    status: str
    message: str


class OnboardingStatusResponse(BaseModel):
    """``GET /api/onboarding/status`` - first-run readiness."""

    first_run: bool
    ready: bool
    memory_count: int
    database_initialized: bool
    embedder_mode: str
    mcp_default_profile: str
    mcp_configured: bool
    mcp_client: Optional[str] = None
    mcp_profile: str
    profile_counts: dict[str, int]
    clients: list[dict[str, str]] = Field(default_factory=list)
    profiles_are_security_boundary: bool
    profile_warning: str
    dogfood_enabled: bool
    dogfood_journal: dict[str, str]
    dogfood_statement: str
    demo_seeded: bool
    demo_memory_count: int
    recommended_next_step: str
    checks: list[OnboardingCheckOut] = Field(default_factory=list)


class OnboardingMcpConfigResponse(BaseModel):
    """``POST /api/onboarding/mcp-config``."""

    client: str
    platform: str
    profile: str
    tool_count: int
    profiles_are_security_boundary: bool
    warning: str
    onboarding_receipt_written: bool
    onboarding_ready: bool
    config: dict[str, Any] = Field(default_factory=dict)
    config_text: str = ""
    config_path: Optional[str] = None


class DemoSeedResponse(BaseModel):
    """``POST /api/seed-demo``."""

    seeded: int
    skipped: bool
    reason: Optional[str] = None
    existing: Optional[int] = None
    entities: Optional[int] = None
    entity_links: Optional[int] = None
    trust_scored: Optional[int] = None
    conflict_candidates: Optional[int] = None


class DemoCleanupAuditOut(BaseModel):
    memory_id: Optional[str] = None
    purged: bool
    residue: dict[str, Any] = Field(default_factory=dict)


class DemoCleanupResponse(BaseModel):
    """``POST /api/onboarding/remove-demo``."""

    removed: int
    remaining: int
    fully_purged: bool
    audits: list[DemoCleanupAuditOut] = Field(default_factory=list)
    entities: int
    entity_links: int
    trust_scored: int
