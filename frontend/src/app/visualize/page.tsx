"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { trustLabelColor, trustLabelHex, trustLabelText, TRUST_LABEL_ORDER } from "@/lib/trust-ui";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Memory, Source, TagCount, TrustBreakdown } from "@/types";
import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// Single-hue marks: every chart here shows one measure, so one series color.
// Identity across charts is carried by titles, not by hue.
const SERIES = "var(--chart-1)";
const GRID = "var(--chart-grid)";

const tooltipStyle = {
  backgroundColor: "hsl(var(--card))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 8,
  fontSize: 12,
  color: "hsl(var(--foreground))",
};

function ChartCard({
  title,
  subtitle,
  children,
  empty,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="text-sm text-muted-foreground py-10 text-center">No data yet.</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

export default function InsightsPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [tags, setTags] = useState<TagCount[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);

  const [byLabel, setByLabel] = useState<Record<string, number> | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const [lowTrust, setLowTrust] = useState<TrustBreakdown[]>([]);
  const [lowTrustLoading, setLowTrustLoading] = useState(false);

  useEffect(() => {
    Promise.all([api.listMemories({ limit: 1000 }), api.listTags(), api.listSources()])
      .then(([m, t, s]) => {
        setMemories(m);
        setTags(t.tags);
        setSources(s.sources);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const loadLowTrust = () => {
    setLowTrustLoading(true);
    api
      .lowTrust(0.4, 8)
      .then((r) => setLowTrust(r.low_trust))
      .catch(() => setLowTrust([]))
      .finally(() => setLowTrustLoading(false));
  };

  // Low-trust memories may already be scored from a previous recompute (CLI
  // or an earlier dashboard visit) — try loading them on mount too.
  useEffect(loadLowTrust, []);

  const recomputeTrust = async () => {
    setRecomputing(true);
    try {
      const result = await api.recomputeTrust();
      setByLabel(result.by_label);
      loadLowTrust();
    } catch {
    } finally {
      setRecomputing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // Timeline: memories per day
  const byDate = new Map<string, number>();
  memories.forEach((m) => {
    const date = m.created_at?.split("T")[0] || "unknown";
    byDate.set(date, (byDate.get(date) || 0) + 1);
  });
  const timeline = Array.from(byDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, count]) => ({ date: date.slice(5), count }));

  // Importance histogram
  const importanceBuckets = [
    { name: "0–0.2", count: memories.filter((m) => m.importance < 0.2).length },
    { name: "0.2–0.4", count: memories.filter((m) => m.importance >= 0.2 && m.importance < 0.4).length },
    { name: "0.4–0.6", count: memories.filter((m) => m.importance >= 0.4 && m.importance < 0.6).length },
    { name: "0.6–0.8", count: memories.filter((m) => m.importance >= 0.6 && m.importance < 0.8).length },
    { name: "0.8–1.0", count: memories.filter((m) => m.importance >= 0.8).length },
  ];

  // Type breakdown
  const typeCounts = [
    { name: "Episodic", count: memories.filter((m) => m.memory_type === "episodic").length },
    { name: "Short-term", count: memories.filter((m) => m.memory_type === "short_term").length },
    { name: "Pinned", count: memories.filter((m) => m.pinned).length },
  ];

  // Memory durability (stability half-life in days) — unreinforced memories
  // stay near the 7-day default; recalled/reinforced ones climb the buckets.
  const stabilityDays = (m: Memory) => m.stability_hours / 24;
  const durabilityBuckets = [
    { name: "<7d", count: memories.filter((m) => stabilityDays(m) < 7).length },
    { name: "7–14d", count: memories.filter((m) => stabilityDays(m) >= 7 && stabilityDays(m) < 14).length },
    { name: "14–30d", count: memories.filter((m) => stabilityDays(m) >= 14 && stabilityDays(m) < 30).length },
    { name: "30–90d", count: memories.filter((m) => stabilityDays(m) >= 30 && stabilityDays(m) < 90).length },
    { name: "90d+", count: memories.filter((m) => stabilityDays(m) >= 90).length },
  ];

  // Projects
  const byProject = new Map<string, number>();
  memories.forEach((m) => {
    const key = m.project || "(no project)";
    byProject.set(key, (byProject.get(key) || 0) + 1);
  });
  const projectData = Array.from(byProject.entries())
    .sort(([, a], [, b]) => b - a)
    .slice(0, 8)
    .map(([name, count]) => ({ name, count }));

  const sourceData = sources.slice(0, 8).map((s) => ({ name: s.name, count: s.memory_count }));
  const tagData = tags.slice(0, 10).map((t) => ({ name: t.name, count: t.count }));

  const trustChartData = TRUST_LABEL_ORDER.map((label) => ({
    name: trustLabelText(label),
    label,
    count: byLabel?.[label] ?? 0,
  }));
  const trustTotal = trustChartData.reduce((a, d) => a + d.count, 0);

  const noData = memories.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Insights</h1>
        <p className="text-sm text-muted-foreground mt-1">
          How your memory grows and where it comes from. {memories.length} memories analyzed.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            Trust distribution
            <Button
              size="sm"
              variant="outline"
              className="ml-auto h-7 text-xs"
              onClick={recomputeTrust}
              disabled={recomputing}
            >
              {recomputing ? (
                <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3 mr-1.5" />
              )}
              Recompute trust
            </Button>
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Provenance/corroboration confidence across all scored memories.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {!byLabel ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              Run recompute to see the distribution.
            </p>
          ) : trustTotal === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">No memories scored yet.</p>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={140}>
                <BarChart
                  data={trustChartData}
                  layout="vertical"
                  margin={{ top: 4, right: 24, bottom: 0, left: 12 }}
                >
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
                  <Bar dataKey="count" name="Memories" radius={[0, 4, 4, 0]} maxBarSize={20}>
                    {trustChartData.map((d) => (
                      <Cell key={d.label} fill={trustLabelHex(d.label)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>

              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1.5">
                  Lowest-trust memories
                </div>
                {lowTrustLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-3">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Loading...
                  </div>
                ) : lowTrust.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-2">
                    No memories below the low-trust threshold.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {lowTrust.map((t) => (
                      <div
                        key={t.memory_id}
                        className="flex items-center gap-2 text-xs border rounded-md px-2 py-1.5"
                      >
                        <Badge
                          variant="outline"
                          className={`text-[10px] shrink-0 ${trustLabelColor(t.label)}`}
                        >
                          {(t.confidence * 100).toFixed(0)}% · {trustLabelText(t.label)}
                        </Badge>
                        <span className="font-mono text-muted-foreground shrink-0">
                          {t.memory_id.slice(0, 8)}
                        </span>
                        <span className="truncate text-muted-foreground">
                          {t.explanation[0] ?? ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard
          title="Memory growth"
          subtitle="New memories stored per day"
          empty={noData || timeline.length === 0}
        >
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={timeline} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Area
                type="monotone"
                dataKey="count"
                name="Memories"
                stroke={SERIES}
                strokeWidth={2}
                fill={SERIES}
                fillOpacity={0.12}
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Importance distribution"
          subtitle="How critical your stored memories are (0 = low, 1 = critical)"
          empty={noData}
        >
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={importanceBuckets} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Memories" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={48} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Memory durability"
          subtitle="Half-life of stored memories — grows each time one is recalled or reinforced"
          empty={noData}
        >
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={durabilityBuckets} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Memories" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={48} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Memory layers" subtitle="Episodic vs short-term vs pinned" empty={noData}>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart
              data={typeCounts}
              layout="vertical"
              margin={{ top: 4, right: 24, bottom: 0, left: 12 }}
            >
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={80} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Memories" fill={SERIES} radius={[0, 4, 4, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Memories by source"
          subtitle="Which AI clients are writing to memory"
          empty={sourceData.length === 0}
        >
          <ResponsiveContainer width="100%" height={200}>
            <BarChart
              data={sourceData}
              layout="vertical"
              margin={{ top: 4, right: 24, bottom: 0, left: 12 }}
            >
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Memories" fill={SERIES} radius={[0, 4, 4, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Memories by project"
          subtitle="Top workspaces"
          empty={projectData.length === 0}
        >
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={projectData}
              layout="vertical"
              margin={{ top: 4, right: 24, bottom: 0, left: 12 }}
            >
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Memories" fill={SERIES} radius={[0, 4, 4, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Top tags" subtitle="Most used tags across all memories" empty={tagData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={tagData}
              layout="vertical"
              margin={{ top: 4, right: 24, bottom: 0, left: 12 }}
            >
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(120,120,120,0.08)" }} />
              <Bar dataKey="count" name="Uses" fill={SERIES} radius={[0, 4, 4, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  );
}
