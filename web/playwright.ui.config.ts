import { defineConfig, devices } from "@playwright/test";

// Bundle 5B–5D run from separate worktrees, so the isolated UI harness needs a
// port each of them can move. CI leaves it unset and keeps the pinned 3104.
const port = process.env.MOVIELENS_UI_PORT ?? "3104";
const origin = `http://localhost:${port}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/ui",
  fullyParallel: true,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: origin,
    colorScheme: "dark",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p ${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    url: `${origin}/ui-preview/discover`,
  },
  projects: [
    {
      name: "mobile-390",
      use: { ...devices["Desktop Chrome"], viewport: { height: 844, width: 390 } },
    },
    {
      name: "tablet-768",
      use: { ...devices["Desktop Chrome"], viewport: { height: 1024, width: 768 } },
    },
    {
      name: "desktop-1440",
      use: { ...devices["Desktop Chrome"], viewport: { height: 1000, width: 1440 } },
    },
  ],
});
