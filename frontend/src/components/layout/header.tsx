"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { ThemeSwitcher } from "@/components/layout/theme-switcher";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Bell, CircleHelp, ExternalLink, Plus, Search, Wifi, WifiOff, X } from "lucide-react";

// How often the online badge re-checks the core. The badge tolerates being a
// few seconds stale, so this is deliberately slower than a UI-critical poll.
const HEALTH_POLL_MS = 30000;

export function Header() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const check = () => api.health().then(() => setOnline(true)).catch(() => setOnline(false));
    // Only poll while the tab is actually visible: a forgotten background tab
    // otherwise keeps hitting /api/health forever to refresh a badge nobody is
    // looking at. Firing on visibilitychange means returning to the tab still
    // gets a fresh badge immediately, instead of waiting out the interval.
    const tick = () => {
      if (document.visibilityState === "visible") check();
    };
    check();
    const iv = setInterval(tick, HEALTH_POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(iv);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const clean = query.trim();
    if (!clean) return;
    router.push(`/memories/?q=${encodeURIComponent(clean)}`);
  };

  return (
    <>
    <header className="premium-header sticky top-0 z-30 flex h-[68px] items-center gap-4 px-4 sm:px-6 lg:px-8">
      <form onSubmit={submit} className="relative hidden w-full max-w-xl md:block">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search memories, people, projects…"
          className="premium-search h-10 w-full rounded-xl border pl-10 pr-16 text-sm outline-none transition focus:ring-2 focus:ring-primary/20"
        />
        <span className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md border bg-background/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">⌘ K</span>
      </form>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {online !== null && (
          <div className={`status-pill hidden sm:flex ${online ? "is-online" : "is-offline"}`}>
            {online ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {online ? "Local core online" : "Core offline"}
          </div>
        )}
        <ThemeSwitcher />
        <button className="icon-button hidden sm:grid" aria-label="Help" onClick={() => setHelpOpen(true)}><CircleHelp className="h-4 w-4" /></button>
        <button className="icon-button relative hidden sm:grid" aria-label="Notifications" onClick={() => setNotifOpen(true)}>
          <Bell className="h-4 w-4" />
          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-primary" />
        </button>
        <Link href="/#quick-capture" className="capture-button">
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">Quick capture</span>
        </Link>
      </div>
    </header>

    {/* Help Dialog */}
    <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CircleHelp className="h-5 w-5" /> Quick Help
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="space-y-2">
            <p className="font-medium">Keyboard shortcuts</p>
            <div className="grid grid-cols-2 gap-1 text-muted-foreground">
              <kbd className="bg-muted rounded px-1.5 py-0.5 text-xs">⌘ K</kbd><span>Search memories</span>
              <kbd className="bg-muted rounded px-1.5 py-0.5 text-xs">⌘ ⇧ A</kbd><span>Quick capture</span>
            </div>
          </div>
          <div className="space-y-2">
            <p className="font-medium">Pages</p>
            <ul className="text-muted-foreground space-y-1">
              <li><strong>Overview</strong> — Dashboard with stats, recent memories, knowledge graph</li>
              <li><strong>Memories</strong> — Search and browse all stored memories</li>
              <li><strong>Projects</strong> — Create projects to group related memories</li>
              <li><strong>Settings</strong> — Connect AI clients, connectors, backup</li>
            </ul>
          </div>
          <div className="space-y-2">
            <p className="font-medium">Learn more</p>
            <a href="https://github.com/ali-ulu/levh" target="_blank" rel="noopener" className="flex items-center gap-1 text-primary hover:underline">
              GitHub Repository <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      </DialogContent>
    </Dialog>

    {/* Notifications Panel */}
    <Dialog open={notifOpen} onOpenChange={setNotifOpen}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bell className="h-5 w-5" /> Notifications
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="text-center py-8 text-sm text-muted-foreground">
            <Bell className="h-8 w-8 mx-auto mb-2 opacity-30" />
            <p>No notifications yet.</p>
            <p className="text-xs mt-1">Memories stored or recalled by connected AI clients will appear here.</p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    </>
  );
}
