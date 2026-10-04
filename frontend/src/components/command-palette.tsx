"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { Memory } from "@/types";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

// The header advertises "⌘ K — Search memories" and there was no listener
// behind it, so the shortcut did nothing. This is that listener's target: a
// search dialog over the same recall endpoint the header's Enter key uses.
//
// The dialog is the access surface, so it goes through the Dialog primitive
// rather than a hand-rolled overlay — focus trapping, Escape and aria-modal
// come from Radix, and the aria-label work stays out of this file.

const DEBOUNCE_MS = 250;
const RESULT_LIMIT = 8;

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function CommandPalette({ open, onOpenChange }: Props) {
  const router = useRouter();
  const t = useT();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  // Reset on every open so a previous search never flashes before the new one.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setError(false);
    setLoading(false);
    setActive(0);
  }, [open]);

  useEffect(() => {
    const clean = query.trim();
    if (!clean) {
      setResults([]);
      setLoading(false);
      setError(false);
      return;
    }

    // Cancellation matters more here than in a page: results arrive after a
    // debounce and the user can keep typing, so a stale response must not
    // overwrite a newer one.
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .recallMemories(clean, RESULT_LIMIT)
        .then((data) => {
          if (cancelled) return;
          setResults(data.memories);
          setActive(0);
          setError(false);
        })
        .catch(() => {
          if (cancelled) return;
          setResults([]);
          setError(true);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const go = (memory: Memory) => {
    onOpenChange(false);
    router.push(`/memories/?q=${encodeURIComponent(memory.content)}`);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!results.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(results[active]);
    }
  };

  // Keep the highlighted row in view when navigating by keyboard.
  useEffect(() => {
    const node = listRef.current?.children[active] as HTMLElement | undefined;
    node?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="top-[12%] max-w-xl translate-y-0 gap-0 p-0"
        aria-describedby={undefined}
        onKeyDown={onKeyDown}
      >
        <DialogTitle className="sr-only">{t("commandPalette.title")}</DialogTitle>

        <div className="flex items-center gap-2 border-b px-4">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label={t("commandPalette.searchLabel")}
            placeholder={t("commandPalette.placeholder")}
            className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>

        <div className="max-h-[55vh] overflow-y-auto p-2">
          {/* A live region so a screen reader hears "3 results" without the
              list itself having to be an ARIA listbox with selection state. */}
          <p aria-live="polite" className="sr-only">
            {loading
              ? t("commandPalette.searching")
              : query.trim()
                ? t(
                    results.length === 1
                      ? "commandPalette.results.one"
                      : "commandPalette.results.other",
                    { count: results.length },
                  )
                : ""}
          </p>

          {error && (
            <p className="px-3 py-2 text-sm text-destructive">
              {t("commandPalette.searchFailed")}
            </p>
          )}

          {!error && query.trim() && !loading && results.length === 0 && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              {t("commandPalette.noMatch", { query: query.trim() })}
            </p>
          )}

          {!query.trim() && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              {t("commandPalette.empty")}
            </p>
          )}

          <ul ref={listRef}>
            {results.map((memory, index) => (
              <li key={memory.id}>
                <button
                  type="button"
                  onClick={() => go(memory)}
                  onMouseEnter={() => setActive(index)}
                  className={cn(
                    "w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                    index === active ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                  )}
                >
                  <span className="line-clamp-2">{memory.content}</span>
                  {memory.project && (
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      {memory.project}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </DialogContent>
    </Dialog>
  );
}
