import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * Browse and movie-detail evidence, captured against the recorded catalog
 * endpoint in the isolated preview. Same components as the authenticated
 * routes; only the resource behind them differs.
 *
 * Captures are viewport-sized rather than full page so the committed PNGs stay
 * small. Several of them scroll a named element into view first: the display
 * headline is deliberately large, so an unscrolled 1440x1000 shot of an error
 * state and of a healthy grid would be the same picture of a headline.
 */

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-5c");
const baseURL = process.env.EVIDENCE_BASE_URL ?? "http://localhost:3106";

const MOBILE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };
const DESKTOP = { width: 1440, height: 1000 };

const RESULTS = ".collection-bar";
const PROBLEM = ".resource-error";
const EMPTY = ".resource-empty";
const CONTROLS = ".canonical-state";

const captures = [
  { name: "browse-header-desktop", path: "/ui-preview/browse", ...DESKTOP },
  { name: "browse-mobile", path: "/ui-preview/browse", focus: RESULTS, ...MOBILE },
  { name: "browse-tablet", path: "/ui-preview/browse", focus: RESULTS, ...TABLET },
  { name: "browse-desktop", path: "/ui-preview/browse", focus: RESULTS, ...DESKTOP },
  {
    name: "browse-filtered-desktop",
    path: "/ui-preview/browse?genre=Drama&sort=newest",
    focus: RESULTS,
    ...DESKTOP,
  },
  {
    name: "browse-empty-mobile",
    path: "/ui-preview/browse?q=zzzz",
    focus: EMPTY,
    ...MOBILE,
  },
  {
    name: "browse-stale-cursor-desktop",
    path: "/ui-preview/browse?cursor=not-a-real-cursor",
    focus: RESULTS,
    ...DESKTOP,
  },
  {
    name: "browse-upstream-error-desktop",
    path: "/ui-preview/browse?fail=catalog",
    focus: PROBLEM,
    ...DESKTOP,
  },
  {
    name: "browse-auth-expired-mobile",
    path: "/ui-preview/browse?fail=catalog-auth",
    focus: PROBLEM,
    ...MOBILE,
  },
  { name: "detail-mobile", path: "/ui-preview/movies/101", ...MOBILE },
  { name: "detail-tablet", path: "/ui-preview/movies/101", ...TABLET },
  { name: "detail-desktop", path: "/ui-preview/movies/101", ...DESKTOP },
  {
    name: "detail-controls-mobile",
    path: "/ui-preview/movies/101",
    focus: CONTROLS,
    ...MOBILE,
  },
  // 109 carries an overview but no poster; 130 carries neither.
  {
    name: "detail-partial-metadata-desktop",
    path: "/ui-preview/movies/109",
    ...DESKTOP,
  },
  {
    name: "detail-unavailable-metadata-mobile",
    path: "/ui-preview/movies/130",
    ...MOBILE,
  },
  {
    name: "detail-not-found-desktop",
    path: "/ui-preview/movies/999999",
    ...DESKTOP,
  },
];

await mkdir(output, { recursive: true });
const browser = await chromium.launch();

for (const capture of captures) {
  // A fresh context per capture, because Browse deliberately keeps its loaded
  // window in `sessionStorage`. Sharing one tab would let an earlier capture's
  // restored grid stand in for the state a later capture is meant to show.
  const context = await browser.newContext({
    colorScheme: "dark",
    viewport: { width: capture.width, height: capture.height },
  });
  const page = await context.newPage();
  await page.goto(`${baseURL}${capture.path}`, { waitUntil: "networkidle" });
  // The dev-server overlay is not part of the product surface being reviewed.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  // Browse loads its catalog after hydration, so "networkidle" alone can catch
  // the reserved skeleton. Every state — results, empty, failure — removes it.
  await page.locator(".catalog-skeleton").waitFor({ state: "detached" });
  if (capture.focus) {
    const target = page.locator(capture.focus).first();
    await target.waitFor({ state: "visible" });
    // Always scroll to the top of the named element, not "only if needed":
    // the display headline is tall enough that the results region can be
    // technically on screen while every poster is still below the fold.
    await target.evaluate((element) => {
      element.scrollIntoView({ block: "start", behavior: "instant" });
      // Clear the sticky header so the region being evidenced is not behind it.
      window.scrollBy({ top: -96, behavior: "instant" });
    });
    // Below-the-fold posters are lazy, so let the ones now in view arrive.
    await page.waitForLoadState("networkidle");
  }
  await page.screenshot({
    animations: "disabled",
    path: resolve(output, `${capture.name}.png`),
  });
  await context.close();
}

await browser.close();
