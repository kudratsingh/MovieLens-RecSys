import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-5d");
const port = process.env.MOVIELENS_UI_PORT ?? "3107";
const baseURL = process.env.EVIDENCE_BASE_URL ?? `http://localhost:${port}`;

const MOBILE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };
const DESKTOP = { width: 1440, height: 1000 };

/**
 * Named states from the Library row of the screenshot matrix, plus the two
 * states a reviewer cannot reach by browsing: a dead collection read and the
 * confirmation that guards removing watched history.
 */
const captures = [
  { name: "library-rated-mobile", path: "/ui-preview/library", ...MOBILE },
  { name: "library-rated-tablet", path: "/ui-preview/library", ...TABLET },
  { name: "library-rated-desktop", path: "/ui-preview/library", ...DESKTOP },
  { name: "library-watchlist-mobile", path: "/ui-preview/library?tab=watchlist", ...MOBILE },
  { name: "library-watchlist-desktop", path: "/ui-preview/library?tab=watchlist", ...DESKTOP },
  {
    name: "library-watchlist-empty-mobile",
    path: "/ui-preview/library?tab=watchlist&empty=watchlist",
    ...MOBILE,
  },
  {
    name: "library-watchlist-empty-desktop",
    path: "/ui-preview/library?tab=watchlist&empty=watchlist",
    ...DESKTOP,
  },
  {
    name: "library-history-long-mobile",
    path: "/ui-preview/library?tab=history",
    ...MOBILE,
    action: loadMore,
  },
  {
    name: "library-history-long-desktop",
    path: "/ui-preview/library?tab=history",
    ...DESKTOP,
    action: loadMore,
  },
  { name: "library-error-desktop", path: "/ui-preview/library?fail=library", ...DESKTOP },
  {
    name: "library-remove-confirm-desktop",
    path: "/ui-preview/library?tab=history",
    ...DESKTOP,
    action: openRemoveConfirmation,
  },
];

async function loadMore(page) {
  await page.getByRole("button", { name: "Load more" }).click();
  await page.getByRole("heading", { level: 3, name: "Shoplifters" }).waitFor();
}

async function openRemoveConfirmation(page) {
  const row = page.getByRole("listitem").filter({ hasText: "Memories of Murder" });
  await row.getByRole("button", { name: "Remove from history" }).click();
  await page
    .getByRole("group", { name: /Confirm removing Memories of Murder/ })
    .waitFor();
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
    path: resolve(output, `${capture.name}.png`),
  });
}

await browser.close();
