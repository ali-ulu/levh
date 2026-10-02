import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
// The same module CI's `node scripts/check-ui-strings.mjs` runs. Importing it
// here proves the detector against synthetic files; the child-process check
// proves the committed baseline is in sync with the tree.
import { scanFile } from "../../scripts/check-ui-strings.mjs";

// Vitest runs with the frontend package as cwd (`npm test`), which is where the
// script and its baseline live.
const FRONTEND = process.cwd();

function scanSource(source: string): string[] {
  const dir = mkdtempSync(join(tmpdir(), "levh-i18n-"));
  try {
    const file = join(dir, "Sample.tsx");
    writeFileSync(file, source);
    return scanFile(file);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

describe("the UI string detector", () => {
  it("flags a hardcoded JSX text node", () => {
    expect(scanSource(`export const A = () => <h1>Settings</h1>;`)).toContain("Settings");
  });

  it("flags a hardcoded aria-label", () => {
    expect(scanSource(`export const A = () => <button aria-label="Close">x</button>;`)).toContain("Close");
  });

  it("flags a hardcoded placeholder", () => {
    expect(scanSource(`export const A = () => <input placeholder="Paste the server token" />;`)).toContain(
      "Paste the server token"
    );
  });

  it("ignores a string that is a catalogue key", () => {
    expect(scanSource(`export const A = () => <h1>{t("app.settings.title")}</h1>;`)).toEqual([]);
  });

  it("ignores a catalogue key held in a data model, not a t() call", () => {
    // The sidebar keeps its nav keys in an array; counting those would make a
    // fully-converted file look unconverted. The key must exist in the
    // catalogue to qualify, so this cannot swallow arbitrary strings.
    expect(
      scanSource(`const groups = [{ labelKey: "sidebar.group.memory" }];`)
    ).toEqual([]);
    expect(scanSource(`const x = "not.a.catalogued.key";`)).toContain("not.a.catalogued.key");
  });

  it("still flags a catalogue key literal in a user-facing attribute", () => {
    // `aria-label="header.action.help"` renders the key to a screen reader.
    // The data-model exemption must not reach user-facing attributes, or the
    // half-translated page the gate exists to catch slips through.
    expect(scanSource(`<button aria-label="header.action.help">x</button>`)).toContain(
      "header.action.help"
    );
  });

  it("ignores Tailwind classes and module specifiers", () => {
    const source = `import { cn } from "@/lib/utils";
export const A = () => <div className={cn("flex flex-col gap-2", "text-sm")} />;`;
    expect(scanSource(source)).toEqual([]);
  });

  it("ignores a decorative aria-hidden glyph", () => {
    expect(scanSource(`export const A = () => <span aria-hidden="true">x</span>;`)).toEqual([]);
  });
});

describe("the committed ratchet is in sync with the tree", () => {
  it("passes the same check CI runs", () => {
    // A non-zero exit means a literal was added to a counted file without
    // lowering another, or a conversion left a stale baseline entry. Both are
    // reported by the script itself; let it speak for itself.
    expect(() =>
      execFileSync("node", ["scripts/check-ui-strings.mjs"], { cwd: FRONTEND, stdio: "pipe" })
    ).not.toThrow();
  });
});
