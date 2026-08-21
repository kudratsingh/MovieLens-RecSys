import { expect, type Page } from "@playwright/test";

/**
 * The auth round trip is the one step in these journeys that legitimately
 * outruns the 10s default `expect` timeout. The redirect lands on a Keycloak
 * that may still be warming a cold JVM on a 4-vCPU runner, and the callback
 * then exchanges an authorization code before the app renders anything at all.
 * A 2026-08-21 CI run lost the PKCE journey to exactly that — `Sign out` was
 * never found, on a stack that was working.
 *
 * The bound is deliberately scoped to the two waits that span the round trip.
 * Everything after sign-in keeps the 10s default, so a page that is genuinely
 * broken still fails fast instead of sitting out half a minute.
 */
const AUTH_ROUND_TRIP_MS = 30_000;

/**
 * Bypass-disabled sign-in through the real Keycloak demo realm.
 *
 * The round trip ends on `/`, which since the 7d cutover redirects a signed-in
 * viewer to Discover. `Sign out` is therefore the product shell's button
 * rather than the legacy dashboard's, which is exactly what this should be
 * waiting for: the journey is signed in when the product is on screen.
 */
export async function signInThroughKeycloak(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();

  // Split into two waits on purpose: reaching the authorize endpoint and being
  // served a usable form are different failures, and the URL says which one
  // happened before the form selector reports a missing element.
  await page.waitForURL(/\/realms\/demo\/protocol\/openid-connect\/auth/, {
    timeout: AUTH_ROUND_TRIP_MS,
  });
  const username = page.locator("#username");
  await expect(username).toBeVisible({ timeout: AUTH_ROUND_TRIP_MS });

  await username.fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();

  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible({
    timeout: AUTH_ROUND_TRIP_MS,
  });
}
