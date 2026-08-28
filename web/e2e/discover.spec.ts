import { expect, test, type Page } from "@playwright/test";

import { learnedRecommendations } from "@/lib/fixtures/discover-fixtures";

import { auditPage, describeViolations } from "./finish-gate-support";

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

test("rating a just-watched movie clears the panel and hands the page back", async ({
  page,
}) => {
  await stubMovieStateWrites(page);
  await page.goto("/discover?demo=learned");

  const featured = page.locator("section.featured-movie");
  await expect(featured).toBeVisible();
  await featured.getByRole("button", { name: "Mark watched" }).click();

  // The panel opens under the ranked card, and reading it means being scrolled
  // to it. Putting it at the top of the viewport is the position this whole
  // interaction has to recover from — at 390 it is where the viewer genuinely
  // ends up, and forcing it at the wider widths keeps the recovery measurable
  // rather than trivially satisfied by a page that happened to fit.
  const panel = page.getByRole("region", { name: /^Rate / });
  await expect(panel).toBeVisible();
  // `instant` only because the document declares `scroll-behavior: smooth`, so
  // a plain call here would still be animating when the next line reads the
  // offset. The scroll under test does its own thing a few lines down.
  await panel.evaluate((element) =>
    element.scrollIntoView({ block: "start", behavior: "instant" }),
  );
  const scrolledDown = await page.evaluate(() => window.scrollY);
  expect(scrolledDown).toBeGreaterThan(0);

  await panel.getByRole("button", { name: /^4 stars for / }).click();

  // 1. The panel leaves rather than standing there with its stars filled in.
  await expect(panel).toHaveCount(0);
  await expect(page.getByText("4 out of 5 recorded")).toHaveCount(0);

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

  const { blocking } = await auditPage(page);
  expect(describeViolations(blocking), describeViolations(blocking)).toBe("");
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
