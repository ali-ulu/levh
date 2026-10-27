import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VirtualMemoryList } from "@/app/memories/virtual-memory-list";
import { LocaleProvider } from "@/lib/i18n";
import type { Memory } from "@/types";

const makeMemory = (i: number): Memory =>
  ({
    id: `m-${i}`,
    content: `Memory number ${i}`,
    memory_type: "episodic",
    importance: 0.5,
    pinned: false,
    project: null,
    source: null,
    tags: [],
    created_at: new Date().toISOString(),
    accessed_at: new Date().toISOString(),
  }) as unknown as Memory;

const noop = () => {};

describe("virtualized memories list (#561)", () => {
  it("mounts only a window of a large list", () => {
    const memories = Array.from({ length: 200 }, (_, i) => makeMemory(i));
    render(
      <LocaleProvider>
        <VirtualMemoryList
          memories={memories}
          selectedIds={new Set()}
          onToggleSelect={noop}
          onOpen={noop}
          onPin={noop}
          onEdit={noop}
          onDelete={noop}
          onTagClick={noop}
          onSelectAll={noop}
        />
      </LocaleProvider>,
    );

    const scroll = screen.getByTestId("memory-virtual-scroll");
    const mounted = scroll.querySelectorAll("[data-index]").length;

    // 200 rows exist in state, but only the viewport plus overscan are in the
    // DOM — the point of the change. Assert it is a small fraction, not a
    // magic number that would break the day the estimate changes.
    expect(mounted).toBeGreaterThan(0);
    expect(mounted).toBeLessThan(30);
    expect(screen.getByText("Memory number 0")).toBeInTheDocument();
    expect(screen.queryByText("Memory number 199")).not.toBeInTheDocument();
  });
});
