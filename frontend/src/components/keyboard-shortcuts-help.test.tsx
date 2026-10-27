import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { KeyboardShortcutsHelp } from "./keyboard-shortcuts-help";

// The shortcuts help modal (#568) is opened by `?` and is the only place the
// dashboard documents its own keyboard surface, so it has to be readable by a
// screen reader and axe-clean like every other dialog.

describe("KeyboardShortcutsHelp", () => {
  it("renders a named dialog with grouped shortcuts and no violations", async () => {
    const { baseElement } = render(<KeyboardShortcutsHelp open onOpenChange={() => {}} />);

    expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    // One representative row per group.
    expect(screen.getByText("Open search")).toBeInTheDocument();
    expect(screen.getByText("Go to memories")).toBeInTheDocument();
    expect(screen.getByText("Zoom in")).toBeInTheDocument();
    expect(await axe(baseElement)).toHaveNoViolations();
  });
});
