"""Request bodies for the REST routes.

One module so a router never has to import another router just to reach a
shape, and so the HTTP contract is readable in one place.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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
