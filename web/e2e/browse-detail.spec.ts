import { expect, test, type Page } from "@playwright/test";

import { CATALOG_PAGE_LIMIT } from "@/lib/browse/query";

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

/**
 * A shift below this is the sub-pixel settling of a reserved box; anything
 * above it is content arriving into space nothing was holding for it. Well
 * under the 0.1 the browser-timing gate enforces for the whole route, because
 * this test isolates one transition rather than measuring a page load.
 */
const SHIFT_TOLERANCE = 0.05;

async function cardTitles(page: Page): Promise<string[]> {
  return page.locator(".catalog-cell .poster-title").allInnerTexts();
}

interface RecordedShift {
  value: number;
  source: string;
}

/**
 * Collect layout shifts the reader did not ask for.
 *
 * Same rule the browser-timing gate applies: a shift within the browser's
 * window for attributing one to a click is the click's own consequence and is
 * not instability. Everything else counts, and is what this file asserts on.
 */
async function collectUnpromptedShifts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const shifts: { value: number; source: string }[] = [];
    (window as unknown as { __shifts: typeof shifts }).__shifts = shifts;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as (PerformanceEntry & {
        value: number;
        hadRecentInput: boolean;
        sources?: { node?: Node | null }[];
      })[]) {
        if (entry.hadRecentInput) continue;
        const node = entry.sources?.[0]?.node as Element | null | undefined;
        const name = node ? node.nodeName.toLowerCase() : "";
        const className = node?.className ? String(node.className).split(" ")[0] : "";
        shifts.push({
          value: entry.value,
          source: node ? `${name}${className ? `.${className}` : ""}` : "unknown",
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

async function unpromptedShifts(page: Page): Promise<RecordedShift[]> {
  return page.evaluate(() => (window as unknown as { __shifts: RecordedShift[] }).__shifts);
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

test("Browse opens on the most-watched cut, and the sort is not a chip", async ({ page }) => {
  const catalogRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/ui-preview/catalog")) catalogRequests.push(request.url());
  });

  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  expect(catalogRequests[0]).toContain("sort=popular");
  // The default cut is still the one canonical address, with nothing in it.
  expect(new URL(page.url()).search).toBe("");
  await expect(page.getByRole("combobox", { name: "Sort" })).toHaveValue("popular");
  // "Most watched here ×" read as a filter the viewer had applied. Only
  // search, genre, and decade belong in that row, and none of them is set.
  await expect(page.getByLabel("Active filters")).toHaveCount(0);
  // The endpoint's interaction ordering, not the alphabet: the recorded
  // catalog's most-watched title leads.
  expect((await cardTitles(page))[0]).toBe("Lady Bird");
});

test("the alphabetical cut is one selection away and pages the same way", async ({ page }) => {
  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  await page.getByRole("combobox", { name: "Sort" }).selectOption("title");
  // `title` is no longer the default, so the address has to spell it out or a
  // reload would quietly reorder the grid underneath the viewer.
  await expect(page).toHaveURL(/sort=title/);
  // Sampled only once the alphabetical window has actually landed: the old
  // grid stays on screen while the new page is in flight.
  await expect(page.locator(".catalog-cell .poster-title").first()).toHaveText("Aftersun");
  await expect(page.getByLabel("Active filters")).toHaveCount(0);

  const firstPage = await cardTitles(page);
  await page.getByRole("button", { name: "Load more movies" }).click();
  await expect(page.getByRole("listitem")).toHaveCount(firstPage.length * 2);

  // A cursor is bound to the query fingerprint it was issued under, and the
  // request has always named the sort, so continuation is unaffected.
  const combined = await cardTitles(page);
  expect(new Set(combined).size).toBe(combined.length);
  expect(combined.slice(0, firstPage.length)).toEqual(firstPage);
  await expect(page).toHaveURL(/sort=title/);
  await expect(page).toHaveURL(/cursor=/);
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

test("a page in flight is waited for in the space it is going to fill", async ({ page }) => {
  await collectUnpromptedShifts(page);
  // Slow enough that the arriving page can no longer be attributed to the click
  // that asked for it. A loaded CI runner produces that ordering by accident,
  // which is why the defect this covers was intermittent rather than visible.
  await page.route("**/api/ui-preview/catalog*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.continue();
  });

  await page.goto("/ui-preview/browse");
  // Reserved at the page size the request asks for, not at a smaller number
  // chosen to look tidy: half a page of placeholders reserves half a page.
  await expect(page.locator(".catalog-skeleton-cell")).toHaveCount(CATALOG_PAGE_LIMIT);
  await expect(page.getByRole("list", GRID)).toBeVisible();
  const firstPage = await page.getByRole("listitem").count();
  expect(firstPage).toBe(CATALOG_PAGE_LIMIT);

  // The foot of the document is where pressing "load more" leaves the viewport:
  // the control sits near the end, so the browser cannot centre it and clamps
  // at the last screenful — the one screenful that a growing page pushes away.
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.getByRole("button", { name: "Load more movies" }).click();
  await expect(page.locator(".catalog-skeleton-cell")).toHaveCount(CATALOG_PAGE_LIMIT);
  await expect(page.getByRole("listitem")).toHaveCount(firstPage * 2);

  const offenders = (await unpromptedShifts(page)).filter(
    (shift) => shift.value > SHIFT_TOLERANCE,
  );
  expect(offenders).toEqual([]);
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

test("every card offers watched beside watchlist, at a thumb-sized target", async ({
  page,
}) => {
  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", GRID)).toBeVisible();

  // The grid used to offer `Watchlist` and nothing else, so the only state a
  // viewer could record from Browse was the one that changes no recommendation.
  const cell = page.locator(".catalog-cell").first();
  const actions = cell.getByRole("group");
  await expect(actions).toHaveAccessibleName(/^Actions for .+/);
  await expect(actions.getByRole("button", { name: /^(Watchlist|In watchlist)$/ })).toBeVisible();
  const watched = actions.getByRole("button", { name: /^(Mark watched|Watched)$/ });
  await expect(watched).toBeVisible();
  expect((await watched.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(36);

  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed catalog read is its own state with a way to retry", async ({ page }) => {
  await page.goto("/ui-preview/browse?fail=catalog");

  const alert = page.getByRole("alert", { name: "Catalog could not be loaded" });
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

  const alert = page.getByRole("alert", { name: "Movie detail not found" });
  await expect(alert).toContainText("Movie detail not found");
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
});
