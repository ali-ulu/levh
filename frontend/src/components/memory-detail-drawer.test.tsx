import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryDetailDrawer } from "./memory-detail-drawer";
import type { Memory } from "@/types";

// The data-loading effects fire on mount; leaving them pending keeps the test
// focused on keyboard/focus behaviour without act() churn from resolved fetches.
vi.mock("@/lib/api", () => {
  const pending = () => new Promise(() => {});
  return {
    api: {
      memoryTrust: pending,
      fetchScoreBreakdown: pending,
      fetchForgettingCurve: pending,
      relatedMemories: pending,
      pinMemory: pending,
      deleteMemory: pending,
      reinforceMemory: pending,
      memoryFeedback: pending,
    },
  };
});

const memory = {
  id: "m-1",
  content: "Remember this",
  memory_type: "episodic",
  pinned: false,
} as Memory;

describe("MemoryDetailDrawer accessibility", () => {
  it("exposes dialog semantics labelled by its title", () => {
    render(<MemoryDetailDrawer memory={memory} onClose={() => {}} />);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Memory Details");
  });

  it("moves focus into the drawer and restores it on close", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "open";
    document.body.appendChild(trigger);
    trigger.focus();

    const { unmount } = render(
      <MemoryDetailDrawer memory={memory} onClose={() => {}} />
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);

    unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<MemoryDetailDrawer memory={memory} onClose={onClose} />);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});