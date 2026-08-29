import { expect, test, type Page } from "@playwright/test";

/**
 * Opens a Library view and waits for hydration. Before it, every control on
 * the page is real markup that does nothing — the exact shape of a press that
 * "did not work" — and a cold dev server on a CI runner makes that window
 * long enough to lose the first click.
 */
async function openLibrary(page: Page, url = "/ui-preview/library") {
  await page.goto(url);
  await expect(page.locator(".library-route")).toHaveAttribute("data-interactive", "true");
}

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

/** The form's own controls, then every control in the row it sits in. */
async function controlRowBoxes(page: Page) {
  return page.evaluate(() => {
    const measure = (node: Element, name: string) => {
      const rect = node.getBoundingClientRect();
      return {
        name,
        x: rect.x,
        y: rect.y,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    };
    const label = (node: Element) =>
      node.id || `${node.tagName.toLowerCase()}.${node.className}`;
    const form = document.querySelector(".library-filter");
    if (!form) throw new Error("no filter form on this tab");
    return {
      form: measure(form, "form"),
      wraps: getComputedStyle(form).flexWrap === "wrap",
      inForm: [...form.querySelectorAll("input, select, button")].map((node) =>
        measure(node, label(node)),
      ),
      row: [
        ...document.querySelectorAll(
          ".library-controls input, .library-controls select, .library-controls button",
        ),
      ].map((node) => measure(node, label(node))),
    };
  });
}

/**
 * The filter row's layout promises, at whatever width the caller is at.
 *
 * Every one of these was false on the Seen tab before the row learned to wrap:
 * the search input collapsed to its own padding (34px at 390 and at 768) while
 * the `Filter` button sat 97px past the form's right edge, painted over the
 * genre select beside it. None of that overflowed the document, which is why
 * the 320px sweep never saw it — a control can be unusable, and can cover
 * another control, entirely inside the page.
 */
async function expectFilterRowIsLaidOut(page: Page, at: string) {
  const { form, wraps, inForm, row } = await controlRowBoxes(page);
  // Below 1024px the bounds take a line of their own, which is what leaves the
  // search a usable width; at and above it the row is one line by design.
  const narrow = (page.viewportSize()?.width ?? 0) < 1024;

  // Half a pixel of tolerance: a fractional layout can round either way.
  const escaped = inForm.filter(
    (control) =>
      control.x < form.x - 0.5 ||
      control.right > form.right + 0.5 ||
      control.y < form.y - 0.5 ||
      control.bottom > form.bottom + 0.5,
  );
  expect(
    escaped.map((control) => control.name).join(", "),
    `controls outside the form at ${at}`,
  ).toBe("");

  const overlaps: string[] = [];
  for (let i = 0; i < row.length; i += 1) {
    for (let j = i + 1; j < row.length; j += 1) {
      const [a, b] = [row[i], row[j]];
      const shared =
        Math.min(a.right, b.right) - Math.max(a.x, b.x) > 1 &&
        Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y) > 1;
      if (shared) overlaps.push(`${a.name} over ${b.name}`);
    }
  }
  expect(overlaps.join(", "), `overlapping controls at ${at}`).toBe("");

  const search = row.find((control) => control.name === "library-search");
  const submit = inForm.find((control) => control.name.startsWith("button."));
  // The genre select is Seen's alone, and Seen is the tab with the bounds.
  const genre = row.find((control) => control.name === "library-genre");
  // A missing control is a broken fixture rather than a failed promise, so it
  // stops the run here instead of reading as a layout regression.
  if (!search || !submit) throw new Error(`no filter row to measure at ${at}`);

  // The field somebody types a title into is never narrower than the select
  // beside it — the shape the collapse took, and a cheaper thing to assert than
  // a pixel count that would have to be maintained per width. Narrow widths
  // only: on the one-line desk layout the search is sized by what the rest of
  // the row leaves it, so this would be measuring a runner's font rather than
  // the layout.
  if (genre && narrow) {
    expect(
      search.width,
      `search (${Math.round(search.width)}px) narrower than the genre select at ${at}`,
    ).toBeGreaterThanOrEqual(genre.width);
  }
  expect(search.height, `search below a 44px target at ${at}`).toBeGreaterThanOrEqual(44);
  expect(submit.height, `submit below a 44px target at ${at}`).toBeGreaterThanOrEqual(44);
  expect(submit.width, `submit below a 44px target at ${at}`).toBeGreaterThanOrEqual(44);

  expect(await horizontalOverflow(page), `document overflows at ${at}`).toBeLessThanOrEqual(1);

  // The mechanism behind all of the above, stated once: a row carrying the
  // bounds has more than one line of controls below 1024px, so a build that
  // stops wrapping is the regression even on a runner whose fonts happen to
  // leave it enough room to look fine.
  if (narrow && row.some((control) => control.name === "library-year-from")) {
    expect(wraps, `the filter row must be allowed to wrap at ${at}`).toBe(true);
  }
}

test("the rated collection reads as a record of the selected persona", async ({ page }) => {
  await openLibrary(page);

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
  await openLibrary(page);
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
  await openLibrary(page);

  const row = page.getByRole("listitem").filter({ hasText: "Memories of Murder" });
  await expect(row.getByRole("link", { name: "Memories of Murder" })).toBeVisible();
  await expect(row).toContainText("2003 · Crime · Mystery");
  await expect(row).not.toContainText("Memories of Murder (2003)");
});

test("the ratings summary stays a live read rather than a model explanation", async ({
  page,
}) => {
  await openLibrary(page);

  const summary = page.getByRole("region", { name: /A readable outline/ });
  await expect(summary).toContainText("This summary is not a deployed-model explanation.");
  await expect(summary).toContainText("source live-ratings-v1");
  await expect(summary).not.toContainText(/LightGBM|ranker|Because you liked/i);
});

test("each collection owns its URL state and its own copy", async ({ page }) => {
  await openLibrary(page);

  await page.getByRole("tab", { name: "Watchlist 4" }).click();
  await expect(page).toHaveURL(/tab=watchlist/);
  await expect(page.getByText(/Saving is organizational only/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Watchlist movies" })).toBeVisible();

  await page.getByRole("tab", { name: "Seen 15" }).click();
  await expect(page).toHaveURL(/tab=history/);
  await expect(page.getByText(/Watched titles are the positive interactions/)).toBeVisible();
});

test("history appends its next page without repeating a row", async ({ page }) => {
  await openLibrary(page, "/ui-preview/library?tab=history");

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
  await openLibrary(page, "/ui-preview/library?tab=watchlist&empty=watchlist");

  await expect(page.getByText("Nothing saved yet")).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed collection read leaves the ratings summary readable", async ({ page }) => {
  await openLibrary(page, "/ui-preview/library?fail=library");

  await expect(page.getByRole("alert", { name: "Rated collection could not be loaded" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /A readable outline/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Watchlist" })).toBeVisible();
});

test("removing history is confirmed and deleting a rating is not", async ({ page }) => {
  await openLibrary(page, "/ui-preview/library?tab=history");

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
  await openLibrary(page, "/ui-preview/library?tab=watchlist");

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
  await openLibrary(page, "/ui-preview/library?tab=history");

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
  await openLibrary(page, "/ui-preview/library?tab=history");
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
  await openLibrary(page, "/ui-preview/library?tab=history");

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

test("the Seen filter row holds its shape at every width", async ({ page }, testInfo) => {
  await openLibrary(page, "/ui-preview/library?tab=history");
  await expectFilterRowIsLaidOut(page, testInfo.project.name);

  if (testInfo.project.name === "mobile-390") {
    // The finish gate sweeps 320 for overflow; the row has to hold its shape
    // there too, and 320 is the width the wrap has the least room to happen in.
    await page.setViewportSize({ width: 320, height: 640 });
    await expectFilterRowIsLaidOut(page, "320px");
  }
});

test("Rated carries the same row without the bounds Seen adds to it", async ({ page }, testInfo) => {
  // Rated and Watchlist run the same form minus the year bounds, so they were
  // never the tab that broke — which is exactly why the check belongs on them
  // too: the fix is in the shared row, not in a Seen-only rule.
  await openLibrary(page);
  await expectFilterRowIsLaidOut(page, `${testInfo.project.name} (rated)`);
});

test("Rated ranks by the movie's own facts as well as by the star value", async ({
  page,
}) => {
  await openLibrary(page);

  const sort = page.locator("#library-sort");
  await expect(sort.locator("option")).toHaveText([
    "Most recent",
    "Title",
    "Highest rated",
    "Newest release",
    "Highest TMDB score",
  ]);
  // The two new orderings are the sort control's alone. Genre and year still
  // belong to Seen, so widening this list must not have dragged them along.
  await expect(page.locator("#library-genre")).toHaveCount(0);
  await expect(page.locator("#library-year-from")).toHaveCount(0);

  const rows = page.getByRole("list", { name: "Rated movies" }).getByRole("listitem");

  await sort.selectOption("release");
  await expect(page).toHaveURL(/sort=release/);
  await expect(rows.first()).toContainText("Decision to Leave");
  await expect(rows.first()).toContainText("2022");

  await sort.selectOption("tmdb");
  await expect(page).toHaveURL(/sort=tmdb/);
  // The ordering and the printed mark read the same field, so the leader has
  // to carry the highest score rather than merely sit at the top.
  await expect(rows.first()).toContainText("Parasite");
  await expect(rows.first()).toContainText("TMDB 8.5");
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a year range that matches nothing says so, and offers a way back", async ({
  page,
}) => {
  await openLibrary(page, "/ui-preview/library?tab=history&year_from=1900&year_to=1910");

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
  await openLibrary(page, "/ui-preview/library?tab=history&cursor=recorded%3A12%3Astale-view");

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
