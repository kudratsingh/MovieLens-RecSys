import { expect, test, type Page } from "@playwright/test";

/**
 * Browse and movie detail against the recorded catalog endpoint.
 *
 * These run at 390, 768, and 1440 in the isolated preview, where the component
 * under test is the same one the authenticated route mounts. What is being
 * checked is the behaviour that only exists across requests and navigations —
 * cursor continuation, restoration, a rejected cursor — plus the metadata and
 * failure states the evidence matrix has to show.
 */

const GRID = { name: "Browse results" };

async function cardTitles(page: Page): Promise<string[]> {
  return page.locator(".catalog-cell .poster-title").allInnerTexts();
}

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("Browse renders a poster grid without claiming a total", async ({ page }) => {
  await page.goto("/ui-preview/browse");

  await expect(page.getByRole("list", GRID)).toBeVisible();
  await expect(page.getByText(/titles loaded/)).toContainText("more available");
  // A cursor endpoint cannot answer "of N", so the UI never says it.
  await expect(page.getByText(/\bof \d+\b/)).toHaveCount(0);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("search and genre filters are serialized into the address", async ({ page }) => {
  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  await page.getByRole("searchbox").fill("the");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/[?&]q=the/);

  await page.getByRole("button", { name: "Filters" }).click();
  await page.getByRole("button", { name: "Drama", exact: true }).click();
  await expect(page).toHaveURL(/genre=Drama/);
  await expect(page).toHaveURL(/[?&]q=the/);
  // A filter edit invalidates the cursor rather than carrying it into a query
  // it was never issued for.
  await expect(page).not.toHaveURL(/cursor=/);
});

test("continuing the cursor appends a page without repeating a title", async ({ page }) => {
  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  const firstPage = await cardTitles(page);
  await page.getByRole("button", { name: "Load more movies" }).click();
  await expect(page.getByRole("listitem")).toHaveCount(firstPage.length * 2);

  const combined = await cardTitles(page);
  expect(new Set(combined).size).toBe(combined.length);
  expect(combined.slice(0, firstPage.length)).toEqual(firstPage);
  await expect(page).toHaveURL(/cursor=/);
});

test("a cursor that no longer matches the query restarts at the top", async ({ page }) => {
  await page.goto("/ui-preview/browse?cursor=not-a-real-cursor");

  await expect(page.getByText(/no longer matches these filters/)).toBeVisible();
  await expect(page.getByRole("list", GRID)).toBeVisible();
  // Next's route announcer is also an alert; the catalog must not add one.
  await expect(page.getByRole("alert", { name: /Catalog/ })).toHaveCount(0);
});

test("returning from a movie restores the query and the position", async ({ page }) => {
  await page.goto("/ui-preview/browse?genre=Drama");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  const target = page.getByRole("listitem").last();
  await target.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(0);

  const title = await target.locator(".poster-title").innerText();
  await target.getByRole("link").first().click();
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/genre=Drama/);
  await expect(page.getByRole("list", GRID)).toBeVisible();
  await expect
    .poll(async () => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(before - 200);
});

test("incomplete metadata reads as a named gap, not a broken card", async ({ page }) => {
  await page.goto("/ui-preview/browse?sort=newest");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  await expect(page.getByText("Details unavailable").first()).toBeVisible();
  await expect(page.getByTestId("poster-fallback").first()).toBeVisible();
  // Deterministic local artwork: nothing on this page reaches a third party.
  const posterHosts = await page
    .locator(".catalog-cell img")
    .evaluateAll((images) =>
      images.map((image) => new URL((image as HTMLImageElement).src).host),
    );
  for (const host of posterHosts) {
    expect(host).toBe(new URL(page.url()).host);
  }
});

test("a failed catalog read is its own state with a way to retry", async ({ page }) => {
  await page.goto("/ui-preview/browse?fail=catalog");

  const alert = page.getByRole("alert", { name: "Catalog upstream-error" });
  await expect(alert).toContainText("Catalog could not be loaded");
  await expect(alert.getByRole("button", { name: "Try again" })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("an expired session offers reauthentication rather than a retry", async ({ page }) => {
  await page.goto("/ui-preview/browse?fail=catalog-auth");

  await expect(page.getByText("Your session expired")).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in again" })).toBeVisible();
});

test("movie detail leads with the movie and discloses its provenance", async ({ page }) => {
  await page.goto("/ui-preview/movies/101");

  await expect(page.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
  await expect(page.getByText("Reviewed snapshot · Complete details")).toBeVisible();
  await expect(page.getByRole("button", { name: "Watchlist" })).toBeVisible();

  await page.getByRole("button", { name: "Record details" }).click();
  await expect(page.getByRole("dialog")).toContainText("carries no ranking score");
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("an unknown movie uses the shared not-found state", async ({ page }) => {
  await page.goto("/ui-preview/movies/999999");

  const alert = page.getByRole("alert", { name: "Movie detail not-found" });
  await expect(alert).toContainText("Movie detail not found");
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
});
