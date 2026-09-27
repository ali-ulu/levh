import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

// The palette is the one surface a keyboard user reaches before they see any
// page, and it is a dialog, so it has to be gated like one: a named dialog, a
// labelled search field, and a result list a screen reader can announce. The
// E2E spec proves the shortcut is wired; this proves the thing it opens is
// usable without a mouse.

const recallMemories = vi.hoisted(() => vi.fn());
const push = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { recallMemories } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { CommandPalette } from "./command-palette";

describe("Command palette is accessible", () => {
  it("the open dialog has no violations", async () => {
    recallMemories.mockResolvedValue({ memories: [], scores: [] });
    const { container } = render(<CommandPalette open onOpenChange={() => {}} />);

    expect(await screen.findByRole("dialog", { name: "Search memories" })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("exposes a labelled search field and a live result count", async () => {
    recallMemories.mockResolvedValue({
      memories: [
        {
          id: "m-1",
          content: "Atlas moved to PostgreSQL.",
          project: "atlas",
        },
      ],
      scores: [0.9],
    });
    render(<CommandPalette open onOpenChange={() => {}} />);

    const field = screen.getByRole("textbox", { name: "Search memories" });
    // Debounced: the recall call only fires after the input settles, and the
    // live region only reports a count once results exist.
    fireEvent.change(field, { target: { value: "atlas" } });

    // The count is announced from a live region rather than by making the
    // list an ARIA listbox — selection state we do not implement would be
    // worse than none.
    expect(await screen.findByText("1 result")).toBeInTheDocument();
    expect(await screen.findByText("Atlas moved to PostgreSQL.")).toBeInTheDocument();
  });
});
