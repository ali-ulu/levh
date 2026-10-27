"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

// The header advertises "⌘ K — Search memories" and there was no listener
// behind it, so the shortcut did nothing. This is that listener's target: a
// cross-entity search dialog (#564) over memories, entities, projects and
// sessions, all on the client from endpoints that already exist.
//
// The dialog is the access surface, so it goes through the Dialog primitive
// rather than a hand-rolled overlay — focus trapping, Escape and aria-modal
// come from Radix, and the aria-label work stays out of this file.

const DEBOUNCE_MS = 250;
const MEMORY_LIMIT = 6;
const ENTITY_LIMIT = 4;
const PROJECT_LIMIT = 4;
const SESSION_LIMIT = 4;
const MIN_QUERY = 2;

type GroupKey = "entities" | "projects" | "sessions" | "memories";

type Row = {
  key: string;
  group: GroupKey;
  label: string;
  meta?: string;
  onSelect: () => void;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function CommandPalette({ open, onOpenChange }: Props) {
  const router = useRouter();
  const t = useT();
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  // Neither the parent callback nor the router object is guaranteed to be
  // referentially stable (a parent can pass an inline arrow; a test double can
  // return a fresh router per render). Holding both in refs keeps the search
  // effect keyed on the query alone — putting either in the dependency array
  // re-runs the effect on every render, which sets state, which renders again:
  // an infinite loop.
  const onOpenChangeRef = useRef(onOpenChange);
  const routerRef = useRef(router);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
    routerRef.current = router;
  });

  // Reset on every open so a previous search never flashes before the new one.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setRows([]);
    setError(false);
    setLoading(false);
    setActive(0);
  }, [open]);

  useEffect(() => {
    const clean = query.trim();
    if (clean.length < MIN_QUERY) {
      setRows([]);
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
      // Each source settles independently: a missing entity index should not
      // blank the memory hits, so the calls never reject as a group.
      Promise.allSettled([
        api.recallMemories(clean, MEMORY_LIMIT),
        api.searchEntities(clean, "", ENTITY_LIMIT),
        api.listProjects(),
        api.listSessions(100),
      ])
        .then(([memories, entities, projects, sessions]) => {
          if (cancelled) return;
          const next: Row[] = [];
          const needle = clean.toLowerCase();
          const countIn = (group: GroupKey) => next.filter((r) => r.group === group).length;

          if (entities.status === "fulfilled") {
            for (const e of entities.value.entities.slice(0, ENTITY_LIMIT)) {
              next.push({
                key: `entity:${e.id}`,
                group: "entities",
                label: e.name,
                meta: e.type,
                onSelect: () => {
                  onOpenChangeRef.current(false);
                  routerRef.current.push(`/graph/?q=${encodeURIComponent(e.name)}`);
                },
              });
            }
          }

          if (projects.status === "fulfilled") {
            for (const p of projects.value.projects) {
              if (countIn("projects") >= PROJECT_LIMIT) break;
              if (!p.name.toLowerCase().includes(needle)) continue;
              next.push({
                key: `project:${p.name}`,
                group: "projects",
                label: p.name,
                meta: t("commandPalette.projectHint"),
                onSelect: () => {
                  onOpenChangeRef.current(false);
                  routerRef.current.push(`/memories/?project=${encodeURIComponent(p.name)}`);
                },
              });
            }
          }

          if (sessions.status === "fulfilled") {
            for (const s of sessions.value) {
              if (countIn("sessions") >= SESSION_LIMIT) break;
              if (!s.name.toLowerCase().includes(needle)) continue;
              next.push({
                key: `session:${s.id}`,
                group: "sessions",
                label: s.name,
                meta: t("commandPalette.sessionHint"),
                onSelect: () => {
                  onOpenChangeRef.current(false);
                  routerRef.current.push(`/memories/?session=${encodeURIComponent(s.id)}`);
                },
              });
            }
          }

          if (memories.status === "fulfilled") {
            for (const m of memories.value.memories.slice(0, MEMORY_LIMIT)) {
              next.push({
                key: `memory:${m.id}`,
                group: "memories",
                label: m.content,
                meta: m.project ?? undefined,
                onSelect: () => {
                  onOpenChangeRef.current(false);
                  routerRef.current.push(`/memories/?q=${encodeURIComponent(m.content)}`);
                },
              });
            }
          }

          setRows(next);
          setActive(0);
          // Only surface an error when nothing answered; a partial result set
          // is still a useful answer.
          setError(
            next.length === 0 &&
              [memories, entities, projects, sessions].every((r) => r.status === "rejected")
          );
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // `t` is a `useCallback` keyed on the locale, so it only changes on a
    // locale flip; `router` and `onOpenChange` are held in refs above.
  }, [query, t]);


  const go = (row: Row | undefined) => {
    if (!row) return;
    row.onSelect();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!rows.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % rows.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + rows.length) % rows.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(rows[active]);
    }
  };

  // Keep the highlighted row in view when navigating by keyboard.
  useEffect(() => {
    const node = listRef.current?.querySelectorAll<HTMLElement>("button")[active];
    node?.scrollIntoView({ block: "nearest" });
  }, [active]);

  // Group headings are derived from the rows that arrived, so an empty source
  // never leaves a title over nothing.
  const groups: GroupKey[] = [];
  for (const row of rows) {
    if (!groups.includes(row.group)) groups.push(row.group);
  }

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
              : query.trim().length >= MIN_QUERY
                ? t(
                    rows.length === 1
                      ? "commandPalette.results.one"
                      : "commandPalette.results.other",
                    { count: rows.length }
                  )
                : ""}
          </p>

          {error && (
            <p className="px-3 py-2 text-sm text-destructive">
              {t("commandPalette.searchFailed")}
            </p>
          )}

          {!error && query.trim().length >= MIN_QUERY && !loading && rows.length === 0 && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              {t("commandPalette.noMatch", { query: query.trim() })}
            </p>
          )}

          {query.trim().length < MIN_QUERY && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              {t("commandPalette.empty")}
            </p>
          )}

          <div ref={listRef}>
            {groups.map((group) => (
              <div key={group}>
                <p className="px-3 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t(`commandPalette.group.${group}`)}
                </p>
                <ul>
                  {rows
                    .filter((row) => row.group === group)
                    .map((row) => {
                      const index = rows.indexOf(row);
                      return (
                        <li key={row.key}>
                          <button
                            type="button"
                            onClick={() => go(row)}
                            onMouseEnter={() => setActive(index)}
                            className={cn(
                              "w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                              index === active
                                ? "bg-accent text-accent-foreground"
                                : "hover:bg-accent/50"
                            )}
                          >
                            <span className="line-clamp-2">{row.label}</span>
                            {row.meta && (
                              <span className="mt-0.5 block text-xs text-muted-foreground">
                                {row.meta}
                              </span>
                            )}
                          </button>
                        </li>
                      );
                    })}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
