import { defineConfig } from "@playwright/test";

/**
 * The browser-timing profile.
 *
 * Deliberately a separate config from `playwright.config.ts`. That one runs the
 * service-backed journeys on Desktop Chrome and must keep doing exactly that;
 * this one measures what a phone experiences, which needs a different device
 * emulation and a much longer per-test budget (each route is loaded twice —
 * once cold for LCP, once warm — and then interacted with).
 *
 * The mobile profile, written down because "the agreed mobile profile" was
 * previously only a phrase in the testing strategy:
 *
 *   viewport            390 x 844   — the mobile column of the evidence matrix
 *   deviceScaleFactor   3           — a modern phone's DPR, so `next/image`
 *                                     picks the same source a real device would
 *   isMobile + hasTouch true        — mobile layout and touch targets, not a
 *                                     narrow desktop window
 *   CPU throttle        4x          — a mid-range phone against a CI runner's
 *                                     core. Configurable via PERF_CPU_THROTTLE;
 *                                     1 disables it.
 *
 * Network throttling is deliberately *not* applied. The stack under test is on
 * loopback, so any emulated RTT would be a number this harness invented rather
 * than one it measured, and it would dominate every result. What is measured
 * here is the application's own cost: server render, hydration, layout
 * stability, and how quickly an action is acknowledged.
 */
export default defineConfig({
  testDir: "./tests/perf",
  globalSetup: "./tests/perf/global-setup.ts",
  // Each route is measured cold and warm and then driven through an
  // interaction, all under CPU throttling. The journey suite's 30 s would be
  // tight for that and a timeout here reads as a false performance failure.
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  // A second worker would be a second throttled browser competing for the same
  // cores, which is measurement noise this harness would then report as the
  // application's latency.
  workers: 1,
  // No retries: a retried performance measurement is a different measurement.
  // A flaky result here is evidence, not something to paper over.
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  outputDir: "./test-results/perf",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001",
    trace: "retain-on-failure",
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
  },
});
