import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { NOAUTH_URL } from "./urls";

// `uv run` needs the repo root (that is where pyproject.toml lives); the helper
// script lives beside this spec in frontend/e2e. Resolve both absolutely so the
// spec does not depend on Playwright's working directory. Playwright transpiles
// specs to CommonJS, so `__dirname` is the current file's directory.
const E2E_DIR = __dirname;
const REPO_ROOT = path.resolve(E2E_DIR, "..", "..");
const MCP_STORE = path.join(E2E_DIR, "mcp_store.py");

// Flow 4: a memory written by an AI client over MCP shows up in the dashboard.
//
// This is the product's central claim — one store shared by the MCP tools and
// the UI — and it is the one thing no isolated test can prove. The Python
// suite tests MCP against the engine, and the frontend suite tests the
// dashboard against a mock; only a single running process shows that a tool
// call and a page read land in the same place.
//
// The MCP call is made over the real SSE transport by e2e/mcp_store.py, the
// same protocol an external client uses. No test-only backdoor.

test("a memory stored over MCP is visible in the dashboard", async ({ page }) => {
  const marker = `e2e-mcp-${Date.now()}`;

  const result = await page.request.get(
    `${NOAUTH_URL}/api/memories?q=${encodeURIComponent(marker)}`
  );
  expect(result.ok()).toBeTruthy();
  expect(await result.json()).toEqual([]);

  // Run the MCP client as a subprocess against the live SSE endpoint. Playwright
  // owns the browser; the Python client owns the protocol call.
  const output = execFileSync(
    "uv",
    [
      "run",
      "--frozen",
      "python",
      MCP_STORE,
      `${NOAUTH_URL}/api/mcp/sse`,
      `Quantum widgets are tracked in the ${marker} ledger.`,
    ],
    { cwd: REPO_ROOT, encoding: "utf8", timeout: 90_000 }
  );
  expect(output).toContain("stored successfully");

  // The same process must now serve it back over REST, and the dashboard must
  // render it — proving the SSE-mounted engine and the route engine are one.
  await page.goto(`${NOAUTH_URL}/memories/?q=${encodeURIComponent(marker)}`);
  await expect(page.getByText(`Quantum widgets are tracked in the ${marker} ledger.`)).toBeVisible();
});
