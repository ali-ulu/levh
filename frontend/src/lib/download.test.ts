import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadText, memoriesToCsv, toCsv } from "./download";
import type { Memory } from "@/types";

const memory = (over: Partial<Memory>): Memory =>
  ({
    id: "m-1",
    content: "hello",
    memory_type: "episodic",
    importance: 0.5,
    pinned: false,
    project: null,
    source: null,
    tags: [],
    created_at: "2026-01-01T00:00:00Z",
    accessed_at: "2026-01-02T00:00:00Z",
    ...over,
  }) as unknown as Memory;

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("toCsv", () => {
  it("quotes a cell that contains a comma, quote or newline", () => {
    const csv = toCsv(["a", "b"], [['he said "hi"', "x,y"]]);
    expect(csv).toBe('a,b\n"he said ""hi""","x,y"');
  });

  it("renders null and undefined as empty cells, not the strings", () => {
    expect(toCsv(["a", "b"], [[null, undefined]])).toBe("a,b\n,");
  });
});

describe("memoriesToCsv", () => {
  it("emits one header row and one row per memory", () => {
    const csv = memoriesToCsv([memory({ id: "m-1" }), memory({ id: "m-2" })]);
    const lines = csv.split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe(
      "id,content,memory_type,importance,pinned,project,source,tags,created_at,accessed_at",
    );
    expect(lines[1].startsWith("m-1,")).toBe(true);
    expect(lines[2].startsWith("m-2,")).toBe(true);
  });

  it("joins tags with a space so the row stays rectangular", () => {
    const csv = memoriesToCsv([memory({ tags: ["alpha", "beta"] })]);
    expect(csv.split("\n")[1]).toContain("alpha beta");
  });
});

describe("downloadText", () => {
  it("creates an object URL, clicks a download anchor, then revokes it", () => {
    const revoke = vi.fn();
    const create = vi.fn().mockReturnValue("blob:fake");
    vi.stubGlobal("URL", { ...URL, createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadText("out.txt", "body", "text/plain");

    expect(create).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    // The blob must not leak: the URL is revoked after the click.
    expect(revoke).toHaveBeenCalledWith("blob:fake");
    expect(document.querySelector("a")).toBeNull();
  });
});
