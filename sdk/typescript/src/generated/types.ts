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

export type AdmitMemoryResponse = {
  stored: boolean;
  decision: StoreDecision;
  memory?: MemoryOut | null;
  held_id?: string | null;
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

export type AgentCheckpointOut = {
  id: string;
  agent_name: string;
  session_id?: string | null;
  project?: string | null;
  checkpoint_type: string;
  title?: string | null;
  summary?: string | null;
  memory_ids_json?: string | null;
  created_at: string;
};

export type AgentConnectRequest = {
  agent_name: string;
  session_id?: string;
  project?: string;
  metadata?: Record<string, unknown>;
  api_key?: string;
};

export type AgentConnectResponse = {
  agent_session_id: string;
  agent_name: string;
  display: string;
  icon: string;
  session_id?: string | null;
  project?: string | null;
  connected_at: string;
};

export type AgentDisconnectResponse = {
  ok: boolean;
  agent_session_id: string;
  disconnected_at: string;
};

export type AgentHeartbeatResponse = {
  ok: boolean;
  agent_session_id: string;
  last_heartbeat: string;
};

export type AgentMetricsResponse = {
  agent_name: string;
  display: string;
  connections: number;
  sessions: number;
  first_seen?: string | null;
  last_seen?: string | null;
  checkpoints?: Record<string, unknown>;
  currently_online: boolean;
};

export type AgentSessionOut = {
  id: string;
  agent_name: string;
  agent_display: string;
  session_id?: string | null;
  project?: string | null;
  status: string;
  connected_at: string;
  last_heartbeat_at: string;
  disconnected_at?: string | null;
  metadata_json?: string;
  online?: boolean | null;
};

export type AgentStatsResponse = {
  total_connections: number;
  currently_online: number;
  online_agents?: Record<string, unknown>[];
  by_agent?: Record<string, unknown>[];
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

export type AttachmentDeleteResponse = {
  deleted: boolean;
};

export type AttachmentListResponse = {
  attachments: AttachmentOut[];
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

export type AttachmentVerifyResponse = {
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

export type BackupRequest = {
  passphrase?: string;
};

export type BenchmarkResponse = {
  embedder_mode: string;
  requested_embedder_mode: string;
  queries: number;
  "hit@1": number;
  "hit@3": number;
  "hit@5": number;
  mrr: number;
};

export type BriefingBody = {
  generated_at: string;
  today?: BriefingItemOut[];
  commitments?: BriefingCommitmentOut[];
  fading?: BriefingFadingOut[];
  counts?: Record<string, unknown>;
};

export type BriefingCommitmentOut = {
  id: string;
  text: string;
  source?: string | null;
  date?: string | null;
  project?: string | null;
};

export type BriefingFadingOut = {
  id: string;
  summary: string;
  retention: number;
};

export type BriefingItemOut = {
  id: string;
  summary: string;
  source?: string | null;
  time: string;
};

export type BriefingResponse = {
  briefing: BriefingBody;
};

export type CheckActionRequest = {
  tool_name?: string;
  action_text: string;
  project?: string;
};

export type CheckpointCreateResponse = {
  checkpoint_id: string;
  agent_name: string;
  title: string;
  created_at: string;
};

export type CheckpointRequest = {
  agent_name?: string;
  title?: string;
  summary?: string;
  project?: string;
  checkpoint_type?: string;
  memory_ids?: string[];
};

export type CollaborationResponse = {
  project: string;
  agents?: Record<string, unknown>[];
  shared_checkpoints?: Record<string, unknown>[];
  collaboration_score: number;
};

export type ConflictDetectResponse = {
  new_candidates: number;
  pairs_examined: number;
  open_total: number;
  stale_pruned: number;
};

export type ConflictListResponse = {
  conflicts?: ConflictOut[];
};

export type ConflictOut = {
  id: string;
  memory_id_a: string;
  memory_id_b: string;
  signal_type: string;
  confidence: number;
  status: string;
  shared_entities?: string[];
  explanation?: Record<string, unknown>;
  created_at?: string | null;
  reviewed_at?: string | null;
};

export type ConflictReviewRequest = {
  action: string;
};

export type ConflictReviewResponse = {
  ok: boolean;
  action: string;
  conflict: ConflictOut;
};

export type ConnectorConfigResponse = {
  name: string;
  description: string;
  required_config_keys?: string[];
  help: string;
};

export type ConnectorListResponse = {
  connectors?: ConnectorOut[];
};

export type ConnectorOut = {
  name: string;
  description: string;
  required_config_keys?: string[];
};

export type ConnectorRequest = {
  connector: string;
  config?: Record<string, unknown>;
  params?: Record<string, unknown>;
  project?: string | null;
  use_gate?: boolean;
};

export type ConnectorSyncResponse = {
  connector: string;
  fetched: number;
  stored: number;
  redacted: number;
  duplicates: number;
  held: number;
  errors: number;
  source_key: string;
  last_synced_at: string;
};

export type ConnectorSyncStateOut = {
  source_key: string;
  connector: string;
  project?: string | null;
  last_synced_at?: string | null;
  last_fetched?: number | null;
  last_stored?: number | null;
  total_stored?: number | null;
  runs?: number | null;
};

export type ConnectorSyncStateResponse = {
  sync_state?: ConnectorSyncStateOut[];
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

export type ConsolidateSimilarResponse = {
  dry_run: boolean;
  clusters_found: number;
  consolidated: number;
  archived: number;
  clusters?: Record<string, unknown>[];
};

export type ContextFileRequest = {
  project?: string | null;
  style?: string;
};

export type ContextFileResponse = {
  filename: string;
  content: string;
};

export type ContextResponse = {
  context: string;
  chars: number;
};

export type CreateSessionRequest = {
  name?: string;
  metadata?: Record<string, unknown>;
};

export type DecisionListResponse = {
  decisions?: DecisionOut[];
};

export type DecisionOut = {
  id: string;
  text: string;
  source?: string | null;
  date?: string | null;
  project?: string | null;
};

export type DedupeRequest = {
  similarity_threshold?: number;
  project?: string | null;
  dry_run?: boolean;
};

export type DedupeResponse = {
  dry_run: boolean;
  groups?: MemoryOut[][];
  duplicates?: number | null;
  removed?: number | null;
};

export type DemoCleanupAuditOut = {
  memory_id?: string | null;
  purged: boolean;
  residue?: Record<string, unknown>;
};

export type DemoCleanupRequest = {
  confirm?: boolean;
};

export type DemoCleanupResponse = {
  removed: number;
  remaining: number;
  fully_purged: boolean;
  audits?: DemoCleanupAuditOut[];
  entities: number;
  entity_links: number;
  trust_scored: number;
};

export type DemoSeedResponse = {
  seeded: number;
  skipped: boolean;
  reason?: string | null;
  existing?: number | null;
  entities?: number | null;
  entity_links?: number | null;
  trust_scored?: number | null;
  conflict_candidates?: number | null;
};

export type EntityGraphStatsResponse = {
  by_type: Record<string, unknown>;
};

export type EntityListResponse = {
  entities?: EntityOut[];
};

export type EntityNeighborOut = {
  id: string;
  type: string;
  name: string;
  shared: number;
};

export type EntityOut = {
  id: string;
  type: string;
  ekey?: string | null;
  name: string;
  updated_at?: string | null;
  mentions: number;
};

export type EntityProfileResponse = {
  entity: EntityOut;
  memories?: MemoryOut[];
  related?: EntityNeighborOut[];
};

export type EntityReindexResponse = {
  memories: number;
  entities: number;
  links: number;
  by_type: Record<string, unknown>;
};

export type EvaluateAdmissionResponse = {
  decision: StoreDecision;
};

export type ExportMemoriesResponse = {
  count: number;
  data: Record<string, unknown>[];
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

export type FileImportResponse = {
  filename: string;
  bytes: number;
  parts: number;
  memories_created: number;
  chars_extracted: number;
  warnings?: string[];
};

export type FindingDecisionRequest = {
  status: string;
  note?: string;
};

export type FindingDecisionResponse = {
  id: string;
  title: string;
  detail: string;
  category: string;
  severity: string;
  source: string;
  status: string;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  decided_at?: string | null;
  note?: string | null;
  external_ref?: string | null;
};

export type FindingDeleteResponse = {
  ok: boolean;
  deleted: string;
};

export type FindingListResponse = {
  findings?: FindingOut[];
  counts?: Record<string, unknown>;
};

export type FindingOut = {
  id: string;
  title: string;
  detail: string;
  category: string;
  severity: string;
  source: string;
  status: string;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  decided_at?: string | null;
  note?: string | null;
  external_ref?: string | null;
};

export type FindingReportRequest = {
  title: string;
  detail?: string;
  category?: string;
  severity?: string;
  source?: string;
};

export type FindingReportResponse = {
  id: string;
  title: string;
  detail: string;
  category: string;
  severity: string;
  source: string;
  status: string;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  decided_at?: string | null;
  note?: string | null;
  external_ref?: string | null;
  repeat: boolean;
  reopened: boolean;
};

export type ForgettingCurveResponse = {
  memory_id: string;
  pinned: boolean;
  stability_hours: number;
  recall_count: number;
  current_retention: number;
  curve?: Record<string, unknown>[];
};

export type GuardCheckResponse = {
  decision: string;
  matched_rules?: GuardRuleMatch[];
  reason: string;
  checked_rules: number;
  tool_name?: string;
  project?: string | null;
};

export type GuardMistakeResponse = {
  rule_id: string;
  violation_id: string;
  statement: string;
  pinned: boolean;
  severity: string;
  total_violations: number;
};

export type GuardRuleListResponse = {
  rules?: GuardRuleOut[];
};

export type GuardRuleMatch = {
  rule_id?: string | null;
  statement: string;
  severity: string;
  score: number;
  matched_terms?: string[];
};

export type GuardRuleOut = {
  id: string;
  statement: string;
  importance: number;
  severity: string;
  task: string;
  correct_action: string;
  root_cause: string;
  project?: string | null;
  created_at: string;
};

export type GuardViolationListResponse = {
  violations?: GuardViolationOut[];
};

export type GuardViolationOut = {
  id: string;
  rule_id: string;
  task?: string | null;
  wrong_action: string;
  root_cause?: string | null;
  tool_name?: string | null;
  severity: string;
  source: string;
  occurred_at: string;
  resolved?: number | null;
  resolution?: string | null;
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

export type HeldAdmitResponse = {
  ok: boolean;
  error?: string | null;
  status?: string | null;
  memory?: MemoryOut | null;
  held_id?: string | null;
};

export type HeldMemoryListResponse = {
  held?: HeldMemoryOut[];
  waiting: number;
};

export type HeldMemoryOut = {
  id: string;
  content: string;
  importance: number;
  tags_json: string;
  session_id?: string | null;
  project?: string | null;
  source?: string | null;
  memory_type: string;
  pinned?: number | null;
  metadata_json: string;
  reasons_json: string;
  max_similarity: number;
  status: string;
  created_at: string;
  decided_at?: string | null;
  admitted_memory_id?: string | null;
};

export type ImportMemoriesResponse = {
  imported: number;
  redacted: number;
  duplicates: number;
  held: number;
  errors: number;
  gated: boolean;
};

export type ImportRequest = {
  data: Record<string, unknown>[];
};

export type LibrarianActionOut = {
  action: Record<string, unknown>;
  result: Record<string, unknown>;
};

export type LibrarianAgentOut = {
  agent: string;
  levh_connected: boolean;
  configs?: LibrarianConfigOut[];
};

export type LibrarianChatResponse = {
  answer: string;
  backend: string;
  actions?: LibrarianActionOut[];
};

export type LibrarianConfigOut = {
  config: string;
  levh_connected: boolean;
};

export type LibrarianScanResponse = {
  at: string;
  agents?: LibrarianAgentOut[];
  activity: Record<string, unknown>;
  findings_recorded: number;
};

export type LibrarianStatusResponse = {
  at: string;
  agents?: LibrarianAgentOut[];
  activity: Record<string, unknown>;
};

export type LowTrustItemOut = {
  memory_id: string;
  confidence: number;
  label: string;
};

export type LowTrustResponse = {
  low_trust?: LowTrustItemOut[];
};

export type MeetingAttendeeOut = {
  name: string;
  email?: string | null;
  last_seen: string;
  interaction_count: number;
  recent?: Record<string, unknown>[];
};

export type MeetingPrepBody = {
  generated_at: string;
  meeting?: Record<string, unknown> | null;
  reason?: string | null;
  people?: MeetingAttendeeOut[];
  open_commitments?: BriefingCommitmentOut[];
};

export type MeetingPrepResponse = {
  meeting_prep: MeetingPrepBody;
  recent_decisions?: DecisionOut[];
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
  valid_from?: string | null;
  valid_to?: string | null;
  superseded_by?: string | null;
};

export type MemoryDeleteResponse = {
  deleted: boolean;
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
  valid_from?: string | null;
  valid_to?: string | null;
  superseded_by?: string | null;
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

export type NameCountOut = {
  name: string;
  memory_count?: number | null;
  count?: number | null;
  last_used?: string | null;
};

export type OnboardingCheckOut = {
  id: string;
  status: string;
  message: string;
};

export type OnboardingMCPConfigRequest = {
  client?: string;
  profile?: string;
};

export type OnboardingMcpConfigResponse = {
  client: string;
  platform: string;
  profile: string;
  tool_count: number;
  profiles_are_security_boundary: boolean;
  warning: string;
  onboarding_receipt_written: boolean;
  onboarding_ready: boolean;
  config?: Record<string, unknown>;
  config_text?: string;
  config_path?: string | null;
};

export type OnboardingStatusResponse = {
  first_run: boolean;
  ready: boolean;
  memory_count: number;
  database_initialized: boolean;
  embedder_mode: string;
  mcp_default_profile: string;
  mcp_configured: boolean;
  mcp_client?: string | null;
  mcp_profile: string;
  profile_counts: Record<string, unknown>;
  clients?: Record<string, unknown>[];
  profiles_are_security_boundary: boolean;
  profile_warning: string;
  dogfood_enabled: boolean;
  dogfood_journal: Record<string, unknown>;
  dogfood_statement: string;
  demo_seeded: boolean;
  demo_memory_count: number;
  recommended_next_step: string;
  checks?: OnboardingCheckOut[];
};

export type OrganizationListResponse = {
  organizations?: OrganizationOut[];
};

export type OrganizationOut = {
  key: string;
  name: string;
  domain: string;
  memory_count: number;
  people?: string[];
  person_count: number;
  sources?: string[];
  last_seen: string;
};

export type OrganizationProfileResponse = {
  organization: OrganizationOut;
  memories?: MemoryOut[];
};

export type PersonListResponse = {
  people?: PersonOut[];
};

export type PersonOut = {
  key: string;
  name: string;
  email?: string | null;
  memory_count: number;
  sources?: string[];
  last_seen: string;
};

export type PersonProfileResponse = {
  person: PersonOut;
  memories?: MemoryOut[];
};

export type PinRequest = {
  pinned?: boolean;
};

export type ProjectListResponse = {
  projects?: NameCountOut[];
};

export type PurgeMemoryResponse = {
  memory_id: string;
  existed: boolean;
  purged: boolean;
  residue: Record<string, unknown>;
};

export type ReadyzResponse = {
  status: string;
  service: string;
  database: string;
  embedder_mode: string;
  embedder_ready: boolean;
  derived_state_current: boolean;
  reasons?: string[];
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
  as_of?: string | null;
  include_superseded?: boolean;
};

export type RecallResponse = {
  memories?: MemoryOut[];
  scores?: number[];
  breakdowns?: Record<string, unknown>[];
  diagnosis?: Record<string, unknown> | null;
};

export type RedactAllRequest = {
  dry_run?: boolean;
};

export type RedactAllResponse = {
  dry_run: boolean;
  scanned: number;
  flagged: number;
  redacted: number;
  items?: SecretAuditItemOut[];
};

export type RedactMemoryResponse = {
  ok: boolean;
  redacted: boolean;
  secrets?: unknown[];
  memory_id: string;
  error?: string | null;
};

export type RelatedMemoriesResponse = {
  memory_id: string;
  related?: MemoryOut[];
};

export type RestoreRequest = {
  content_b64: string;
  passphrase?: string;
  replace?: boolean;
};

export type RestoreResponse = {
  memories: number;
  sessions: number;
  attachments: number;
  replace: boolean;
  safety_backup_path?: string | null;
  attachment_files_written?: number | null;
  attachment_files_failed?: number | null;
  attachments_by_reference?: number | null;
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

export type ScoreBreakdownResponse = {
  score: number;
  components: Record<string, unknown>;
  weights: Record<string, unknown>;
};

export type SecretAuditItemOut = {
  id: string;
  secret_types?: string[];
  preview: string;
  project?: string | null;
  source?: string | null;
};

export type SecretAuditResponse = {
  audit: Record<string, unknown>;
};

export type ServerConfigResponse = {
  db_path: string;
  embedder_mode: string;
  requested_embedder_mode: string;
  embedder_fallback_reason?: string | null;
  embedder_dimension?: number | null;
  short_term_max: number;
  weights: Record<string, unknown>;
  decay_half_life_hours: number;
  reinforcement_gain: number;
  max_stability_hours: number;
  auto_summarize_sessions: boolean;
  outbound: Record<string, unknown>;
  recall_log: Record<string, unknown>;
  version: string;
};

export type Session = {
  id?: string;
  name?: string;
  status?: SessionStatus;
  metadata?: Record<string, unknown>;
  memory_count?: number;
  created_at?: string;
  ended_at?: string | null;
};

export type SessionDeleteResponse = {
  ok: boolean;
  session_id?: string | null;
  error?: string | null;
  memory_count?: number | null;
  memories_detached?: number | null;
  memories_deleted?: number | null;
};

export type SessionStatus = "active" | "ended";

export type SourceListResponse = {
  sources?: NameCountOut[];
};

export type StoreDecision = {
  action: string;
  reasons?: string[];
  reason_codes?: string[];
  redacted_content?: string | null;
  redacted: boolean;
  secrets?: unknown[];
  max_similarity: number;
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

export type SummarizeSessionResponse = {
  summarized: boolean;
  reason?: string | null;
  summary?: MemoryOut | null;
};

export type TagListResponse = {
  tags?: NameCountOut[];
};

export type TimelineGroupOut = {
  date: string;
  count: number;
  items?: TimelineItemOut[];
};

export type TimelineItemOut = {
  id: string;
  summary: string;
  source?: string | null;
  memory_type: string;
};

export type TimelineResponse = {
  timeline?: TimelineGroupOut[];
};

export type TrustBreakdownResponse = {
  memory_id: string;
  confidence: number;
  label: string;
  components: Record<string, unknown>;
  evidence: Record<string, unknown>;
  explanation: string[];
};

export type TrustRecomputeResponse = {
  scored: number;
  by_label: Record<string, unknown>;
};

export type UpdateRequest = {
  content?: string | null;
  importance?: number | null;
  tags?: string[] | null;
  project?: string | null;
  pinned?: boolean | null;
};

export type UploadedFileResponse = {
  path: string;
  filename: string;
  bytes: number;
};

export type UsageBillingResponse = {
  summary: Record<string, unknown>;
  by_agent?: Record<string, unknown>[];
};

export type ValidationError = {
  loc: string | number[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
};

export type VerifyAllAttachmentsResponse = {
  ok: number;
  missing: number;
  changed: number;
};
