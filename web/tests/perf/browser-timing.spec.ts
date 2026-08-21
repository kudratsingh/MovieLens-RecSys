import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "../e2e/keycloak";
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
 * after the journeys and under the same single worker, so what it measures is
 * the deployed path and a timing measurement can never be blamed for a journey
 * failure.
 *
 * **Persona ownership.** This suite shares one seeded database with the
 * journeys, so it follows the ownership table in `tests/e2e/browser-auth.spec.ts`
 * exactly: every route is measured against the persona whose journey already
 * owns that kind of write, and every write is undone.
 *
 * | Persona                   | Owner                   | What this suite does        |
 * |---------------------------|-------------------------|-----------------------------|
 * | 900000101 Action Fan      | Library                 | edits a rating, then undoes |
 * | 900000102 Drama Fan       | Discover                | watchlists, then removes    |
 * | 900000103 Eclectic Viewer | Browse (watchlist only) | watchlists, then removes    |
 * | 900000104 Cold Start      | PKCE, then Quick Picks  | **reads only**              |
 *
 * Cold Start is measured and never written. The ownership note requires it to
 * stay below five watched signals, and Quick Picks is the one route whose whole
 * point is that counter: classifying a movie there to time the animation would
 * spend the signal the journey is asserting on.
 */

const LIBRARY_PERSONA = 900000101;
const DISCOVER_PERSONA = 900000102;
const BROWSE_PERSONA = 900000103;
const COLD_START_PERSONA = 900000104;
const CATALOG_PAGE_SIZE = 24;

// Not serial: the config already pins one worker, and a route that fails its
// budget must not stop the remaining routes from being measured. A report that
// stops at the first problem is the least useful moment to lose the rest of the
// evidence.
test.afterAll(() => {
  writeReport();
});

interface RoutePresence {
  present: boolean;
  reason: string;
}

/**
 * Sign in, warm the route, then load it again as the measured pass.
 *
 * The performance gate is defined against "a warm application process". A
 * first-ever hit on a Next route pays for module evaluation and connection
 * setup that a reader on a running deployment never sees, so measuring it would
 * report the deployment's cold start as the page's cost.
 *
 * It also decides whether the route is really there, which a status code alone
 * cannot. A missing route can answer 404, or it can redirect — and the redirect
 * is the dangerous one, because it answers 200 and a naive check then measures
 * whatever it landed on and files the numbers under a route that does not
 * exist. A CI run did exactly that before this compared paths.
 */
async function prepare(page: Page, path: string): Promise<RoutePresence> {
  await throttleCpu(page, CPU_THROTTLE);
  await installVitals(page);
  await signInThroughKeycloak(page);
  const response = await page.goto(path);
  const status = response?.status() ?? 0;
  if (status === 404) {
    return { present: false, reason: "route answered HTTP 404" };
  }
  const requested = new URL(path, "http://localhost").pathname;
  const landed = new URL(page.url()).pathname;
  if (landed !== requested) {
    return {
      present: false,
      reason: `route redirected to ${landed}, whose cost is not ${requested}'s`,
    };
  }
  await settle(page);
  return { present: true, reason: "" };
}

function skipMissingRoute(route: string, path: string, presence: RoutePresence): boolean {
  if (presence.present) {
    return false;
  }
  record({
    route,
    path,
    skipped: `${presence.reason} — not available on this branch`,
    structural: {},
  });
  // Not a failure: the route may be scheduled work, and the report lists it so
  // a reader can see the gate has a hole rather than assuming it was measured.
  test.skip(true, `${path} is not available: ${presence.reason}`);
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
    const overrun = `acknowledgement ${measurement.acknowledgement?.ms.toFixed(1)} ms > ${BUDGETS.ackMs} ms`;
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

/** The movie id the featured Discover card links to. */
async function featuredMovieId(page: Page): Promise<number> {
  const featured = page.locator("section.featured-movie");
  const href = await featured.getByRole("link", { name: /Open movie/ }).getAttribute("href");
  const match = /\/movies\/(\d+)/.exec(href ?? "");
  expect(match, `expected a movie link on the featured card, got ${href}`).not.toBeNull();
  return Number(match?.[1]);
}

test("Discover: fan-out render, deferred evidence, and feedback acknowledgement", async ({
  page,
}) => {
  const path = `/discover?userId=${DISCOVER_PERSONA}`;
  const presence = await prepare(page, path);
  if (skipMissingRoute("discover", path, presence)) {
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
  const movieId = await featuredMovieId(page);
  const ackMs = await clickAndMeasureAcknowledgement(
    page,
    page.locator("section.featured-movie").getByRole("button", { name: "Watchlist" }),
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

  await clearWatchlist(page, DISCOVER_PERSONA, movieId);
  assertStructural(measurement);
  assertTiming(measurement);
});

test("Browse: bounded grid, lazy posters, and a cursor continuation", async ({ page }) => {
  const path = `/browse?user=${BROWSE_PERSONA}`;
  const presence = await prepare(page, path);
  if (skipMissingRoute("browse", path, presence)) {
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
  // Entered the way a reader enters it — from a Browse card — so the movie
  // under test is one the catalog actually offers, and the watchlist mutation
  // stays inside what the Browse journey owns for this persona.
  await throttleCpu(page, CPU_THROTTLE);
  await installVitals(page);
  await signInThroughKeycloak(page);
  await page.goto(`/browse?user=${BROWSE_PERSONA}`);
  await settle(page);
  // A card that rendered artwork, so the reserved-box check has something to
  // check rather than passing vacuously on a fallback mark.
  const card = page.locator(".catalog-cell:has(img) a[href^='/movies/']").first();
  const href = await card.getAttribute("href");
  const movieId = Number(/\/movies\/(\d+)/.exec(href ?? "")?.[1]);
  expect(movieId, `expected a movie link with artwork, got ${href}`).toBeGreaterThan(0);

  const path = `/movies/${movieId}?user=${BROWSE_PERSONA}`;
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

  // The status paragraph and its error twin share a class; only the status one
  // carries the committed message.
  await expect(page.locator('p.canonical-state-message[role="status"]')).toContainText(
    /watchlist/i,
  );
  await clearWatchlist(page, BROWSE_PERSONA, movieId);
  assertStructural(measurement);
  assertTiming(measurement);
});

test("Library: tabbed collections and a rating-edit acknowledgement", async ({ page }) => {
  const path = `/library?userId=${LIBRARY_PERSONA}&tab=rated`;
  const presence = await prepare(page, path);
  if (skipMissingRoute("library", path, presence)) {
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

test("Quick Picks: the cold-start decision queue, read without spending a signal", async ({
  page,
}) => {
  const path = `/quick-picks?user=${COLD_START_PERSONA}`;
  const presence = await prepare(page, path);
  if (skipMissingRoute("quick-picks", path, presence)) {
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
      no_per_card_tmdb_fan_out: tmdbFanOut(requests, await page.locator(".poster-frame").count()),
    },
    requests,
  };
  record(measurement);
  assertStructural(measurement);
  assertTiming(measurement);
});
