import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LocaleProvider } from "@/lib/i18n";
import { EntityRelationshipMap, type MapNeighbor } from "./entity-relationship-map";

const typeHex = () => "#3b82f6";
const center = { id: "person:alice", type: "person", name: "Alice", mentions: 12 };
const related: MapNeighbor[] = [
  { id: "organization:acme", type: "organization", name: "Acme", shared: 4 },
  { id: "task:ship", type: "task", name: "Ship the release", shared: 2 },
];

function renderMap(props: Partial<React.ComponentProps<typeof EntityRelationshipMap>> = {}) {
  return render(
    <LocaleProvider>
      <EntityRelationshipMap
        center={center}
        related={related}
        typeHex={typeHex}
        ariaLabel="Relationship map for Alice"
        {...props}
      />
    </LocaleProvider>,
  );
}

describe("EntityRelationshipMap", () => {
  it("exposes every node as an accessible, focusable button", () => {
    renderMap();
    const nodes = screen.getAllByRole("button", { name: /Entity:/ });
    // The centre plus its two satellites.
    expect(nodes).toHaveLength(3);
    nodes.forEach((node) => expect(node).toHaveAttribute("tabindex", "0"));
    expect(screen.getByRole("button", { name: "Entity: Alice, person, 2 connections" })).toBeInTheDocument();
  });

  it("opens a satellite with Enter but leaves the centre inert", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderMap({ onSelect });

    const satellite = screen.getByRole("button", { name: /Entity: Acme/ });
    satellite.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("organization:acme");

    const centre = screen.getByRole("button", { name: /Entity: Alice/ });
    centre.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("moves focus between nodes with the arrow keys", async () => {
    const user = userEvent.setup();
    renderMap();
    const centre = screen.getByRole("button", { name: /Entity: Alice/ });
    centre.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).not.toBe(centre);
  });

  it("grows the graph one hop at a time through the expand control", async () => {
    const expand = vi.fn(async () => [
      { id: "person:bob", type: "person", name: "Bob", shared: 1 },
    ]);
    const user = userEvent.setup();
    renderMap({ expand });

    expect(screen.queryByRole("button", { name: /Entity: Bob/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Expand to 2 hops/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Entity: Bob/ })).toBeInTheDocument();
    });
    // One call per node at the current frontier (the two satellites).
    expect(expand).toHaveBeenCalledTimes(2);
  });

  it("has zoom controls and resets the viewport on Escape", async () => {
    const user = userEvent.setup();
    const { container } = renderMap();
    const group = () => container.querySelector("svg g")?.getAttribute("transform");

    expect(group()).toBe("translate(0 0) scale(1)");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(group()).not.toBe("translate(0 0) scale(1)");

    fireEvent.keyDown(container.querySelector("svg")!.parentElement!, { key: "Escape" });
    expect(group()).toBe("translate(0 0) scale(1)");
  });
});
