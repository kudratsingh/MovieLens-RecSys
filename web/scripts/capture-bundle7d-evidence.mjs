import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * The Bundle 7D cutover evidence.
 *
 * Deliberately small: this is not a second copy of the 7A matrix, which is
 * still the finish gate's screenshot set and is still valid for the states it
 * covers. What is recaptured here is only what the cutover changed —
 *
 *   the front door, signed out and signed in (B1);
 *   the legacy dashboard in its new home, labelled, with a serving-contract
 *     panel that reports the policy the response carried (B1);
 *   Browse, movie detail, and Library on the shared product shell at 390px,
 *     where the bottom navigation the design contract requires is the thing
 *     to look at (B3).
 *
 * Every capture is service-backed: the seeded Compose stack with
 * `DEV_AUTH_BYPASS=false`, real Keycloak, real RLS, and the real catalog. No
 * state here needs failure injection, because none of them is a state a
 * healthy stack cannot hold still in.
 *
 * Usage:
 *   MOVIELENS_DEMO_URL=http://localhost:3001 node scripts/capture-bundle7d-evidence.mjs
 */

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-7d");

const BASE = process.env.MOVIELENS_DEMO_URL ?? "http://localhost:3001";

const MOBILE = { suffix: "mobile", width: 390, height: 844 };
const TABLET = { suffix: "tablet", width: 768, height: 1024 };
const DESKTOP = { suffix: "desktop", width: 1440, height: 1000 };

const ACTION_FAN = 900000101;
const DRAMA_FAN = 900000102;
const ECLECTIC = 900000103;

/** Captured before there is a session, because that is the point of it. */
const SIGNED_OUT = [
  {
    name: "sign-in-door",
    path: "/",
    viewports: [MOBILE, DESKTOP],
    settle: (page) => page.getByRole("button", { name: "Continue with Keycloak" }).waitFor(),
  },
];

const SIGNED_IN = [
  {
    // The whole cutover in one file: `/` now answers with the product. The
    // 7A capture of the same name, in `../bundle-7a/`, is the dashboard this
    // replaces — they are meant to be read side by side.
    name: "landing-after-sign-in",
    path: "/",
    viewports: [MOBILE, TABLET, DESKTOP],
    settle: (page) => page.getByRole("heading", { level: 1 }).waitFor(),
  },
  {
    name: "legacy-dashboard",
    path: "/legacy",
    viewports: [MOBILE, DESKTOP],
    settle: async (page) => {
      await page.getByText(/This is the legacy dashboard/).waitFor();
      // The panel is the finding this capture exists for, so wait for it to
      // hold a policy rather than the placeholder it renders before the first
      // response lands.
      await page
        .getByTestId("serving-contract-policy")
        .filter({ hasNotText: "Not read yet" })
        .waitFor();
    },
  },
  {
    name: "browse-shell",
    path: `/browse?user=${ECLECTIC}`,
    viewports: [MOBILE],
    settle: (page) => page.getByRole("navigation", { name: "Primary mobile" }).waitFor(),
  },
  {
    name: "movie-detail-shell",
    path: `/movies/1?user=${ACTION_FAN}`,
    viewports: [MOBILE],
    settle: (page) => page.getByRole("navigation", { name: "Primary mobile" }).waitFor(),
  },
  {
    name: "library-shell",
    path: `/library?userId=${ACTION_FAN}`,
    viewports: [MOBILE],
    settle: (page) => page.getByRole("navigation", { name: "Primary mobile" }).waitFor(),
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

for (const item of SIGNED_OUT) await capture(page, item);

await signIn(page);

// Recorded rather than assumed: the legacy panel's whole point is that it
// reports what the response said, so the run has to say what the response said.
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

for (const item of SIGNED_IN) await capture(page, item);

await browser.close();
