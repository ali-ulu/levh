"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { trustLabelColor } from "@/lib/trust-ui";
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

type Translator = ReturnType<typeof useT>;

const TRUST_LABEL_KEYS: Record<string, string> = {
  high: "memoryDetail.trust.label.high",
  medium_high: "memoryDetail.trust.label.mediumHigh",
  medium: "memoryDetail.trust.label.medium",
  low: "memoryDetail.trust.label.low",
  very_low: "memoryDetail.trust.label.veryLow",
};

function humanizeHours(hours: number, t: Translator): string {
  if (hours < 24) return t("memoryDetail.duration.hours", { count: hours.toFixed(0) });
  const days = hours / 24;
  if (days < 60) return t("memoryDetail.duration.days", { count: days.toFixed(1) });
  return t("memoryDetail.duration.months", { count: (days / 30).toFixed(1) });
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
  const t = useT();
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
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  // Move focus into the drawer on open and restore it to the trigger on close,
  // so keyboard users are not left behind on the page underneath.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  // Escape closes the drawer and Tab is trapped inside it.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

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
    if (!confirm(t("memoryDetail.deleteConfirm"))) return;
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

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative w-full max-w-lg bg-background border-l shadow-xl overflow-y-auto focus:outline-none"
      >
        <div className="sticky top-0 bg-background border-b p-4 flex items-center justify-between z-10">
          <h2 id={titleId} className="text-lg font-semibold">
            {t("memoryDetail.title")}
          </h2>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={togglePin} disabled={busy} aria-label={pinned ? t("app.memories.action.unpin") : t("app.memories.action.pin")}>
              {pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
            </Button>
            <Button variant="ghost" size="icon" onClick={remove} disabled={busy} aria-label={t("app.memories.action.delete")}>
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label={t("memoryDetail.close")}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="h-4 w-4" />
                {t("memoryDetail.content")}
                {pinned && (
                  <Badge variant="secondary" className="text-[11px] ml-auto">
                    <Pin className="h-2.5 w-2.5 mr-1" />
                    {t("memoryDetail.pinnedNeverDecays")}
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
                  {t("memoryDetail.trust.title")}
                  {trust && (
                    <Badge
                      variant="outline"
                      className={`ml-auto text-[11px] ${trustLabelColor(trust.label)}`}
                    >
                      {(trust.confidence * 100).toFixed(0)}% · {t(TRUST_LABEL_KEYS[trust.label] ?? "memoryDetail.trust.label.unknown")}
                    </Badge>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2.5">
                {trustLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t("memoryDetail.trust.loading")}
                  </div>
                ) : trust ? (
                  <>
                    <div className="space-y-1.5">
                      {(
                        [
                          ["memoryDetail.trust.component.source", trust.components.source_score],
                          ["memoryDetail.trust.component.corroboration", trust.components.corroboration_score],
                          ["memoryDetail.trust.component.review", trust.components.review_score],
                          ["memoryDetail.trust.component.recency", trust.components.recency_score],
                        ] as const
                      ).map(([labelKey, value]) => (
                        <div key={labelKey} className="space-y-0.5">
                          <div className="flex justify-between text-xs">
                            <span className="text-muted-foreground">{t(labelKey)}</span>
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
                          <span className="text-muted-foreground">{t("memoryDetail.trust.risk")}</span>
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
                        {t("memoryDetail.trust.conflictCandidate", { status: trust.evidence.conflict_status })}
                      </div>
                    )}
                  </>
                ) : trustError && !trust ? (
                  <p className="text-xs text-muted-foreground">{t("memoryDetail.trust.loadFailed")}</p>
                ) : null}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Brain className="h-4 w-4" />
                {t("memoryDetail.strength.title")}
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  {memory.pinned ? t("memoryDetail.strength.pinned") : t("memoryDetail.strength.fades")}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {curveLoading ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("memoryDetail.retention.computing")}
                </div>
              ) : curve ? (
                <>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{t("memoryDetail.retention.current")}</span>
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
                        formatter={(v) => [`${(Number(v) * 100).toFixed(0)}%`, t("memoryDetail.retention.predicted")]}
                        labelFormatter={(d) => t("memoryDetail.retention.day", { day: String(d) })}
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
                      {t("memoryDetail.retention.halfLife")}: <strong className="text-foreground">{humanizeHours(curve.stability_hours, t)}</strong>
                      {" · "}
                      {t("memoryDetail.retention.reinforced")} <strong className="text-foreground">{curve.recall_count}×</strong>
                    </span>
                    {!memory.pinned && (
                      <div className="flex gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={markStale}
                          disabled={reinforcing}
                          title={t("memoryDetail.retention.staleTitle")}
                        >
                          <TrendingDown className="h-3 w-3 mr-1.5" />
                          {t("memoryDetail.retention.stale")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={reinforce}
                          disabled={reinforcing}
                          title={t("memoryDetail.retention.reinforceTitle")}
                        >
                          {reinforcing ? (
                            <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                          ) : (
                            <BatteryCharging className="h-3 w-3 mr-1.5" />
                          )}
                          {t("memoryDetail.retention.reinforce")}
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {memory.pinned
                      ? t("memoryDetail.retention.pinnedHelp")
                      : t("memoryDetail.retention.help")}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">{t("memoryDetail.retention.loadFailed")}</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t("memoryDetail.metadata.title")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Tag className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.type")}</span>
                </div>
                <Badge
                  variant={memory.memory_type === "episodic" ? "default" : "secondary"}
                  className="text-xs justify-self-start"
                >
                  {memory.memory_type === "episodic" ? t("app.memories.filter.episodic") : t("app.memories.filter.shortTerm")}
                </Badge>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <BarChart3 className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.importance")}</span>
                </div>
                <span className="text-sm">{memory.importance?.toFixed(2) ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Activity className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.accessCount")}</span>
                </div>
                <span className="text-sm">{memory.frequency ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <FolderGit2 className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.project")}</span>
                </div>
                <span className="text-sm">{memory.project ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <FileText className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.source")}</span>
                </div>
                <span className="text-sm">{memory.source ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <User className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.session")}</span>
                </div>
                <span className="text-sm font-mono truncate">{memory.session_id ?? "—"}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.created")}</span>
                </div>
                <span className="text-sm">{formatDate(memory.created_at)}</span>

                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <span>{t("memoryDetail.metadata.lastAccessed")}</span>
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
                {t("memoryDetail.score.title")}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">{t("memoryDetail.score.hscore")}</span>
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
                  {displayHscore !== undefined ? displayHscore.toFixed(4) : t("memoryDetail.score.notAvailable")}
                </Badge>
              </div>

              <p className="text-xs text-muted-foreground">
                {t("memoryDetail.score.formula")}
              </p>

              {breakdownLoading && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("memoryDetail.score.loading")}
                </div>
              )}

              {breakdown && !breakdownLoading && (
                <div className="space-y-2 mt-2">
                  <div className="text-xs font-medium text-muted-foreground">
                    {recallQuery
                      ? t("memoryDetail.score.componentsForQuery", { query: recallQuery })
                      : t("memoryDetail.score.baselineComponents")}
                  </div>
                  <div className="space-y-1.5">
                    {(
                      [
                        ["memoryDetail.score.penalty.similarity", breakdown.components.similarity_penalty, breakdown.weights.alpha],
                        ["memoryDetail.score.penalty.decay", breakdown.components.decay_penalty, breakdown.weights.beta],
                        ["memoryDetail.score.penalty.importance", breakdown.components.importance_penalty, breakdown.weights.gamma],
                        ["memoryDetail.score.penalty.frequency", breakdown.components.frequency_penalty, breakdown.weights.delta],
                      ] as const
                    ).map(([labelKey, value, weight]) => (
                      <div key={labelKey} className="space-y-0.5">
                        <div className="flex justify-between text-xs">
                          <span className="text-muted-foreground">{t(labelKey)}</span>
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
                    {t("memoryDetail.score.weights")}: &alpha;={breakdown.weights.alpha} &beta;={breakdown.weights.beta}{" "}
                    &gamma;={breakdown.weights.gamma} &delta;={breakdown.weights.delta}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-1 text-xs mt-2">
                <span className="text-muted-foreground">{t("memoryDetail.score.guide.perfect")}</span>
                <span className="text-muted-foreground">{t("memoryDetail.score.guide.good")}</span>
                <span className="text-muted-foreground">{t("memoryDetail.score.guide.weak")}</span>
                <span className="text-muted-foreground">{t("memoryDetail.score.guide.unrelated")}</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Network className="h-4 w-4" />
                {t("memoryDetail.related.title")}
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  {t("memoryDetail.related.nearest")}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {relatedLoading ? (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-4">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("memoryDetail.related.loading")}
                </div>
              ) : !related || related.length === 0 ? (
                <p className="text-xs text-muted-foreground py-2">
                  {t("memoryDetail.related.empty")}
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
                          <Pin className="h-2.5 w-2.5" /> {t("app.memories.pinned")}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
              <p className="text-[11px] text-muted-foreground mt-2">
                {t("memoryDetail.related.help")}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
