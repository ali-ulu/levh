"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { MoonStar, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export function ThemeSwitcher({ compact = false }: { compact?: boolean }) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);
  if (!mounted) return <div className={cn("h-9 rounded-xl bg-muted/40", compact ? "w-9" : "w-36")} />;

  const isDark = resolvedTheme === "dark";
  const toggle = () => setTheme(isDark ? "light" : "dark");

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${isDark ? "Aurora Glass" : "Deep Space"} theme`}
      title={`Theme: ${isDark ? "Deep Space" : "Aurora Glass"}`}
      className={cn(
        "theme-switcher group relative flex h-9 items-center rounded-xl border border-white/20 px-1 text-xs font-medium shadow-sm transition-all hover:-translate-y-0.5",
        compact ? "w-9 justify-center" : "w-[146px] justify-between"
      )}
    >
      <span
        className={cn(
          "absolute inset-y-1 w-[66px] rounded-lg bg-white/75 shadow-sm transition-transform duration-300 dark:bg-white/10",
          compact ? "hidden" : isDark ? "translate-x-[70px]" : "translate-x-0"
        )}
      />
      {compact ? (
        isDark ? <MoonStar className="h-4 w-4" /> : <Sparkles className="h-4 w-4" />
      ) : (
        <>
          <span className={cn("relative z-10 flex w-[66px] items-center justify-center gap-1.5", !isDark && "text-slate-950")}> 
            <Sparkles className="h-3.5 w-3.5" /> Aurora
          </span>
          <span className={cn("relative z-10 flex w-[66px] items-center justify-center gap-1.5", isDark && "text-white")}> 
            <MoonStar className="h-3.5 w-3.5" /> Void
          </span>
        </>
      )}
    </button>
  );
}
