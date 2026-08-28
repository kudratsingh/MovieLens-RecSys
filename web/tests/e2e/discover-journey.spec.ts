import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";

/**
 * The service-backed Discover journey.
 *
 * This runs against the bypass-disabled demo stack in CI: real Keycloak, a real
 * FastAPI, real RLS, real committed state. It proves the parts of Bundle 5B
 * that fixtures cannot — that the route's copy follows a policy the deployed
 * router actually chose, that a mutation commits through the BFF, and that
 * "Recommendations refreshed" is only said after a real refetch answers.
 *
 * This journey owns **Drama Fan (900000102)** and writes to no other persona.
 * `browser-auth.spec.ts` carries the ownership table for the whole run; the
 * short version is that both spec files draw on one seeded database, so a
 * shared persona would let one journey's cleanup surface as another journey's
 * failure. It marks the featured movie watched and rates it, then removes the
 * watched interaction — which takes the rating with it — in a `finally`, so a
 * failure part-way through still leaves the persona as it was found.
 *
 * The second test is the P0 repro. It needs a state row above revision 0 and a
 * relay that has never seen it, which is a real revision counter and therefore
 * out of reach of the fixture harness.
 */

const PERSONA_ID = 900000102;
const DISCOVER = `/discover?userId=${PERSONA_ID}`;

/** Reads the movie ID the primary card links to, so cleanup can undo it. */
async function featuredMovieId(page: Page): Promise<number> {
  // Only the primary movie renders an `Open movie` link.
  const href = await page.getByRole("link", { name: /Open movie/ }).getAttribute("href");
  const match = /\/movies\/(\d+)/.exec(href ?? "");
  expect(match, `expected a movie link, got ${href}`).not.toBeNull();
  return Number(match?.[1]);
}

/** Undoes one sub-resource for the owned persona, whatever state it is in. */
async function clearMovieState(
  page: Page,
  movieId: number,
  resource: "watched" | "watchlist",
) {
  await page.evaluate(
    async ({ userId, movie, sub }) => {
      const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${userId}/movies/${movie}/${sub}`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "x-csrf-token": csrf,
        },
      });
    },
    { userId: PERSONA_ID, movie: movieId, sub: resource },
  );
}

function removeWatched(page: Page, movieId: number) {
  return clearMovieState(page, movieId, "watched");
}

test("sign in, read the served policy, open the evidence, and refresh on feedback", async ({
  page,
}) => {
  await signInThroughKeycloak(page);
  await page.goto(DISCOVER);

  // 1. The primary movie is the first read, and the label follows the response.
  const featured = page.getByRole("heading", { level: 1 });
  await expect(featured).toBeVisible();
  await expect(
    page.getByText(/Popular while we learn|Ranked by the learned model/).first(),
  ).toBeVisible();

  // The two labels are mutually exclusive: whichever policy the router chose,
  // the other one must not appear anywhere on the page.
  const learned = await page.getByText("Ranked by the learned model").count();
  const fallback = await page.getByText("Popular while we learn").count();
  expect(learned === 0 || fallback === 0).toBe(true);
  await expect(page.getByText(/because you liked/i)).toHaveCount(0);

  // 2. Evidence is reachable in two deliberate actions and does not block it.
  await page.getByRole("button", { name: "Why this?" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Serving policy")).toBeVisible();
  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);

  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(page.getByTestId("technical-evidence")).toBeVisible();
  // The audit read may succeed or fail depending on which services the demo
  // stack is running; either way it resolves into its own region and the
  // reason above it stays readable.
  await expect(drawer.getByRole("heading", { name: "Prediction audit" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(featured).toBeVisible();

  const movieId = await featuredMovieId(page);

  // 3. A committed action refreshes recommendations, and only then says so.
  //    Holding the refetch open is what makes the ordering observable.
  let releaseRefresh: () => void = () => {};
  const refreshHeld = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  await page.route("**/api/users/*/recommendations*", async (route) => {
    await refreshHeld;
    await route.continue();
  });

  try {
    const markWatched = page.getByRole("button", { name: "Mark watched" }).first();
    await markWatched.click();

    // One press is enough on any run, not only the first one against a fresh
    // stack. Removing a watched interaction leaves the state row behind at a
    // higher revision, so this used to need a second press after the route
    // reported a conflict; the shared write path now re-reads the canonical
    // record and replays the same intent against it.
    await expect(page.getByText(/Refreshing recommendations/)).toBeVisible();
    await expect(page.getByText(/changed somewhere else/)).toHaveCount(0);
    await expect(page.getByText(/Recommendations refreshed/)).toHaveCount(0);

    releaseRefresh();
    await expect(page.getByText(/Recommendations refreshed/)).toBeVisible();
    await page.unroute("**/api/users/*/recommendations*");

    // 4. Watched reveals the rating control, and rating commits the same way.
    const ratingPanel = page.getByRole("region", { name: /^Rate / });
    await expect(ratingPanel).toBeVisible();
    await expect(
      ratingPanel.getByText(/a 1 and a 5 are the same learned signal today/),
    ).toBeVisible();

    await ratingPanel.getByRole("button", { name: /^4 stars for / }).click();
    await expect(page.getByText(/Rating saved for/)).toBeVisible();
    // The title is already excluded by the watched write above, so this
    // refetch legitimately returns the same ranked set — and the status line
    // now says that rather than claiming a refresh the viewer cannot see.
    await expect(page.locator("#discover-status")).toContainText(
      /Recommendations refreshed|The ranked list is unchanged/,
    );
    await expect(ratingPanel.getByText("4 out of 5 recorded")).toBeVisible();

    // 5. The committed state is durable: an independent read observes it.
    const detail = await page.evaluate(
      async ({ userId, movie }) =>
        (await fetch(`/api/users/${userId}/movies/${movie}`, { cache: "no-store" }).then(
          (response) => response.json(),
        )) as { item?: { state?: { rating?: number; watched_at?: string | null } } },
      { userId: PERSONA_ID, movie: movieId },
    );
    expect(detail.item?.state?.rating).toBe(4);
    expect(detail.item?.state?.watched_at).not.toBeNull();
  } finally {
    await page.unroute("**/api/users/*/recommendations*").catch(() => {});
    await removeWatched(page, movieId);
  }
});

test("the first press on a title that already carries state still commits", async ({
  page,
}) => {
  // The P0 this file exists to hold: a recommendation carries no revision, so
  // the first press can only assert `expected_revision=0`, and any title that
  // has ever been written and reverted — which the product's own undo
  // affordances do routinely — sits above that. Before the shared write path
  // replayed against the canonical record, this press was silently discarded
  // and only a second one committed. It needs a real revision counter, so it
  // cannot be proven in fixture mode.
  await signInThroughKeycloak(page);
  await page.goto(DISCOVER);

  // The route streams, and `app/discover/loading.tsx` renders an `h1` of its
  // own ("Finding a strong first pick…"). Waiting on the level-1 heading alone
  // therefore resolves against the placeholder, and a region named after it
  // never exists — so wait for the movie's own section first.
  const featuredSection = page.locator("section.featured-movie");
  await expect(featuredSection).toBeVisible();
  const heading = featuredSection.getByRole("heading", { level: 1 });
  // The primary movie's section is labelled by its own title, so the controls
  // are scoped to it rather than to the rail cards behind it.
  const title = (await heading.textContent()) ?? "";
  const featured = page.getByRole("region", { name: title });
  const movieId = await featuredMovieId(page);
  const status = page.locator("#discover-status");
  const watchlist = () => featured.getByRole("button", { name: /^Watchlist$/ });

  try {
    // 1. Drive the row past revision 0 the way a viewer would: save, then undo.
    //    The decision advances the featured slot on commit (F1), so the way
    //    back is the offered `Undo` beside the status line rather than a second
    //    press on a card that has already moved on — and the undo restores both
    //    the server row and the cursor, which is what puts the same title back
    //    in front of us carrying a revision above zero and no flag set.
    await watchlist().click();
    await expect(status).toContainText(/saved to watchlist/);
    await page
      .getByRole("button", { name: `Undo saving ${title} to the watchlist` })
      .click();
    await expect(status).toContainText(/is back, and the change was undone/);

    // 2. Come back with an empty relay — a new tab, or simply a viewer who
    //    arrived here without having touched this title in this tab.
    await page.evaluate(() => window.sessionStorage.clear());
    await page.reload();
    await expect(featuredSection).toBeVisible();
    await expect(heading).toHaveText(title);
    expect(await featuredMovieId(page)).toBe(movieId);

    // 3. One press, and it is committed rather than reported as a conflict.
    //    The settled status copy is what tells the two outcomes apart — the
    //    control shows the optimistic frame either way, and the card it sat on
    //    has advanced by the time the commit lands.
    await watchlist().click();
    await expect(status).toContainText(/saved to watchlist/);
    await expect(page.getByText(/changed somewhere else/)).toHaveCount(0);
    await expect(heading).not.toHaveText(title);

    // 4. Durable, not just optimistic.
    const detail = await page.evaluate(
      async ({ userId, movie }) =>
        (await fetch(`/api/users/${userId}/movies/${movie}`, { cache: "no-store" }).then(
          (response) => response.json(),
        )) as { item?: { state?: { watchlisted_at?: string | null } } },
      { userId: PERSONA_ID, movie: movieId },
    );
    expect(detail.item?.state?.watchlisted_at).not.toBeNull();
  } finally {
    await clearMovieState(page, movieId, "watchlist");
  }
});

test("Discover is not reachable without a session", async ({ page }) => {
  await page.goto(DISCOVER);

  // The door, and it keeps the destination: a deep link that survives sign-in
  // is what S10 fixed, so a bare `/` here would be the regression.
  await expect(page).toHaveURL(/\/\?next=%2Fdiscover/);
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
});
