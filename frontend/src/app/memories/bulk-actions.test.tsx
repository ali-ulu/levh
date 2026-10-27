import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Memory } from "@/types";

const mocks = vi.hoisted(() => ({
  listMemories: vi.fn(),
  listProjects: vi.fn(),
  listSources: vi.fn(),
  listTags: vi.fn(),
  pinMemory: vi.fn(),
  deleteMemory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    listMemories: mocks.listMemories,
    listProjects: mocks.listProjects,
    listSources: mocks.listSources,
    listTags: mocks.listTags,
    pinMemory: mocks.pinMemory,
    deleteMemory: mocks.deleteMemory,
    memoryTrust: () => new Promise(() => {}),
    fetchScoreBreakdown: () => new Promise(() => {}),
    fetchForgettingCurve: () => new Promise(() => {}),
    relatedMemories: () => new Promise(() => {}),
  },
}));

import MemoriesPage from "./page";

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

describe("memories bulk actions (#562)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listProjects.mockResolvedValue({ projects: [] });
    mocks.listSources.mockResolvedValue({ sources: [] });
    mocks.listTags.mockResolvedValue({ tags: [] });
    mocks.pinMemory.mockResolvedValue({});
    mocks.deleteMemory.mockResolvedValue({ deleted: true });
  });

  it("shows the floating toolbar only once a row is selected", async () => {
    mocks.listMemories.mockResolvedValue([makeMemory(1), makeMemory(2)]);
    render(<MemoriesPage />);
    await screen.findByText("Memory number 1");

    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText(/Select: Memory number 1/));
    const bar = await screen.findByRole("toolbar");
    expect(bar).toHaveTextContent("1 selected");
  });

  it("pins every selected memory with one API call each and toasts", async () => {
    mocks.listMemories.mockResolvedValue([makeMemory(1), makeMemory(2), makeMemory(3)]);
    render(<MemoriesPage />);
    await screen.findByText("Memory number 1");

    await userEvent.click(screen.getByLabelText("Select all"));
    await userEvent.click(await screen.findByRole("button", { name: /Pin \(3\)/ }));

    await waitFor(() => expect(mocks.pinMemory).toHaveBeenCalledTimes(3));
    expect(mocks.pinMemory).toHaveBeenCalledWith("m-1", true);
    expect(mocks.pinMemory).toHaveBeenCalledWith("m-3", true);
  });

  it("selects a range with Shift+click", async () => {
    mocks.listMemories.mockResolvedValue([makeMemory(1), makeMemory(2), makeMemory(3)]);
    render(<MemoriesPage />);
    await screen.findByText("Memory number 1");

    const cb1 = screen.getByLabelText(/Select: Memory number 1/) as HTMLInputElement;
    const cb3 = screen.getByLabelText(/Select: Memory number 3/) as HTMLInputElement;
    await userEvent.click(cb1);
    fireEvent.click(cb3, { shiftKey: true });

    expect(await screen.findByRole("toolbar")).toHaveTextContent("3 selected");
  });

  it("asks before a bulk delete", async () => {
    mocks.listMemories.mockResolvedValue([makeMemory(1), makeMemory(2)]);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<MemoriesPage />);
    await screen.findByText("Memory number 1");

    await userEvent.click(screen.getByLabelText("Select all"));
    await userEvent.click(await screen.findByRole("button", { name: /Delete \(2\)/ }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.deleteMemory).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("has no accessibility violations with the toolbar open", async () => {
    mocks.listMemories.mockResolvedValue([makeMemory(1), makeMemory(2)]);
    const { container } = render(<MemoriesPage />);
    await screen.findByText("Memory number 1");

    await userEvent.click(screen.getByLabelText("Select all"));
    await screen.findByRole("toolbar");
    expect(await axe(container)).toHaveNoViolations();
  });
});
