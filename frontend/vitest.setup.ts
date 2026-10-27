import "@testing-library/jest-dom/vitest";
import { afterEach, expect } from "vitest";
import { cleanup } from "@testing-library/react";
import { toHaveNoViolations } from "jest-axe";

// jest-axe ships a matcher for Jest's global `expect`; vitest needs it
// registered explicitly. This is what turns the axe checks in `*.a11y.test.tsx`
// into real assertions instead of calls whose result nobody reads.
expect.extend(toHaveNoViolations);

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
// jsdom never lays out, so `clientWidth`/`clientHeight`/`offsetHeight` are
// always 0. TanStack Virtual reads exactly those to size the viewport and each
// measured row, so without a non-zero box every virtualized list would render
// an empty window in tests. These stand in for the layout the browser performs.
Object.defineProperty(HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get: () => 1024,
});
Object.defineProperty(HTMLElement.prototype, "clientHeight", {
  configurable: true,
  get: () => 768,
});
Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
  configurable: true,
  get: () => 1024,
});
// Matches the lists' `estimateSize`; a real browser would report each row's
// true height, and the virtualizer re-measures on mount there.
Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
  configurable: true,
  get: () => 96,
});
