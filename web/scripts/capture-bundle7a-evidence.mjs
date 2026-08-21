import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * The Bundle 7A finish-gate screenshot matrix.
 *
 * Two modes, because two kinds of state exist and pretending otherwise is how
 * evidence stops meaning anything:
 *
 *   `service`  — signs in through the real Keycloak on the seeded Compose
 *                stack and captures the states the deployed system produces on
 *                its own: the two policy labels, a populated and an empty
 *                Library, movie detail, Quick Picks, and the signed-out door.
 *   `fixture`  — captures the states a healthy stack cannot be asked to hold
 *                still in: a load in flight, an empty ranked set, a failed
 *                upstream read, a failed poster. These come from the isolated
 *                harness and explicit failure injection, and every file they
 *                produce is labelled as such in the evidence README.
 *
 * Usage:
 *   MODE=fixture MOVIELENS_UI_PORT=3113 node scripts/capture-bundle7a-evidence.mjs
 *   MODE=service MOVIELENS_DEMO_URL=http://localhost:3001 \
 *     node scripts/capture-bundle7a-evidence.mjs
 */

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-7a");

const MODE = process.env.MODE ?? "fixture";
const FIXTURE_BASE =
  process.env.EVIDENCE_BASE_URL ?? `http://localhost:${process.env.MOVIELENS_UI_PORT ?? "3113"}`;
const SERVICE_BASE = process.env.MOVIELENS_DEMO_URL ?? "http://localhost:3001";

const VIEWPORTS = [
  { suffix: "mobile", width: 390, height: 844 },
  { suffix: "tablet", width: 768, height: 1024 },
  { suffix: "desktop", width: 1440, height: 1000 },
];

const ACTION_FAN = 900000101;
const DRAMA_FAN = 900000102;
const COLD_START = 900000104;

/** States the deployed stack produces without being asked to fail. */
const SERVICE_CAPTURES = [
  {
    // Not part of the named matrix, and captured because the finish review's
    // five-second test needs a picture of what a signed-in viewer actually
    // meets first. Until the 7d cutover that is still the pre-redesign
    // dashboard rather than a movie.
    name: "landing-after-sign-in",
    path: "/",
    settle: (page) => page.getByRole("button", { name: "Sign out" }).waitFor(),
  },
  {
    name: "discover-learned",
    path: `/discover?userId=${DRAMA_FAN}`,
    settle: (page) => page.getByRole("heading", { level: 1 }).waitFor(),
  },
  {
    name: "discover-fallback",
    path: `/discover?userId=${COLD_START}`,
    settle: (page) => page.getByText("Popular while we learn").first().waitFor(),
  },
  {
    name: "library-populated",
    path: `/library?userId=${ACTION_FAN}`,
    settle: (page) => page.getByRole("tab", { name: /^Rated/ }).waitFor(),
  },
  {
    name: "library-empty",
    path: `/library?userId=${COLD_START}&tab=watchlist`,
    settle: (page) => page.getByRole("tab", { name: /^Watchlist/ }).waitFor(),
  },
  {
    name: "movie-detail",
    path: `/movies/1?user=${ACTION_FAN}`,
    settle: (page) => page.getByRole("heading", { level: 1 }).waitFor(),
  },
  {
    name: "quick-picks",
    path: `/quick-picks?user=${COLD_START}`,
    settle: (page) =>
      page.locator(".quick-picks-page[data-interactive='true']").waitFor(),
  },
];

/** States that only exist while something is in flight or broken. */
const FIXTURE_CAPTURES = [
  {
    name: "discover-loading",
    path: "/discover?demo=loading",
    settle: (page) => page.getByText("Loading movies").first().waitFor({ state: "attached" }),
  },
  {
    name: "discover-empty",
    path: "/discover?demo=empty",
    settle: (page) => page.getByText("No recommendations right now").waitFor(),
  },
  {
    name: "discover-upstream-error",
    path: "/discover?demo=recommendations-error",
    settle: (page) =>
      page.getByRole("alert", { name: /Recommendations upstream-error/ }).waitFor(),
  },
  {
    name: "discover-poster-error",
    path: "/discover?demo=poster-failure",
    settle: (page) => page.getByTestId("poster-fallback").first().waitFor(),
  },
];

async function signIn(page, base) {
  await page.goto(`${base}/`);
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.waitForURL(/\/realms\/demo\/protocol\/openid-connect\/auth/, { timeout: 60_000 });
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await page.getByRole("button", { name: "Sign out" }).waitFor({ timeout: 60_000 });
}

async function capture(page, base, item) {
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto(`${base}${item.path}`, { waitUntil: "networkidle" });
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

if (MODE === "service") {
  // The signed-out door is the one state that has to be captured before there
  // is a session to capture anything else with.
  await capture(page, SERVICE_BASE, {
    name: "auth-required",
    path: "/",
    settle: (target) => target.getByRole("button", { name: "Continue with Keycloak" }).waitFor(),
  });

  await signIn(page, SERVICE_BASE);

  // Recorded rather than assumed: which policy the warm persona was actually
  // served decides whether `discover-learned` is a learned capture at all, and
  // the README has to say which one this run produced.
  const policy = await page.evaluate(
    async (id) =>
      (await fetch(`/api/users/${id}/recommendations?limit=10`, { cache: "no-store" }).then(
        (response) => response.json(),
      )).serving_policy,
    DRAMA_FAN,
  );
  console.log(`warm persona serving policy: ${JSON.stringify(policy)}`);

  for (const item of SERVICE_CAPTURES) await capture(page, SERVICE_BASE, item);
} else {
  for (const item of FIXTURE_CAPTURES) await capture(page, FIXTURE_BASE, item);
}

await browser.close();
