import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LiveFeed } from "./live-feed";
import type { LiveEvent } from "@/types";

const live = vi.fn<() => { events: LiveEvent[]; connected: boolean }>();

vi.mock("@/lib/use-live-events", () => ({
  useLiveEvents: () => live(),
}));

describe("LiveFeed accessibility", () => {
  beforeEach(() => live.mockReset());

  it("announces the empty wait state politely", () => {
    live.mockReturnValue({ events: [], connected: false });
    render(<LiveFeed />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent(/Waiting for activity/);
  });

  it("renders events in a polite live log region", () => {
    live.mockReturnValue({
      events: [
        { event: "stored", payload: { content: "hello" }, receivedAt: Date.now() },
      ],
      connected: true,
    });
    render(<LiveFeed />);

    const log = screen.getByRole("log");
    expect(log).toHaveAttribute("aria-live", "polite");
    expect(log).toHaveTextContent("hello");
  });
});