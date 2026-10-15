import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

// Flow 8: the action buttons on the review surfaces actually do something.
//
// The render suite (`serve.spec.ts`) proves the pages paint and hydrate. It
// does not click anything, so a button wired to a handler that throws — or to
// an endpoint that 500s — still ships green. These specs click the real
// controls and assert on the effect, which is what "the buttons work" means.

test("the quick-add FAB opens its dialog, not the overlapping librarian chat", async ({
  page,
}) => {
  // The librarian widget is injected into every dashboard page and its FAB is
  // position:fixed with a z-index far above the app's. Pinned to the same
  // corner it sat exactly on the quick-add FAB, so a click on "Add new memory"
  // opened the librarian chat instead — a working-looking button that never
  // reached its handler. The widget loads here because the middleware injects
  // it regardless of LEVH_LIBRARIAN (that flag gates the background watcher,
  // not the widget), which is why the rest of the suite missed this.
  await page.goto(`${NOAUTH_URL}/`);

  const quickAdd = page.locator("button.fab-button");
  await expect(quickAdd).toBeVisible();
  await expect(page.locator("#lvw-fab")).toBeVisible();

  // The two FABs must not share a pixel: a stacked one makes the lower
  // unclickable no matter how the click is dispatched.
  const [a, b] = await Promise.all([
    quickAdd.boundingBox(),
    page.locator("#lvw-fab").boundingBox(),
  ]);
  expect(a && b).toBeTruthy();
  const overlaps =
    a!.x < b!.x + b!.width &&
    a!.x + a!.width > b!.x &&
    a!.y < b!.y + b!.height &&
    a!.y + a!.height > b!.y;
  expect(overlaps).toBe(false);

  await quickAdd.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("#lvw")).not.toHaveClass(/open/);
});

test("the conflicts page lists a candidate and a review action persists", async ({
  page,
  request,
}) => {
  // Deterministic seed: two memories sharing the same person entity that
  // assert opposing facts (antonym signal), so exactly one candidate exists.
  const seeder = Date.now();
  const person = `Button Probe <probe-${seeder}@example.test>`;
  for (const content of [
    `The release pipeline is approved for ${seeder}.`,
    `The release pipeline is rejected for ${seeder}.`,
  ]) {
    const r = await request.post(`${NOAUTH_URL}/api/memories`, {
      data: { content, metadata: { attendees: [person] } },
    });
    expect(r.ok()).toBeTruthy();
  }

  // Detect first so the candidate row exists regardless of read-path timing.
  const detected = await request.post(`${NOAUTH_URL}/api/conflicts/detect`);
  expect(detected.ok()).toBeTruthy();

  await page.goto(`${NOAUTH_URL}/conflicts/`);
  await expect(page.getByRole("heading", { name: "Conflicts" })).toBeVisible();

  await expect(
    page.getByText(`The release pipeline is approved for ${seeder}.`),
  ).toBeVisible();

  // The review button round-trips through POST /api/conflicts/{id}/review.
  // That endpoint awaits a derived-state rebuild whose check-then-insert once
  // raced to a 500 — exactly the failure this spec exists to catch.
  const reviewResponse = page.waitForResponse(
    (r) => r.url().includes("/api/conflicts/") && r.url().endsWith("/review"),
  );
  await page.getByRole("button", { name: "Confirm" }).first().click();
  const res = await reviewResponse;
  expect(res.status()).toBeLessThan(400);
});

test("the onboarding demo buttons round-trip the real seed and purge endpoints", async ({
  page,
  request,
}) => {
  // The onboarding card renders while the store is not fully ready OR demo
  // data is present. On this shared, already-populated store the plain seed
  // would skip ("store not empty"), so force it to get demo data present — the
  // state that keeps the card on screen regardless of the MCP receipt another
  // spec may have written.
  const seeded = await request.post(`${NOAUTH_URL}/api/seed-demo?force=true`);
  expect(seeded.ok()).toBeTruthy();

  page.on("dialog", (d) => d.accept());
  await page.goto(`${NOAUTH_URL}/`);

  await expect(page.getByRole("button", { name: "Demo data loaded" })).toBeVisible();
  const removeButton = page.getByRole("button", { name: "Remove demo data" });
  await expect(removeButton).toBeVisible();

  // Remove must hit the real purge endpoint and succeed — a button whose
  // handler throws here is invisible to the render-only suite.
  const removeResponse = page.waitForResponse(
    (r) => r.url().includes("/api/onboarding/remove-demo"),
  );
  await removeButton.click();
  expect((await removeResponse).status()).toBeLessThan(400);
  await expect
    .poll(async () => {
      const r = await request.get(`${NOAUTH_URL}/api/onboarding/status`);
      return (await r.json()).demo_seeded;
    })
    .toBe(false);
});
