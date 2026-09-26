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