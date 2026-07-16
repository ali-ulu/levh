"use client";

import { Building2, FileText, FolderGit2, Lightbulb, Network, ShieldCheck, Users } from "lucide-react";

interface Props {
  memories: number;
  entityCounts: Record<string, number>;
}

const nodes = [
  { key: "person", label: "People", icon: Users, x: 18, y: 28, tone: "violet" },
  { key: "organization", label: "Organizations", icon: Building2, x: 78, y: 24, tone: "cyan" },
  { key: "project", label: "Projects", icon: FolderGit2, x: 18, y: 72, tone: "blue" },
  { key: "decision", label: "Decisions", icon: Lightbulb, x: 80, y: 72, tone: "amber" },
  { key: "document", label: "Documents", icon: FileText, x: 50, y: 12, tone: "rose" },
  { key: "task", label: "Tasks", icon: ShieldCheck, x: 50, y: 88, tone: "emerald" },
];

export function KnowledgeConstellation({ memories, entityCounts }: Props) {
  return (
    <div className="constellation relative min-h-[320px] overflow-hidden rounded-[22px]">
      <div className="constellation-stars" />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {nodes.map((node) => (
          <line key={node.key} x1="50" y1="50" x2={node.x} y2={node.y} className={`constellation-line tone-${node.tone}`} />
        ))}
        <circle cx="50" cy="50" r="23" className="orbit-ring orbit-a" />
        <circle cx="50" cy="50" r="36" className="orbit-ring orbit-b" />
      </svg>

      <div className="constellation-core">
        <span className="core-halo" />
        <Network className="relative z-10 h-6 w-6" />
        <strong className="relative z-10 mt-1 text-sm">Memory fabric</strong>
        <small className="relative z-10 text-[10px] opacity-70">{memories.toLocaleString()} records</small>
      </div>

      {nodes.map((node) => {
        const Icon = node.icon;
        const count = entityCounts[node.key] ?? 0;
        return (
          <div
            key={node.key}
            className={`constellation-node tone-${node.tone}`}
            style={{ left: `${node.x}%`, top: `${node.y}%` }}
          >
            <span className="node-orb"><Icon className="h-4 w-4" /></span>
            <span className="mt-1 text-[10px] font-medium">{node.label}</span>
            <span className="text-[9px] opacity-60">{count.toLocaleString()}</span>
          </div>
        );
      })}
    </div>
  );
}
