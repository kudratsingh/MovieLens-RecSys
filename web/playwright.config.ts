import { defineConfig, devices } from "@playwright/test";

/**
 * The service-backed journeys: real Keycloak, real API, real RLS, and one
 * seeded Compose stack shared by every test in the run. `playwright.ui.config`
 * is the fixture-mode harness and is the one that may run in parallel.
 *
 * A single worker is a correctness setting here, not a tuning knob. Playwright
 * defaults to one worker per two cores, so CI ran the two spec files at once
 * against the same database; the 2026-08-21 run lost a `DELETE .../watched` to
 * a 409 because a second journey had already moved that row's revision, and
 * the login flow timed out under the doubled load. `fullyParallel: false` does
 * not prevent that on its own — it keeps a file's tests in order, while
 * separate files and retried tests still overlap.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // A retry that passes and a test that fails outright are one character apart
  // in the dot reporter CI defaults to, and the distinction is the whole
  // signal when chasing flakiness. `list` names each outcome; `github`
  // annotates the failing line in the PR.
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
