import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

// Mobile navigation (#557): below `lg` the fixed sidebar is hidden, so this
// Sheet is the only navigation surface. It has to behave like a dialog —
// named, focus-trapped, Escape-closable — or the fix is worse than the bug.

vi.mock("next/navigation", () => ({
  usePathname: () => "/memories",
}));

import { MobileSidebar } from "./sidebar";

describe("MobileSidebar", () => {
  it("renders the nav as a named dialog with no a11y violations", async () => {
    const { baseElement } = render(<MobileSidebar open onOpenChange={() => {}} />);

    expect(await screen.findByRole("dialog", { name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /All memories/ })).toBeInTheDocument();
    expect(await axe(baseElement)).toHaveNoViolations();
  });

  it("closes when a navigation link is followed", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<MobileSidebar open onOpenChange={onOpenChange} />);

    await user.click(await screen.findByRole("link", { name: /Knowledge graph/ }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("closes on Escape", async () => {
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    render(<MobileSidebar open onOpenChange={onOpenChange} />);

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
