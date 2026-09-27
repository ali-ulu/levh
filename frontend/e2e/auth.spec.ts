import { expect, test } from "@playwright/test";
import { AUTH_TOKEN, AUTH_URL, NOAUTH_URL, TOKEN_STORAGE_KEY } from "./urls";

// Flow 5: the token gate.
//
// This needs the second server (8931, started with LEVH_TOKEN). The gate is
// the only thing standing between a LAN-exposed LEVH and everyone on that
// network, so it is worth testing against a genuinely gated server rather
// than by mocking `api.health()` — the mock would pass even if the server
// stopped reporting `auth_required`, which is precisely the regression that
// would silently open the door.

test("an ungated server does not show the token prompt", async ({ page }) => {
  await page.goto(`${NOAUTH_URL}/`);
  await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();
  await expect(page.getByText("This LEVH server requires a token")).toHaveCount(0);
});

test("a gated server locks the page content and unlocks with the token", async ({ page }) => {
  await page.goto(`${AUTH_URL}/`);

  // The gate wraps <main>, not the chrome: the sidebar and header render on a
  // locked server too. So the tell is not "no sidebar" — it is that the page's
  // own content is replaced by the token card. Assert on the card and on the
  // memories heading, which only the unlocked page renders.
  const card = page.getByText("This LEVH server requires a token");
  await expect(card).toBeVisible();
  await expect(page.getByRole("heading", { name: "Memories" })).toHaveCount(0);

  // Scope to the gate's own field by its placeholder: the header's search box
  // is also a textbox and comes earlier in the DOM.
  await page.getByPlaceholder("Access token").fill(AUTH_TOKEN);
  await page.getByRole("button", { name: "Unlock" }).click();

  await expect(card).toHaveCount(0);
  await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();
});

test("a wrong token is rejected by the API even though the UI admits it", async ({ page }) => {
  // AuthGate treats any stored token as "open" (see its docstring) and lets
  // the UI surface 401s, so typing a wrong token does unlock the shell. What
  // must not happen is that value being honoured by the server. Assert both
  // halves: the token persists verbatim, and the API rejects it while
  // accepting the real one.
  await page.goto(`${AUTH_URL}/`);
  await page.getByPlaceholder("Access token").fill("definitely-not-the-token");
  await page.getByRole("button", { name: "Unlock" }).click();

  await expect(page.getByRole("link", { name: "All memories" })).toBeVisible();

  const stored = await page.evaluate((key) => localStorage.getItem(key), TOKEN_STORAGE_KEY);
  expect(stored).toBe("definitely-not-the-token");

  const denied = await page.request.get(`${AUTH_URL}/api/memories`, {
    headers: { "X-LEVH-Token": "definitely-not-the-token" },
  });
  // 401 is the usual answer. 429 means the auth rate limiter tripped first —
  // also a rejection, and the stronger posture, so accept it rather than
  // pinning the spec to the gentler of the two.
  expect([401, 429]).toContain(denied.status());

  const allowed = await page.request.get(`${AUTH_URL}/api/memories`, {
    headers: { "X-LEVH-Token": AUTH_TOKEN },
  });
  expect(allowed.ok()).toBeTruthy();
});
