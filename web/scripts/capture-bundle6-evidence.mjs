import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-6");
const baseURL = process.env.EVIDENCE_BASE_URL ?? "http://localhost:3104";

const MOBILE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };
const DESKTOP = { width: 1440, height: 1000 };

/** `act` runs after the deck reports itself interactive. */
const captures = [
  { name: "quick-picks-mobile", path: "/ui-preview/quick-picks", ...MOBILE },
  { name: "quick-picks-tablet", path: "/ui-preview/quick-picks", ...TABLET },
  { name: "quick-picks-desktop", path: "/ui-preview/quick-picks", ...DESKTOP },
  {
    name: "quick-picks-learned-desktop",
    path: "/ui-preview/quick-picks?policy=learned",
    ...DESKTOP,
  },
  {
    name: "quick-picks-mutation-failure-mobile",
    path: "/ui-preview/quick-picks?fail=commit",
    ...MOBILE,
    act: failDecision,
  },
  {
    name: "quick-picks-mutation-failure-desktop",
    path: "/ui-preview/quick-picks?fail=commit",
    ...DESKTOP,
    act: failDecision,
  },
  {
    name: "quick-picks-queue-error-desktop",
    path: "/ui-preview/quick-picks?fail=queue",
    ...DESKTOP,
  },
  {
    name: "quick-picks-reduced-motion-desktop",
    path: "/ui-preview/quick-picks",
    ...DESKTOP,
    reducedMotion: "reduce",
  },
];

async function failDecision(page) {
  await page.getByRole("button", { name: /Not for me/ }).click();
  await page.locator(".quick-picks-error").waitFor();
}

await mkdir(output, { recursive: true });
const browser = await chromium.launch();

for (const capture of captures) {
  const page = await browser.newPage({
    colorScheme: "dark",
    reducedMotion: capture.reducedMotion ?? "no-preference",
    viewport: { width: capture.width, height: capture.height },
  });
  await page.goto(`${baseURL}${capture.path}`, { waitUntil: "networkidle" });
  await page
    .locator(".quick-picks-page[data-interactive='true']")
    .waitFor({ timeout: 15_000 });
  await capture.act?.(page);
  await page.screenshot({
    animations: "disabled",
    path: resolve(output, `${capture.name}.png`),
  });
  await page.close();
}

await browser.close();
