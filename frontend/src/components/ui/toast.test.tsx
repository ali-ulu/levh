import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Toaster, toast, __resetToasts } from "./toast";

// The toast surface (#555) is the only place a failed API call becomes visible
// to the user, so it is worth its own test: the store lives outside React and
// the render is a portal, both of which are easy to get wrong.

describe("Toaster", () => {
  beforeEach(() => {
    __resetToasts();
  });

  it("renders a toast pushed before mount from a plain module call", () => {
    // `toast.*` is called from `lib/api.ts`, which has no React tree. The
    // store must survive the ordering where the message exists before the
    // component does.
    toast.error("Could not save memory");
    render(<Toaster />);
    expect(screen.getByText("Could not save memory")).toBeInTheDocument();
  });

  it("shows success, error and info variants", () => {
    render(<Toaster />);
    act(() => {
      toast.success("Saved");
      toast.info("Heads up");
    });
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("Heads up")).toBeInTheDocument();
  });

  it("dismisses a toast when its close button is clicked", async () => {
    const user = userEvent.setup();
    render(<Toaster />);
    act(() => {
      toast.error("Gone soon");
    });
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Gone soon")).not.toBeInTheDocument();
  });

  it("announces toasts in a polite live region", () => {
    render(<Toaster />);
    act(() => {
      toast.info("Waiting");
    });
    const region = screen.getByText("Waiting").closest("[aria-live]");
    expect(region).toHaveAttribute("aria-live", "polite");
  });
});
