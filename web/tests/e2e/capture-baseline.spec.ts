import { expect, test, type Page } from "@playwright/test";

const EVIDENCE = "../docs/frontend/evidence/baseline";

/**
 * The pre-redesign dashboard moved to `/legacy` with the 7d cutover, so this
 * capture follows it there. It is the same surface these baseline images have
 * always shown; what changed is that it is no longer what `/` renders.
 */
async function signIn(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.goto("/legacy");
  await expect(page.getByRole("button", { name: "Action Fan" })).toBeVisible();
}

async function selectPersona(page: Page, name: string) {
  await page.getByRole("button", { name }).click();
  await expect(page.getByRole("heading", { name: "Your next watch" })).toBeVisible();
}

async function capture(page: Page, name: string, fullPage = false) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${EVIDENCE}/${name}`, fullPage });
}

test("capture the authenticated pre-redesign baseline matrix", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page);
  await selectPersona(page, "Action Fan");
  await capture(page, "action-desktop-first-viewport.png");
  await capture(page, "action-desktop-full-page.png", true);

  await selectPersona(page, "Cold Start");
  await capture(page, "cold-desktop-first-viewport.png");

  await page.setViewportSize({ width: 768, height: 1024 });
  await selectPersona(page, "Action Fan");
  await capture(page, "action-tablet-first-viewport.png");

  await page.setViewportSize({ width: 390, height: 844 });
  await capture(page, "action-mobile-first-viewport.png");
  await capture(page, "action-mobile-full-page.png", true);

  await selectPersona(page, "Cold Start");
  await capture(page, "cold-mobile-first-viewport.png");

  await page.route("**/api/users/**", async (route) => {
    await route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Captured upstream API failure" }),
    });
  });
  await page.reload();
  await expect(page.getByText("Captured upstream API failure")).toBeVisible();
  await capture(page, "api-error-mobile.png", true);
  await page.unroute("**/api/users/**");

  await page.route("**/_next/image**", (route) => route.abort());
  await page.reload();
  await selectPersona(page, "Action Fan");
  await capture(page, "poster-fallback-mobile.png", true);
});
