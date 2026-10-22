import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AutoUpdate } from "./auto-update";

// Issue #522: a checkout whose auto-update stopped was invisible everywhere a
// user looks. This section is the dashboard half of the fix — the report must
// name the skip and the command that resumes updates, and must stay silent on
// an install that never ran the supervisor (no state is normal, not a fault).

const mocks = vi.hoisted(() => ({ health: vi.fn() }));

// The mock returns the call through an arrow so the rejection is created lazily,
// at call time, rather than when the mock is configured.
vi.mock("@/lib/api", () => ({ api: { health: () => mocks.health() } }));

function status(overrides: Record<string, unknown> = {}) {
  return {
    present: true,
    status: "skipped",
    behind: 0,
    reason: "checkout is on 'fix/x', auto-update only follows 'main'",
    action: "run `levh update --branch fix/x`",
    needs_attention: true,
    stale: false,
    checked_at: "2026-10-22T12:00:00+00:00",
    ...overrides,
  };
}

describe("AutoUpdate", () => {
  beforeEach(() => mocks.health.mockReset());

  it("names the skip and the command that resumes updates", async () => {
    mocks.health.mockResolvedValue({ status: "ok", auto_update: status() });

    render(<AutoUpdate />);

    await waitFor(() =>
      expect(
        screen.getByText(/auto-update only follows 'main'/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/levh update --branch fix\/x/)).toBeInTheDocument();
    expect(screen.getByText("skipped")).toBeInTheDocument();
  });

  it("shows how far behind a stale checkout is", async () => {
    mocks.health.mockResolvedValue({
      status: "ok",
      auto_update: status({ behind: 3 }),
    });

    render(<AutoUpdate />);

    await waitFor(() =>
      expect(screen.getByText(/3 commit/)).toBeInTheDocument(),
    );
  });

  it("renders nothing when no supervisor state exists", async () => {
    mocks.health.mockResolvedValue({ status: "ok" });

    const { container } = render(<AutoUpdate />);

    await waitFor(() => expect(mocks.health).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when health cannot be read", async () => {
    // `mockRejectedValueOnce`, not `mockRejectedValue`: the persistent form
    // makes vitest 5.0.1 treat the stored rejection as unhandled and fail the
    // test before the assertion runs.
    mocks.health.mockRejectedValueOnce(new Error("offline"));

    const { container } = render(<AutoUpdate />);

    await waitFor(() => expect(mocks.health).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
