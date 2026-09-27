import { render, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Decision,
  EntityRow,
  Finding,
  Memory,
  Project,
  Source,
  TagCount,
  TimelineDay,
} from "@/types";

// Page-level accessibility. `ui.a11y.test.tsx` gates the primitives; this file
// gates the composition — the page shells that own the headings, the tab
// strips and the empty/loading states, none of which exist in a primitive
// test. A page can pass every primitive check and still ship an h1 that
// duplicates the layout's, a filter button with no accessible name, or a
// table with no headers.
//
// Every page is exercised with data present, not just its empty state. The
// empty state is the easy half: it renders one card and a paragraph. The
// populated state is where the icon-only row actions, the badges and the
// grouped lists actually appear, and it is the state users spend their time in.

const mocks = vi.hoisted(() => ({
  listMemories: vi.fn(),
  listProjects: vi.fn(),
  listSources: vi.fn(),
  listTags: vi.fn(),
  listDecisions: vi.fn(),
  timeline: vi.fn(),
  listFindings: vi.fn(),
  entityStats: vi.fn(),
  listEntities: vi.fn(),
  getEntity: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    listMemories: mocks.listMemories,
    listProjects: mocks.listProjects,
    listSources: mocks.listSources,
    listTags: mocks.listTags,
    listDecisions: mocks.listDecisions,
    timeline: mocks.timeline,
    listFindings: mocks.listFindings,
    entityStats: mocks.entityStats,
    listEntities: mocks.listEntities,
    getEntity: mocks.getEntity,
    // The drawer is reachable from several of these pages; leaving its loaders
    // pending keeps the assertion on the page, not on drawer internals.
    memoryTrust: () => new Promise(() => {}),
    fetchScoreBreakdown: () => new Promise(() => {}),
    fetchForgettingCurve: () => new Promise(() => {}),
    relatedMemories: () => new Promise(() => {}),
  },
}));

import DecisionsPage from "./decisions/page";
import FindingsPage from "./findings/page";
import GraphPage from "./graph/page";
import MemoriesPage from "./memories/page";
import TimelinePage from "./timeline/page";

const memory: Memory = {
  id: "m-1",
  content: "Atlas moved to PostgreSQL in production.",
  importance: 0.7,
  frequency: 3,
  tags: ["infra", "atlas"],
  session_id: "s-1",
  project: "atlas",
  source: "cli",
  pinned: true,
  memory_type: "episodic",
  metadata: {},
  hscore: 0.42,
  created_at: "2026-09-20T10:00:00Z",
  accessed_at: "2026-09-21T10:00:00Z",
  stability_hours: 72,
  recall_count: 4,
};

const decision: Decision = {
  id: "d-1",
  text: "We decided to use PostgreSQL for the primary store.",
  source: "connector:slack",
  date: "2026-09-19",
  project: "atlas",
};

const day: TimelineDay = {
  date: "2026-09-20",
  count: 1,
  items: [
    { id: "m-1", summary: "Atlas moved to PostgreSQL.", source: "cli", memory_type: "episodic" },
  ],
};

const finding: Finding = {
  id: "f-1",
  title: "Stale tool count in README",
  detail: "README says 69 tools; the registry has 73.",
  category: "docs",
  severity: "medium",
  source: "quality-scan",
  status: "open",
  occurrences: 2,
  first_seen_at: "2026-09-18T00:00:00Z",
  last_seen_at: "2026-09-20T00:00:00Z",
  decided_at: null,
  note: null,
  external_ref: null,
};

const entity: EntityRow = {
  id: "e-1",
  type: "person",
  ekey: "ali",
  name: "Ali",
  updated_at: "2026-09-20",
  mentions: 5,
};

describe("Page shells are accessible", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listProjects.mockResolvedValue({ projects: [{ name: "atlas" } as Project] });
    mocks.listSources.mockResolvedValue({ sources: [{ name: "cli" } as Source] });
    mocks.listTags.mockResolvedValue({ tags: [{ name: "infra", count: 2 } as TagCount] });
  });

  it("memories, populated", async () => {
    mocks.listMemories.mockResolvedValue([memory]);
    const { container } = render(<MemoriesPage />);

    expect(await screen.findByText(memory.content)).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("memories, empty", async () => {
    mocks.listMemories.mockResolvedValue([]);
    const { container } = render(<MemoriesPage />);

    expect(await screen.findByText(/No memories yet/)).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("decisions, populated", async () => {
    mocks.listDecisions.mockResolvedValue({ decisions: [decision] });
    const { container } = render(<DecisionsPage />);

    expect(await screen.findByText(decision.text)).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("timeline, populated", async () => {
    mocks.timeline.mockResolvedValue({ timeline: [day] });
    const { container } = render(<TimelinePage />);

    expect(await screen.findByText("Atlas moved to PostgreSQL.")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("findings, populated", async () => {
    mocks.listFindings.mockResolvedValue({ findings: [finding], counts: { open: 1 } });
    const { container } = render(<FindingsPage />);

    expect(await screen.findByText(finding.title)).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("graph, entity list populated", async () => {
    mocks.entityStats.mockResolvedValue({ by_type: { person: 1 } });
    mocks.listEntities.mockResolvedValue({ entities: [entity] });
    const { container } = render(<GraphPage />);

    expect(await screen.findByText("Ali")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("catches an unnamed control on a page shell", async () => {
    // Proves the page-level gate is live, the same way the primitive suite
    // proves its matcher is registered. Renders the shell with an injected
    // icon-only button that has no accessible name — the exact defect the row
    // actions in these pages are one missing aria-label away from.
    mocks.listMemories.mockResolvedValue([memory]);
    const { container } = render(<MemoriesPage />);
    await screen.findByText(memory.content);

    const orphan = document.createElement("button");
    orphan.innerHTML = "<svg aria-hidden='true'></svg>";
    container.appendChild(orphan);

    const results = await axe(container);
    expect(results.violations.length).toBeGreaterThan(0);
    await waitFor(() => expect(orphan).toBeInTheDocument());
  });
});
