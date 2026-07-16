"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { trustLabelColor, trustLabelText } from "@/lib/trust-ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ForgettingCurve, Memory, RelatedMemory, ScoreBreakdown, TrustBreakdown } from "@/types";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BatteryCharging,
  Brain,
  Clock,
  FileText,
  FolderGit2,
  Loader2,
  Network,
  Pin,
  PinOff,
  ShieldCheck,
  Tag,
  Trash2,
  TrendingDown,
  User,
  X,
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

function humanizeHours(hours: number): string {
  if (hours < 24) return `${hours.toFixed(0)}h`;
  const days = hours / 24;
  if (days < 60) return `${days.toFixed(1)} days`;
  return `${(days / 30).toFixed(1)} months`;
}

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function MemoryDetailDrawer({
  memory,
  score,
  recallQuery,
  onClose,
  onChanged,
  onSelectRelated,
}: {
  memory: Memory;
  score?: number;
  recallQuery?: string;
  onClose: () => void;
  onChanged?: () => void;
  /** Called when the user clicks a related memory — lets the parent page
   * "jump" the drawer to that memory instead of closing it. */
  onSelectRelated?: (memory: Memory) => void;
}) {
  const displayHscore = score ?? memory.hscore ?? undefined;
  const [breakdown, setBreakdown] = useState<ScoreBreakdown | null>(null);
  const [breakdownLoading, setBreakdownLoading] = useState(false);
  const [pinned, setPinned] = useState(memory.pinned);
  const [busy, setBusy] = useState(false);
  const [curve, setCurve] = useState<ForgettingCurve | null>(null);
  const [curveLoading, setCurveLoading] = useState(false);
  const [reinforcing, setReinforcing] = useState(false);
  const [related, setRelated] = useState<RelatedMemory[] | null>(null);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [trust, setTrust] = useState<TrustBreakdown | null>(null);
  const [trustLoading, setTrustLoading] = useState(false);
  const [trustError, setTrustError] = useState(false);
  const trustMemoryIdRef = useRef(memory.id);

  useEffect(() => {
    trustMemoryIdRef.current = memory.id;
  }, [memory.id]);

  const loadTrust = useCallback(() => {
    const requestedId = memory.id;
    if (!requestedId) return;
    setTrustLoading(true);
    setTrustError(false);
    api
      .memoryTrust(requestedId)
      .then((r) => {
        if (trustMemoryIdRef.current !== requestedId) return;
        setTrust(r);
      })
      .catch(() => {
        if (trustMemoryIdRef.current !== requestedId) return;
        setTrust(null);
        setTrustError(true);
      })
      .finally(() => {
        if (trustMemoryIdRef.current !== requestedId) return;
        setTrustLoading(false);
      });
  }, [memory.id]);

  useEffect(() => {
    loadTrust();
  }, [loadTrust]);

  useEffect(() => {
    if (!memory.id) return;
    setBreakdownLoading(true);
    api
      .fetchScoreBreakdown(memory.id, recallQuery ?? "")
      .then(setBreakdown)
      .catch(() => setBreakdown(null))
      .finally(() => setBreakdownLoading(false));
  }, [memory.id, recallQuery]);

  const loadCurve = () => {
    setCurveLoading(true);
    api
      .fetchForgettingCurve(memory.id, 30)
      .then(setCurve)
      .catch(() => setCurve(null))
      .finally(() => setCurveLoading(false));
  };

  useEffect(loadCurve, [memory.id]);

  // Jumping to a related memory swaps `memory` in place (same drawer, new
  // anchor), so pin state and the related list must re-sync per memory.id.
  useEffect(() => {
    setPinned(memory.pinned);
  }, [memory.id, memory.pinned]);

  useEffect(() => {
    setRelatedLoading(true);
    api
      .relatedMemories(memory.id, 5)
      .then((r) => setRelated(r.related))
      .catch(() => setRelated(null))
      .finally(() => setRelatedLoading(false));
  }, [memory.id]);

  const togglePin = async () => {
    setBusy(true);
    try {
      const updated = await api.pinMemory(memory.id, !pinned);
      setPinned(updated.pinned);
      loadTrust();
      onChanged?.();
    } catch {}
    setBusy(false);
  };

  const reinforce = async () => {
    setReinforcing(true);
    try {
      await api.reinforceMemory(memory.id);
      loadCurve();
      loadTrust();
      onChanged?.();
    } catch {}
    setReinforcing(false);
  };

  const markStale = async () => {
    setReinforcing(true);
    try {
      await api.memoryFeedback(memory.id, false);
      loadCurve();
      loadTrust();
      onChanged?.();
    } catch {}
    setReinforcing(false);
  };

  const remove = async () => {
    if (!confirm("Delete this memory permanently?")) return;
    setBusy(true);
    try {
      await api.deleteMemory(memory.id);
      onChanged?.();
      onClose();
    } catch {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative w-full max-w-lg bg-background border-l shadow-xl overflow-y-auto">
        <div className="sticky top-0 bg-background border-b p-4 flex items-center justify-between z-10">
          <h2 className="text-lg font-semibold">Memory Details</h2>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={togglePin} disabled={busy} aria-label={pinned ? "Unpin" : "Pin"}>
              {pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
            </Button>
            <Button variant="ghost" size="icon" onClick={remove} disabled={busy} aria-label="Delete">
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="h-4 w-4" />
                Content
                {pinned && (
                  <Badge variant="secondary" className="text-[11px] ml-auto">
                    <Pin className="h-2.5 w-2.5 mr-1" />
                    pinned — never decays
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{memory.content}</p>
            </CardContent>
          </Card>

          {(trustLoading || trust || trustError) && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4" />
                  Trust
                  {trust && (
                    <Badge
                      variant="outline"
                      className={`ml-auto text-[11px] ${trustLabelColor(trust.label)}`}
                    >
                      {(trust.confidence * 100).toFixed(0)}% · {trustLabelText(trust.label)}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2.5">
                {trustLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Loading trust score...
                  </div>
                ) : trust ? (
                  <>
                    <div className="space-y-1.5">
                      {(
                        [
                          ["Source", trust.components.source_score],
                          ["Corroboration", trust.components.corroboration_score],
                          ["Review", trust.components.review_score],
                          ["Recency", trust.components.recency_score],
                        ] as const
                      ).map(([label, value]) => (
                        <div key={label} className="space-y-0.5">
                          <div className="flex justify-between text-xs">
                            <span className="text-muted-foreground">{label}</span>
                            <span className="font-mono">{value.toFixed(2)}</span>
                          </div>
                          <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full rounded-full bg-primary"
                              style={{ width: `${Math.min(100, value * 100)}%` }}
                            />
                          </div>
                        </div>
                      ))}
                      <div className="space-y-0.5">
                        <div className="flex justify-between text-xs">
                          <span className="text-muted-foreground">Risk (subtracts)</span>
                          <span className="font-mono">−{trust.components.risk_penalty.toFixed(2)}</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-red-500"
                            style={{ width: `${Math.min(100, trust.components.risk_penalty * 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>

                    {trust.explanation.length > 0 && (
                      <div className="space-y-0.5 pt-1 border-t">
                        {trust.explanation.slice(0, 2).map((line, i) => (
                          <p key={i} className="text-[11px] text-muted-foreground">
                            {line}
                          </p>
                        ))}
                      </div>
                    )}

                    {(trust.evidence.conflict_status === "open" ||
                      trust.evidence.conflict_status === "confirmed") && (
                      <div className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-md px-2 py-1.5">
                        <AlertTriangle className="h-3 w-3 shrink-0" />
                        Conflict candidate ({trust.evidence.conflict_status}) — under review
                      </div>
                    )}
                  </>
                ) : trustError && !trust ? (
                  <p className="text-xs text-muted-foreground">Could not load trust score.</p>
                ) : null}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Brain className="h-4 w-4" />
                Memory Strength
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  {memory.pinned ? "pinned — permanent" : "fades like human memory"}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {curveLoading ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Computing retention curve...
                </div>
              ) : curve ? (
                <>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Current retention</span>
                    <Badge
                      variant="outline"
                      className={
                        curve.current_retention >= 0.7
                          ? "border-green-500/60 text-green-600 dark:text-green-400"
                          : curve.current_retention >= 0.4
                          ? "border-yellow-500/60 text-yellow-600 dark:text-yellow-400"
                          : "border-red-500/60 text-red-600 dark:text-red-400"
                      }
                    >
                      {(curve.current_retention * 100).toFixed(0)}%
                    </Badge>
                  </div>
                  <ResponsiveContainer width="100%" height={100}>
                    <AreaChart data={curve.curve} margin={{ top: 4, right: 4, bottom: 0, left: -32 }}>
                      <XAxis dataKey="day" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                      <YAxis
                        domain={[0, 1]}
                        tick={{ fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v) => `${Math.round(v * 100)}%`}
                      />
                      <Tooltip
                        formatter={(v: number) => [`${(v * 100).toFixed(0)}%`, "Predicted retention"]}
                        labelFormatter={(d) => `Day ${d}`}
                        contentStyle={{
                          backgroundColor: "hsl(var(--card))",
                          border: "1px solid hsl(var(--border))",
                          borderRadius: 8,
                          fontSize: 11,
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="retention"
                        stroke="var(--chart-1)"
                        strokeWidth={2}
                        fill="var(--chart-1)"
                        fillOpacity={0.15}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">
                      Half-life: <strong className="text-foreground">{humanizeHours(curve.stability_hours)}</strong>
                      {" · "}
                      Reinforced <strong className="text-foreground">{curve.recall_count}×</strong>
                    </span>
                    {!memory.pinned && (
                      <div className="flex gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={markStale}
                          disabled={reinforcing}
                          title="Wrong or outdated — make it fade fast"
                        >
                          <TrendingDown className="h-3 w-3 mr-1.5" />
                          Stale
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={reinforce}
                          disabled={reinforcing}
                          title="Still true and useful — strengthen it"
                        >
                          {reinforcing ? (
                            <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                          ) : (
                            <BatteryCharging className="h-3 w-3 mr-1.5" />
                          )}
                          Reinforce
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {memory.pinned
                      ? "Pinned memories never decay — this curve is flat forever."
                      : "Predicted relevance if this memory is never recalled again. Recalling it — or reinforcing it here — resets the clock and raises the curve, like remembering something makes it stick."}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">Could not load retention curve.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Metadata</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Tag className="h-3 w-3" />
                  <span>Type</span>
                </div>
                <Badge
                  variant={memory.memory_type === "episodic" ? "default" : "secondary"}
                  className="text-xs justify-self-start"
                >
                  {memory.memory_type === "episodic" ? "Episodic" : "Short-term"}
                </Badge>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <BarChart3 className="h-3 w-3" />
                  <span>Importance</span>
                </div>
                <span className="text-sm">{memory.importance?.toFixed(2) ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Activity className="h-3 w-3" />
                  <span>Access count</span>
                </div>
                <span className="text-sm">{memory.frequency ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <FolderGit2 className="h-3 w-3" />
                  <span>Project</span>
                </div>
                <span className="text-sm">{memory.project ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <FileText className="h-3 w-3" />
                  <span>Source</span>
                </div>
                <span className="text-sm">{memory.source ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <User className="h-3 w-3" />
                  <span>Session</span>
                </div>
                <span className="text-sm font-mono truncate">{memory.session_id ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <span>Created</span>
                </div>
                <span className="text-sm">{formatDate(memory.created_at)}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <span>Last accessed</span>
                </div>
                <span className="text-sm">{formatDate(memory.accessed_at)}</span>
              </div>

              {(memory.tags?.length ?? 0) > 0 && (
                <div className="flex flex-wrap gap-1 pt-1">
                  {memory.tags!.map((t) => (
                    <Badge key={t} variant="outline" className="text-xs">
                      {t}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                Why this score?
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">H-score (lower = more relevant)</span>
                <Badge
                  variant="outline"
                  className={
                    (displayHscore ?? 1) <= 0.35
                      ? "border-green-500/60 text-green-600 dark:text-green-400"
                      : (displayHscore ?? 1) <= 0.6
                      ? "border-yellow-500/60 text-yellow-600 dark:text-yellow-400"
                      : "border-red-500/60 text-red-600 dark:text-red-400"
                  }
                >
                  {displayHscore !== undefined ? displayHscore.toFixed(4) : "n/a"}
                </Badge>
              </div>

              <p className="text-xs text-muted-foreground">
                H(x,&psi;) = &alpha;(1−similarity) + &beta;(1−decay) + &gamma;(1−importance) +
                &delta;(1−frequency)
              </p>

              {breakdownLoading && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading score breakdown...
                </div>
              )}

              {breakdown && !breakdownLoading && (
                <div className="space-y-2 mt-2">
                  <div className="text-xs font-medium text-muted-foreground">
                    {recallQuery
                      ? `Components for query: "${recallQuery}"`
                      : "Baseline components (memory vs itself)"}
                  </div>
                  <div className="space-y-1.5">
                    {(
                      [
                        ["α similarity penalty", breakdown.components.similarity_penalty, breakdown.weights.alpha],
                        ["β decay penalty", breakdown.components.decay_penalty, breakdown.weights.beta],
                        ["γ importance penalty", breakdown.components.importance_penalty, breakdown.weights.gamma],
                        ["δ frequency penalty", breakdown.components.frequency_penalty, breakdown.weights.delta],
                      ] as const
                    ).map(([label, value, weight]) => (
                      <div key={label} className="space-y-0.5">
                        <div className="flex justify-between text-xs">
                          <span className="text-muted-foreground">{label}</span>
                          <span className="font-mono">{value.toFixed(4)}</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-primary"
                            style={{ width: `${Math.min(100, (value / Math.max(weight, 0.0001)) * 100)}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="text-xs text-muted-foreground pt-1 border-t">
                    Weights: &alpha;={breakdown.weights.alpha} &beta;={breakdown.weights.beta}{" "}
                    &gamma;={breakdown.weights.gamma} &delta;={breakdown.weights.delta}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-1 text-xs mt-2">
                <span className="text-muted-foreground">0.00 = perfect match</span>
                <span className="text-muted-foreground">0.35 = good match</span>
                <span className="text-muted-foreground">0.60 = weak match</span>
                <span className="text-muted-foreground">1.00 = unrelated</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Network className="h-4 w-4" />
                Related Memories
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  nearest neighbours
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {relatedLoading ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Finding related memories...
                </div>
              ) : !related || related.length === 0 ? (
                <p className="text-xs text-muted-foreground py-2">
                  No related memories found yet — store more content in this project
                  to build connections.
                </p>
              ) : (
                <div className="space-y-1">
                  {related.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => onSelectRelated?.(r)}
                      disabled={!onSelectRelated}
                      className="w-full text-left rounded-lg border p-2 hover:bg-muted/50 transition-colors disabled:cursor-default disabled:hover:bg-transparent"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-xs leading-relaxed line-clamp-2 flex-1">
                          {r.content}
                        </p>
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          {(r.similarity * 100).toFixed(0)}%
                        </Badge>
                      </div>
                      {r.pinned && (
                        <span className="text-[10px] text-muted-foreground inline-flex items-center gap-1 mt-1">
                          <Pin className="h-2.5 w-2.5" /> pinned
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
              <p className="text-[11px] text-muted-foreground mt-2">
                Computed live from embedding similarity — no manual linking needed.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
