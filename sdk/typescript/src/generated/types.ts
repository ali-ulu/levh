// GENERATED FILE — do not edit by hand.
//
// Produced by `python scripts/generate_sdk.py` from the committed `openapi.json`
// at the repository root. `tests/test_typescript_sdk.py` fails if this file and
// the contract disagree, so regenerate rather than hand-patching.


export type AdmissionEvalRequest = {
  content: string;
  min_length?: number;
  project?: string | null;
};

export type AdmitRequest = {
  content: string;
  force?: boolean;
  importance?: number;
  memory_type?: string;
  metadata?: Record<string, unknown>;
  min_length?: number;
  pinned?: boolean;
  project?: string | null;
  session_id?: string | null;
  source?: string | null;
  tags?: string[];
};

export type AgentConnectRequest = {
  agent_name: string;
  api_key?: string;
  metadata?: Record<string, unknown>;
  project?: string;
  session_id?: string;
};

export type AskRequest = {
  min_importance?: number;
  project?: string | null;
  question: string;
  session_id?: string | null;
  top_k?: number;
};

export type AttachFileRequest = {
  derived_by?: string;
  derived_text?: string | null;
  path: string;
};

export type AttachmentUploadRequest = {
  content_b64: string;
  filename: string;
};

export type BackupRequest = {
  passphrase?: string;
};

export type CheckpointRequest = {
  agent_name?: string;
  checkpoint_type?: string;
  memory_ids?: string[];
  project?: string;
  summary?: string;
  title?: string;
};

export type ConflictReviewRequest = {
  action: string;
};

export type ConnectorRequest = {
  config?: Record<string, unknown>;
  connector: string;
  params?: Record<string, unknown>;
  project?: string | null;
  use_gate?: boolean;
};

export type ConnectorUploadRequest = {
  content_b64: string;
  filename: string;
};

export type ConsolidateRequest = {
  dry_run?: boolean;
  min_age_days?: number;
  min_cluster_size?: number;
  project?: string | null;
  similarity_threshold?: number;
};

export type ContextFileRequest = {
  project?: string | null;
  style?: string;
};

export type CreateSessionRequest = {
  metadata?: Record<string, unknown>;
  name?: string;
};

export type DedupeRequest = {
  dry_run?: boolean;
  project?: string | null;
  similarity_threshold?: number;
};

export type DemoCleanupRequest = {
  confirm?: boolean;
};

export type FeedbackRequest = {
  helpful: boolean;
};

export type FileImportRequest = {
  content_b64: string;
  filename: string;
  project?: string | null;
  tags?: string[];
};

export type FindingDecisionRequest = {
  note?: string;
  status: string;
};

export type FindingReportRequest = {
  category?: string;
  detail?: string;
  severity?: string;
  source?: string;
  title: string;
};

export type HTTPValidationError = {
  detail?: ValidationError[];
};

export type ImportRequest = {
  data: Record<string, unknown>[];
};

export type MemoryType = "short_term" | "episodic";

export type MistakeRequest = {
  correct_action: string;
  project?: string | null;
  root_cause?: string;
  severity?: string;
  source?: string;
  task?: string;
  tool_name?: string;
  wrong_action: string;
};

export type OnboardingMCPConfigRequest = {
  client?: string;
  profile?: string;
};

export type PinRequest = {
  pinned?: boolean;
};

export type RecallRequest = {
  memory_types?: MemoryType[];
  min_importance?: number;
  project?: string | null;
  query: string;
  reinforce?: boolean;
  session_id?: string | null;
  top_k?: number;
};

export type RedactAllRequest = {
  dry_run?: boolean;
};

export type RestoreRequest = {
  content_b64: string;
  passphrase?: string;
  replace?: boolean;
};

export type ReviewRequest = {
  action: string;
  reason?: string;
  snooze_days?: number;
};

export type StoreRequest = {
  content: string;
  force?: boolean;
  importance?: number;
  memory_type?: string;
  metadata?: Record<string, unknown>;
  min_length?: number;
  pinned?: boolean;
  project?: string | null;
  session_id?: string | null;
  source?: string | null;
  tags?: string[];
};

export type UpdateRequest = {
  content?: string | null;
  importance?: number | null;
  pinned?: boolean | null;
  project?: string | null;
  tags?: string[] | null;
};

export type ValidationError = {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: string | number[];
  msg: string;
  type: string;
};
