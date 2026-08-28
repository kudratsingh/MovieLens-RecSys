import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";

/**
 * The product shell and the sign-in door, against the bypass-disabled demo
 * Compose stack.
 *
 * **This file writes nothing.** It signs in, reads five routes, and follows
 * redirects. That is deliberate rather than incidental: the two things it
 * checks — that every route renders the one shell, and that a deep link
 * survives the door — are properties of chrome and routing, and a journey that
 * also mutated a persona would be indistinguishable from one that raced with
 * `browser-auth.spec.ts`. It therefore takes no slot in the persona ownership
 * table in that file, reads Action Fan (the default persona every route falls
 * back to), and never touches Cold Start.
 *
 * Fixture mode cannot cover either half. `/quick-picks` has no fixture branch
 * and redirects without a session; the isolated harness renders `Exit preview`
 * where the product renders `Sign out`; and a door that hands a viewer back to
 * the address they asked for is only proved by a real Keycloak round trip.
 */

const ACTION_FAN = 900000101;
/** In the reviewed demo fixture, and not written to by anything here. */
const MOVIE_ID = 1;

const AUTH_ROUND_TRIP_MS = 30_000;

/**
 * The Keycloak half of `signInThroughKeycloak`, for the tests that need to
 * start from a door this file loaded itself rather than from `/`.
 */
async function submitKeycloakLogin(page: Page) {
  await page.waitForURL(/\/realms\/demo\/protocol\/openid-connect\/auth/, {
    timeout: AUTH_ROUND_TRIP_MS,
  });
  const username = page.locator("#username");
  await expect(username).toBeVisible({ timeout: AUTH_ROUND_TRIP_MS });
  await username.fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
}

const PRODUCT_ROUTES = [
  `/discover?userId=${ACTION_FAN}`,
  `/browse?user=${ACTION_FAN}`,
  `/library?userId=${ACTION_FAN}`,
  `/movies/${MOVIE_ID}?user=${ACTION_FAN}`,
  `/quick-picks?user=${ACTION_FAN}`,
];

/**
 * `/quick-picks` shipped outside the shell: no `<main>`, no skip link, neither
 * navigation, no way to sign out, and `Demo persona 900000101` where every
 * other route names the persona. It was finish-gate item B3, cleared for
 * Browse and movie detail in the cutover and never applied here.
 */
test("every product route renders the one product shell", async ({ page }) => {
  test.slow();
  await signInThroughKeycloak(page);

  for (const route of PRODUCT_ROUTES) {
    await page.goto(route);

    await expect(page.getByRole("main"), route).toHaveCount(1);
    await expect(page.getByRole("main"), route).toHaveAttribute("id", "main-content");
    await expect(
      page.getByRole("link", { name: "Skip to content" }),
      route,
    ).toHaveAttribute("href", "#main-content");

    await expect(page.getByRole("navigation", { name: "Primary" }), route).toBeVisible();
    // Present at desktop widths but `display: none`, so it is out of the
    // accessibility tree and a role locator cannot see it — the structural
    // locator is the one that can say "the route did not drop it" here. That
    // it is *usable* on a phone is the next test's job, at 390.
    await expect(page.locator("nav.bottom-navigation"), route).toHaveCount(1);
    await expect(
      page.locator("nav.bottom-navigation"),
      route,
    ).toHaveAttribute("aria-label", "Primary mobile");

    await expect(page.getByRole("button", { name: "Sign out" }), route).toBeVisible();

    // The persona is named, not printed as an ID and not left as a placeholder.
    const persona = page.locator(".shell-header .persona-cluster");
    await expect(persona, route).toContainText("Exploring as");
    await expect(persona, route).not.toContainText(String(ACTION_FAN));
    await expect(persona, route).not.toContainText("Demo persona");
  }
});

test("the mobile shell keeps its bottom navigation on every route", async ({ page }) => {
  test.slow();
  await page.setViewportSize({ width: 390, height: 844 });
  await signInThroughKeycloak(page);

  for (const route of PRODUCT_ROUTES) {
    await page.goto(route);
    const mobile = page.getByRole("navigation", { name: "Primary mobile" });
    await expect(mobile, route).toBeVisible();
    await expect(mobile.getByRole("link"), route).toHaveCount(3);
    // Both identities are named at this width; below 1050px they used to be
    // `display: none`, which took them out of the accessibility tree too.
    await expect(page.locator(".shell-header .actor-copy"), route).toBeVisible();
    await expect(page.locator(".shell-header .persona-cluster"), route).toBeVisible();
  }
});

/**
 * Every signed-out deep link used to land on the default persona's Discover
 * page: the movie, the tab, and the requested persona were all discarded at
 * the door. On a demo whose sessions expire, that is most of the time a shared
 * link is followed.
 */
test("a signed-out deep link survives the door", async ({ page }) => {
  test.slow();
  const target = `/library?userId=${ACTION_FAN}&tab=watchlist`;

  await page.goto(target);
  await expect(page).toHaveURL(`/?next=${encodeURIComponent(target)}`);
  // The door says where it is about to put the viewer, so the promise is
  // visible before it is kept.
  await expect(page.getByText(/You will land back on Library/)).toBeVisible();

  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await submitKeycloakLogin(page);

  await expect(page).toHaveURL(target, { timeout: AUTH_ROUND_TRIP_MS });
  await expect(page.getByRole("tab", { name: /^Watchlist/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("a movie deep link keeps both the movie and the requested persona", async ({
  page,
}) => {
  test.slow();
  const target = `/movies/${MOVIE_ID}?user=${ACTION_FAN}`;

  await page.goto(target);
  await expect(page).toHaveURL(`/?next=${encodeURIComponent(target)}`);
  await expect(page.getByText(/You will land back on that movie/)).toBeVisible();

  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await submitKeycloakLogin(page);

  await expect(page).toHaveURL(target, { timeout: AUTH_ROUND_TRIP_MS });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("the door refuses a return address that is not ours", async ({ page }) => {
  test.slow();
  // The door is the app's only unauthenticated surface, so the one thing it
  // must not become is an open redirect.
  await page.goto("/?next=https%3A%2F%2Fexample.com%2Fowned");
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
  await expect(page.getByText(/You will land back on/)).toHaveCount(0);

  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await submitKeycloakLogin(page);

  // The default product address, not the address the query asked for.
  await expect(page).toHaveURL(/\/discover\?userId=\d+$/, {
    timeout: AUTH_ROUND_TRIP_MS,
  });
});
