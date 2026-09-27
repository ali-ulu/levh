import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

// Flow 2: a memory stored through the UI is actually persisted and readable.
//
// The page-level a11y suite mocks `@/lib/api`, so it proves the form renders
// and submits a call — not that the call is correct. Here the store goes
// through the real admission gate into the real database, and the read-back
// goes through the real list endpoint. A field renamed on one side of the
// API and not the other passes every unit test and fails here.

test("a memory stored from the dashboard appears in the list", async ({ page }) => {
  const marker = `e2e-ui-${Date.now()}`;

  await page.goto(`${NOAUTH_URL}/memories/`);
  await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();

  // Open the quick-capture dialog via its hash route — the same entry point
  // the header's "Quick capture" button uses.
  await page.goto(`${NOAUTH_URL}/#quick-capture`);
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  await dialog.getByRole("textbox").first().fill(`Atlas uses ${marker} for the primary store.`);
  await dialog.getByRole("button", { name: "Store memory" }).click();
  await expect(dialog).toBeHidden();

  // Reload so the assertion reads from the server, not from the client state
  // the submit left behind.
  await page.goto(`${NOAUTH_URL}/memories/`);
  await expect(page.getByText(`Atlas uses ${marker} for the primary store.`)).toBeVisible();
});

// Flow 3: search reaches the same record through the API's query path.
//
// Distinct from flow 2: the list endpoint and the search path are separate
// code paths (list filters vs. the search engine), and the dashboard's search
// box routes through a URL query param that the page reads on mount. A typo
// in that param name silently shows the unfiltered list — which looks like a
// working search to anyone not reading closely.

test("search returns the stored memory and excludes non-matches", async ({ page }) => {
  const needle = `needle-${Date.now()}`;

  await page.goto(`${NOAUTH_URL}/#quick-capture`);
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").first().fill(`The ${needle} appears only in this memory.`);
  await dialog.getByRole("button", { name: "Store memory" }).click();
  await expect(dialog).toBeHidden();

  await page.goto(`${NOAUTH_URL}/memories/?q=${encodeURIComponent(needle)}`);
  await expect(page.getByText(`The ${needle} appears only in this memory.`)).toBeVisible();

  // The filter must actually exclude: a search that ignores its query and
  // returns everything would still show the match above.
  await page.goto(`${NOAUTH_URL}/memories/?q=zzz-no-such-memory-zzz`);
  await expect(page.getByText(/No memories match/)).toBeVisible();
});
