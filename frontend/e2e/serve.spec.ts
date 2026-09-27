import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

// Flow 1: the server comes up and the dashboard it serves is actually alive.
//
// This is the "is the release wired together" check. It is deliberately
// boring — it asserts the built export is served by the API process and that
// React hydrated, because every other spec in this file is meaningless if it
// did not. A stale or missing frontend/out is exactly how a release ships a
// dashboard that 404s its own bundles (see DashboardStaticFiles in
// server/api.py), and no unit test can see that.

test("the served dashboard loads and hydrates", async ({ page }) => {
  const response = await page.goto(`${NOAUTH_URL}/`);
  expect(response?.status()).toBe(200);

  // The sidebar is rendered by React, not in the static HTML shell alone —
  // its presence means hydration completed rather than the export being
  // served as inert markup.
  await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();

  // The header polls /api/health and flips this badge. Seeing the online
  // state proves the same origin is answering API calls, not just static files.
  await expect(page.getByText("Local core online")).toBeVisible();
});

test("every top-level page renders without a client-side crash", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  const routes = [
    "/",
    "/memories/",
    "/graph/",
    "/timeline/",
    "/decisions/",
    "/findings/",
    "/settings/",
    "/projects/",
  ];

  for (const route of routes) {
    await page.goto(`${NOAUTH_URL}${route}`);
    // The app shell renders on every route; wait for it so a route that
    // throws during hydration fails here instead of silently on the next one.
    await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();
  }

  expect(errors, `uncaught page errors: ${errors.join("\n")}`).toEqual([]);
});
