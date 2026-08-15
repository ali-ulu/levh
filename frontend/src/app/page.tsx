"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { AskPanel } from "@/components/ask-panel";
import { KnowledgeConstellation } from "@/components/knowledge-constellation";
import { LiveFeed } from "@/components/live-feed";
import { MemoryDetailDrawer } from "@/components/memory-detail-drawer";
import { MemoryQuickAdd } from "@/components/memory-quick-add";
import { OnboardingEmptyState } from "@/components/onboarding-empty-state";
import type {
  Briefing,
  ConflictCandidate,
  Memory,
  OnboardingStatus,
  Organization,
  Person,
  ReviewItem,
  Stats,
  SyncState,
} from "@/types";
import {
  ArrowRight,
  Building2,
  CheckCircle2,
  CircleAlert,
  Database,
  FolderGit2,
  GitCompareArrows,
  Layers3,
  Network,
  Pin,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Users,
  Waves,
} from "lucide-react";

function fmt(value?: number | null) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en", { notation: value > 9999 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function relativeDate(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "";
  const delta = Math.max(0, Date.now() - time);
  const minutes = Math.floor(delta / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null);
  const [recent, setRecent] = useState<Memory[]>([]);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [conflicts, setConflicts] = useState<ConflictCandidate[]>([]);
  const [review, setReview] = useState<ReviewItem[]>([]);
  const [entityCounts, setEntityCounts] = useState<Record<string, number>>({});
  const [people, setPeople] = useState<Person[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [syncState, setSyncState] = useState<SyncState[]>([]);
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    const [s, m, o, b, c, r, e, p, org, sync] = await Promise.all([
      api.getStats().catch(() => null),
      api.listMemories({ limit: 8 }).catch(() => [] as Memory[]),
      api.onboardingStatus().catch(() => null),
      api.briefing(7).catch(() => ({ briefing: null as Briefing | null })),
      api.listConflicts("open", 4).catch(() => ({ conflicts: [] as ConflictCandidate[] })),
      api.reviewQueue(0.5, 6).catch(() => ({ review: [] as ReviewItem[] })),
      api.entityStats().catch(() => ({ by_type: {} as Record<string, number> })),
      api.listPeople().catch(() => ({ people: [] as Person[] })),
      api.listOrganizations().catch(() => ({ organizations: [] as Organization[] })),
      api.connectorSyncState().catch(() => ({ sync_state: [] as SyncState[] })),
    ]);
    setStats(s);
    setRecent(m);
    setOnboarding(o);
    setBriefing(b.briefing);
    setConflicts(c.conflicts);
    setReview(r.review);
    setEntityCounts(e.by_type);
    setPeople(p.people.slice(0, 4));
    setOrganizations(org.organizations.slice(0, 3));
    setSyncState(sync.sync_state);
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 20000);
    return () => clearInterval(timer);
  }, [load]);

  const uniqueConnectors = useMemo(
    () => new Set(syncState.map((item) => item.connector)).size,
    [syncState]
  );

  const hscoreHealth = stats?.avg_hscore === undefined || stats?.avg_hscore === null
    ? null
    : Math.max(0, Math.round((1 - Math.min(1, stats.avg_hscore)) * 100));

  const metrics = [
    { label: "Total memories", value: fmt(stats?.total_memories), note: `${fmt(stats?.episodic_count)} episodic`, icon: Database, tone: "blue" },
    { label: "Active projects", value: fmt(stats?.projects_count), note: `${fmt(stats?.sessions_count)} sessions`, icon: FolderGit2, tone: "violet" },
    { label: "Open conflicts", value: fmt(conflicts.length), note: "deterministic candidates", icon: GitCompareArrows, tone: "rose" },
    { label: "H-score health", value: hscoreHealth === null ? "—" : `${hscoreHealth}%`, note: "derived recall quality", icon: ShieldCheck, tone: "emerald" },
    { label: "Connector sources", value: fmt(uniqueConnectors), note: `${fmt(syncState.length)} sync histories`, icon: Waves, tone: "cyan" },
    { label: "Review queue", value: fmt(review.length), note: "human-in-the-loop", icon: RefreshCw, tone: "amber" },
  ];

  return (
    <div className="space-y-5 sm:space-y-6">
      <section className="dashboard-hero relative overflow-hidden rounded-[26px] px-5 py-6 sm:px-7 sm:py-7">
        <div className="hero-wave" />
        <div className="relative z-10 flex flex-col justify-between gap-5 md:flex-row md:items-end">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <span className="pulse-dot" />
              Local memory fabric · all systems operational
            </div>
            <h1 className="max-w-3xl text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">
              System pulse: <span className="gradient-text">optimal flow</span>
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-base">
              Your work context, provenance, review signals, and connected knowledge—alive in one local continuity layer.
            </p>
          </div>
          <div className="hero-status-grid">
            <div><span>Storage</span><strong>Local</strong></div>
            <div><span>Embedding</span><strong>{onboarding?.embedder_mode ?? "—"}</strong></div>
            <div><span>Profile</span><strong>{onboarding?.mcp_profile ?? "work"}</strong></div>
          </div>
        </div>
      </section>

      {onboarding && (!onboarding.ready || onboarding.demo_seeded) && (
        <OnboardingEmptyState status={onboarding} onChanged={load} />
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <article key={metric.label} className={`metric-card tone-${metric.tone}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="metric-icon"><Icon className="h-4 w-4" /></div>
                <span className="metric-spark" />
              </div>
              <p className="mt-4 text-[11px] font-medium uppercase tracking-[0.13em] text-muted-foreground">{metric.label}</p>
              <div className="mt-1 text-2xl font-semibold tracking-[-0.04em] tabular-nums">{metric.value}</div>
              <p className="mt-1 truncate text-[10px] text-muted-foreground">{metric.note}</p>
            </article>
          );
        })}
      </section>

      <section className="ask-shell">
        <AskPanel onViewSource={async (id) => {
          const memory = await api.getMemory(id).catch(() => null);
          if (memory) setSelectedMemory(memory);
        }} />
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(310px,.7fr)]">
        <div className="space-y-5">
          <div className="grid gap-5 lg:grid-cols-[minmax(0,.95fr)_minmax(0,1.05fr)]">
            <article className="premium-card rounded-[22px] border p-5 sm:p-6">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold tracking-[-0.02em]">Recent memories</h2>
                  <p className="mt-1 text-xs text-muted-foreground">Fresh context entering the fabric</p>
                </div>
                <Link href="/memories" className="text-link">View all <ArrowRight className="h-3.5 w-3.5" /></Link>
              </div>
              <div className="space-y-1">
                {recent.length === 0 ? (
                  <div className="empty-panel">No memories yet. Load demo data or capture your first memory.</div>
                ) : recent.map((memory) => (
                  <button key={memory.id} onClick={() => setSelectedMemory(memory)} className="memory-row group">
                    <span className="memory-source"><Layers3 className="h-3.5 w-3.5" /></span>
                    <span className="min-w-0 flex-1 text-left">
                      <span className="block truncate text-sm font-medium">{memory.content}</span>
                      <span className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                        <span>{memory.source || "local"}</span>
                        <span>·</span>
                        <span>{relativeDate(memory.created_at)}</span>
                        {memory.project && <><span>·</span><span className="truncate">{memory.project}</span></>}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {memory.pinned && <Pin className="h-3.5 w-3.5 text-amber-500" />}
                      <span className="score-chip">{memory.hscore == null ? "—" : memory.hscore.toFixed(2)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </article>

            <article className="premium-card rounded-[22px] border p-4 sm:p-5">
              <div className="mb-2 flex items-center justify-between px-1">
                <div>
                  <h2 className="text-base font-semibold tracking-[-0.02em]">Connected knowledge</h2>
                  <p className="mt-1 text-xs text-muted-foreground">A living map of your context</p>
                </div>
                <Link href="/graph" className="text-link">Open graph <ArrowRight className="h-3.5 w-3.5" /></Link>
              </div>
              <KnowledgeConstellation memories={stats?.total_memories ?? 0} entityCounts={entityCounts} />
            </article>
          </div>

          <article className="premium-card rounded-[22px] border p-5 sm:p-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold tracking-[-0.02em]">Context network</h2>
                <p className="mt-1 text-xs text-muted-foreground">People and organizations with the strongest memory footprint</p>
              </div>
              <div className="flex gap-2">
                <Link href="/people" className="mini-action"><Users className="h-3.5 w-3.5" /> People</Link>
                <Link href="/organizations" className="mini-action"><Building2 className="h-3.5 w-3.5" /> Orgs</Link>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {people.map((person, index) => (
                <Link key={person.key} href={`/people/?person=${encodeURIComponent(person.key)}`} className="identity-card">
                  <span className={`identity-avatar avatar-${index % 4}`}>{(person.name || "?").slice(0, 2).toUpperCase()}</span>
                  <span className="min-w-0">
                    <strong className="block truncate text-sm">{person.name}</strong>
                    <small className="text-[10px] text-muted-foreground">{person.memory_count} memories</small>
                  </span>
                </Link>
              ))}
              {people.length === 0 && organizations.slice(0, 4).map((org, index) => (
                <Link key={org.key} href="/organizations" className="identity-card">
                  <span className={`identity-avatar avatar-${index % 4}`}>{(org.name || "?").slice(0, 2).toUpperCase()}</span>
                  <span className="min-w-0">
                    <strong className="block truncate text-sm">{org.name}</strong>
                    <small className="text-[10px] text-muted-foreground">{org.memory_count} memories</small>
                  </span>
                </Link>
              ))}
              {people.length === 0 && organizations.length === 0 && (
                <div className="empty-panel col-span-full">Entities appear here after indexing people and organizations.</div>
              )}
            </div>
          </article>
        </div>

        <aside className="space-y-5">
          <article className="premium-card rounded-[22px] border p-5">
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="section-icon tone-amber"><Sparkles className="h-4 w-4" /></span>
                <div>
                  <h2 className="text-sm font-semibold">Daily briefing</h2>
                  <p className="text-[10px] text-muted-foreground">What deserves attention now</p>
                </div>
              </div>
              <Link href="/briefing" className="text-link">View all</Link>
            </div>
            <div className="space-y-1.5">
              {(briefing?.today ?? []).slice(0, 2).map((item) => (
                <div key={item.id} className="briefing-row">
                  <span className="briefing-dot tone-blue"><Radio className="h-3.5 w-3.5" /></span>
                  <span className="min-w-0 flex-1"><strong>{item.summary}</strong><small>{item.source || "memory"}</small></span>
                </div>
              ))}
              {(briefing?.commitments ?? []).slice(0, 2).map((item) => (
                <div key={item.id} className="briefing-row">
                  <span className="briefing-dot tone-violet"><CheckCircle2 className="h-3.5 w-3.5" /></span>
                  <span className="min-w-0 flex-1"><strong>{item.text}</strong><small>{item.project || item.source || "commitment"}</small></span>
                </div>
              ))}
              {(briefing?.today?.length ?? 0) === 0 && (briefing?.commitments?.length ?? 0) === 0 && (
                <div className="empty-panel">Your briefing is clear. New decisions and commitments will surface here.</div>
              )}
            </div>
          </article>

          <article className="premium-card rounded-[22px] border p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold">Conflicts & review</h2>
                <p className="mt-1 text-[10px] text-muted-foreground">Signals requiring human judgment</p>
              </div>
              <span className="count-badge">{conflicts.length + review.length}</span>
            </div>
            <div className="space-y-1.5">
              {conflicts.slice(0, 3).map((conflict) => (
                <Link href="/conflicts" key={conflict.id} className="signal-row">
                  <CircleAlert className="h-4 w-4 shrink-0 text-rose-500" />
                  <span className="min-w-0 flex-1">
                    <strong>Potential conflict</strong>
                    <small>{conflict.shared_entities.slice(0, 2).join(" · ") || conflict.signal_type}</small>
                  </span>
                  <span className="severity-chip">{Math.round(conflict.confidence * 100)}%</span>
                </Link>
              ))}
              {review.slice(0, Math.max(0, 3 - conflicts.length)).map((item) => (
                <Link href="/review" key={item.id} className="signal-row">
                  <RefreshCw className="h-4 w-4 shrink-0 text-amber-500" />
                  <span className="min-w-0 flex-1"><strong>{item.content}</strong><small>{item.reason}</small></span>
                  <span className="severity-chip is-warm">{Math.round(item.retention * 100)}%</span>
                </Link>
              ))}
              {conflicts.length === 0 && review.length === 0 && (
                <div className="empty-panel">No open conflict or review signals.</div>
              )}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <Link href="/conflicts" className="mini-action justify-center">Review conflicts</Link>
              <Link href="/review" className="mini-action justify-center">Review memory</Link>
            </div>
          </article>

          <LiveFeed />
        </aside>
      </section>

      <section id="quick-capture" className="scroll-mt-24">
        <MemoryQuickAdd onAdded={load} />
      </section>

      {selectedMemory && (
        <MemoryDetailDrawer
          memory={selectedMemory}
          onClose={() => setSelectedMemory(null)}
          onChanged={load}
          onSelectRelated={setSelectedMemory}
        />
      )}
    </div>
  );
}
