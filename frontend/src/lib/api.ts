import type {
  BenchmarkResult,
  Briefing,
  Connector,
  ConflictCandidate,
  Decision,
  EntityRow,
  ForgettingCurve,
  MeetingPrep,
  Memory,
  Organization,
  OnboardingStatus,
  Person,
  ReviewAction,
  ReviewItem,
  Project,
  RelatedMemory,
  ScoreBreakdown,
  ServerConfig,
  Session,
  Source,
  Stats,
  SummarizeSessionResult,
  SyncState,
  TagCount,
  TimelineDay,
  TrustBreakdown,
} from "@/types";
import { getToken } from "./token";

// Same-origin by default (dashboard is served by the FastAPI server).
// Set NEXT_PUBLIC_API_URL only when running `next dev` against a separate API.
const API = process.env.NEXT_PUBLIC_API_URL || "";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      // Token gate: attach when set on the server (see /lib/token). A
      // user-supplied header for the same key still wins via spread order.
      ...(token ? { "X-LEVH-Token": token } : {}),
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = `${res.status}: ${body.detail}`;
    } catch {}
    throw new Error(`API error ${detail}`);
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(`API error: invalid response from ${path}`);
  }
}

export function wsUrl(): string {
  const token = getToken();
  // The WebSocket handshake can't set custom headers from the browser, so the
  // token rides as a query param when present (the backend accepts either).
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  if (API) return API.replace(/^http/, "ws") + "/ws/memory" + qs;
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/memory${qs}`;
}

export const api = {
  // Health — never gated; auth_required reflects whether the server needs a token.
  health: () =>
    fetchApi<{ status: string; service?: string; auth_required?: boolean }>("/api/health"),
  config: () => fetchApi<ServerConfig>("/api/config"),

  // Memories
  storeMemory: (data: {
    content: string;
    importance?: number;
    tags?: string[];
    session_id?: string;
    project?: string;
    source?: string;
    pinned?: boolean;
    memory_type?: string;
  }) => fetchApi<Memory>("/api/memories", { method: "POST", body: JSON.stringify(data) }),

  listMemories: (params?: {
    memory_type?: string;
    session_id?: string;
    project?: string;
    source?: string;
    tag?: string;
    pinned?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }) => {
    const clean = Object.fromEntries(
      Object.entries(params ?? {}).filter(([, v]) => v !== undefined && v !== "")
    );
    const qs = new URLSearchParams(clean as Record<string, string>).toString();
    return fetchApi<Memory[]>(`/api/memories${qs ? `?${qs}` : ""}`);
  },

  getMemory: (id: string) => fetchApi<Memory>(`/api/memories/${id}`),

  updateMemory: (
    id: string,
    data: {
      content?: string;
      importance?: number;
      tags?: string[];
      project?: string;
      pinned?: boolean;
    }
  ) => fetchApi<Memory>(`/api/memories/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  pinMemory: (id: string, pinned: boolean) =>
    fetchApi<Memory>(`/api/memories/${id}/pin`, {
      method: "PATCH",
      body: JSON.stringify({ pinned }),
    }),

  ask: (question: string, top_k = 6, project?: string) =>
    fetchApi<{
      question: string;
      answer: string;
      sources: {
        n: number;
        id: string;
        content: string;
        created_at: string;
        project: string | null;
        score: number;
      }[];
    }>("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question, top_k, project: project || null }),
    }),

  reinforceMemory: (id: string) =>
    fetchApi<Memory>(`/api/memories/${id}/reinforce`, { method: "POST" }),

  memoryFeedback: (id: string, helpful: boolean) =>
    fetchApi<Memory>(`/api/memories/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ helpful }),
    }),

  listFading: (threshold = 0.35, limit = 10) =>
    fetchApi<(Memory & { retention: number })[]>(
      `/api/memories/fading?threshold=${threshold}&limit=${limit}`
    ),

  fetchForgettingCurve: (id: string, days = 30) =>
    fetchApi<ForgettingCurve>(`/api/memories/${id}/forgetting-curve?days=${days}`),

  deleteMemory: (id: string) =>
    fetchApi<{ deleted: boolean }>(`/api/memories/${id}`, { method: "DELETE" }),

  // reinforce=false by default for dashboard search previews: browsing
  // memory in the UI shouldn't reset decay clocks or inflate frequency the
  // way a genuine AI recall does — only explicit AI tool calls should do that.
  recallMemories: (query: string, top_k = 10, project?: string, reinforce = false) =>
    fetchApi<{ memories: Memory[]; scores: number[] }>("/api/memories/recall", {
      method: "POST",
      body: JSON.stringify({ query, top_k, project: project || null, reinforce }),
    }),

  relatedMemories: (id: string, top_k = 5) =>
    fetchApi<{ memory_id: string; related: RelatedMemory[] }>(
      `/api/memories/${id}/related?top_k=${top_k}`
    ),

  consolidate: (session_id?: string) =>
    fetchApi<{ consolidated: number }>(
      `/api/memories/consolidate${session_id ? `?session_id=${session_id}` : ""}`,
      { method: "POST" }
    ),

  dedupe: (dry_run: boolean, similarity_threshold = 0.95, project?: string) =>
    fetchApi<{ dry_run: boolean; removed?: number; duplicates?: number; groups?: Memory[][] }>(
      "/api/memories/dedupe",
      {
        method: "POST",
        body: JSON.stringify({ dry_run, similarity_threshold, project: project || null }),
      }
    ),

  exportMemories: (session_id?: string) =>
    fetchApi<{ count: number; data: Memory[] }>(
      `/api/memories/export${session_id ? `?session_id=${session_id}` : ""}`,
      { method: "POST" }
    ),

  importMemories: (data: unknown[]) =>
    fetchApi<{ imported: number }>("/api/memories/import", {
      method: "POST",
      body: JSON.stringify({ data }),
    }),

  consolidateSimilar: (dry_run: boolean, min_age_days = 7) =>
    fetchApi<{
      dry_run: boolean;
      clusters_found: number;
      consolidated: number;
      archived: number;
      clusters: { size: number; project: string | null; summary: string }[];
    }>("/api/memories/consolidate-similar", {
      method: "POST",
      body: JSON.stringify({ dry_run, min_age_days }),
    }),

  auditSecrets: () =>
    fetchApi<{
      audit: {
        scanned: number;
        flagged: number;
        items: { id: string; secrets: string[]; preview: string }[];
      };
    }>("/api/memories/audit-secrets"),

  redactAll: (dry_run: boolean) =>
    fetchApi<{ dry_run: boolean; scanned: number; flagged: number; redacted: number }>(
      "/api/memories/redact-all",
      { method: "POST", body: JSON.stringify({ dry_run }) }
    ),

  memoryTrust: (id: string) => fetchApi<TrustBreakdown>(`/api/memories/${id}/trust`),

  recomputeTrust: () =>
    fetchApi<{ scored: number; by_label: Record<string, number> }>(
      "/api/memories/trust/recompute",
      { method: "POST" }
    ),

  lowTrust: (threshold = 0.4, limit = 50) =>
    fetchApi<{ low_trust: TrustBreakdown[] }>(
      `/api/memories/low-trust?threshold=${threshold}&limit=${limit}`
    ),

  evaluateAdmission: (content: string, project = "") =>
    fetchApi<{
      decision: {
        action: string;
        reasons: string[];
        redacted: boolean;
        secrets: string[];
        redacted_content: string;
        max_similarity: number;
      };
    }>("/api/memories/evaluate-admission", {
      method: "POST",
      body: JSON.stringify({ content, project: project || null }),
    }),

  // Sessions
  createSession: (name: string) =>
    fetchApi<Session>("/api/sessions", { method: "POST", body: JSON.stringify({ name }) }),

  listSessions: (limit = 50) => fetchApi<Session[]>(`/api/sessions?limit=${limit}`),

  endSession: (id: string) =>
    fetchApi<Session>(`/api/sessions/${id}/end`, { method: "PATCH" }),

  summarizeSession: (id: string) =>
    fetchApi<SummarizeSessionResult>(`/api/sessions/${id}/summarize`, { method: "POST" }),

  // Projects / Sources / Tags
  listProjects: () => fetchApi<{ projects: Project[] }>("/api/projects"),
  listSources: () => fetchApi<{ sources: Source[] }>("/api/sources"),
  listTags: () => fetchApi<{ tags: TagCount[] }>("/api/tags"),
  listPeople: () => fetchApi<{ people: Person[] }>("/api/people"),
  getPerson: (key: string) =>
    fetchApi<{ person: Person; memories: Memory[] }>(`/api/people/${encodeURIComponent(key)}`),

  // Organizations
  listOrganizations: () =>
    fetchApi<{ organizations: Organization[] }>("/api/organizations"),
  getOrganization: (key: string) =>
    fetchApi<{ organization: Organization; memories: Memory[] }>(
      `/api/organizations/${encodeURIComponent(key)}`
    ),

  // Decisions
  listDecisions: (days = 90) =>
    fetchApi<{ decisions: Decision[] }>(`/api/decisions?days=${days}`),

  // Entity knowledge graph
  reindexEntities: () =>
    fetchApi<{ memories: number; entities: number; links: number; by_type: Record<string, number> }>(
      "/api/entities/reindex",
      { method: "POST" }
    ),
  entityStats: () => fetchApi<{ by_type: Record<string, number> }>("/api/entities/stats"),
  listEntities: (type = "", limit = 200) => {
    const qs = new URLSearchParams();
    if (type) qs.set("type", type);
    qs.set("limit", String(limit));
    return fetchApi<{ entities: EntityRow[] }>(`/api/entities?${qs}`);
  },
  getEntity: (id: string) =>
    fetchApi<{ entity: EntityRow; memories: Memory[]; related: { id: string; type: string; name: string; shared: number }[] }>(
      `/api/entities/${encodeURIComponent(id)}`
    ),

  // Spaced-repetition review
  reviewQueue: (threshold = 0.5, limit = 50) =>
    fetchApi<{ review: ReviewItem[] }>(
      `/api/memories/review?threshold=${threshold}&limit=${limit}`
    ),
  reviewMemory: (id: string, action: ReviewAction, snooze_days = 7, reason = "") =>
    fetchApi<{
      ok: boolean;
      action: string;
      memory_id: string;
      removed?: boolean;
      review_count?: number;
      review_due_at?: string | null;
      pinned?: boolean;
    }>(`/api/memories/${id}/review`, {
      method: "POST",
      body: JSON.stringify({ action, snooze_days, reason }),
    }),

  // Conflict candidates (deterministic review signal — never a verdict)
  detectConflicts: () =>
    fetchApi<{ new_candidates: number; pairs_examined: number; open_total: number }>(
      "/api/conflicts/detect",
      { method: "POST" }
    ),
  listConflicts: (status = "open", limit = 100) =>
    fetchApi<{ conflicts: ConflictCandidate[] }>(
      `/api/conflicts?status=${status}&limit=${limit}`
    ),
  reviewConflict: (id: string, action: string) =>
    fetchApi<{ ok: boolean; action: string; conflict: ConflictCandidate }>(
      `/api/conflicts/${encodeURIComponent(id)}/review`,
      { method: "POST", body: JSON.stringify({ action }) }
    ),

  // Timeline
  timeline: (days = 30) => fetchApi<{ timeline: TimelineDay[] }>(`/api/timeline?days=${days}`),

  // Briefing
  briefing: (days = 7) => fetchApi<{ briefing: Briefing }>(`/api/briefing?days=${days}`),

  // Meeting prep
  meetingPrep: (query = "", withinDays = 14) => {
    const qs = new URLSearchParams();
    if (query) qs.set("query", query);
    qs.set("within_days", String(withinDays));
    return fetchApi<{ meeting_prep: MeetingPrep }>(`/api/meeting-prep?${qs.toString()}`);
  },

  // Backup / Restore — backup returns a binary blob, so raw fetch (not fetchApi).
  backup: async (passphrase?: string): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`${API}/api/backup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase: passphrase || "" }),
    });
    if (!res.ok) throw new Error(`API error ${res.status}`);
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "levh-backup.json";
    return { blob: await res.blob(), filename };
  },
  restore: (content_b64: string, passphrase?: string, replace = false) =>
    fetchApi<{ memories: number; sessions: number; replace: boolean }>("/api/restore", {
      method: "POST",
      body: JSON.stringify({ content_b64, passphrase: passphrase || "", replace }),
    }),

  // Full export — memories + entity graph + trust scores + conflicts.
  // Binary response, so raw fetch (not fetchApi).
  exportFull: async (format: "json" | "sqlite" | "pdf"): Promise<{ blob: Blob; filename: string }> => {
    const token = getToken();
    const res = await fetch(`${API}/api/export/full.${format}`, {
      headers: token ? { "X-LEVH-Token": token } : {},
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = `${res.status}: ${body.detail}`;
      } catch {}
      throw new Error(`API error ${detail}`);
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : `levh-full-export.${format}`;
    return { blob: await res.blob(), filename };
  },

  // Context
  generateContextFile: (project: string | null, style: "claude" | "cursor") =>
    fetchApi<{ filename: string; content: string }>("/api/context-file", {
      method: "POST",
      body: JSON.stringify({ project, style }),
    }),

  // Stats
  getStats: () => fetchApi<Stats>("/api/stats"),

  // Onboarding: seed a deterministic demo corpus into an empty store.
  seedDemo: (force = false) =>
    fetchApi<{
      seeded: number;
      skipped: boolean;
      reason?: string;
      existing?: number;
      entities?: number;
      entity_links?: number;
      trust_scored?: number;
      conflict_candidates?: number;
    }>(`/api/seed-demo${force ? "?force=true" : ""}`, { method: "POST" }),

  onboardingStatus: () => fetchApi<OnboardingStatus>("/api/onboarding/status"),

  onboardingMcpConfig: (client: string, profile = "work") =>
    fetchApi<{
      client: string;
      platform: string;
      profile: string;
      tool_count: number;
      profiles_are_security_boundary: boolean;
      warning: string;
      config: Record<string, unknown>;
    }>("/api/onboarding/mcp-config", {
      method: "POST",
      body: JSON.stringify({ client, profile }),
    }),

  removeDemoData: () =>
    fetchApi<{
      removed: number;
      remaining: number;
      fully_purged: boolean;
    }>("/api/onboarding/remove-demo", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }),

  // Score Breakdown
  fetchScoreBreakdown: (memoryId: string, query: string) =>
    fetchApi<ScoreBreakdown>(
      `/api/memories/${memoryId}/score-breakdown?query=${encodeURIComponent(query)}`
    ),

  // Recall quality benchmark (hit@k / MRR on a labelled corpus)
  runBenchmark: (embedder_mode?: string, top_k = 5) =>
    fetchApi<BenchmarkResult>(
      `/api/benchmark/recall?embedder_mode=${embedder_mode ?? ""}&top_k=${top_k}`,
      { method: "POST" }
    ),

  // Connectors
  listConnectors: () => fetchApi<{ connectors: Connector[] }>("/api/connectors"),
  connectorImport: (connector: string, config: Record<string, unknown>, project?: string) =>
    fetchApi<{ connector: string; fetched: number; stored: number }>(
      "/api/connectors/import",
      {
        method: "POST",
        body: JSON.stringify({ connector, config, project: project || null }),
      }
    ),

  // Connector Framework v2 — gate-filtered incremental sync
  connectorSync: (
    connector: string,
    config: Record<string, unknown>,
    project?: string,
    useGate = true
  ) =>
    fetchApi<{
      connector: string;
      fetched: number;
      stored: number;
      redacted: number;
      duplicates: number;
      held: number;
      errors: number;
    }>("/api/connectors/sync", {
      method: "POST",
      body: JSON.stringify({
        connector,
        config,
        project: project || null,
        use_gate: useGate,
      }),
    }),
  connectorSyncState: () =>
    fetchApi<{ sync_state: SyncState[] }>("/api/connectors/sync-state"),
};
