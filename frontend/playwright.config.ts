import { defineConfig, devices } from "@playwright/test";
import { AUTH_TOKEN, AUTH_URL, NOAUTH_URL } from "./e2e/urls";

// End-to-end tests against the real thing: `levh serve` booting the FastAPI
// app, which serves the built Next.js export at "/" and the REST + MCP API on
// the same port. Nothing here is mocked — these specs are the only place in
// the repo where the frontend, the HTTP layer and the engine run together.
//
// The component and page suites (vitest) mock `@/lib/api` on purpose: they
// test rendering in isolation and they are fast. That leaves a gap no unit
// test can cover — a page that renders perfectly against a mocked client but
// calls an endpoint that does not exist, or reads a response field under the
// wrong name. This suite closes it.
//
// Two servers, one per concern:
//   - 8930, no token: the ordinary local setup. Verifies the app works with
//     the gate open.
//   - 8931, LEVH_TOKEN set: verifies the gate actually locks the dashboard,
//     which cannot be tested against an ungated server.

const NOAUTH_DB = "/tmp/levh-e2e-noauth.db";
const AUTH_DB = "/tmp/levh-e2e-auth.db";

export default defineConfig({
  testDir: "./e2e",
  // One worker: both servers share a SQLite file per process, and the specs
  // assert on store contents. Parallel specs would race on that store and
  // produce flakes that look like product bugs.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["github"], ["list"], ["html", { open: "never" }]]
    : [["list"]],
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: [
    {
      command: `rm -f ${NOAUTH_DB} && bash e2e/start-server.sh 8930 ${NOAUTH_DB}`,
      url: `${NOAUTH_URL}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `rm -f ${AUTH_DB} && bash e2e/start-server.sh 8931 ${AUTH_DB} ${AUTH_TOKEN}`,
      url: `${AUTH_URL}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
