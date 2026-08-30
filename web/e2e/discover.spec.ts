import { expect, test, type JSHandle, type Page } from "@playwright/test";

import { learnedRecommendations } from "@/lib/fixtures/discover-fixtures";

import { auditPage, describeViolations, scrollingHasSettled } from "./finish-gate-support";

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
  await expect(page.getByRole("alert", { name: /Watch history could not be loaded/ })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed recommendation region leaves watch history readable", async ({ page }) => {
  await page.goto("/discover?demo=recommendations-error");

  await expect(page.getByRole("alert", { name: /Recommendations could not be loaded/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /has watched/ })).toBeVisible();

  // A history row is a link with artwork, not a dead line of text: the read
  // model carries `poster_url` and `release_year`, so the title prints once
  // without its year and the row opens the movie.
  const heat = page.getByRole("link", { name: /^Heat/ });
  await expect(heat).toBeVisible();
  await expect(heat).toHaveAttribute("href", /^\/movies\/6\?/);
  await expect(heat.locator("img")).toBeVisible();
  await expect(page.getByText("Heat (1995)")).toHaveCount(0);
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

  // Two nodes can carry this during streaming: the route-level `loading.tsx`
  // and the region skeleton underneath it. Either one is the announcement this
  // test is about, so match the first rather than assert there is only one.
  await expect(page.getByText("Loading movies").first()).toBeAttached();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("an expired session offers a reauthentication path", async ({ page }) => {
  await page.goto("/discover?demo=auth-expired");

  // Both regions fail the same way, so the name is not unique on the page.
  await expect(
    page.getByRole("alert", { name: /Your session expired/ }).first(),
  ).toBeVisible();
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

  // The isolated harness carries no session, so a live read fails at the auth
  // boundary — which is the point: it fails visibly rather than quietly
  // answering with recorded data.
  await expect(
    page.getByRole("alert", { name: /Your session expired/ }).first(),
  ).toBeVisible();
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

/**
 * Where a control's label actually starts drawing.
 *
 * A stretched grid item's *box* always begins at its column edge, so a bounding
 * box cannot see this defect at all: what was indented was the centred text
 * inside a button with no visible border. The text node's own rectangle is what
 * a reader sees.
 */
async function labelLeft(locator: import("@playwright/test").Locator) {
  return locator.evaluate((element) => {
    const label = Array.from(element.childNodes).find(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
    );
    if (!label) return null;
    const range = document.createRange();
    range.selectNodeContents(label);
    return range.getBoundingClientRect().left;
  });
}

test("the three decisions line up on the mobile action strip", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile layout assertion");
  await page.goto("/discover?demo=learned");

  const actions = page.locator(".featured-actions");
  await expect(actions).toBeVisible();

  const watchlist = actions.getByRole("button", { name: "Watchlist" });
  const watched = actions.getByRole("button", { name: "Mark watched" });
  const dismiss = actions.getByRole("button", { name: "Not for me" });

  const [watchlistBox, watchedBox, dismissBox] = await Promise.all([
    watchlist.boundingBox(),
    watched.boundingBox(),
    dismiss.boundingBox(),
  ]);

  // The two bordered buttons share a row; the quiet one starts the next.
  expect(watchedBox?.y ?? 0).toBeCloseTo(watchlistBox?.y ?? 0, 0);
  expect(dismissBox?.y ?? 0).toBeGreaterThan(watchlistBox?.y ?? 0);

  // `Not for me` is the one quiet control in the strip, so it has no visible
  // box to align to — only its label. Stretched across its grid column like the
  // two bordered buttons above it, that centred label started ~52px right of
  // their left edge (~43px right of their own labels) and read as an indent
  // rather than as the third action. It now starts where their borders do,
  // within a pixel or two of border width and sub-pixel rounding.
  const dismissLabelLeft = await labelLeft(dismiss);
  expect(dismissLabelLeft).not.toBeNull();
  expect(Math.abs((dismissLabelLeft ?? 0) - (watchlistBox?.x ?? 0))).toBeLessThanOrEqual(2);
});

test("Quick Picks is reachable from Discover without a fourth navigation slot", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  const entry = page.getByRole("link", { name: /Quick picks/ });
  await expect(entry).toBeVisible();
  await expect(entry).toHaveAttribute("href", "/quick-picks?user=900000101");
  // The route contract keeps the shell at three primary routes.
  await expect(
    page.getByRole("navigation", { name: /Primary/ }).first().getByRole("link", {
      name: /Quick/,
    }),
  ).toHaveCount(0);
});

test("the Quick Picks entry stays a thumb-sized target", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile touch-target assertion");
  await page.goto("/discover?demo=learned");

  const box = await page.getByRole("link", { name: /Quick picks/ }).boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
});

test("the rail starts after the featured movie and never repeats it", async ({ page }) => {
  await page.goto("/discover?demo=learned");

  const featured = page.getByRole("heading", { level: 1 });
  const title = (await featured.textContent()) ?? "";
  expect(title.length).toBeGreaterThan(0);

  // The featured slot is a queue position and the rail is what is still ahead
  // of it, so no title is ever both the decision and part of what follows it.
  const rail = page.getByRole("region", { name: /ranked set/ });
  await expect(rail.getByText(title, { exact: true })).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("the evidence drawer answers in a sentence before it answers in a table", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  const opening = drawer.getByText(/^Picked from/);
  await expect(opening).toBeVisible();
  // Plain language first; the identifiers keep their place under a heading.
  await expect(opening).not.toContainText("lightgbm");
  await expect(drawer.getByRole("heading", { name: "Model evidence" })).toBeVisible();
  await expect(drawer.getByText("item-item-lightgbm")).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a fallback drawer says what it is without borrowing learned phrasing", async ({
  page,
}) => {
  await page.goto("/discover?demo=fallback");
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText(/watched most across this tenant/)).toBeVisible();
  await expect(drawer.getByText(/^Picked from/)).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

/**
 * How many lines an element's own label actually occupies.
 *
 * A bounding box cannot answer this: a clamped title and a wrapped pill label
 * both leave the element's box exactly where the layout put it. Only the text
 * is measured, and only its distinct top edges count — a pill holds an icon
 * beside its label, and two rectangles on one line are still one line.
 */
async function textLines(locator: import("@playwright/test").Locator) {
  return locator.evaluate((element) => {
    const text = [...element.childNodes].filter(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
    );
    if (text.length === 0) return 0;
    const range = document.createRange();
    range.setStartBefore(text[0]);
    range.setEndAfter(text[text.length - 1]);
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
  });
}

test("every rail card's controls sit on one baseline, whatever the title does", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const cards = page.locator(".rail-item");
  const count = await cards.count();
  expect(count).toBeGreaterThan(2);

  const rows: { title: string; lines: number; y: number }[] = [];
  for (let index = 0; index < count; index += 1) {
    const card = cards.nth(index);
    const title = card.locator(".poster-title");
    const box = await card.locator(".movie-state-row").boundingBox();
    rows.push({
      title: (await title.textContent()) ?? "",
      lines: await textLines(title),
      y: box?.y ?? -1,
    });
  }

  // The test only means something if the rail is actually mixed: a set of
  // uniformly short titles would pass a ragged layout too.
  expect(rows.some((row) => row.lines === 1)).toBe(true);
  expect(rows.some((row) => row.lines === 2)).toBe(true);

  // Reserving two title lines and pinning the control row to the bottom of the
  // card puts every card's controls on the same line, so `Toy Story` and
  // `To Kill a Mockingbird` no longer offer their decisions at two heights.
  const tops = rows.map((row) => Math.round(row.y));
  expect(new Set(tops).size, JSON.stringify(rows)).toBe(1);
});

test("no rail control wraps its label onto a second line", async ({ page }) => {
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const controls = page.locator(".rail-item .movie-state-row button");
  const count = await controls.count();
  expect(count).toBeGreaterThan(0);

  const wrapped: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const control = controls.nth(index);
    // `Mark watched` wrapped inside its own pill at rail width, which is what
    // pushed one card's third action a line below its neighbour's.
    if ((await textLines(control)) > 1) {
      wrapped.push((await control.getAttribute("aria-label")) ?? (await control.innerText()));
    }
    // A label kept on one line by clipping is not kept on one line.
    const clipped = await control.evaluate((node) => node.scrollWidth - node.clientWidth);
    expect(clipped, `${await control.innerText()} is clipped by ${clipped}px`).toBeLessThanOrEqual(0);
  }
  expect(wrapped.join(", ")).toBe("");
});

test("a rail card spends most of its height on the poster", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1440", "Desktop density assertion");
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const card = page.locator(".rail-item").first();
  const poster = await card.locator(".poster-frame").boundingBox();
  const row = await card.locator(".movie-state-row").boundingBox();
  const caption = (row?.y ?? 0) + (row?.height ?? 0) - ((poster?.y ?? 0) + (poster?.height ?? 0));

  // The caption and its controls used to run to 69% of the poster's height —
  // three full-width ovals, one of them two lines tall. The floor here is the
  // regression guard, not the target: the target is the poster staying the
  // thing a viewer is looking at.
  expect(caption / (poster?.height ?? 1)).toBeLessThan(0.55);
});

const WATCHED_AT = "2026-08-21T09:00:00Z";

function committedState(overrides: Record<string, unknown> = {}) {
  return {
    outcome: "changed",
    replayed: false,
    request_id: "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
    state: {
      movie_id: 101,
      user_id: 900000101,
      tenant_id: "demo",
      revision: 3,
      updated_at: WATCHED_AT,
      rating: null,
      rating_updated_at: null,
      watched_at: null,
      watchlisted_at: null,
      dismissed_at: null,
      ...overrides,
    },
  };
}

/**
 * The writes, which are the one part of `/discover` this harness cannot reach
 * on its own: the BFF write path needs a session and the isolated mode has none
 * by design. Stubbing the two mutations and the re-read at the network boundary
 * leaves everything above them — the panel's lifecycle, the confirmation, the
 * scroll, the focus move — running exactly as it ships. Whether the write
 * actually commits is `tests/e2e/discover-journey.spec.ts`'s job, against a
 * real API.
 */
async function stubMovieStateWrites(page: Page) {
  await page.route(
    (url) => url.pathname === "/api/auth/csrf",
    (route) => route.fulfill({ json: { csrfToken: "fixture-csrf-token" } }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/watched"),
    (route) => route.fulfill({ json: committedState({ watched_at: WATCHED_AT }) }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/rating"),
    (route) =>
      route.fulfill({
        json: committedState({ watched_at: WATCHED_AT, rating: 4, revision: 4 }),
      }),
  );
  // A watched title is excluded server-side, so the honest re-read is the
  // ranked set without it. It is also what stops the queue running dry.
  await page.route(
    (url) => url.pathname.endsWith("/recommendations"),
    (route) =>
      route.fulfill({
        json: {
          ...learnedRecommendations,
          items: learnedRecommendations.items.slice(1),
        },
      }),
  );
}

/**
 * The `<title>` element the next decision is going to replace.
 *
 * Every committed decision on this route ends with `router.refresh()`, and Next
 * renders the route's metadata inside the tree that refresh replaces: when the
 * new payload commits, React unmounts the old `<title>` and mounts a new one.
 * Holding the old node is how the wait below can tell "the refresh has landed"
 * from "the refresh has not started yet", which are otherwise the same document.
 */
async function documentTitleNode(page: Page): Promise<JSHandle<Element | null>> {
  return page.evaluateHandle(() => document.head.querySelector("title"));
}

/**
 * The refresh a decision started has committed, and the head is whole again.
 *
 * The two DOM operations that swap the title normally land in the same commit,
 * but on a contended runner they split — observed here 302ms apart — and for
 * that stretch the document has no title at all. A full-page axe audit sampled
 * in the gap reports `document-title`, which is a true statement about the
 * instant and a false one about the page; it is what failed this file's last
 * assertion at 390 roughly once in forty. So the audit waits for the hand-off's
 * own last act rather than for a duration.
 */
async function refreshHasLanded(page: Page, replacing: JSHandle<Element | null>) {
  await page.waitForFunction((previous) => {
    const title = document.head.querySelector("title");
    return title !== null && title !== previous;
  }, replacing);
}

test("rating a just-watched movie clears the panel and hands the page back", async ({
  page,
}) => {
  await stubMovieStateWrites(page);
  await page.goto("/discover?demo=learned");

  const featured = page.locator("section.featured-movie");
  await expect(featured).toBeVisible();
  const beforeWatched = await documentTitleNode(page);
  await featured.getByRole("button", { name: "Mark watched" }).click();

  // The panel opens under the ranked card, and reading it means being scrolled
  // to it. Putting it at the top of the viewport is the position this whole
  // interaction has to recover from — at 390 it is where the viewer genuinely
  // ends up, and forcing it at the wider widths keeps the recovery measurable
  // rather than trivially satisfied by a page that happened to fit.
  const panel = page.getByRole("region", { name: /^Rate / });
  await expect(panel).toBeVisible();
  // The watch is a decision like any other, so it too ends in a refresh. Letting
  // that one finish here is what leaves the rating with a document of its own to
  // rebuild, and the wait after the rating with only one refresh to wait on.
  await refreshHasLanded(page, beforeWatched);
  // `instant` only because the document declares `scroll-behavior: smooth`, so
  // a plain call here would still be animating when the next line reads the
  // offset. The scroll under test does its own thing a few lines down.
  await panel.evaluate((element) =>
    element.scrollIntoView({ block: "start", behavior: "instant" }),
  );
  const scrolledDown = await page.evaluate(() => window.scrollY);
  expect(scrolledDown).toBeGreaterThan(0);

  const beforeRating = await documentTitleNode(page);
  await panel.getByRole("button", { name: /^4 stars for / }).click();

  // 1. The panel leaves rather than standing there with its stars filled in —
  //    and it takes the shared control's own ending with it, so no `You rated
  //    4/5` chip is left behind at the foot of the page either.
  await expect(panel).toHaveCount(0);
  await expect(page.locator(".rating-star-row")).toHaveCount(0);
  await expect(page.getByText(/^You rated /)).toHaveCount(0);

  // 2. One sentence in its place, in the product's own terms, with the way to
  //    change it still named.
  const status = page.locator("#discover-status");
  await expect(status).toHaveText(
    /^Rated .+ 4\/5\. Ratings do not reorder the list — the watch already counts\.$/,
  );
  await expect(page.getByRole("link", { name: "Manage in Library" })).toBeVisible();

  // 3. The viewer is back at the movie rather than at the foot of the page:
  //    the title and the decisions are both on screen, and the page moved up to
  //    put them there. Asserted as visibility rather than as an offset — how
  //    far the document can scroll depends on how much sits below the movie,
  //    and "the movie is on screen" is the promise either way.
  await expect(featured.getByRole("heading", { level: 1 })).toBeInViewport({ ratio: 1 });
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(scrolledDown);

  // 4. And focus went with it, onto the first control the surface declares, so
  //    the next decision is one key away — and it is on screen, because a focus
  //    ring the viewer cannot see is not a place to be handed back to.
  const watchlist = featured.getByRole("button", { name: "Watchlist" });
  await expect(watchlist).toBeFocused();
  await expect(watchlist).toBeInViewport({ ratio: 1 });

  // 5. And then the whole hand-off is over: the silent refresh has rebuilt the
  //    document, the head names it again, and the page has stopped travelling.
  //    Only now is there a settled document to audit — the four assertions above
  //    all pass while the refresh is still in the air.
  await refreshHasLanded(page, beforeRating);
  await expect(page).toHaveTitle(/\S/);
  await scrollingHasSettled(page);

  const { blocking } = await auditPage(page);
  expect(describeViolations(blocking), describeViolations(blocking)).toBe("");
});

test("the rating prompt is the same star control a movie's own page offers", async ({
  page,
}) => {
  await stubMovieStateWrites(page);
  await page.goto("/discover?demo=learned");

  const beforeWatched = await documentTitleNode(page);
  await page
    .locator("section.featured-movie")
    .getByRole("button", { name: "Mark watched" })
    .click();
  const panel = page.getByRole("region", { name: /^Rate / });
  await expect(panel).toBeVisible();
  // Same reason the rating test waits: this one audits with the panel still
  // open, and the watch's refresh would otherwise be rebuilding the head under
  // it. Today the assertions below happen to outlast the refresh; that is luck,
  // not an ordering.
  await refreshHasLanded(page, beforeWatched);

  // The three things the compact editor could not do, at every width the
  // matrix runs: a preview that fills from the left, one tab stop for the row,
  // and targets that stay at least 44px even where the glyph shrinks.
  await panel.getByRole("button", { name: /^4 stars for / }).hover();
  await expect(panel.getByRole("button", { name: /^1 star for / })).toHaveClass(/is-filled/);
  await expect(panel.getByRole("button", { name: /^5 stars for / })).not.toHaveClass(
    /is-filled/,
  );
  await expect(panel.getByRole("button", { name: /^1 star for / })).toHaveAttribute(
    "tabindex",
    "0",
  );
  await expect(panel.getByRole("button", { name: /^2 stars for / })).toHaveAttribute(
    "tabindex",
    "-1",
  );

  const target = await panel.getByRole("button", { name: /^3 stars for / }).boundingBox();
  expect(target?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect(target?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);

  // Audited with the panel open, which is the one state the existing rating
  // test cannot cover: by the time it audits, the panel has ended itself.
  const { blocking } = await auditPage(page);
  expect(describeViolations(blocking), describeViolations(blocking)).toBe("");
});

/**
 * The undo offer is rendered the moment the decision's write commits, but the
 * re-read that follows the decision runs on behind it — and the route treats a
 * write and its tail as one busy period. A press landing in that window used to
 * produce nothing at all: no status change, no error, and the watchlist entry
 * still standing. It is usually tens of milliseconds wide, which is why it took
 * a cold compile to surface it, so this holds the re-read open and presses into
 * it deliberately.
 */
test("an Undo pressed while the decision is still re-reading is never lost", async ({
  page,
}) => {
  let releaseRefresh: () => void = () => {};
  const refreshHeld = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  const writes: string[] = [];

  await page.route(
    (url) => url.pathname === "/api/auth/csrf",
    (route) => route.fulfill({ json: { csrfToken: "fixture-csrf-token" } }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/watchlist"),
    (route) => {
      const method = route.request().method();
      writes.push(method);
      return route.fulfill({
        json: committedState({ watchlisted_at: method === "PUT" ? WATCHED_AT : null }),
      });
    },
  );
  await page.route(
    (url) => url.pathname.endsWith("/recommendations"),
    async (route) => {
      await refreshHeld;
      await route.fulfill({ json: learnedRecommendations });
    },
  );

  await page.goto("/discover?demo=learned");
  const status = page.locator("#discover-status");
  await page
    .locator("section.featured-movie")
    .getByRole("button", { name: "Watchlist" })
    .click();
  await expect(status).toContainText("Refreshing recommendations");

  const undo = page.getByRole("button", { name: /^Undo saving/ });
  await undo.click();

  // The press is answered while the re-read is still open, and the button stops
  // inviting a second reversal of one decision without ceasing to be focusable.
  await expect(status).toHaveText(`Undoing ${HANDMAIDEN}…`);
  await expect(undo).toHaveAttribute("aria-disabled", "true");
  expect(writes).toEqual(["PUT"]);

  releaseRefresh();

  // And it runs the moment the re-read lets it: once, and with the cursor back
  // on the title the viewer asked to return to.
  await expect(status).toContainText(`${HANDMAIDEN} is back, and the change was undone`);
  expect(writes).toEqual(["PUT", "DELETE"]);
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
});

test("the rating confirmation clears itself instead of going stale", async ({ page }) => {
  await stubMovieStateWrites(page);
  await page.goto("/discover?demo=learned");

  const featured = page.locator("section.featured-movie");
  await featured.getByRole("button", { name: "Mark watched" }).click();
  const panel = page.getByRole("region", { name: /^Rate / });
  await panel.getByRole("button", { name: /^4 stars for / }).click();

  const status = page.locator("#discover-status");
  await expect(status).toHaveText(/^Rated /);
  // It stands long enough to read and then the region goes back to its resting
  // line — a sentence about one movie must never be on screen over another.
  await expect(status).toHaveText(/^Recorded feedback updates/, { timeout: 8_000 });
  await expect(page.getByRole("link", { name: "Manage in Library" })).toHaveCount(0);
});

test("a watchlisted featured title says so and offers a way past it", async ({
  page,
}) => {
  await page.goto("/discover?demo=watchlisted");

  const featured = page.getByRole("region", { name: HANDMAIDEN });
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(featured.getByText("On your watchlist")).toBeVisible();
  await expect(featured.getByRole("button", { name: "Skip" })).toBeVisible();
  // The setting the skip leads to is permanently reachable, not only through
  // the one-time question.
  await expect(
    page.getByRole("button", { name: "Feature watchlisted titles" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("skipping moves on, keeps the title in the rail, and records nothing", async ({
  page,
}) => {
  await page.goto("/discover?demo=watchlisted");

  // Nothing may leave the browser: a skip is not a decision (ADR 0012).
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") requests.push(request.url());
  });

  await page.getByRole("button", { name: "Skip" }).first().click();

  await expect(
    page.getByText("Skipped The Handmaiden — still on your watchlist."),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: "In the Mood for Love" }),
  ).toBeVisible();
  const rail = page.getByRole("region", { name: "Next in this ranked set" });
  await expect(rail.getByRole("link", { name: new RegExp(HANDMAIDEN) })).toBeVisible();
  expect(requests).toEqual([]);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("the third skip offers the setting once, with a keyboard path to both answers", async ({
  page,
}) => {
  await page.goto("/discover?demo=watchlisted");

  const question = "Stop featuring titles on your watchlist?";
  for (const step of [1, 2]) {
    await page.getByRole("button", { name: "Skip" }).first().click();
    await expect(page.getByText(question), `after skip ${step}`).toHaveCount(0);
  }
  // The third skip is driven from the keyboard, because the offer it raises has
  // to be reachable by whoever earned it.
  await page.getByRole("button", { name: "Skip" }).first().focus();
  await page.keyboard.press("Enter");

  const nudge = page.getByRole("group", { name: question });
  await expect(nudge).toBeVisible();
  await expect(nudge.getByRole("button", { name: "Stop featuring them" })).toBeVisible();
  await expect(nudge.getByRole("button", { name: "Keep featuring them" })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("with featuring off, a saved title keeps its rail card and loses the slot", async ({
  page,
}) => {
  await page.goto("/discover?demo=watchlist-held-back");

  // 101–104 are on the watchlist; 105 is the first title with no state at all.
  await expect(page.getByRole("heading", { level: 1, name: "Perfect Blue" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Skip" })).toHaveCount(0);
  const rail = page.getByRole("region", { name: "Next in this ranked set" });
  await expect(rail.getByRole("link", { name: new RegExp(HANDMAIDEN) })).toBeVisible();
  await expect(rail.getByRole("button", { name: "In watchlist" }).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Feature watchlisted titles" }),
  ).toHaveAttribute("aria-pressed", "false");
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

/**
 * What the movie-detail read answers while a refusal is being corrected: the
 * same title, already watched, at a revision above the one the write asserted.
 * It is the shape `isMovieDetailResponse` requires, because the resource
 * boundary drops anything else and the correction would silently not happen.
 */
const WATCHED_DETAIL = {
  tenant_id: "demo",
  user_id: 900000101,
  item: {
    movie_id: 101,
    title: "The Handmaiden (2016)",
    genres: ["Thriller", "Drama"],
    interaction_count: 812,
    metadata_source: "reviewed-fixture",
    source_status: "complete",
    release_year: 2016,
    overview: null,
    poster_url: null,
    tmdb_id: null,
    details: null,
    state: {
      movie_id: 101,
      user_id: 900000101,
      tenant_id: "demo",
      revision: 9,
      updated_at: WATCHED_AT,
      rating: null,
      rating_updated_at: null,
      watched_at: WATCHED_AT,
      watchlisted_at: null,
      dismissed_at: null,
    },
  },
};

test("a refused transition corrects the card and says so, without asking for a reload", async ({
  page,
}) => {
  // The refusal as the API answers it: `422` with `code: transition_refused`,
  // never the `409` that means somebody committed first. The client used to
  // tell the two apart by matching the sentence in `detail` (issue #74).
  const writes: string[] = [];
  await page.route(
    (url) => url.pathname === "/api/auth/csrf",
    (route) => route.fulfill({ json: { csrfToken: "fixture-csrf-token" } }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/watchlist"),
    (route) => {
      writes.push(route.request().method());
      return route.fulfill({
        status: 422,
        json: {
          detail: "a watched movie cannot be added to the watchlist",
          code: "transition_refused",
        },
      });
    },
  );
  await page.route(
    (url) => /\/movies\/\d+$/.test(url.pathname),
    (route) => route.fulfill({ json: WATCHED_DETAIL }),
  );

  await page.goto("/discover?demo=learned");
  const featured = page.getByRole("region", { name: HANDMAIDEN });
  await featured.getByRole("button", { name: "Watchlist" }).click();

  const status = page.locator("#discover-status");
  await expect(status).toContainText("a watched movie cannot be added to the watchlist");
  // The correction is announced rather than left to be noticed: the card is
  // about to look different, and a reader who cannot see it needs telling.
  await expect(status).toContainText("Its current state is shown.");
  // None of the three things a refusal must never say.
  await expect(status).not.toContainText("changed somewhere else");
  await expect(status).not.toContainText(/reload/i);
  await expect(status).not.toContainText(/try again/i);

  // The record said watched, so the card says watched: the correction is
  // visible and not only announced. The optimistic frame is gone too — the
  // watchlist control claims nothing it did not get.
  await expect(featured.getByRole("button", { name: "Watched" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(featured.getByRole("button", { name: "Watchlist" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  // One attempt: a rule asked twice answers the same way.
  expect(writes).toEqual(["PUT"]);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("`Why this?` says what never comes back and what still can", async ({ page }) => {
  await page.goto("/discover?demo=watchlist-held-back");

  await page.getByRole("button", { name: "Why this?" }).click();
  const drawer = page.getByRole("dialog");
  await expect(
    drawer.getByText(/never come back to Discover/),
  ).toBeVisible();
  await expect(
    drawer.getByText(/you have turned off featuring them/),
  ).toBeVisible();
});
