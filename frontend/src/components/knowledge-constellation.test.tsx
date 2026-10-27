import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LocaleProvider } from "@/lib/i18n";
import { KnowledgeConstellation } from "./knowledge-constellation";

function renderConstellation(entityCounts: Record<string, number>) {
  return render(
    <LocaleProvider>
      <KnowledgeConstellation memories={42} entityCounts={entityCounts} />
    </LocaleProvider>,
  );
}

describe("KnowledgeConstellation", () => {
  it("renders one node per real entity type, including events", () => {
    renderConstellation({ person: 3, organization: 1, event: 2, document: 4, task: 5 });
    for (const label of ["People", "Organizations", "Events", "Documents", "Tasks"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // The types the index never emits must not appear (#558).
    expect(screen.queryByText("Projects")).not.toBeInTheDocument();
    expect(screen.queryByText("Decisions")).not.toBeInTheDocument();
  });

  it("shows the live count for each type", () => {
    renderConstellation({ person: 7, organization: 0, event: 0, document: 0, task: 0 });
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("dims a node whose count is zero instead of hiding it", () => {
    const { container } = renderConstellation({ person: 1 });
    const zeroNodes = container.querySelectorAll(".constellation-node.opacity-40");
    // The four types with no data are still present, just muted.
    expect(zeroNodes).toHaveLength(4);
  });

  it("orders nodes by mention count", () => {
    const { container } = renderConstellation({ person: 1, organization: 9, event: 0, document: 0, task: 0 });
    const first = container.querySelector(".constellation-node");
    expect(first?.textContent).toContain("Organizations");
  });
});
