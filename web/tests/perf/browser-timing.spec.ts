import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  BUDGETS,
  CPU_THROTTLE,
  ENFORCE_ACK,
  ENFORCE_LCP,
  belowFoldLazyLoading,
  boundedPage,
  clickAndMeasureAcknowledgement,
  installVitals,
  measureAcknowledgement,
  posterReservation,
  readVitals,
  record,
  settle,
  throttleCpu,
  tmdbFanOut,
  trackMatching,
  trackRequests,
  writeReport,
  type RouteMeasurement,
  type StructuralClaim,
} from "./measure";

/**
 * What the product costs a phone, measured in the browser.
 *
 * This is the other half of Bundle 7's performance gate and it is deliberately
 * kept apart from the k6 numbers. k6 measures the API: how long the service
 * takes to answer. This measures the reader's experience: when the largest
 * thing on the page finished painting, whether the layout moved under their
 * thumb while it did, and how long after a tap something visibly happened. The
 * two must never be added together or quoted for each other, which is why they
 * live in different suites and produce different reports.
 *
 * It also checks the structural promises the frontend testing strategy makes,
 * because a good number on one run is not the same as a design that cannot
 * shift: reserved poster boxes, below-fold laziness, a bounded catalog page, no
 * per-card TMDB fan-out, and technical evidence that loads on disclosure rather
 * than blocking the first movie.
 *
 * Runs against the bypass-disabled Compose stack with a real Keycloak login,
 * exactly like the journey suite, so what it measures is the deployed path.
 */

const DEFAULT_PERSONA = 900000101;
const LIBRARY_PERSONA = 900000103;
const CATALOG_PAGE_SIZE = 24;

// Not serial: the config already pins one worker, and a route that fails its
// budget must not stop the remaining routes from being measured. A report that
// stops at the first problem is the least useful moment to lose the rest of the
// evidence.
test.afterAll(() => {
  writeReport();
});

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/**
 * Sign in, warm the route, then load it again as the measured pass.
 *
 * The performance gate is defined against "a warm application process". A
 * first-ever hit on a Next route pays for module evaluation and connection
 * setup that a reader on a running deployment never sees, so measuring it would
 * report the deployment's cold start as the page's cost.
 */
async function prepare(page: Page, path: string): Promise<number> {
  await throttleCpu(page, CPU_THROTTLE);
  await installVitals(page);
  await signIn(page);
  const warm = await page.goto(path);
  const status = warm?.status() ?? 0;
  if (status !== 404) {
    await settle(page);
  }
  return status;
}

function skipMissingRoute(route: string, path: string, status: number): boolean {
  if (status !== 404) {
    return false;
  }
  record({
    route,
    path,
    skipped: `route answered HTTP 404 — not implemented on this branch`,
    structural: {},
  });
  // Not a failure: the route is scheduled work, and the report lists it so a
  // reader can see the gate has a hole rather than assuming it was measured.
  test.skip(true, `${path} is not implemented on this branch`);
  return true;
}

/** Apply the timing budgets, honouring the advisory/enforced split. */
function assertTiming(measurement: RouteMeasurement): void {
  // CLS is enforced everywhere: a reserved layout cannot shift because a runner
  // was busy, so a breach is always the markup's.
  expect(
    measurement.cls ?? 0,
    `${measurement.route} cumulative layout shift`,
  ).toBeLessThanOrEqual(BUDGETS.cls);

  const enforced: string[] = [];
  const advisory: string[] = [];
  if ((measurement.lcpMs ?? 0) > BUDGETS.lcpMs) {
    const overrun = `LCP ${measurement.lcpMs?.toFixed(0)} ms > ${BUDGETS.lcpMs} ms`;
    (ENFORCE_LCP ? enforced : advisory).push(overrun);
  }
  if ((measurement.acknowledgement?.ms ?? 0) > BUDGETS.ackMs) {
    const overrun =
      `acknowledgement ${measurement.acknowledgement?.ms.toFixed(1)} ms > ${BUDGETS.ackMs} ms`;
    (ENFORCE_ACK ? enforced : advisory).push(overrun);
  }
  if (advisory.length > 0) {
    console.warn(
      `[browser-timing] ADVISORY ${measurement.route}: ${advisory.join("; ")}. ` +
        "Reported, not enforced — see ADR 0010 for the promotion rule.",
    );
  }
  expect(enforced, `${measurement.route} timing budgets`).toEqual([]);
}

function assertStructural(measurement: RouteMeasurement): void {
  for (const [name, claim] of Object.entries(measurement.structural)) {
    expect(claim.ok, `${measurement.route}/${name}: ${claim.detail}`).toBe(true);
  }
}

/** Undo a canonical-state mutation through the BFF, the way the client would. */
async function clearWatchlist(page: Page, userId: number, movieId: number) {
  await page.evaluate(
    async ({ user, movie }) => {
      const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${user}/movies/${movie}/watchlist`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "x-csrf-token": csrf,
        },
      });
    },
    { user: userId, movie: movieId },
  );
}

test("Discover: fan-out render, deferred evidence, and feedback acknowledgement", async ({
  page,
}) => {
  const path = `/discover?userId=${DEFAULT_PERSONA}`;
  const status = await prepare(page, path);
  if (skipMissingRoute("discover", path, status)) {
    return;
  }

  const requests = trackRequests(page);
  const audits = trackMatching(page, "/audits");
  const features = trackMatching(page, "/features");
  await page.goto(path);
  await settle(page);

  const cards = await page.locator(".poster-frame").count();
  const structural: Record<string, StructuralClaim> = {
    reserved_poster_boxes: await posterReservation(page),
    below_fold_lazy_loading: await belowFoldLazyLoading(page),
    no_per_card_tmdb_fan_out: tmdbFanOut(requests, cards),
    // The claim is that the reader gets a movie before the system gets an
    // audit. Two requests that have not happened yet are the proof.
    technical_data_is_deferred: {
      ok: audits.length === 0 && features.length === 0,
      detail:
        `before disclosure: ${audits.length} audit and ${features.length} feature ` +
        "requests (both must be zero — the first movie does not wait on them)",
    },
  };

  // Now open the disclosure and prove the deferred reads actually happen.
  await page.getByRole("button", { name: "Why this?" }).click();
  await page.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(page.getByTestId("technical-evidence")).toBeVisible();
  structural.technical_data_loads_on_disclosure = {
    ok: audits.length > 0 && features.length > 0,
    detail: `after disclosure: ${audits.length} audit and ${features.length} feature requests`,
  };
  await page.keyboard.press("Escape");

  const vitals = await readVitals(page);
  // The featured card is the first-read object, so its Watchlist control is the
  // action whose acknowledgement matters.
  const featured = page.locator("section.featured-movie");
  const movieId = await featuredMovieId(featured);
  const ackMs = await clickAndMeasureAcknowledgement(
    page,
    featured.getByRole("button", { name: "Watchlist" }),
    "#discover-status",
  );

  const measurement: RouteMeasurement = {
    route: "discover",
    path,
    lcpMs: vitals.lcp,
    cls: vitals.cls,
    shifts: vitals.shifts,
    acknowledgement: { label: "watchlist the featured movie", ms: ackMs },
    structural,
    requests,
  };
  record(measurement);

  await clearWatchlist(page, DEFAULT_PERSONA, movieId);
  assertStructural(measurement);
  assertTiming(measurement);
});

async function featuredMovieId(featured: Locator): Promise<number> {
  const href = await featured.getByRole("link", { name: /Open movie/ }).getAttribute("href");
  const match = /\/movies\/(\d+)/.exec(href ?? "");
  expect(match, `expected a movie link on the featured card, got ${href}`).not.toBeNull();
  return Number(match?.[1]);
}

test("Browse: bounded grid, lazy posters, and a cursor continuation", async ({ page }) => {
  const path = `/browse?user=${DEFAULT_PERSONA}`;
  const status = await prepare(page, path);
  if (skipMissingRoute("browse", path, status)) {
    return;
  }

  const requests = trackRequests(page);
  await page.goto(path);
  await settle(page);

  const firstPageCards = await page.locator(".catalog-cell").count();
  const vitals = await readVitals(page);

  // The continuation is the interesting half: it appends 24 cards below the
  // fold, which is exactly where a grid without reserved boxes would shift.
  const loadMore = page.locator(".browse-more button");
  await expect(loadMore).toBeVisible();
  const ackMs = await clickAndMeasureAcknowledgement(page, loadMore, ".browse-more");
  await expect(page.locator(".catalog-cell")).toHaveCount(firstPageCards * 2);
  await settle(page);
  const afterContinuation = await readVitals(page);
  const continuedCards = await page.locator(".catalog-cell").count();

  const measurement: RouteMeasurement = {
    route: "browse",
    path,
    lcpMs: vitals.lcp,
    // The worse of the two readings: a continuation that shifts the grid is a
    // layout failure even if the first paint was perfectly stable.
    cls: Math.max(vitals.cls, afterContinuation.cls),
    shifts: afterContinuation.shifts,
    acknowledgement: { label: "load the next cursor page", ms: ackMs },
    structural: {
      reserved_poster_boxes: await posterReservation(page),
      below_fold_lazy_loading: await belowFoldLazyLoading(page),
      no_per_card_tmdb_fan_out: tmdbFanOut(requests, continuedCards),
      bounded_first_page: boundedPage(firstPageCards, CATALOG_PAGE_SIZE),
      bounded_continuation: boundedPage(continuedCards, CATALOG_PAGE_SIZE * 2),
    },
    requests,
  };
  record(measurement);
  assertStructural(measurement);
  assertTiming(measurement);
});

test("Movie detail: reserved artwork and a canonical-state acknowledgement", async ({ page }) => {
  // The detail route is entered the way a reader enters it — from a card — so
  // the movie under test is one the catalog actually offers.
  await throttleCpu(page, CPU_THROTTLE);
  await installVitals(page);
  await signIn(page);
  await page.goto(`/discover?userId=${DEFAULT_PERSONA}`);
  await settle(page);
  const movieId = await featuredMovieId(page.locator("section.featured-movie"));
  const path = `/movies/${movieId}?user=${DEFAULT_PERSONA}`;
  await page.goto(path);
  await settle(page);

  const requests = trackRequests(page);
  await page.goto(path);
  await settle(page);

  const vitals = await readVitals(page);
  const controls = page.locator(".canonical-state");
  const ackMs = await clickAndMeasureAcknowledgement(
    page,
    controls.getByRole("button", { name: "Watchlist" }),
    ".canonical-state",
  );

  const measurement: RouteMeasurement = {
    route: "movie-detail",
    path,
    lcpMs: vitals.lcp,
    cls: vitals.cls,
    shifts: vitals.shifts,
    acknowledgement: { label: "watchlist from the detail controls", ms: ackMs },
    structural: {
      reserved_poster_boxes: await posterReservation(page),
      no_per_card_tmdb_fan_out: tmdbFanOut(requests, 1),
    },
    requests,
  };
  record(measurement);

  // The status paragraph and its error twin share a class; only the status
  // one carries the committed message.
  await expect(page.locator('p.canonical-state-message[role="status"]')).toContainText(
    /watchlist/i,
  );
  await clearWatchlist(page, DEFAULT_PERSONA, movieId);
  assertStructural(measurement);
  assertTiming(measurement);
});

test("Library: tabbed collections and a rating-edit acknowledgement", async ({ page }) => {
  const path = `/library?userId=${LIBRARY_PERSONA}&tab=rated`;
  const status = await prepare(page, path);
  if (skipMissingRoute("library", path, status)) {
    return;
  }

  const requests = trackRequests(page);
  await page.goto(path);
  await settle(page);
  const vitals = await readVitals(page);

  // Editing a star is the route's primary action and its acknowledgement is
  // optimistic, so this is the clearest read on "did something visibly happen".
  const select = page.locator("select[id^='library-rating-']").first();
  await expect(select).toBeVisible();
  const original = await select.inputValue();
  // Read the replacement off the control rather than assuming the encoding:
  // the options are half-star values written without a trailing zero ("5", not
  // "5.0"), and a hard-coded string would silently stop matching.
  const replacement = await select.evaluate((element) => {
    const control = element as HTMLSelectElement;
    const values = Array.from(control.options)
      .map((option) => option.value)
      .filter((value) => value !== "" && value !== control.value);
    return values[values.length - 1];
  });
  const ackMs = await measureAcknowledgement(page, ".library-panel", async () => {
    await select.selectOption(replacement);
  });

  const measurement: RouteMeasurement = {
    route: "library",
    path,
    lcpMs: vitals.lcp,
    cls: vitals.cls,
    shifts: vitals.shifts,
    acknowledgement: { label: "edit a rating", ms: ackMs },
    structural: {
      reserved_poster_boxes: await posterReservation(page),
      no_per_card_tmdb_fan_out: tmdbFanOut(requests, await page.locator(".library-row").count()),
    },
    requests,
  };
  record(measurement);

  // Put the persona back: this fixture is shared with the journey suite.
  await expect(select).toHaveValue(replacement);
  await select.selectOption(original);
  await expect(select).toHaveValue(original);
  assertStructural(measurement);
  assertTiming(measurement);
});

test("Quick Picks: measured when the route exists", async ({ page }) => {
  const path = "/quick-picks";
  const status = await prepare(page, path);
  if (skipMissingRoute("quick-picks", path, status)) {
    return;
  }

  const requests = trackRequests(page);
  await page.goto(path);
  await settle(page);
  const vitals = await readVitals(page);

  const measurement: RouteMeasurement = {
    route: "quick-picks",
    path,
    lcpMs: vitals.lcp,
    cls: vitals.cls,
    shifts: vitals.shifts,
    structural: {
      reserved_poster_boxes: await posterReservation(page),
      no_per_card_tmdb_fan_out: tmdbFanOut(requests, 1),
    },
    requests,
  };
  record(measurement);
  assertStructural(measurement);
  assertTiming(measurement);
});
