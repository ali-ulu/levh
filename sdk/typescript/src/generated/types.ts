// GENERATED FILE — do not edit by hand.
//
// Produced by `python scripts/generate_sdk.py` from the committed `openapi.json`
// at the repository root. `tests/test_typescript_sdk.py` fails if this file and
// the contract disagree, so regenerate rather than hand-patching.


export type AdmissionEvalRequest = {
  content: string;
  project?: string | null;
  min_length?: number;
};

export type AdmitRequest = {
  content: string;
  importance?: number;
  tags?: string[];
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  pinned?: boolean;
  memory_type?: string;
  metadata?: Record<string, unknown>;
  force?: boolean;
  min_length?: number;
};

export type AgentConnectRequest = {
  agent_name: string;
  session_id?: string;
  project?: string;
  metadata?: Record<string, unknown>;
  api_key?: string;
};

export type AskRequest = {
  question: string;
  top_k?: number;
  project?: string | null;
  session_id?: string | null;
  min_importance?: number;
};

export type AskResponse = {
  question: string;
  answer: string;
  sources?: AskSource[];
};

export type AskSource = {
  n: number;
  id: string;
  content: string;
  created_at: string;
  project?: string | null;
  score: number;
};

export type AttachFileRequest = {
  path: string;
  derived_text?: string | null;
  derived_by?: string;
};

export type AttachmentOut = {
  id: string;
  memory_id: string;
  path: string;
  sha256: string;
  mime?: string | null;
  size: number;
  derived_text?: string | null;
  derived_by?: string;
  status?: string;
  created_at: string;
  verified_at?: string | null;
};

export type AttachmentUploadRequest = {
  filename: string;
  content_b64: string;
};

export type BackupRequest = {
  passphrase?: string;
};

export type CheckpointRequest = {
  agent_name?: string;
  title?: string;
  summary?: string;
  project?: string;
  checkpoint_type?: string;
  memory_ids?: string[];
};

export type ConflictReviewRequest = {
  action: string;
};

export type ConnectorRequest = {
  connector: string;
  config?: Record<string, unknown>;
  params?: Record<string, unknown>;
  project?: string | null;
  use_gate?: boolean;
};

export type ConnectorUploadRequest = {
  filename: string;
  content_b64: string;
};

export type ConsolidateRequest = {
  similarity_threshold?: number;
  min_age_days?: number;
  min_cluster_size?: number;
  project?: string | null;
  dry_run?: boolean;
};

export type ConsolidateResponse = {
  consolidated: number;
};

export type ContextFileRequest = {
  project?: string | null;
  style?: string;
};

export type CreateSessionRequest = {
  name?: string;
  metadata?: Record<string, unknown>;
};

export type DedupeRequest = {
  similarity_threshold?: number;
  project?: string | null;
  dry_run?: boolean;
};

export type DemoCleanupRequest = {
  confirm?: boolean;
};

export type FadingMemoryOut = {
  retention: number;
  id: string;
  content: string;
  memory_type: string;
  importance: number;
  frequency: number;
  tags: string[];
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  pinned: boolean;
  metadata: Record<string, unknown>;
  hscore?: number | null;
  created_at: string;
  accessed_at: string;
  decay_factor: number;
  stability_hours: number;
  recall_count: number;
};

export type FeedbackRequest = {
  helpful: boolean;
};

export type FileImportRequest = {
  filename: string;
  content_b64: string;
  project?: string | null;
  tags?: string[];
};

export type FindingDecisionRequest = {
  status: string;
  note?: string;
};

export type FindingReportRequest = {
  title: string;
  detail?: string;
  category?: string;
  severity?: string;
  source?: string;
};

export type HTTPValidationError = {
  detail?: ValidationError[];
};

export type HealthResponse = {
  status: string;
  service: string;
  auth_required: boolean;
  unauthenticated_remote_access: boolean;
  api_host: string;
};

export type ImportRequest = {
  data: Record<string, unknown>[];
};

export type Memory = {
  id?: string;
  content: string;
  memory_type?: MemoryType;
  embedding?: number[] | null;
  importance?: number;
  frequency?: number;
  tags?: string[];
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  pinned?: boolean;
  metadata?: Record<string, unknown>;
  hscore?: number | null;
  created_at?: string;
  accessed_at?: string;
  decay_factor?: number;
  stability_hours?: number;
  recall_count?: number;
};

export type MemoryOut = {
  id?: string;
  content: string;
  memory_type?: MemoryType;
  embedding?: number[] | null;
  importance?: number;
  frequency?: number;
  tags?: string[];
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  pinned?: boolean;
  metadata?: Record<string, unknown>;
  hscore?: number | null;
  created_at?: string;
  accessed_at?: string;
  decay_factor?: number;
  stability_hours?: number;
  recall_count?: number;
  attachments?: AttachmentOut[];
};

export type MemoryStats = {
  total_memories?: number;
  short_term_count?: number;
  episodic_count?: number;
  avg_hscore?: number;
  avg_importance?: number;
  sessions_count?: number;
  pinned_count?: number;
  projects_count?: number;
};

export type MemoryType = "short_term" | "episodic";

export type MistakeRequest = {
  task?: string;
  wrong_action: string;
  correct_action: string;
  root_cause?: string;
  tool_name?: string;
  severity?: string;
  source?: string;
  project?: string | null;
};

export type OnboardingMCPConfigRequest = {
  client?: string;
  profile?: string;
};

export type PinRequest = {
  pinned?: boolean;
};

export type RecallRequest = {
  query: string;
  top_k?: number;
  memory_types?: MemoryType[];
  session_id?: string | null;
  project?: string | null;
  min_importance?: number;
  explain?: boolean;
  reinforce?: boolean;
};

export type RedactAllRequest = {
  dry_run?: boolean;
};

export type RestoreRequest = {
  content_b64: string;
  passphrase?: string;
  replace?: boolean;
};

export type ReviewMemoryResponse = {
  ok: boolean;
  action?: string | null;
  memory_id?: string | null;
  review_count?: number | null;
  review_due_at?: string | null;
  pinned?: boolean | null;
  error?: string | null;
};

export type ReviewQueueItem = {
  id: string;
  content: string;
  project?: string | null;
  source?: string | null;
  importance: number;
  hscore?: number | null;
  retention: number;
  stability_hours: number;
  last_accessed: string;
  recall_count: number;
  review_count: number;
  reason: string;
};

export type ReviewQueueResponse = {
  review?: ReviewQueueItem[];
};

export type ReviewRequest = {
  action: string;
  snooze_days?: number;
  reason?: string;
};

export type StoreRequest = {
  content: string;
  importance?: number;
  tags?: string[];
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  pinned?: boolean;
  memory_type?: string;
  metadata?: Record<string, unknown>;
  force?: boolean;
  min_length?: number;
};

export type UpdateRequest = {
  content?: string | null;
  importance?: number | null;
  tags?: string[] | null;
  project?: string | null;
  pinned?: boolean | null;
};

export type ValidationError = {
  loc: string | number[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
};
