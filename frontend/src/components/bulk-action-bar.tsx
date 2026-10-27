"use client";

import { Download, Loader2, Pin, PinOff, Trash2, X } from "lucide-react";
import { useT } from "@/lib/i18n";

export interface BulkActionBarProps {
  count: number;
  busy?: boolean;
  /** Each action is optional so a list with no mutations (the entity grid) can
   * reuse the bar for export alone. */
  onPin?: () => void;
  onUnpin?: () => void;
  onDelete?: () => void;
  onExportJson: () => void;
  onExportCsv: () => void;
  onClear: () => void;
}

/** The floating toolbar the memories and graph lists share once rows are
 * multi-selected (#562). Presentational: the page owns the selection and the
 * API calls, so the same bar works over any list of ids. */
export function BulkActionBar({
  count,
  busy,
  onPin,
  onUnpin,
  onDelete,
  onExportJson,
  onExportCsv,
  onClear,
}: BulkActionBarProps) {
  const t = useT();
  if (count === 0) return null;

  const btn =
    "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs hover:bg-accent disabled:opacity-50";

  return (
    <div
      role="toolbar"
      aria-label={t("bulk.toolbar.label")}
      className="fixed inset-x-0 bottom-4 z-40 mx-auto flex w-fit max-w-[calc(100vw-2rem)] flex-wrap items-center gap-1.5 rounded-xl border bg-background/95 p-2 shadow-lg backdrop-blur"
    >
      <span className="px-1.5 text-xs font-medium tabular-nums">
        {t("bulk.selected", { count })}
      </span>
      {onPin && (
        <button type="button" className={btn} onClick={onPin} disabled={busy}>
          <Pin className="h-3.5 w-3.5" />
          {t("bulk.pin", { count })}
        </button>
      )}
      {onUnpin && (
        <button type="button" className={btn} onClick={onUnpin} disabled={busy}>
          <PinOff className="h-3.5 w-3.5" />
          {t("bulk.unpin", { count })}
        </button>
      )}
      <button type="button" className={btn} onClick={onExportJson} disabled={busy}>
        <Download className="h-3.5 w-3.5" />
        {t("bulk.exportJson")}
      </button>
      <button type="button" className={btn} onClick={onExportCsv} disabled={busy}>
        <Download className="h-3.5 w-3.5" />
        {t("bulk.exportCsv")}
      </button>
      {onDelete && (
        <button
          type="button"
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-destructive/40 px-2.5 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
          onClick={onDelete}
          disabled={busy}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          {t("bulk.delete", { count })}
        </button>
      )}
      <button
        type="button"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-accent"
        onClick={onClear}
        aria-label={t("bulk.clear")}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
