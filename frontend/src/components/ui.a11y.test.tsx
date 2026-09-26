import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";

// A gate, not a one-off check: the primitives below are what every page is
// assembled from, so a regression here reaches the whole dashboard at once.
// The repo's a11y coverage was manual and thin (a handful of aria-label
// attributes across 20 pages); axe catches the mechanical classes a reviewer
// cannot see by reading — a form control with no accessible name, a dialog
// with no title, a heading order that skips a level.
//
// Scoped to the primitives on purpose. Page-level checks need API mocks and
// would make this suite slow and brittle for the same signal.

describe("UI primitives are accessible", () => {
  it("a labelled form control has no violations", async () => {
    const { container } = render(
      <form>
        <Label htmlFor="memory">Memory</Label>
        <Input id="memory" name="memory" />
        <Label htmlFor="notes">Notes</Label>
        <Textarea id="notes" name="notes" />
        <Button type="submit">Store</Button>
      </form>
    );

    expect(await axe(container)).toHaveNoViolations();
  });

  it("an unlabelled form control is caught", async () => {
    // Proves the gate is live: an input with no label, aria-label or
    // aria-labelledby is exactly what axe exists to reject. Without this,
    // a silently-broken matcher registration would look like a clean suite.
    const { container } = render(
      <form>
        <Input name="orphan" />
      </form>
    );

    const results = await axe(container);
    expect(results.violations.length).toBeGreaterThan(0);
  });

  it("a dialog with a title and description has no violations", async () => {
    const { baseElement } = render(
      <Dialog open>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete memory</DialogTitle>
            <DialogDescription>This cannot be undone.</DialogDescription>
          </DialogHeader>
          <Button variant="destructive">Delete</Button>
        </DialogContent>
      </Dialog>
    );

    expect(await axe(baseElement)).toHaveNoViolations();
  });

  it("a card with heading, body and badge has no violations", async () => {
    const { container } = render(
      <Card>
        <CardHeader>
          <CardTitle>Atlas</CardTitle>
          <CardDescription>Production database</CardDescription>
        </CardHeader>
        <CardContent>
          <Badge>pinned</Badge>
          <p>Uses PostgreSQL in production.</p>
        </CardContent>
      </Card>
    );

    expect(await axe(container)).toHaveNoViolations();
  });
});
