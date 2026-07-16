"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  BarChart3,
  BrainCircuit,
  Building2,
  CalendarClock,
  Clock3,
  Database,
  FolderGit2,
  Gavel,
  GitCompareArrows,
  History,
  Network,
  RefreshCw,
  Settings,
  Sunrise,
  Users,
} from "lucide-react";

const groups = [
  {
    label: "Memory",
    items: [
      { href: "/", label: "Overview", icon: BrainCircuit },
      { href: "/memories", label: "All memories", icon: Database },
      { href: "/graph", label: "Knowledge graph", icon: Network },
      { href: "/timeline", label: "Timeline", icon: Clock3 },
      { href: "/projects", label: "Projects", icon: FolderGit2 },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/briefing", label: "Daily briefing", icon: Sunrise },
      { href: "/review", label: "Review queue", icon: RefreshCw },
      { href: "/conflicts", label: "Conflicts", icon: GitCompareArrows },
      { href: "/meeting-prep", label: "Meeting prep", icon: CalendarClock },
      { href: "/decisions", label: "Decisions", icon: Gavel },
      { href: "/visualize", label: "Insights", icon: BarChart3 },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/people", label: "People", icon: Users },
      { href: "/organizations", label: "Organizations", icon: Building2 },
      { href: "/sessions", label: "Sessions", icon: History },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

function isActive(pathname: string, href: string) {
  const clean = pathname.replace(/\/+$/, "") || "/";
  return clean === href;
}

function LogoMark() {
  return (
    <span className="levh-logo-shell" aria-hidden="true">
      <img src="/brand/levh-mark.png" alt="" className="levh-logo-mark" />
    </span>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="premium-sidebar fixed left-0 top-0 z-40 hidden h-screen w-[248px] flex-col lg:flex">
      <div className="px-5 pb-5 pt-6">
        <Link href="/" className="flex items-center gap-3">
          <LogoMark />
          <div>
            <div className="text-[17px] font-semibold tracking-[-0.02em]">LEVH</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Context continuity</div>
          </div>
        </Link>
      </div>

      <nav className="sidebar-scroll flex-1 overflow-y-auto px-3 pb-4">
        {groups.map((group) => (
          <div key={group.label} className="mb-5">
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">{group.label}</p>
            <div className="space-y-1">
              {group.items.map((item) => {
                const active = isActive(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn("premium-nav-item", active && "is-active")}
                  >
                    <span className="nav-icon"><item.icon className="h-4 w-4" /></span>
                    <span>{item.label}</span>
                    {active && <span className="active-glow" />}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="px-4 pb-4">
        <div className="connector-status-card">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
            </span>
            <span className="text-xs font-medium">Local fabric active</span>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">SQLite · MCP · REST · WebSocket</p>
          <div className="mt-3 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>LEVH Engine v2.27</span>
            <span>local-first</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
