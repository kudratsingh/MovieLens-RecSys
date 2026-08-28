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

  await page.getByRole("tab", { name: "Seen 15" }).click();
  await expect(page).toHaveURL(/tab=history/);
  await expect(page.getByText(/Watched titles are the positive interactions/)).toBeVisible();
});

test("history appends its next page without repeating a row", async ({ page }) => {
  await page.goto("/ui-preview/library?tab=history");

  const rows = page.getByRole("list", { name: "Seen movies" }).getByRole("listitem");
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

/**
 * The Seen tab: an evolution of History, not a new route.
 *
 * `history` is still the URL value, the API value, and the tab's identity in
 * the type — only what the reader sees changed. What is new is the spotlight
 * above the list and the search, genre, year and ranking controls beside it,
 * and all of them walk the same page of rows the list shows.
 */
test("Seen leads with one title at a time, walking the list it sits above", async ({
  page,
}) => {
  await page.goto("/ui-preview/library?tab=history");

  const spotlight = page.getByRole("region", { name: "Seen spotlight" });
  await expect(spotlight).toBeVisible();
  await expect(spotlight.getByText("1 of 15", { exact: true })).toBeVisible();
  // The spotlight's title is the list's first row, because it is the list.
  const first = await spotlight.getByRole("heading", { level: 3 }).innerText();
  const rows = page.getByRole("list", { name: "Seen movies" }).getByRole("listitem");
  await expect(rows.first()).toContainText(first);
  await expect(spotlight.getByRole("button", { name: "Previous seen title" })).toBeDisabled();

  await spotlight.getByRole("button", { name: "Next seen title" }).click();
  await expect(spotlight.getByText("2 of 15", { exact: true })).toBeVisible();
  await expect(spotlight.getByRole("heading", { level: 3 })).not.toHaveText(first);
  await expect(spotlight.getByRole("button", { name: "Previous seen title" })).toBeEnabled();

  // The arrow keys move it too, and focus stays on the pressed control so a
  // repeated press keeps working.
  await page.keyboard.press("ArrowRight");
  await expect(spotlight.getByText("3 of 15", { exact: true })).toBeVisible();
  await expect(spotlight.getByRole("button", { name: "Next seen title" })).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(spotlight.getByText("2 of 15", { exact: true })).toBeVisible();

  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("the enriched fields arrive on top of a card that is already complete", async ({
  page,
}) => {
  await page.goto("/ui-preview/library?tab=history");
  const spotlight = page.getByRole("region", { name: "Seen spotlight" });

  // Runtime and the crowd score come from the detail record; the title, the
  // year and the seen-on date never waited for them.
  await expect(spotlight.getByText(/^Seen on /)).toBeVisible();
  await expect(spotlight.getByText("8.1 / 10 · 4,812 ratings")).toBeVisible();
  await expect(spotlight.getByText(/2h 25m/)).toBeVisible();
});

test("Seen offers the filters and the rankings the collection can answer", async ({
  page,
}) => {
  await page.goto("/ui-preview/library?tab=history");

  // Located by id: both controls are wrapped labels, and a select's accessible
  // name picks up its selected option's text as well as the label's.
  const sort = page.locator("#library-sort");
  await expect(sort.locator("option")).toHaveText([
    "Most recent",
    "Title",
    "Highest rated",
    "Newest release",
    "Highest TMDB score",
  ]);

  await page.locator("#library-genre").selectOption("Animation");
  await expect(page).toHaveURL(/genre=Animation/);
  const rows = page.getByRole("list", { name: "Seen movies" }).getByRole("listitem");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Perfect Blue");
  // The spotlight walks the filtered list, so it says what the filter left.
  await expect(
    page.getByRole("region", { name: "Seen spotlight" }).getByText("1 of 1", {
      exact: true,
    }),
  ).toBeVisible();

  await sort.selectOption("tmdb");
  await expect(page).toHaveURL(/sort=tmdb/);
  // The filter still stands, and a title the snapshot never scored prints
  // nothing in the mark's place rather than an "unscored" label.
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).not.toContainText("TMDB");

  await page.locator("#library-genre").selectOption("");
  await expect(page).not.toHaveURL(/genre=/);
  // Highest crowd score first, with the score printed beside the row it belongs
  // to — the ordering and the mark read the same field, so they cannot disagree.
  await expect(rows.first()).toContainText("Parasite");
  await expect(rows.first()).toContainText("TMDB 8.5");
});

test("a year range that matches nothing says so, and offers a way back", async ({
  page,
}) => {
  await page.goto("/ui-preview/library?tab=history&year_from=1900&year_to=1910");

  await expect(page.getByText("No matches in this collection")).toBeVisible();
  await expect(page.getByText("Nothing in Seen matches these filters.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Seen spotlight" })).toHaveCount(0);

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByRole("list", { name: "Seen movies" })).toBeVisible();
  await expect(page).not.toHaveURL(/year_from/);
});

test("a page link that no longer matches the view restarts from the top", async ({
  page,
}) => {
  // A cursor is bound to the query fingerprint it was issued under, so this one
  // — kept from a differently sorted view — is a `400` the route recovers from
  // rather than an outage it reports.
  await page.goto("/ui-preview/library?tab=history&cursor=recorded%3A12%3Astale-view");

  await expect(
    page.getByText(
      "That page link no longer matches this view, so the list starts from the beginning.",
    ),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: "Seen movies" })).toBeVisible();
  await expect(page).not.toHaveURL(/cursor=/);
  // Recovered, not reported: the collection region never renders its failure.
  await expect(
    page.getByRole("alert", { name: /Seen collection could not be loaded/ }),
  ).toHaveCount(0);
});
