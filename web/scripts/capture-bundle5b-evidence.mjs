import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * Captures the Bundle 5B Discover state matrix.
 *
 * The route is driven through its recorded scenarios so every state — including
 * the ones that need a broken upstream — is reachable deterministically. Start
 * the isolated harness first:
 *
 *   MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3104
 *   npm run evidence:bundle5b
 */

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-5b");
const baseURL = process.env.EVIDENCE_BASE_URL ?? "http://localhost:3104";

const VIEWPORTS = [
  { suffix: "mobile", width: 390, height: 844 },
  { suffix: "tablet", width: 768, height: 1024 },
  { suffix: "desktop", width: 1440, height: 1000 },
];

const STATES = [
  { name: "learned", scenario: "learned" },
  { name: "fallback", scenario: "fallback" },
  { name: "empty", scenario: "empty" },
  { name: "loading", scenario: "loading" },
  { name: "auth-expired", scenario: "auth-expired" },
  { name: "partial-recommendations-error", scenario: "recommendations-error" },
  {
    name: "partial-history-error",
    scenario: "history-error",
    // The failing region sits below the primary movie; scroll so the capture
    // shows the failure and the intact decision above it.
    async prepare(page) {
      await page.getByRole("heading", { level: 2, name: /has watched/ }).scrollIntoViewIfNeeded();
    },
  },
  {
    name: "partial-evidence-error",
    scenario: "evidence-error",
    async prepare(page) {
      await page.getByRole("button", { name: "Why this?" }).click();
      await page.getByRole("button", { name: "Show prediction audit" }).click();
      await page.getByTestId("technical-evidence").waitFor();
    },
  },
  { name: "poster-failure", scenario: "poster-failure" },
  {
    name: "why-this",
    scenario: "learned",
    async prepare(page) {
      await page.getByRole("button", { name: "Why this?" }).click();
      await page.getByRole("dialog").waitFor();
    },
  },
  {
    name: "technical-evidence",
    scenario: "learned",
    async prepare(page) {
      await page.getByRole("button", { name: "Why this?" }).click();
      await page.getByRole("button", { name: "Show prediction audit" }).click();
      await page.getByTestId("technical-evidence").waitFor();
    },
  },
];

await mkdir(output, { recursive: true });
const browser = await chromium.launch();

for (const viewport of VIEWPORTS) {
  const page = await browser.newPage({
    colorScheme: "dark",
    viewport: { width: viewport.width, height: viewport.height },
  });
  for (const state of STATES) {
    await page.goto(`${baseURL}/discover?demo=${state.scenario}`, {
      waitUntil: "networkidle",
    });
    await state.prepare?.(page);
    await page.screenshot({
      animations: "disabled",
      path: resolve(output, `discover-${state.name}-${viewport.suffix}.png`),
    });
  }
  await page.close();
}

await browser.close();
