/**
 * Renders docs/assets/social-preview.html to social-preview.png at exactly
 * 1280x640, the size GitHub wants for a repository social preview.
 *
 * Playwright is a devDependency of the frontend rather than of the repository
 * root, so run this with `web/node_modules` on the resolution path:
 *
 *   cd web && npm ci && node ../docs/assets/render-social-preview.mjs
 *
 * deviceScaleFactor stays at 1 deliberately. A 2x render is sharper on a
 * retina display and roughly four times the bytes, and GitHub rescales the
 * upload anyway — the card is type on a flat ground, which survives that far
 * better than it survives an aggressive quantisation to get a 2x file under
 * the size budget.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.join(here, "social-preview.html");
const target = path.join(here, "social-preview.png");

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1280, height: 640 },
  deviceScaleFactor: 1,
});

await page.goto(`file://${source}`);
// The card sets Iowan Old Style and Avenir Next. Screenshotting before they
// resolve silently produces a fallback-serif render that looks almost right.
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: target });

await browser.close();
console.log(`wrote ${target}`);
