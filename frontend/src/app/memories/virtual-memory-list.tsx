"use client";

import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useT } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Memory } from "@/types";
import { Clock, Eye, FolderGit2, Pencil, Pin, PinOff, Trash2 } from "lucide-react";

interface Props {
  memories: Memory[];
  selectedIds: Set<string>;
  onToggleSelect: (m: Memory, index: number, shiftKey: boolean) => void;
  onOpen: (m: Memory) => void;
  onPin: (m: Memory) => void;
  onEdit: (m: Memory) => void;
  onDelete: (m: Memory) => void;
  onTagClick: (tag: string) => void;
  onSelectAll: (checked: boolean) => void;
}

function relativeTime(dateStr: string, t: ReturnType<typeof useT>) {
  const time = new Date(dateStr).getTime();
  if (!Number.isFinite(time)) return "";
  const delta = Math.max(0, Date.now() - time);
  const minutes = Math.floor(delta / 60000);
  if (minutes < 1) return t("app.memories.relative.justNow");
  if (minutes < 60) return t("app.memories.relative.minutes", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("app.memories.relative.hours", { count: hours });
  const days = Math.floor(hours / 24);
  if (days < 30) return t("app.memories.relative.days", { count: days });
  return new Date(dateStr).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** Windowed memories list (#561). Only the rows intersecting the viewport plus
 * a small overscan are in the DOM, so a 200-item result scrolls without the
 * layout thrash the full `map()` caused. Row markup is unchanged from the
 * previous non-virtual list. */
export function VirtualMemoryList({
  memories,
  selectedIds,
  onToggleSelect,
  onOpen,
  onPin,
  onEdit,
  onDelete,
  onTagClick,
  onSelectAll,
}: Props) {
  const t = useT();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);

  // `useVirtualizer` returns functions React Compiler cannot memoize, so it
  // skips this component. That is the library's documented behavior and costs
  // nothing here: the list re-renders only when the window of rows changes.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: memories.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 96,
    overscan: 5,
    // jsdom never lays the scroll container out, so the measured height is 0
    // and the virtualizer would render an empty window. A fixed initial rect
    // keeps the first screen of rows present in tests and during the prerender.
    initialRect: { width: 1000, height: 720 },
    getItemKey: (index) => memories[index]?.id ?? index,
  });

  const items = virtualizer.getVirtualItems();
  const allSelected = memories.length > 0 && memories.every((m) => selectedIds.has(m.id));

  const moveFocus = (from: number, delta: number) => {
    const next = Math.max(0, Math.min(memories.length - 1, from + delta));
    virtualizer.scrollToIndex(next, { align: "auto" });
    // The row may mount only after the scroll, so defer the focus a frame.
    requestAnimationFrame(() => rowRefs.current[next]?.focus());
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <input
          id="memories-select-all"
          type="checkbox"
          className="h-4 w-4 accent-primary"
          checked={allSelected}
          onChange={(e) => onSelectAll(e.target.checked)}
        />
        <label htmlFor="memories-select-all" className="text-xs text-muted-foreground">
          {t("bulk.selectAll")}
        </label>
      </div>

      <div
        ref={scrollRef}
        className="h-[calc(100vh-22rem)] min-h-[400px] overflow-auto"
        data-testid="memory-virtual-scroll"
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a") {
            e.preventDefault();
            onSelectAll(true);
          }
        }}
      >
        <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
          {items.map((row) => {
            const m = memories[row.index];
            if (!m) return null;
            const checked = selectedIds.has(m.id);
            return (
              <div
                key={row.key}
                data-index={row.index}
                ref={(el) => {
                  virtualizer.measureElement(el);
                  rowRefs.current[row.index] = el;
                }}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${row.start}px)`,
                }}
                className="pb-2"
              >
                <div
                  className={`memory-card group ${m.pinned ? "pinned" : ""} ${checked ? "ring-1 ring-primary" : ""}`}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      moveFocus(row.index, 1);
                    } else if (e.key === "ArrowUp") {
                      e.preventDefault();
                      moveFocus(row.index, -1);
                    }
                  }}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0 accent-primary"
                      checked={checked}
                      aria-label={t("bulk.selectRow", { preview: m.content.slice(0, 60) })}
                      // A controlled checkbox fires both `change` and `click`;
                      // driving selection from `click` alone keeps Shift+click
                      // (range select) from toggling the row twice.
                      onChange={() => {}}
                      onClick={(e) => onToggleSelect(m, row.index, e.shiftKey)}
                    />

                    <div
                      className={`memory-type-indicator ${
                        m.memory_type === "episodic" ? "episodic" : "short-term"
                      }`}
                    />

                    <div className="flex-1 min-w-0">
                      <p className="text-sm leading-relaxed line-clamp-2">{m.content}</p>
                      <div className="flex flex-wrap items-center gap-1.5 mt-2">
                        {m.pinned && (
                          <Badge
                            variant="secondary"
                            className="text-[11px] bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20"
                          >
                            <Pin className="h-2.5 w-2.5 mr-1" />
                            {t("app.memories.pinned")}
                          </Badge>
                        )}
                        {m.project && (
                          <Badge variant="outline" className="text-[11px]">
                            <FolderGit2 className="h-2.5 w-2.5 mr-1" />
                            {m.project}
                          </Badge>
                        )}
                        {m.source && (
                          <Badge variant="outline" className="text-[11px]">
                            {m.source}
                          </Badge>
                        )}
                        {m.tags.slice(0, 4).map((tag) => (
                          <button
                            key={tag}
                            onClick={(e) => {
                              e.stopPropagation();
                              onTagClick(tag);
                            }}
                            className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded border border-dashed border-muted-foreground/30 text-muted-foreground hover:border-primary/50 hover:text-primary transition-colors"
                          >
                            {tag}
                          </button>
                        ))}
                        {m.tags.length > 4 && (
                          <span className="text-[10px] text-muted-foreground/60">
                            +{m.tags.length - 4}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="flex items-center gap-1 text-[11px] text-muted-foreground tabular-nums">
                        <Clock className="h-3 w-3" />
                        {relativeTime(m.created_at, t)}
                      </span>
                      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => onPin(m)}
                          aria-label={m.pinned ? t("app.memories.action.unpin") : t("app.memories.action.pin")}
                        >
                          {m.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => onOpen(m)}
                          aria-label={t("app.memories.action.view")}
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => onEdit(m)}
                          aria-label={t("app.memories.action.edit")}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => onDelete(m)}
                          aria-label={t("app.memories.action.delete")}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
