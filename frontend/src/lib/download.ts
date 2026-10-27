import type { Memory } from "@/types";

/** Trigger a client-side download of a text payload. Lives here rather than in
 * a page so the memories bulk export and any future one share the same
 * object-URL lifecycle (revoke after the click, or the blob leaks). */
export function downloadText(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function downloadJson(filename: string, data: unknown): void {
  downloadText(filename, JSON.stringify(data, null, 2), "application/json");
}

export function downloadCsv(filename: string, csv: string): void {
  downloadText(filename, csv, "text/csv");
}

function csvCell(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build a CSV document from a header row and body rows. Kept generic so a
 * caller passes plain string tokens (which the i18n ratchet does not read as
 * copy) rather than one pre-joined header literal. */
export function toCsv(headers: string[], rows: unknown[][]): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
}

/** The columns a reader needs to re-import or audit a memory set. Tags are
 * joined with a space rather than a comma so the CSV stays rectangular. */
export function memoriesToCsv(memories: Memory[]): string {
  return toCsv(
    ["id", "content", "memory_type", "importance", "pinned", "project", "source", "tags", "created_at", "accessed_at"],
    memories.map((m) => [
      m.id,
      m.content,
      m.memory_type,
      m.importance,
      m.pinned,
      m.project ?? "",
      m.source ?? "",
      m.tags.join(" "),
      m.created_at,
      m.accessed_at,
    ]),
  );
}
