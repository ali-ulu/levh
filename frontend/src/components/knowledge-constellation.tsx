"use client";

import { useT } from "@/lib/i18n";
import { Building2, CalendarDays, FileText, Network, ShieldCheck, Users } from "lucide-react";

interface Props {
  memories: number;
  entityCounts: Record<string, number>;
}

// The five entity types the server actually extracts into the graph. This list
// used to include `project` and `decision`, which the index never emits, so
// those two nodes were permanently 0 while the real `event` count went
// unshown (#558).
const TYPES = [
  { key: "person", labelKey: "knowledgeConstellation.people", icon: Users, tone: "violet" },
  { key: "organization", labelKey: "knowledgeConstellation.organizations", icon: Building2, tone: "cyan" },
  { key: "event", labelKey: "knowledgeConstellation.events", icon: CalendarDays, tone: "amber" },
  { key: "document", labelKey: "knowledgeConstellation.documents", icon: FileText, tone: "rose" },
  { key: "task", labelKey: "knowledgeConstellation.tasks", icon: ShieldCheck, tone: "emerald" },
];

const CENTER = 50;
const RADIUS = 34;

/** Deterministic ring layout: one node per angle, from the top stepping
 * clockwise. No randomness, so the prerendered markup and the first client
 * paint agree; the node order comes from the counts, not a seed. */
function ringPosition(index: number, total: number) {
  const angle = (2 * Math.PI * index) / Math.max(1, total) - Math.PI / 2;
  return { x: CENTER + RADIUS * Math.cos(angle), y: CENTER + RADIUS * Math.sin(angle) };
}

export function KnowledgeConstellation({ memories, entityCounts }: Props) {
  const t = useT();

  // Ordered by mention count so the busiest type sits at the top; ties fall
  // back to the key so the order is stable across renders (#558).
  const nodes = TYPES.map((type) => ({
    ...type,
    count: entityCounts[type.key] ?? 0,
  })).sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));

  return (
    <div className="constellation relative min-h-[320px] overflow-hidden rounded-[22px]">
      <div className="constellation-stars" />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {nodes.map((node, i) => {
          const { x, y } = ringPosition(i, nodes.length);
          return (
            <line key={node.key} x1={CENTER} y1={CENTER} x2={x} y2={y} className={`constellation-line tone-${node.tone}`} />
          );
        })}
        <circle cx="50" cy="50" r="23" className="orbit-ring orbit-a" />
        <circle cx="50" cy="50" r="36" className="orbit-ring orbit-b" />
      </svg>

      <div className="constellation-core">
        <span className="core-halo" />
        <Network className="relative z-10 h-6 w-6" />
        <strong className="relative z-10 mt-1 text-sm">{t("knowledgeConstellation.core")}</strong>
        <small className="relative z-10 text-[10px] opacity-70">
          {t("knowledgeConstellation.records", { count: memories.toLocaleString() })}
        </small>
      </div>

      {nodes.map((node, i) => {
        const Icon = node.icon;
        const { x, y } = ringPosition(i, nodes.length);
        return (
          <div
            key={node.key}
            className={`constellation-node tone-${node.tone} ${node.count === 0 ? "opacity-40" : ""}`}
            style={{ left: `${x}%`, top: `${y}%` }}
          >
            <span className="node-orb"><Icon className="h-4 w-4" /></span>
            <span className="mt-1 text-[10px] font-medium">{t(node.labelKey)}</span>
            <span className="text-[9px] opacity-60">{node.count.toLocaleString()}</span>
          </div>
        );
      })}
    </div>
  );
}
