import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The palette is the one surface a keyboard user reaches before they see any
// page, and it is a dialog, so it has to be gated like one: a named dialog, a
// labelled search field, and result groups a screen reader can announce. The
// E2E spec proves the shortcut is wired; this proves the thing it opens is
// usable without a mouse, and that a query fans out across the four sources
// (#564) rather than memories alone.

const mocks = vi.hoisted(() => ({
  recallMemories: vi.fn(),
  searchEntities: vi.fn(),
  listProjects: vi.fn(),
  listSessions: vi.fn(),
  push: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    recallMemories: mocks.recallMemories,
    searchEntities: mocks.searchEntities,
    listProjects: mocks.listProjects,
    listSessions: mocks.listSessions,
  },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

import { CommandPalette } from "./command-palette";

describe("Command palette is accessible", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.recallMemories.mockResolvedValue({ memories: [], scores: [] });
    mocks.searchEntities.mockResolvedValue({ entities: [] });
    mocks.listProjects.mockResolvedValue({ projects: [] });
    mocks.listSessions.mockResolvedValue([]);
  });

  it("the open dialog has no violations", async () => {
    const { container } = render(<CommandPalette open onOpenChange={() => {}} />);

    expect(await screen.findByRole("dialog", { name: "Search memories" })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("exposes a labelled search field and a live result count", async () => {
    mocks.recallMemories.mockResolvedValue({
      memories: [{ id: "m-1", content: "Atlas moved to PostgreSQL.", project: "atlas" }],
      scores: [0.9],
    });
    render(<CommandPalette open onOpenChange={() => {}} />);

    const field = screen.getByRole("textbox", { name: "Search memories" });
    fireEvent.change(field, { target: { value: "atlas" } });

    // The count is announced from a live region rather than by making the
    // list an ARIA listbox — selection state we do not implement would be
    // worse than none.
    expect(await screen.findByText("1 result")).toBeInTheDocument();
    expect(await screen.findByText("Atlas moved to PostgreSQL.")).toBeInTheDocument();
  });

  it("groups cross-entity hits and deep-links the selected row (#564)", async () => {
    mocks.recallMemories.mockResolvedValue({
      memories: [{ id: "m-1", content: "Atlas migrated the billing service." }],
      scores: [0.8],
    });
    mocks.searchEntities.mockResolvedValue({
      entities: [{ id: "e-1", type: "organization", name: "Atlas Corp" }],
    });
    mocks.listProjects.mockResolvedValue({
      projects: [{ name: "atlas", memory_count: 4, last_used: null }],
    });
    mocks.listSessions.mockResolvedValue([
      { id: "s-1", name: "atlas planning", status: "active", memory_count: 2 },
    ]);
    render(<CommandPalette open onOpenChange={() => {}} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Search memories" }), {
      target: { value: "atlas" },
    });

    // Each source lands in its own labelled section.
    expect(await screen.findByText("Entities")).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Memories")).toBeInTheDocument();

    // An entity row opens the graph filtered to it, not the memories page.
    fireEvent.click(screen.getByRole("button", { name: /Atlas Corp/ }));
    await waitFor(() =>
      expect(mocks.push).toHaveBeenCalledWith(`/graph/?q=${encodeURIComponent("Atlas Corp")}`)
    );
  });
});
