import { expect, test } from "@playwright/test";

/**
 * Responsive coverage for the live `/discover` route, driven through the
 * isolated recorded-scenario harness so every state is reachable without a
 * backend. Each Playwright project runs the file at 390, 768, and 1440 widths.
 */

const HANDMAIDEN = "The Handmaiden";

async function pageOverflow(page: import("@playwright/test").Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("the primary movie is the first read and the policy label follows the response", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(
    page.getByRole("region", { name: HANDMAIDEN }).getByText("Ranked by the learned model"),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: /Primary/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the whole catalog" })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a fallback response says so and never borrows learned copy", async ({ page }) => {
  await page.goto("/discover?demo=fallback");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(page.getByText("Popular while we learn").first()).toBeVisible();
  await expect(page.getByText("Ranked by the learned model")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("technical evidence is two deliberate actions away and does not block the movie", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("LightGBM rank over learned item-item candidates")).toBeVisible();
  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);

  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(page.getByTestId("technical-evidence")).toBeVisible();
  await expect(drawer.getByText("item-item-v3", { exact: true })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
});

test("a failed history region leaves the movie decision usable", async ({ page }) => {
  await page.goto("/discover?demo=history-error");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(page.getByRole("button", { name: "Why this?" })).toBeVisible();
  await expect(page.getByRole("alert", { name: /Watch history/ })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed recommendation region leaves watch history readable", async ({ page }) => {
  await page.goto("/discover?demo=recommendations-error");

  await expect(page.getByRole("alert", { name: /Recommendations/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /has watched/ })).toBeVisible();
  await expect(page.getByText("Heat (1995)")).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed evidence region still lets the reason and policy be read", async ({ page }) => {
  await page.goto("/discover?demo=evidence-error");

  await page.getByRole("button", { name: "Why this?" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("LightGBM rank over learned item-item candidates")).toBeVisible();

  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(drawer.getByRole("alert", { name: /Prediction audits/ })).toBeVisible();
  await expect(drawer.getByRole("alert", { name: /Online features/ })).toBeVisible();
});

test("an empty ranked set offers a way forward instead of an error", async ({ page }) => {
  await page.goto("/discover?demo=empty");

  await expect(page.getByText("No recommendations right now")).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the catalog" }).first()).toBeVisible();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("loading announces itself without claiming a failure", async ({ page }) => {
  await page.goto("/discover?demo=loading");

  await expect(page.getByText("Loading movies")).toBeAttached();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("an expired session offers a reauthentication path", async ({ page }) => {
  await page.goto("/discover?demo=auth-expired");

  await expect(page.getByRole("alert", { name: /Recommendations auth-expired/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in again" }).first()).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed poster keeps the movie identity and does not move the layout", async ({
  page,
}) => {
  await page.goto("/discover?demo=poster-failure");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(
    page.getByRole("region", { name: HANDMAIDEN }).getByTestId("poster-fallback"),
  ).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a live read with no API reachable fails visibly instead of showing recorded data", async ({
  page,
}) => {
  // No `demo` parameter, so the route reads live even inside the isolated mode.
  await page.goto("/discover");

  await expect(page.getByRole("alert", { name: /Recommendations/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toHaveCount(0);
  await expect(page.getByText("Recorded contract fixtures")).toHaveCount(0);
});

test("mobile keeps the primary decision and the bottom navigation reachable", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile shell assertion");
  await page.goto("/discover?demo=learned");

  const bottom = page.getByRole("navigation", { name: "Primary mobile" });
  await expect(bottom).toBeVisible();
  await expect(bottom.getByRole("link", { name: "Browse" })).toHaveAttribute(
    "href",
    "/browse?user=900000101",
  );
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
});
