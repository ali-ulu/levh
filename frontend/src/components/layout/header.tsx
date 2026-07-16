"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { ThemeSwitcher } from "@/components/layout/theme-switcher";
import { Bell, CircleHelp, Plus, Search, Wifi, WifiOff } from "lucide-react";

export function Header() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const router = useRouter();

  useEffect(() => {
    const check = () => api.health().then(() => setOnline(true)).catch(() => setOnline(false));
    check();
    const iv = setInterval(check, 15000);
    return () => clearInterval(iv);
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const clean = query.trim();
    if (!clean) return;
    router.push(`/memories/?q=${encodeURIComponent(clean)}`);
  };

  return (
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
        <button className="icon-button hidden sm:grid" aria-label="Help"><CircleHelp className="h-4 w-4" /></button>
        <button className="icon-button relative hidden sm:grid" aria-label="Notifications">
          <Bell className="h-4 w-4" />
          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-primary" />
        </button>
        <Link href="/#quick-capture" className="capture-button">
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">Quick capture</span>
        </Link>
      </div>
    </header>
  );
}
