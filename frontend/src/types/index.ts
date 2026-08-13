export interface Memory {
  id: string;
  content: string;
  importance: number;
  frequency: number;
  tags: string[];
  session_id: string | null;
  project: string | null;
  source: string | null;
  pinned: boolean;
  memory_type: "short_term" | "episodic";
  metadata: Record<string, unknown>;
  hscore: number | null;
  created_at: string;
  accessed_at: string;
  stability_hours: number;
  recall_count: number;
}

export interface Session {
  id: string;
  name: string;
  metadata: Record<string, unknown>;
  status: "active" | "ended";
  memory_count: number;
  created_at: string;
  ended_at: string | null;
}

export interface Stats {
  total_memories: number;
  short_term_count: number;
  episodic_count: number;
  avg_hscore: number;
  avg_importance: number;
  sessions_count: number;
  pinned_count: number;
  projects_count: number;
}

export interface Project {
  name: string;
  memory_count: number;
  last_used: string | null;
}

export interface Source {
  name: string;
  memory_count: number;
  last_used: string | null;
}

export interface Person {
  key: string;
  name: string;
  email: string | null;
  memory_count: number;
  sources: string[];
  last_seen: string;
}

export interface TagCount {
  name: string;
  count: number;
}

export interface Organization {
  key: string;
  name: string;
  domain: string;
  memory_count: number;
  people: string[];
  person_count: number;
  sources: string[];
  last_seen: string;
}

export interface EntityRow {
  id: string;
  type: string;
  ekey: string;
  name: string;
  updated_at: string | null;
  mentions: number;
}

export interface Decision {
  id: string;
  text: string;
  source: string | null;
  date: string;
  project: string | null;
}

export interface ReviewItem {
  id: string;
  content: string;
  project: string | null;
  source: string | null;
  importance: number;
  hscore: number | null;
  retention: number;
  stability_hours: number;
  last_accessed: string;
  recall_count: number;
  review_count: number;
  reason: string;
}

export type ReviewAction = "keep" | "reinforce" | "weaken" | "pin" | "forget" | "snooze";

export interface MeetingPrepPerson {
  name: string;
  email: string | null;
  last_seen: string;
  interaction_count: number;
  recent: { id: string; summary: string; date: string }[];
}

export interface MeetingPrep {
  generated_at: string;
  reason: string;
  meeting: {
    id: string;
    title: string;
    when: string;
    project: string | null;
    source: string | null;
    attendees: string[];
  } | null;
  people: MeetingPrepPerson[];
  open_commitments: { id: string; text: string; date: string; project: string | null }[];
  recent_decisions: Decision[];
}

export interface TimelineItem {
  id: string;
  summary: string;
  source: string | null;
  memory_type: string;
}

export interface TimelineDay {
  date: string;
  count: number;
  items: TimelineItem[];
}

export interface BriefingItem {
  id: string;
  summary: string;
  source: string | null;
  time: string;
}

export interface Commitment {
  id: string;
  text: string;
  source: string | null;
  date: string;
  project: string | null;
}

export interface FadingItem {
  id: string;
  summary: string;
  retention: number;
}

export interface Briefing {
  generated_at: string;
  today: BriefingItem[];
  commitments: Commitment[];
  fading: FadingItem[];
  counts: {
    today: number;
    commitments: number;
    fading: number;
    recent_total: number;
  };
}

export interface ScoreBreakdown {
  score: number;
  components: {
    similarity_penalty: number;
    decay_penalty: number;
    importance_penalty: number;
    frequency_penalty: number;
  };
  weights: { alpha: number; beta: number; gamma: number; delta: number };
}

export interface TrustBreakdown {
  memory_id: string;
  confidence: number;
  label: string;
  components: {
    source_score: number;
    corroboration_score: number;
    review_score: number;
    recency_score: number;
    risk_penalty: number;
  };
  evidence: {
    source: string;
    linked_entities: string[];
    corroborating_memories: string[];
    distinct_source_types: string[];
    conflict_status?: string | null;
  };
  explanation: string[];
}

// Deterministic conflict-CANDIDATE review signal — never a verdict, never an
// automatic delete. See server/core/conflict.py.
export interface ConflictCandidate {
  id: string;
  memory_id_a: string;
  memory_id_b: string;
  signal_type: string;
  confidence: number;
  status: string;
  shared_entities: string[];
  explanation: Record<string, any>;
  created_at: string | null;
  reviewed_at: string | null;
}

export interface ServerConfig {
  db_path: string;
  embedder_mode: string;
  embedder_dimension: number | null;
  short_term_max: number;
  weights: { alpha: number; beta: number; gamma: number; delta: number };
  decay_half_life_hours: number;
  reinforcement_gain: number;
  max_stability_hours: number;
  auto_summarize_sessions: boolean;
  version: string;
}

export interface ForgettingCurve {
  memory_id: string;
  pinned: boolean;
  stability_hours: number;
  recall_count: number;
  current_retention: number;
  curve: { day: number; retention: number }[];
}

export interface Connector {
  name: string;
  description: string;
  required_config_keys: string[];
}

export interface SyncState {
  source_key: string;
  connector: string;
  project: string | null;
  last_synced_at: string;
  last_fetched: number;
  last_stored: number;
  total_stored: number;
  runs: number;
}

export interface RelatedMemory extends Memory {
  similarity: number;
}

export interface BenchmarkResult {
  embedder_mode: string;
  queries: number;
  "hit@1": number;
  "hit@3": number;
  "hit@5": number;
  mrr: number;
}

export interface SummarizeSessionResult {
  summarized: boolean;
  reason?: string;
  summary?: Memory;
}

export interface LiveEvent {
  event: string;
  payload: Record<string, any>;
  receivedAt: number;
}

export interface OnboardingCheck {
  id: string;
  status: "pass" | "pending" | "warn" | "fail";
  message: string;
}

export interface OnboardingStatus {
  first_run: boolean;
  ready: boolean;
  memory_count: number;
  database_initialized: boolean;
  embedder_mode: string;
  mcp_default_profile: string;
  mcp_configured: boolean;
  mcp_client: string | null;
  mcp_profile: string;
  profile_counts: Record<string, number>;
  clients: { id: string; platform: string; description: string }[];
  profiles_are_security_boundary: boolean;
  profile_warning: string;
  dogfood_enabled: boolean;
  dogfood_journal: { name: string; scope: string };
  dogfood_statement: string;
  demo_seeded: boolean;
  demo_memory_count: number;
  recommended_next_step: string;
  checks: OnboardingCheck[];
}

// Mistake guard: a rule is what was learned, a violation is the incident
// that taught it. Recording happens through the MCP tool; the dashboard
// reads them back.
export interface GuardRule {
  id: string;
  statement: string;
  importance: number;
  severity: string;
  task: string;
  correct_action: string;
  root_cause: string;
  project: string | null;
  created_at: string;
}

export interface GuardViolation {
  id: string;
  rule_id: string;
  task: string | null;
  wrong_action: string;
  root_cause: string | null;
  tool_name: string | null;
  severity: string;
  source: string;
  occurred_at: string;
  resolved: number;
  resolution: string | null;
}
