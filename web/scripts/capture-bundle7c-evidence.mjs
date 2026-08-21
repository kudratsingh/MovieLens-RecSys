import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-7c");
const port = process.env.MOVIELENS_UI_PORT ?? "3110";
const baseURL = process.env.EVIDENCE_BASE_URL ?? `http://localhost:${port}`;

const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 1000 };

/**
 * Only the surfaces 7c actually re-renders.
 *
 * Browse, the poster grid, and every Discover state that does not involve a
 * control are unchanged, so their Bundle 5 captures still stand. What is
 * recaptured here is the converged control row, the one confirmation shape now
 * shared by movie detail and Library, and the Quick Picks entry point.
 */
const captures = [
  {
    name: "discover-quick-picks-entry-desktop",
    path: "/discover?demo=learned",
    ...DESKTOP,
    fullPage: true,
  },
  {
    name: "discover-quick-picks-entry-mobile",
    path: "/discover?demo=learned",
    ...MOBILE,
    fullPage: true,
  },
  { name: "detail-state-controls-desktop", path: "/ui-preview/movies/103", ...DESKTOP },
  { name: "detail-state-controls-mobile", path: "/ui-preview/movies/103", ...MOBILE },
  {
    name: "detail-remove-confirm-desktop",
    path: "/ui-preview/movies/103",
    ...DESKTOP,
    action: openDetailConfirmation,
  },
  { name: "library-rated-mobile", path: "/ui-preview/library", ...MOBILE },
  // Quick Picks renders the shared star editor now, so the fold is evidenced too.
  { name: "quick-picks-rating-desktop", path: "/ui-preview/quick-picks", ...DESKTOP },
  {
    name: "library-remove-confirm-desktop",
    path: "/ui-preview/library?tab=history",
    ...DESKTOP,
    action: openLibraryConfirmation,
  },
];

async function openDetailConfirmation(page) {
  await page.getByRole("button", { name: "Watched · remove" }).click();
  await page.getByRole("group", { name: /^Confirm removing/ }).waitFor();
}

async function openLibraryConfirmation(page) {
  const row = page.getByRole("listitem").filter({ hasText: "Memories of Murder" });
  await row.getByRole("button", { name: "Remove from history" }).click();
  await page.getByRole("group", { name: /Confirm removing Memories of Murder/ }).waitFor();
}

await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "dark" });

for (const capture of captures) {
  await page.setViewportSize({ width: capture.width, height: capture.height });
  await page.goto(`${baseURL}${capture.path}`, { waitUntil: "networkidle" });
  await capture.action?.(page);
  await page.screenshot({
    animations: "disabled",
    fullPage: Boolean(capture.fullPage),
    path: resolve(output, `${capture.name}.png`),
  });
}

await browser.close();
