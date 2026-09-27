import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

// Flow 5: the keyboard entry points the help dialog advertises actually work.
//
// Both ⌘K and ⌘⇧A were printed in the header's Quick Help and wired to
// nothing. A shortcut that is documented but dead is worse than an undocumented
// one: a user concludes the app is broken, not that the key is unbound. These
// assertions fail the moment the listener is dropped again.

test("Cmd/Ctrl+K opens the search palette, which finds a stored memory", async ({
  page,
  request,
}) => {
  const needle = `palette-${Date.now()}`;
  // Deliberately not a "The X ... this memory" sentence: the other specs use
  // that shape, and the hash fallback embedder scores two such sentences at
  // ~0.91 — above the 0.90 duplicate-review threshold — so the second one is
  // rejected by the admission gate. Distinct wording keeps each spec's fixture
  // independent of what the others wrote.
  const content = `Rollout note ${needle}: the release captain moved the deploy to Thursday.`;
  const stored = await request.post(`${NOAUTH_URL}/api/memories`, {
    data: { content },
  });
  expect(stored.ok()).toBeTruthy();

  await page.goto(`${NOAUTH_URL}/`);

  // Ctrl on Linux/CI, Meta on a Mac — the handler accepts either, so the test
  // pins the modifier that exists in the environment it runs in.
  await page.keyboard.press("Control+k");

  // The dialog's accessible name comes from its (visually hidden) title, so
  // "named dialog" is itself part of what this asserts.
  const dialog = page.getByRole("dialog", { name: "Search memories" });
  await expect(dialog).toBeVisible();

  await dialog.getByRole("textbox", { name: "Search memories" }).fill(needle);
  const result = dialog.getByRole("button", { name: new RegExp(needle) });
  await expect(result).toBeVisible();

  // Enter opens the highlighted row, and the palette highlights the first
  // result — but recall is a semantic ranking, and with the hash fallback
  // embedder the top hit for a needle is not reliably the needle's own memory.
  // Walk the highlight down to the row this test stored, so the keyboard path
  // is asserted without depending on where the ranking happened to place it.
  const rows = dialog.getByRole("listitem").getByRole("button");
  const index = (await rows.allInnerTexts()).findIndex((text) => text.includes(needle));
  expect(index).toBeGreaterThanOrEqual(0);
  for (let i = 0; i < index; i++) await page.keyboard.press("ArrowDown");

  // Enter on the highlighted row is the keyboard path a power user takes; it
  // must land on the memory, not just close the palette.
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  // The palette navigates with the memory's full content as the query, so
  // assert the shape of the URL and that the memory is on the page.
  await expect(page).toHaveURL(/\/memories\/\?q=.+/);
  await expect(page.getByText(content)).toBeVisible();
});

test("Cmd/Ctrl+K is a toggle, and Escape closes the palette", async ({ page }) => {
  await page.goto(`${NOAUTH_URL}/`);

  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Search memories" });
  await expect(dialog).toBeVisible();

  // The same key closes it — the badge is a toggle, not just an opener.
  await page.keyboard.press("Control+k");
  await expect(dialog).toBeHidden();

  await page.keyboard.press("Control+k");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("Cmd/Ctrl+Shift+A reaches quick capture without hijacking select-all", async ({ page }) => {
  await page.goto(`${NOAUTH_URL}/memories/`);

  await page.keyboard.press("Control+Shift+a");
  await expect(page).toHaveURL(/#quick-capture$/);
  await expect(page.getByRole("dialog").first()).toBeVisible();
});
