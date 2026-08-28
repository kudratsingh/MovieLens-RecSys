import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * The frontend sweep's evidence matrix.
 *
 * Unlike the bundle captures, this one is deliberately *complete* rather than
 * differential: the sweep touched the write path, the poster pipeline, the
 * catalog's default ordering, the shell, and the read models, so there is no
 * surface left that an earlier matrix still describes accurately. Five product
 * routes at the three contracted viewports is the whole set.
 *
 * Every capture is service-backed: the seeded Compose stack with
 * `DEV_AUTH_BYPASS=false`, real Keycloak, real FastAPI, real RLS, the local
 * catalog snapshot, the feature and model servers, and the web BFF.
 *
 * **Persona ownership is the journeys' table, and this script only reads.**
 * Discover is captured as Drama Fan, Browse as Eclectic Viewer, Library and
 * movie detail as Action Fan, Quick Picks as Cold Start. Nothing here presses a
 * decision control — a capture that spent one of Cold Start's signals would
 * leave the persona dirty for every later run, and the whole point of that
 * persona is that it is handed on at zero.
 *
 * The run records the two things a reader would otherwise have to take on
 * trust: the serving policy the warm persona was actually served, and the
 * poster coverage of the catalog behind the pictures.
 *
 * Usage:
 *   MOVIELENS_DEMO_URL=http://localhost:3001 node scripts/capture-sweep-evidence.mjs
 */

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/sweep-2026-08-27");

const BASE = process.env.MOVIELENS_DEMO_URL ?? "http://localhost:3001";

const MOBILE = { suffix: "mobile-390", width: 390, height: 844 };
const TABLET = { suffix: "tablet-768", width: 768, height: 1024 };
const DESKTOP = { suffix: "desktop-1440", width: 1440, height: 1000 };
const ALL = [MOBILE, TABLET, DESKTOP];

const ACTION_FAN = 900000101;
const DRAMA_FAN = 900000102;
const ECLECTIC = 900000103;
const COLD_START = 900000104;

/** The movie detail subject: a title Action Fan's catalog actually offers. */
const DETAIL_MOVIE = 318;

const ROUTES = [
  {
    name: "discover",
    path: `/discover?userId=${DRAMA_FAN}`,
    viewports: ALL,
    settle: (page) => page.locator("section.featured-movie h1").waitFor(),
  },
  {
    name: "browse",
    path: `/browse?user=${ECLECTIC}`,
    viewports: ALL,
    settle: (page) => page.getByRole("list", { name: "Browse results" }).waitFor(),
  },
  {
    name: "movie-detail",
    path: `/movies/${DETAIL_MOVIE}?user=${ACTION_FAN}`,
    viewports: ALL,
    settle: (page) => page.getByRole("heading", { level: 1 }).waitFor(),
  },
  {
    name: "library",
    path: `/library?userId=${ACTION_FAN}&tab=rated`,
    viewports: ALL,
    settle: (page) => page.getByRole("tab", { name: /Rated/ }).waitFor(),
  },
  {
    name: "quick-picks",
    path: `/quick-picks?user=${COLD_START}`,
    viewports: ALL,
    // The card, not the deck wrapper: the queue arrives from the API and the
    // wrapper is on screen before it does.
    settle: (page) => page.locator(".quick-pick-card h1").waitFor(),
  },
];

async function signIn(page) {
  await page.goto(`${BASE}/`);
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.waitForURL(/\/realms\/demo\/protocol\/openid-connect\/auth/, { timeout: 60_000 });
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await page.getByRole("button", { name: "Sign out" }).waitFor({ timeout: 60_000 });
}

async function capture(page, item) {
  for (const viewport of item.viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto(`${BASE}${item.path}`, { waitUntil: "networkidle" });
    await item.settle?.(page);
    await page.screenshot({
      animations: "disabled",
      path: resolve(output, `${item.name}-${viewport.suffix}.png`),
    });
    console.log(`captured ${item.name}-${viewport.suffix}`);
  }
}

await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "dark" });

await signIn(page);

const policy = await page.evaluate(
  async (id) =>
    (
      await fetch(`/api/users/${id}/recommendations?limit=10`, { cache: "no-store" }).then(
        (response) => response.json(),
      )
    ).serving_policy,
  DRAMA_FAN,
);
console.log(`warm persona serving policy: ${JSON.stringify(policy)}`);

const coverage = await page.evaluate(
  async (id) => {
    const body = await fetch(`/api/users/${id}/catalog?limit=48`, { cache: "no-store" }).then(
      (response) => response.json(),
    );
    const items = body.items ?? [];
    return { returned: items.length, withPoster: items.filter((item) => item.poster_url).length };
  },
  ECLECTIC,
);
console.log(
  `catalog poster coverage on the first Browse window: ${coverage.withPoster}/${coverage.returned}`,
);

for (const item of ROUTES) await capture(page, item);

await browser.close();
