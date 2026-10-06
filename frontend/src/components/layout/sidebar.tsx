"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";
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
  Inbox,
  Network,
  RefreshCw,
  Settings,
  ShieldAlert,
  Sparkles,
  Sunrise,
  Bot,
  Users,
} from "lucide-react";

const groups = [
  {
    labelKey: "sidebar.group.memory",
    items: [
      { href: "/", labelKey: "sidebar.nav.overview", icon: BrainCircuit },
      { href: "/assistant", labelKey: "sidebar.nav.assistant", icon: Sparkles },
      { href: "/memories", labelKey: "sidebar.nav.memories", icon: Database },
      { href: "/graph", labelKey: "sidebar.nav.graph", icon: Network },
      { href: "/timeline", labelKey: "sidebar.nav.timeline", icon: Clock3 },
      { href: "/projects", labelKey: "sidebar.nav.projects", icon: FolderGit2 },
    ],
  },
  {
    labelKey: "sidebar.group.intelligence",
    items: [
      { href: "/briefing", labelKey: "sidebar.nav.briefing", icon: Sunrise },
      { href: "/review", labelKey: "sidebar.nav.review", icon: RefreshCw },
      { href: "/conflicts", labelKey: "sidebar.nav.conflicts", icon: GitCompareArrows },
      { href: "/guard", labelKey: "sidebar.nav.guard", icon: ShieldAlert },
      { href: "/findings", labelKey: "sidebar.nav.findings", icon: Inbox },
      { href: "/meeting-prep", labelKey: "sidebar.nav.meetingPrep", icon: CalendarClock },
      { href: "/decisions", labelKey: "sidebar.nav.decisions", icon: Gavel },
      { href: "/visualize", labelKey: "sidebar.nav.insights", icon: BarChart3 },
    ],
  },
  {
    labelKey: "sidebar.group.workspace",
    items: [
      { href: "/people", labelKey: "sidebar.nav.people", icon: Users },
      { href: "/organizations", labelKey: "sidebar.nav.organizations", icon: Building2 },
      { href: "/sessions", labelKey: "sidebar.nav.sessions", icon: History },
      { href: "/agents", labelKey: "sidebar.nav.agents", icon: Bot },
      { href: "/settings", labelKey: "sidebar.nav.settings", icon: Settings },
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
      {/* Static export (`output: "export"` + `images.unoptimized`) — next/image adds no optimization here. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/brand/levh-mark.png" alt="" className="levh-logo-mark" />
    </span>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const t = useT();
  return (
    <aside className="premium-sidebar fixed left-0 top-0 z-40 hidden h-screen w-[248px] flex-col lg:flex">
      <div className="px-5 pb-5 pt-6">
        <Link href="/" className="flex items-center gap-3">
          <LogoMark />
          <div>
            <div className="text-[17px] font-semibold tracking-[-0.02em]">LEVH</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{t("sidebar.tagline")}</div>
          </div>
        </Link>
      </div>

      <nav className="sidebar-scroll flex-1 overflow-y-auto px-3 pb-4">
        {groups.map((group) => (
          <div key={group.labelKey} className="mb-5">
            <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/70">{t(group.labelKey)}</p>
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
                    <span>{t(item.labelKey)}</span>
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
            <span className="text-xs font-medium">{t("sidebar.status.active")}</span>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{t("sidebar.status.stack")}</p>
          <div className="mt-3 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>LEVH Engine v2.33</span>
            <span>{t("sidebar.status.localFirst")}</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
