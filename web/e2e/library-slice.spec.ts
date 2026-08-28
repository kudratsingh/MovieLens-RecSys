import { expect, test, type Page } from "@playwright/test";

/**
 * Responsive coverage for the Library route against the recorded client.
 *
 * These run at 390, 768, and 1440 through the isolated UI projects. Service-
 * backed proof that a rating survives a round trip lives in the browser-auth
 * journey; what is checked here is that every collection state is legible and
 * operable at each width.
 */

async function horizontalOverflow(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("the rated collection reads as a record of the selected persona", async ({ page }) => {
  await page.goto("/ui-preview/library");

  await expect(
    page.getByRole("heading", { level: 1, name: "A record of what moved you." }),
  ).toBeVisible();
  // The persona is named once, by the shell. The route used to print its own
  // `Exploring as {persona}` eyebrow directly under the header that already
  // said it.
  await expect(page.locator(".persona-cluster")).toContainText("Action Fan");
  await expect(page.locator(".library-intro")).not.toContainText("Exploring as");
  await expect(
    page.getByText(/not the signed-in actor's private library/i),
  ).toContainText("Action Fan");
  await expect(page.getByRole("tab", { name: "Rated 12" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("list", { name: "Rated movies" })).toBeVisible();
  await expect(page.getByLabel("Rating for Memories of Murder")).toHaveValue("4.5");
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("rows carry artwork, and a row without it carries the shared mark", async ({
  page,
}) => {
  await page.goto("/ui-preview/library");
  await expect(page.getByRole("list", { name: "Rated movies" })).toBeVisible();

  // Library was the one route in a poster-first product with no artwork on any
  // tab at any width. The frame is reserved either way, so a missing poster
  // never moves the rows below it.
  const thumbs = page.locator(".library-thumb");
  await expect(thumbs.first()).toBeVisible();
  const frames = await page.$$eval(".library-thumb", (nodes) =>
    nodes.map((node) => ({
      marked: Boolean(node.querySelector('[data-testid="poster-fallback"]')),
      broken: Boolean(
        node.querySelector("img")?.complete && node.querySelector("img")?.naturalWidth === 0,
      ),
      width: Math.round(node.getBoundingClientRect().width),
    })),
  );
  expect(frames.length).toBeGreaterThan(0);
  expect(frames.filter((frame) => frame.broken)).toEqual([]);
  expect(frames.some((frame) => !frame.marked)).toBe(true);
  expect(frames.some((frame) => frame.marked)).toBe(true);
  for (const frame of frames) expect(frame.width).toBeGreaterThan(0);

  // The mark's caption is dropped at row density — 56px cannot carry it — and
  // the row's own title is the name, so nothing is lost.
  const mark = page.locator('.library-thumb [data-testid="poster-fallback"]').first();
  await expect(mark.locator("span").first()).toBeVisible();
  await expect(mark.getByText("Artwork unavailable")).toBeHidden();
});

test("a row prints its year once, on the metadata line", async ({ page }) => {
  await page.goto("/ui-preview/library");

  const row = page.getByRole("listitem").filter({ hasText: "Memories of Murder" });
  await expect(row.getByRole("link", { name: "Memories of Murder" })).toBeVisible();
  await expect(row).toContainText("2003 · Crime · Mystery");
  await expect(row).not.toContainText("Memories of Murder (2003)");
});

test("the ratings summary stays a live read rather than a model explanation", async ({
  page,
}) => {
  await page.goto("/ui-preview/library");

  const summary = page.getByRole("region", { name: /A readable outline/ });
  await expect(summary).toContainText("This summary is not a deployed-model explanation.");
  await expect(summary).toContainText("source live-ratings-v1");
  await expect(summary).not.toContainText(/LightGBM|ranker|Because you liked/i);
});

test("each collection owns its URL state and its own copy", async ({ page }) => {
  await page.goto("/ui-preview/library");

  await page.getByRole("tab", { name: "Watchlist 4" }).click();
  await expect(page).toHaveURL(/tab=watchlist/);
  await expect(page.getByText(/Saving is organizational only/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Watchlist movies" })).toBeVisible();

  await page.getByRole("tab", { name: "History 15" }).click();
  await expect(page).toHaveURL(/tab=history/);
  await expect(page.getByText(/Watched titles are the positive interactions/)).toBeVisible();
});

test("history appends its next page without repeating a row", async ({ page }) => {
  await page.goto("/ui-preview/library?tab=history");

  const rows = page.getByRole("list", { name: "History movies" }).getByRole("listitem");
  await expect(rows).toHaveCount(12);

  await page.getByRole("button", { name: "Load more" }).click();

  await expect(rows).toHaveCount(15);
  await expect(page).toHaveURL(/cursor=recorded%3A12/);
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 3, name: "Shoplifters" })).toHaveCount(1);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("an empty collection offers a way out to Browse", async ({ page }) => {
  await page.goto("/ui-preview/library?tab=watchlist&empty=watchlist");

  await expect(page.getByText("Nothing saved yet")).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed collection read leaves the ratings summary readable", async ({ page }) => {
  await page.goto("/ui-preview/library?fail=library");

  await expect(page.getByRole("alert", { name: "Rated collection could not be loaded" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /A readable outline/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Watchlist" })).toBeVisible();
});

test("removing history is confirmed and deleting a rating is not", async ({ page }) => {
  await page.goto("/ui-preview/library?tab=history");

  const row = page.getByRole("listitem").filter({ hasText: "Memories of Murder" });
  await row.getByRole("button", { name: "Remove rating" }).click();
  await expect(row.getByText(/Watched Aug 16, 2026 · Not rated/)).toBeVisible();

  await row.getByRole("button", { name: "Remove from history" }).click();
  const confirm = page.getByRole("group", {
    name: "Confirm removing Memories of Murder from watched history",
  });
  await expect(confirm).toContainText("deletes the watched interaction and its rating");
  await expect(confirm).toContainText("stops counting as a positive signal for Action Fan");

  await confirm.getByRole("button", { name: "Keep it" }).click();
  await expect(row.getByRole("button", { name: "Remove from history" })).toBeFocused();
});

test("primary controls stay thumb-sized on a narrow screen", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile touch-target assertion");
  await page.goto("/ui-preview/library?tab=watchlist");

  const button = page
    .getByRole("listitem")
    .first()
    .getByRole("button", { name: "Mark watched" });
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
});
