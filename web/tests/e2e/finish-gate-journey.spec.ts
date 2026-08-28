import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";
import { COLD_START, clearDismissal, resetColdStart } from "./personas";

/**
 * The Bundle 7 service-backed finish gate.
 *
 * This is the handoff's ten-step journey run end to end against the seeded
 * Compose stack with `DEV_AUTH_BYPASS=false`: real Keycloak, real FastAPI, real
 * RLS, real catalog metadata, the feature and model servers, and the web BFF.
 * The fixture harness under `web/e2e/` proves the states are legible; this
 * proves they are the states the deployed system actually produces, and that
 * every claim the product makes about serving follows the response rather than
 * a constant in the frontend.
 *
 * **Persona ownership.** `browser-auth.spec.ts` carries the table for the whole
 * run and this file honours it step by step, because the run shares one
 * database and a persona written by two journeys makes one journey's cleanup
 * look like the other journey's bug:
 *
 * | Persona                   | Steps here                                    |
 * | ------------------------- | --------------------------------------------- |
 * | 900000101 Action Fan      | 5, 6 — watchlist/watched/rating and Library   |
 * | 900000102 Drama Fan       | 1, 2, 3, 7 — Discover policy, evidence, refresh |
 * | 900000103 Eclectic Viewer | 4, 9 — Browse, and the injected read failures |
 * | 900000104 Cold Start      | 2, 8 — the fallback label, dismissal and undo |
 *
 * Cold Start is read-only apart from one dismissal that is undone in the same
 * step, and its exit state is the strict one the whole run depends on: **zero
 * positive signals**, not merely fewer than five. Step 8 restores it in a
 * `finally` even though a dismissal is not a positive signal, because a failure
 * part way through the deck can leave one behind. Every write below is
 * reversed, each reversal tolerates finding the persona already restored so the
 * file can be re-run against a stack a previous run left mid-flight, and
 * `persona-hygiene.spec.ts` runs after this file and fails the run if any
 * journey forgot.
 *
 * **Failure injection.** Step 9 uses Playwright `route` interception at the BFF
 * boundary rather than a fixture: the page, the components, and the resource
 * state machine are the shipped ones, and only the bytes the BFF returns are
 * replaced. That is the one honest way to hold an upstream failure still long
 * enough to assert on it without teaching the product a demo mode.
 *
 * **The cutover.** Steps 1, 4, 5, and 10 also carry the three blocking items
 * the 7A finish-gate review recorded: that `/` is the product and not the
 * pre-redesign dashboard (B1), that every primary navigation reaches
 * `/discover` (B2), and that Browse and movie detail render the shared shell
 * with its mobile navigation and a resolved persona name (B3). They are
 * asserted here rather than in a separate file because they are properties of
 * the journey a viewer actually takes, and because a second file would pay for
 * a second Keycloak round trip to prove less.
 */

const DRAMA_FAN = 900000102;
const ACTION_FAN = 900000101;
const ECLECTIC = 900000103;

/** The whole journey is one test, and it signs in once. */
test.describe.configure({ mode: "serial" });

type ServingPolicy = {
  name?: string;
  learned?: boolean;
  positive_signal_count?: number | null;
  threshold?: number | null;
};

type RecommendationRead = {
  policy: string;
  serving_policy?: ServingPolicy;
  items: { movie_id: number; title: string }[];
};

/** Reads a persona's ranked set straight from the BFF, in the page's session. */
async function readRecommendations(page: Page, userId: number): Promise<RecommendationRead> {
  return page.evaluate(
    async (id) =>
      (await fetch(`/api/users/${id}/recommendations?limit=10`, { cache: "no-store" }).then(
        (response) => response.json(),
      )) as RecommendationRead,
    userId,
  );
}

/**
 * Removes the watched interaction, which takes any rating with it. Used as the
 * one cleanup for every positive signal this journey creates, and deliberately
 * indifferent to the response: a persona that is already clean is the state
 * this is trying to reach.
 */
async function removeWatched(page: Page, userId: number, movieId: number) {
  await page.evaluate(
    async ({ user, movie }) => {
      const token = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${user}/movies/${movie}/watched`, {
        method: "DELETE",
        headers: { "x-csrf-token": token, "Idempotency-Key": crypto.randomUUID() },
      });
    },
    { user: userId, movie: movieId },
  );
}

async function movieState(page: Page, userId: number, movieId: number) {
  return page.evaluate(
    async ({ user, movie }) =>
      (await fetch(`/api/users/${user}/movies/${movie}`, { cache: "no-store" }).then(
        (response) => response.json(),
      )) as {
        item?: {
          title?: string;
          state?: {
            rating: number | null;
            watched_at: string | null;
            watchlisted_at: string | null;
            revision: number;
          } | null;
        };
      },
    { user: userId, movie: movieId },
  );
}

test("the ten-step finish-gate journey holds against the seeded stack", async ({ page }) => {
  // Ten steps, one Keycloak round trip, and several full-page loads against a
  // stack that is also serving a model. The default 30s is a per-test budget,
  // not a per-step one.
  test.setTimeout(300_000);

  // ---------------------------------------------------------------- step 1 --
  // Sign in through Keycloak and select a named demo persona.
  await signInThroughKeycloak(page);

  // The front door is the product (B1). The sign-in round trip ends on `/`,
  // and a signed-in viewer is handed to Discover rather than to the
  // pre-redesign dashboard. Asserted on the URL as well as on the screen: a
  // shell that happens to look right on a route that is still `/` is the
  // failure the cutover exists to remove.
  await expect(page).toHaveURL(/\/discover\?userId=\d+$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // Every primary navigation points at the product's own routes (B2), so
  // Discover — and through it Quick Picks, whose only entry point Discover is
  // — can be reached by clicking rather than by typing a URL.
  const primaryNav = page.getByRole("navigation", { name: "Primary" });
  await expect(primaryNav.getByRole("link", { name: "For you" })).toHaveAttribute(
    "href",
    /^\/discover\?/,
  );
  await expect(primaryNav.getByRole("link", { name: "Browse" })).toHaveAttribute(
    "href",
    /^\/browse\?/,
  );
  await expect(primaryNav.getByRole("link", { name: "Library" })).toHaveAttribute(
    "href",
    /^\/library\?/,
  );

  // The named personas are still offered, on the retained legacy dashboard.
  // The product selects a persona by URL and has no picker of its own; the
  // cutover kept the dashboard rather than inventing one, and the review
  // records that as an open follow-up rather than as work this PR did.
  await page.getByRole("link", { name: "Legacy dashboard" }).click();
  await expect(page).toHaveURL(/\/legacy$/);
  await expect(page.getByText(/This is the legacy dashboard/)).toBeVisible();
  for (const persona of ["Action Fan", "Drama Fan", "Eclectic Viewer", "Cold Start"]) {
    await expect(page.getByRole("button", { name: persona })).toBeVisible();
  }
  await page.getByRole("button", { name: "Drama Fan" }).click();

  // The dashboard's serving-contract panel asserted `Popularity baseline` as a
  // constant while the router served something else. It now reports what the
  // response carried, which is checked against that response rather than
  // against a string this test also invented.
  const dashboardPolicy = (await readRecommendations(page, DRAMA_FAN)).serving_policy?.name;
  expect(dashboardPolicy, "the API reported no serving policy").toBeTruthy();
  await expect(page.getByTestId("serving-contract-policy")).toHaveText(
    String(dashboardPolicy),
  );

  await page.getByRole("link", { name: "Open the movie-discovery product" }).click();
  await expect(page).toHaveURL(/\/discover\?userId=\d+$/);

  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  const shell = page.locator(".persona-cluster");
  await expect(shell).toContainText("Exploring as");
  await expect(shell).toContainText("Drama Fan");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // ---------------------------------------------------------------- step 2 --
  // Distinguish learned and cold-start policy labels. The assertion is not
  // "this persona is learned" — which artifact set is deployed decides that —
  // but that the copy follows the policy the response reported, in both
  // directions.
  const warmResponse = await readRecommendations(page, DRAMA_FAN);
  const warmLearned = warmResponse.serving_policy?.learned ?? false;
  const learnedLabel = page.getByText("Ranked by the learned model");
  const fallbackLabel = page.getByText("Popular while we learn");
  if (warmLearned) {
    await expect(learnedLabel.first()).toBeVisible();
    await expect(fallbackLabel).toHaveCount(0);
  } else {
    await expect(fallbackLabel.first()).toBeVisible();
    await expect(learnedLabel).toHaveCount(0);
  }
  // Nothing invents a reason from watched history alone.
  await expect(page.getByText(/because you liked/i)).toHaveCount(0);

  // Cold Start has no signals, so its response must not report learned serving
  // and the route must not borrow learned copy for it.
  const coldResponse = await readRecommendations(page, COLD_START);
  expect(
    coldResponse.serving_policy?.learned ?? false,
    `Cold Start reported ${JSON.stringify(coldResponse.serving_policy)}`,
  ).toBe(false);
  expect(coldResponse.serving_policy?.positive_signal_count ?? 0).toBeLessThan(
    coldResponse.serving_policy?.threshold ?? 5,
  );

  await page.goto(`/discover?userId=${COLD_START}`);
  await expect(page.getByText("Popular while we learn").first()).toBeVisible();
  await expect(page.getByText("Ranked by the learned model")).toHaveCount(0);

  // ---------------------------------------------------------------- step 3 --
  // Open a recommendation's explanation and check it is supported: the drawer
  // quotes the response, and the heavier audit read is one further action in
  // rather than sitting in front of the movie.
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  const featured = page.getByRole("heading", { level: 1 });
  await expect(featured).toBeVisible();
  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);

  await page.getByRole("button", { name: "Why this?" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Serving policy")).toBeVisible();
  // No calibrated probability is claimed for a rank score.
  await expect(drawer.getByText(/%\s*match/i)).toHaveCount(0);
  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(page.getByTestId("technical-evidence")).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "Prediction audit" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(featured).toBeVisible();

  // ---------------------------------------------------------------- step 4 --
  // Search and filter Browse, continue the cursor, open a movie, and return to
  // the same query, the same loaded window, and the same position.
  await page.goto(`/browse?user=${ECLECTIC}`);
  const grid = page.getByRole("list", { name: "Browse results" });
  await expect(grid).toBeVisible();

  // Browse runs the shared product shell (B3). The two things its former
  // route-owned header dropped are the ones asserted: a persona that reads as
  // a name rather than as a database ID, and the bottom navigation the design
  // contract requires for the three primary routes on small screens — of
  // which Browse is one.
  const catalogShell = page.locator(".shell-header");
  await expect(catalogShell).toContainText("Exploring as");
  await expect(catalogShell).toContainText("Eclectic Viewer");
  await expect(catalogShell).not.toContainText(String(ECLECTIC));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("navigation", { name: "Primary mobile" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByRole("searchbox").fill("the");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/[?&]q=the/);
  await expect(grid).toBeVisible();

  const filters = page.getByRole("button", { name: "Filters" });
  await filters.click();
  await page.getByRole("button", { name: "Drama", exact: true }).click();
  await expect(page).toHaveURL(/genre=Drama/);
  // A filter edit invalidates a cursor that was issued for a different query.
  await expect(page).not.toHaveURL(/cursor=/);
  // The sheet stays open so several filters can be set in one visit, and its
  // scrim covers the grid until it is dismissed. Escape closes it and hands
  // focus back to the trigger, which is also the accessibility contract.
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(filters).toBeFocused();
  await expect(page.getByText(/Loading the catalog/)).toHaveCount(0);

  const firstPage = await page.locator(".catalog-cell .poster-title").allInnerTexts();
  expect(firstPage.length).toBeGreaterThan(0);
  const loadMore = page.getByRole("button", { name: "Load more movies" });
  if (await loadMore.count()) {
    await loadMore.click();
    await expect(page).toHaveURL(/cursor=/);
    const combined = await page.locator(".catalog-cell .poster-title").allInnerTexts();
    expect(combined.length).toBeGreaterThan(firstPage.length);
    expect(new Set(combined).size, "the cursor repeated a title").toBe(combined.length);
    expect(combined.slice(0, firstPage.length)).toEqual(firstPage);
  }

  const browseUrl = page.url();
  const window = await page.getByRole("listitem").count();
  const card = page.getByRole("listitem").last();
  await card.scrollIntoViewIfNeeded();
  const scrolledTo = await page.evaluate(() => globalThis.scrollY);
  expect(scrolledTo).toBeGreaterThan(0);
  const openedTitle = await card.locator(".poster-title").innerText();
  await card.getByRole("link").first().click();
  await expect(page.getByRole("heading", { level: 1, name: openedTitle })).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(browseUrl);
  await expect(page.getByRole("listitem")).toHaveCount(window);
  await expect
    .poll(async () => page.evaluate(() => globalThis.scrollY))
    .toBeGreaterThan(scrolledTo - 200);

  // ---------------------------------------------------------------- step 5 --
  // Watchlist, mark watched, rate, and observe the canonical committed state.
  // Movie 1 is Action Fan's designated write target: it is not in the persona's
  // seeded history, so removing it again lands back on the seeded state.
  const subject = 1;
  await removeWatched(page, ACTION_FAN, subject);
  await page.goto(`/movies/${subject}?user=${ACTION_FAN}`);
  const subjectTitle = (await page.getByRole("heading", { level: 1 }).innerText()).trim();

  // The other half of B3: movie detail ran the same second header as Browse.
  const detailShell = page.locator(".shell-header");
  await expect(detailShell).toContainText("Action Fan");
  await expect(detailShell).not.toContainText(String(ACTION_FAN));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("navigation", { name: "Primary mobile" })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 720 });

  const statePanel = page.locator(".movie-state-panel");

  if (await statePanel.getByRole("button", { name: "In watchlist" }).count()) {
    await statePanel.getByRole("button", { name: "In watchlist" }).click();
    await expect(statePanel.getByRole("button", { name: "Watchlist" })).toBeVisible();
  }
  await statePanel.getByRole("button", { name: "Watchlist" }).click();
  await expect(statePanel.getByRole("button", { name: "In watchlist" })).toBeVisible();
  // Saving is organizational: the route says so rather than implying a model
  // effect it does not have.
  await expect(page.getByRole("status").first()).toContainText(
    /changes no recommendation input/,
  );

  const saved = await movieState(page, ACTION_FAN, subject);
  expect(saved.item?.state?.watchlisted_at).not.toBeNull();
  expect(saved.item?.state?.watched_at).toBeNull();

  // A saved movie has to be retrievable, not merely acknowledged — that is one
  // of the discovery tasks, and a watchlist nobody can open is not a watchlist.
  await page.goto(`/library?userId=${ACTION_FAN}&tab=watchlist`);
  await expect(
    page.getByRole("listitem").filter({ has: page.locator(`#library-movie-${subject}`) }),
  ).toHaveCount(1);
  await page.goBack();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await statePanel.getByRole("button", { name: "Mark watched" }).click();
  await expect(statePanel.getByRole("button", { name: "Watched · remove" })).toBeVisible();

  await statePanel.getByRole("button", { name: /^4 stars/ }).click();
  await expect(page.getByText("Rating saved.")).toBeVisible();

  // Canonical, not optimistic: an independent read sees the committed record,
  // with a revision the server issued. The cleared watchlist is the documented
  // watched transition, not a lost write — the library-feedback contract has
  // `Mark watched` preserve the first watched time and clear the watchlist.
  const committed = await movieState(page, ACTION_FAN, subject);
  expect(committed.item?.state?.rating).toBe(4);
  expect(committed.item?.state?.watched_at).not.toBeNull();
  expect(committed.item?.state?.watchlisted_at).toBeNull();
  expect(committed.item?.state?.revision ?? 0).toBeGreaterThan(
    saved.item?.state?.revision ?? 0,
  );

  // ---------------------------------------------------------------- step 6 --
  // Find and edit, then remove, that state in Library.
  const needle = subjectTitle.split(" (")[0];
  const row = page
    .getByRole("listitem")
    .filter({ has: page.locator(`#library-movie-${subject}`) });

  await page.goto(
    `/library?userId=${ACTION_FAN}&tab=rated&q=${encodeURIComponent(needle)}`,
  );
  await expect(page.getByRole("tab", { name: /^Rated/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(row).toHaveCount(1);
  await expect(row.getByText(/Rated 4\.0 of 5/)).toBeVisible();

  await row.getByRole("combobox").selectOption("3");
  await expect(row.getByText(/Rated 3\.0 of 5/)).toBeVisible();
  // The row renders optimistically; only the announcement proves a committed
  // write, so every navigation below waits for it first.
  await expect(page.getByText(/Rating saved for .+ library/)).toBeAttached();

  await page.goto(
    `/library?userId=${ACTION_FAN}&tab=history&q=${encodeURIComponent(needle)}`,
  );
  await expect(row).toHaveCount(1);
  await expect(row.getByText(/Rated 3\.0 of 5/)).toBeVisible();

  // Deleting the star is not removing the interaction — the two destructive
  // actions stay distinguishable, and only the second one is confirmed.
  await row.getByRole("button", { name: "Remove rating" }).click();
  await expect(row.getByText(/Not rated/)).toBeVisible();
  await expect(
    page.getByText(/Rating removed from .+ It is still watched history/),
  ).toBeAttached();
  const afterStarDelete = await movieState(page, ACTION_FAN, subject);
  expect(afterStarDelete.item?.state?.rating).toBeNull();
  expect(afterStarDelete.item?.state?.watched_at).not.toBeNull();

  await row.getByRole("button", { name: "Remove from history" }).click();
  const confirm = row.getByRole("group", { name: /^Confirm removing/ });
  await expect(confirm).toContainText("deletes the watched interaction and its rating");
  await confirm.getByRole("button", { name: "Remove from history" }).click();
  await expect(row.getByText(/No longer watched/)).toBeVisible();
  // The row renders the removal optimistically. Reloading on that render
  // abandons the DELETE in flight, so the reload waits for the announcement
  // only a committed write produces.
  await expect(
    page.getByText(/removed from .+ watched history, along with its rating/),
  ).toBeAttached();
  await page.reload();
  await expect(row).toHaveCount(0);

  // Back to the seeded state: this movie is not in Action Fan's history, is
  // not rated, and is not saved — `Mark watched` already consumed the
  // watchlist entry above.
  const restored = await movieState(page, ACTION_FAN, subject);
  expect(restored.item?.state?.watched_at ?? null).toBeNull();
  expect(restored.item?.state?.rating ?? null).toBeNull();
  expect(restored.item?.state?.watchlisted_at ?? null).toBeNull();

  // ---------------------------------------------------------------- step 7 --
  // Refresh Discover and verify only the documented immediate effects: the
  // watched title leaves the unseen set, the refresh claim is made after the
  // refetch answers and not before, and nothing says the model learned a
  // preference strength from the star.
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const featuredHref = await page
    .getByRole("link", { name: /Open movie/ })
    .getAttribute("href");
  const featuredId = Number(/\/movies\/(\d+)/.exec(featuredHref ?? "")?.[1]);
  expect(featuredId).toBeGreaterThan(0);

  // Holding the refetch open is what makes the ordering observable: without it
  // "refreshing" and "refreshed" collapse into one frame.
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
    const flowStatus = page.locator("#discover-status");
    await markWatched.click();
    // A recommendation carries no revision, so a first write can only assert
    // "no state yet". If this movie already has a state row — a previous run,
    // another surface — the write is refused, the route re-reads the canonical
    // record, and the next attempt asserts a revision the server issued.
    // Retrying once exercises that correction rather than papering over it.
    await expect
      .poll(async () => (await flowStatus.textContent()) ?? "")
      .toMatch(/Refreshing recommendations|changed somewhere else/);
    if (((await flowStatus.textContent()) ?? "").includes("changed somewhere else")) {
      await markWatched.click();
    }
    await expect(page.getByText(/Refreshing recommendations/)).toBeVisible();
    await expect(page.getByText(/Recommendations refreshed/)).toHaveCount(0);
    releaseRefresh();
    await expect(page.getByText(/Recommendations refreshed/)).toBeVisible();
    await page.unroute("**/api/users/*/recommendations*");

    // Watched reveals the rating control, and the note stays inside what the
    // deployed recommender actually does with a star.
    const ratingPanel = page.getByRole("region", { name: /^Rate / });
    await expect(ratingPanel).toBeVisible();
    await expect(
      ratingPanel.getByText(/a 1 and a 5 are the same learned signal today/),
    ).toBeVisible();

    // The documented immediate effect: the title is excluded from the next
    // ranked set. Nothing claims the model retrained.
    const refreshed = await readRecommendations(page, DRAMA_FAN);
    expect(refreshed.items.map((item) => item.movie_id)).not.toContain(featuredId);
    await expect(page.getByText(/retrained|learned your taste|model updated/i)).toHaveCount(0);
  } finally {
    await page.unroute("**/api/users/*/recommendations*").catch(() => {});
    await removeWatched(page, DRAMA_FAN, featuredId);
  }

  // ---------------------------------------------------------------- step 8 --
  // Dismiss and undo through Quick Picks. A dismissal is an exclusion the
  // deployed serving path honours, and undo restores eligibility — neither
  // moves the positive signal count, which is what leaves Cold Start where the
  // ownership table expects it.
  await page.goto(`/quick-picks?user=${COLD_START}`);
  await expect(page.locator(".quick-picks-page")).toHaveAttribute("data-interactive", "true");
  const pickId = Number(await page.locator(".quick-pick-card").getAttribute("data-movie-id"));
  const pickTitle = (await page.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(pickId).toBeGreaterThan(0);
  expect((await readRecommendations(page, COLD_START)).items.map((item) => item.movie_id))
    .toContain(pickId);

  const signalCount = async () =>
    Number(((await page.locator(".quick-pick-progress-count").textContent()) ?? "").trim().split(" ")[0]);
  const signalsBefore = await signalCount();

  try {
    const notForMe = page.getByRole("button", { name: /Not for me/ });
    const status = page.getByRole("status");
    await notForMe.click();
    await expect(status).not.toBeEmpty();
    // A recommendation carries no revision, so a first write can only assert
    // zero; if another journey already wrote this row the deck re-reads the
    // canonical record and the retry asserts a revision the server issued.
    if (!((await status.textContent()) ?? "").includes(`${pickTitle}: not for me saved.`)) {
      await expect(page.locator(".quick-picks-error")).toContainText("try again");
      await notForMe.click();
    }
    await expect(status).toContainText(`${pickTitle}: not for me saved.`);
    expect((await readRecommendations(page, COLD_START)).items.map((item) => item.movie_id))
      .not.toContain(pickId);
    expect(await signalCount(), "a dismissal moved the positive signal count").toBe(
      signalsBefore,
    );

    await page.locator("button.quick-picks-undo").click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(pickTitle);
    expect((await readRecommendations(page, COLD_START)).items.map((item) => item.movie_id))
      .toContain(pickId);
    expect(await signalCount()).toBe(signalsBefore);
  } finally {
    // Nothing above is meant to leave a positive signal, and on the happy path
    // the undo has already put the dismissal back too. Both are restored anyway:
    // a step that fails between the dismissal and the undo — or a deck control
    // that classifies rather than dismisses — would otherwise hand the rest of
    // the run a persona that is no longer empty, which is precisely the class of
    // leak this file's ownership note promises not to produce.
    await clearDismissal(page, pickId);
    await resetColdStart(page);
  }

  // ---------------------------------------------------------------- step 9 --
  // Expire auth, fail one upstream resource, fail poster metadata, and recover.
  // Every injection replaces bytes at the BFF boundary; the page, the resource
  // state machine, and the components are the shipped ones.

  // (a) One upstream resource fails. The catalog read is the region under test;
  //     the shell, the filters, and the rest of the route stay usable, and the
  //     region owns its own retry.
  await page.route("**/api/users/*/catalog*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "injected upstream failure" }),
    }),
  );
  await page.goto(`/browse?user=${ECLECTIC}`);
  const catalogAlert = page.getByRole("alert", { name: "Catalog could not be loaded" });
  await expect(catalogAlert).toContainText("Catalog could not be loaded");
  await expect(page.getByRole("searchbox")).toBeVisible();
  await expect(page.getByRole("navigation", { name: /Primary/ }).first()).toBeVisible();

  await page.unroute("**/api/users/*/catalog*");
  await catalogAlert.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("list", { name: "Browse results" })).toBeVisible();

  // (b) The same read answers 401. That is an expired session rather than an
  //     outage, and the region says so and offers reauthentication instead of
  //     a retry that cannot work.
  //
  //     A different filter set on purpose: Browse keeps a per-tab window per
  //     filter key for thirty minutes, so returning to a query it already
  //     holds restores that window instead of re-reading — and an injection
  //     aimed at a read that never happens proves nothing.
  await page.route("**/api/users/*/catalog*", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "token expired" }),
    }),
  );
  await page.goto(`/browse?user=${ECLECTIC}&q=heat`);
  await expect(page.getByText("Your session expired")).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in again" })).toBeVisible();
  await page.unroute("**/api/users/*/catalog*");

  // (c) The real thing: drop the browser's session cookie and prove the
  //     protected route sends the viewer back to sign-in, then recover through
  //     Keycloak and land on a working product route.
  const sessionCookies = (await page.context().cookies()).filter((cookie) =>
    cookie.name.includes("session-token"),
  );
  expect(sessionCookies.length, "no Auth.js session cookie to expire").toBeGreaterThan(0);
  // Everything, not only the session token. Dropping Keycloak's SSO cookie too
  // makes the recovery below a full authorization-code round trip rather than
  // a silent re-issue, which is the stronger of the two proofs — and it leaves
  // no half-cleared Auth.js CSRF or PKCE cookie to make the next sign-in
  // nondeterministic.
  await page.context().clearCookies();
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  // The door carries the destination it interrupted, so the recovery below
  // lands where the viewer was going rather than on the front door.
  await expect(page).toHaveURL(/\/\?next=%2Fdiscover/);
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();

  await signInThroughKeycloak(page);
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // (d) Poster metadata fails. The movie keeps its identity through the
  //     deterministic fallback and the grid does not move.
  // Asserted before the injection so the injection cannot be vacuous: a route
  // whose posters are all already missing would "pass" this step without ever
  // exercising the fallback.
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const optimizedPosters = await page.locator('img[src*="/_next/image"]').count();
  expect(optimizedPosters, "no optimized poster to fail").toBeGreaterThan(0);

  await page.route("**/_next/image*", (route) => route.abort());
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("poster-fallback").first()).toBeVisible();
  // The movie keeps its identity: the title, the reason, and the actions are
  // all still there next to the fallback mark.
  await expect(page.getByRole("link", { name: /Open movie/ })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "a failed poster moved the layout").toBeLessThanOrEqual(1);

  await page.unroute("**/_next/image*");
  await page.goto(`/discover?userId=${DRAMA_FAN}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // --------------------------------------------------------------- step 10 --
  // Log out and prove the protected product is gone — not merely hidden.
  await page.goto("/");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();

  for (const route of [
    `/discover?userId=${DRAMA_FAN}`,
    `/browse?user=${ECLECTIC}`,
    `/library?userId=${ACTION_FAN}`,
    `/quick-picks?user=${COLD_START}`,
    `/movies/${subject}?user=${ACTION_FAN}`,
    // The retained rollback is a product route like any other, not an
    // unauthenticated back door into a persona's dashboard.
    "/legacy",
  ]) {
    await page.goto(route);
    // The door, at `/`, carrying the destination it interrupted: a protected
    // route is gone for a signed-out viewer, and the deep link is not (S10).
    // Polled rather than read once — the redirect settles after `goto` returns.
    await expect(page, route).toHaveURL(`/?next=${encodeURIComponent(route)}`);
    await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
  }

  // The data path is closed too, not just the routes: the BFF holds the token,
  // and without a session there is nothing for it to hold.
  const afterLogout = await page.evaluate(
    async (id) => (await fetch(`/api/users/${id}/recommendations`, { cache: "no-store" })).status,
    DRAMA_FAN,
  );
  expect(afterLogout).toBe(401);
});
