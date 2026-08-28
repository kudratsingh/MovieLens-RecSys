import { expect, test } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";
import { COLD_START, clearDismissal, resetColdStart } from "./personas";

/**
 * The service-backed browser journeys, run against the bypass-disabled demo
 * Compose stack: real Keycloak, real FastAPI, real RLS, real committed state.
 *
 * **Persona ownership.** Every journey in the run shares one seeded database,
 * so a journey writing to a persona another journey is asserting on is
 * indistinguishable from a bug in the code under test. One journey, one
 * persona:
 *
 * | Persona                   | Journey                               |
 * | ------------------------- | ------------------------------------- |
 * | 900000101 Action Fan      | Library — rating and watched history  |
 * | 900000102 Drama Fan       | Discover — `discover-journey.spec.ts` |
 * | 900000103 Eclectic Viewer | Browse — watchlist only               |
 * | 900000104 Cold Start      | PKCE, then Quick Picks                |
 *
 * Cold Start is the one deliberate share, and its rule is the strict one:
 * **every journey that touches it hands it on with zero positive signals**, in
 * a `finally`, tolerating whatever it found on arrival. Not "below five" — the
 * seeder leaves this persona empty, the run's cold-start assertions are about a
 * persona with nothing to learn from, and the k6 page workload's teardown reads
 * that emptiness back at the end. `resetColdStart` in `./personas` is the only
 * restore that achieves it, and its comment explains why the obvious candidates
 * do not.
 *
 * The destructive rating and history work lives on Action Fan for the same
 * reason: it is a warm persona whose seeded history does not contain the movie
 * these journeys create, so removing that movie again *is* the seeded state.
 *
 * Each journey below restores what it changed and tolerates finding the
 * persona already changed, so the file can be re-run against a stack a
 * previous run left mid-flight. `persona-hygiene.spec.ts` runs last and fails
 * the run if any of them forgot.
 */

test("real Keycloak PKCE session reaches the role-gated demo API and logs out", async ({
  page,
}) => {
  // Cold Start: see the ownership note above. This journey rates a title, and
  // a rating implies watched, so it owes the run a zero-signal restore.
  const personaId = COLD_START;
  const subject = 1;
  await signInThroughKeycloak(page);

  // The front door hands a signed-in viewer to the product rather than to the
  // pre-redesign dashboard. Asserted on the URL as well as on the screen: a
  // shell that happens to look right on a route that is still `/` is the
  // failure the 7d cutover exists to remove.
  await expect(page).toHaveURL(/\/discover\?userId=\d+$/);
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();

  const actor = await page.evaluate(async () =>
    fetch("/api/auth/actor", { cache: "no-store" }).then((response) => response.json()),
  );
  expect(actor).toMatchObject({
    tenant_id: "demo",
    authorized_party: "movielens-web",
  });
  expect(actor.roles).toContain("demo-impersonator");

  const publicSession = await page.evaluate(async () =>
    fetch("/api/auth/session", { cache: "no-store" }).then((response) => response.json()),
  );
  expect(publicSession).not.toHaveProperty("accessToken");
  expect(publicSession).not.toHaveProperty("refreshToken");
  expect(publicSession).not.toHaveProperty("idToken");

  await page.goto("/ui-preview/discover");
  await expect(page.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
  await expect(page.getByText("Recorded persona")).toBeVisible();
  await page.goto("/");

  // Start from the persona's seeded state rather than from whatever a previous
  // run left on this title.
  await resetColdStart(page);

  let restored: number[] = [];
  try {
    const durableMutation = await page.evaluate(
      async ({ userId, movieId }) => {
        const csrfToken = await fetch("/api/auth/csrf", { cache: "no-store" })
          .then((response) => response.json())
          .then((body: { csrfToken: string }) => body.csrfToken);
        const headers = { "Content-Type": "application/json", "x-csrf-token": csrfToken };
        const mutation = await fetch(`/api/users/${userId}/ratings`, {
          method: "POST",
          headers,
          body: JSON.stringify({ movie_id: movieId, rating: 4 }),
        });
        const [movieDetail, immediateRead] = await Promise.all([
          fetch(`/api/users/${userId}/movies/${movieId}`, { cache: "no-store" }).then(
            (response) => response.json(),
          ),
          fetch(`/api/users/${userId}`, { cache: "no-store" }).then((response) =>
            response.json(),
          ),
        ]);
        return {
          mutationStatus: mutation.status,
          rating: movieDetail.item.state?.rating,
          historyContainsMovie: immediateRead.history.items.some(
            (item: { movie_id: number }) => item.movie_id === movieId,
          ),
        };
      },
      { userId: personaId, movieId: subject },
    );
    expect(durableMutation).toEqual({
      mutationStatus: 200,
      rating: 4,
      historyContainsMovie: true,
    });

    // The same write as the one that just succeeded, minus the CSRF token. It
    // is refused in the BFF before it can reach the API, so it is deliberately
    // aimed at this journey's own persona: borrowing another journey's persona
    // would suggest the refusal was about who is being written to, and would
    // put a second writer on a row this file promises not to touch.
    const rejectedMutation = await page.evaluate(
      async ({ userId, movieId }) => {
        const response = await fetch(`/api/users/${userId}/ratings`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ movie_id: movieId, rating: 4 }),
        });
        return response.status;
      },
      { userId: personaId, movieId: subject },
    );
    expect(rejectedMutation).toBe(403);
  } finally {
    // Before the sign-out below, which takes the session this needs with it.
    restored = await resetColdStart(page);
  }
  // The rating above implied a watched interaction, so the restore had work to
  // do. Asserting that it did it is what stops this journey from quietly
  // handing Quick Picks — and the k6 workload after it — a persona with one
  // signal on it again.
  expect(restored, "the Cold Start restore did not remove the rated title").toEqual([
    subject,
  ]);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
  await page.goto("/ui-preview/discover");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
});

/**
 * The Library half of the Bundle 7 journey, against the seeded Compose stack
 * with `DEV_AUTH_BYPASS=false`.
 *
 * It proves the part fixture tests cannot: a rating created on movie detail is
 * committed, is findable and editable in Rated and History, and that the two
 * destructive actions are genuinely different — deleting the star leaves the
 * watched interaction in place, and only the confirmed history removal takes it
 * away.
 *
 * Action Fan is this journey's persona because it is warm — the route it
 * exercises is about editing an existing collection — and because Toy Story is
 * not in its seeded history. Creating that interaction and removing it again
 * therefore lands back on exactly the seeded state.
 */
test("a rating created on detail is findable, editable, and removable in Library", async ({
  page,
}) => {
  test.slow();
  const userId = 900000101;
  const movieId = 1;
  // A row is identified by the anchor its title link carries, not by matching
  // its text: a title is a substring of other rows' titles often enough that
  // `hasText` is the wrong instrument for "this exact movie is gone".
  const movieRow = page
    .getByRole("listitem")
    .filter({ has: page.locator(`#library-movie-${movieId}`) });

  await signInThroughKeycloak(page);

  // Start from a known state: the journey creates the interaction it asserts
  // on, and must not inherit one a previous run left behind. Removing the
  // watched interaction takes its rating with it, so this clears both.
  await page.evaluate(
    async ([user, movie]) => {
      const csrfToken = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${user}/movies/${movie}/watched`, {
        method: "DELETE",
        headers: { "x-csrf-token": csrfToken, "Idempotency-Key": crypto.randomUUID() },
      });
    },
    [userId, movieId],
  );

  await page.goto(`/movies/${movieId}?user=${userId}`);
  const fullTitle = (await page.getByRole("heading", { level: 1 }).innerText()).trim();
  const needle = fullTitle.split(" (")[0];
  const ratingPanel = page.getByRole("group", { name: "Your rating" });

  // The rating round trip on the movie's own page, against real Keycloak, real
  // RLS, and a real committed revision. What the fixture harness cannot show is
  // that the acknowledgement waits for the API: the row only collapses because
  // a write landed, so the chip below *is* the evidence the record changed.
  await ratingPanel.getByRole("button", { name: "4 stars" }).click();
  await expect(page.getByText("Rating saved.")).toBeVisible();
  await expect(ratingPanel.getByText("You rated 4/5")).toBeVisible();
  await expect(ratingPanel.getByRole("button", { name: "4 stars" })).toHaveCount(0);

  // Changing it goes back through the same stars, pre-filled with what is
  // stored — not with what was last pressed.
  await ratingPanel.getByRole("button", { name: /^Change rating for / }).click();
  await expect(ratingPanel.getByRole("button", { name: "4 stars" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await ratingPanel.getByRole("button", { name: "5 stars" }).click();
  await expect(ratingPanel.getByText("You rated 5/5")).toBeVisible();

  // And clearing it from the reopened row leaves the watched interaction
  // behind, which is the ADR 0012 transition the two controls have to keep
  // visibly distinct.
  await ratingPanel.getByRole("button", { name: /^Change rating for / }).click();
  await ratingPanel.getByRole("button", { name: "Clear rating" }).click();
  await expect(page.getByText(/Rating removed\..* stays in watched history/)).toBeVisible();
  await expect(ratingPanel.getByRole("button", { name: "4 stars" })).toBeVisible();

  // Put the rating the rest of this journey asserts on back.
  await ratingPanel.getByRole("button", { name: "4 stars" }).click();
  await expect(ratingPanel.getByText("You rated 4/5")).toBeVisible();

  // Rated: the new rating is there and can be edited in place.
  await page.goto(`/library?userId=${userId}&tab=rated&q=${encodeURIComponent(needle)}`);
  await expect(page.getByRole("tab", { name: /^Rated/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(movieRow).toHaveCount(1);
  await expect(movieRow.getByText(/Rated 4\.0 of 5/)).toBeVisible();

  await movieRow.getByRole("combobox").selectOption("3");
  await expect(movieRow.getByText(/Rated 3\.0 of 5/)).toBeVisible();
  // A Library row renders its new value optimistically and the route announces
  // the write only once the API has answered, so the row alone is not evidence
  // that anything left the browser. Navigating on the optimistic render
  // abandons the request in flight, which is what left the movie in history on
  // the first attempt of CI run 32512812081. Every navigation below waits for
  // the announcement that only a committed write produces.
  await expect(page.getByText(/Rating saved for .+ library/)).toBeAttached();

  // History: the same interaction, reached from its own collection.
  await page.goto(`/library?userId=${userId}&tab=history&q=${encodeURIComponent(needle)}`);
  await expect(movieRow).toHaveCount(1);
  await expect(movieRow.getByText(/Rated 3\.0 of 5/)).toBeVisible();

  // Deleting the star is not removing the watched interaction.
  await movieRow.getByRole("button", { name: "Remove rating" }).click();
  await expect(movieRow.getByText(/Not rated/)).toBeVisible();
  await expect(
    page.getByText(/Rating removed from .+ It is still watched history/),
  ).toBeAttached();
  await page.reload();
  await expect(movieRow).toHaveCount(1);

  // Removing history is confirmed, and only then does the movie leave.
  await movieRow.getByRole("button", { name: "Remove from history" }).click();
  const confirm = movieRow.getByRole("group", { name: /^Confirm removing/ });
  await expect(confirm).toContainText("deletes the watched interaction and its rating");
  await confirm.getByRole("button", { name: "Remove from history" }).click();
  await expect(movieRow.getByText(/No longer watched/)).toBeVisible();
  await expect(
    page.getByText(/removed from .+ watched history, along with its rating/),
  ).toBeAttached();

  // Back to the seeded state: Toy Story is not in Action Fan's history.
  await page.reload();
  await expect(movieRow).toHaveCount(0);
});

/**
 * The Bundle 7 journey step for Browse and detail, run against the seeded
 * Compose stack rather than a fixture: search the catalog, continue the
 * cursor, open a movie, save it, and come back to the same query, the same
 * loaded window, and the same position.
 *
 * Eclectic Viewer is this journey's persona. The only thing it writes is a
 * watchlist entry, which it removes again before it finishes — a watchlist
 * entry is not a watched signal and changes no model input, so this is the
 * lightest write in the file and the persona is left as it was found.
 */
test("search, cursor continuation, detail, and watchlist survive a round trip", async ({
  page,
}) => {
  const userId = 900000103;
  await signInThroughKeycloak(page);

  await page.goto(`/browse?user=${userId}`);
  const grid = page.getByRole("list", { name: "Browse results" });
  await expect(grid).toBeVisible();

  // Search reaches the catalog endpoint; it is not a client-side filter over
  // an already-loaded page.
  await page.getByRole("searchbox").fill("the");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/[?&]q=the/);
  await expect(page).toHaveURL(new RegExp(`user=${userId}`));
  await expect(grid).toBeVisible();

  // The grid keeps the previous results mounted while the next query is in
  // flight, so reading it straight after the click can capture the search
  // results the clear was supposed to drop — which is how CI came to compare a
  // filtered first page against an unfiltered window on 2026-08-21. Waiting
  // for the unfiltered response and then for the status line to stop reporting
  // a load pins both ends: the fetch has answered, and its results are the
  // ones on screen.
  const defaultCut = page.waitForResponse(
    (response) =>
      response.url().includes("/catalog") &&
      !new URL(response.url()).searchParams.has("q") &&
      response.ok(),
  );
  await page.getByRole("button", { name: "Clear the title search" }).click();
  await defaultCut;
  await expect(page.getByText(/Loading the catalog/)).toHaveCount(0);
  await expect(grid).toBeVisible();
  const firstPage = await page.locator(".catalog-cell .poster-title").allInnerTexts();
  expect(firstPage.length).toBeGreaterThan(0);

  await page.getByRole("button", { name: "Load more movies" }).click();
  await expect(page.getByRole("listitem")).toHaveCount(firstPage.length * 2);
  const loaded = await page.locator(".catalog-cell .poster-title").allInnerTexts();
  expect(new Set(loaded).size).toBe(loaded.length);
  expect(loaded.slice(0, firstPage.length)).toEqual(firstPage);
  await expect(page).toHaveURL(/cursor=/);

  const browseUrl = page.url();
  const card = page.getByRole("listitem").last();
  await card.scrollIntoViewIfNeeded();
  const scrolledTo = await page.evaluate(() => window.scrollY);
  expect(scrolledTo).toBeGreaterThan(0);
  const title = await card.locator(".poster-title").innerText();
  const cardHref = (await card.getByRole("link").first().getAttribute("href")) ?? "";
  const movieId = Number(/\/movies\/(\d+)/.exec(cardHref)?.[1]);
  expect(movieId, `expected a movie link, got ${cardHref}`).toBeGreaterThan(0);
  await card.getByRole("link").first().click();

  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  // Normalize first, so the run does not depend on state a previous run left.
  if (await page.getByRole("button", { name: "In watchlist" }).count()) {
    await page.getByRole("button", { name: "In watchlist" }).click();
    await expect(page.getByRole("button", { name: "Watchlist" })).toBeVisible();
  }
  await page.getByRole("button", { name: "Watchlist" }).click();
  await expect(page.getByRole("button", { name: "In watchlist" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText(
    "changes no recommendation input",
  );

  await page.goBack();
  await expect(page).toHaveURL(browseUrl);
  await expect(page.getByRole("listitem")).toHaveCount(loaded.length);
  await expect
    .poll(async () => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(scrolledTo - 200);
  // The state committed on detail is reflected on the restored card. Since
  // P2-9 the card's controls are the shared movie-state family, so the button
  // names the action and the surrounding group names the movie — which is what
  // binds the assertion to this title rather than to whatever is last.
  const restoredCard = page.getByRole("listitem").last();
  const actions = restoredCard.getByRole("group", { name: `Actions for ${title}` });
  const savedToggle = actions.getByRole("button", { name: "In watchlist" });
  await expect(savedToggle).toBeVisible();
  await expect(savedToggle).toHaveAttribute("aria-pressed", "true");

  await savedToggle.click();
  const emptyToggle = actions.getByRole("button", { name: "Watchlist", exact: true });
  await expect(emptyToggle).toBeVisible();
  await expect(emptyToggle).toHaveAttribute("aria-pressed", "false");

  // The relabelled control is the optimistic frame, and the context closing on
  // it is how this journey left a watchlist row on Eclectic Viewer after
  // reporting the removal. The persona's exit state is a committed one, so it
  // is read back from the API rather than inferred from the button.
  await expect
    .poll(async () =>
      page.evaluate(
        async ({ user, movie }) => {
          const response = await fetch(`/api/users/${user}/movies/${movie}`, {
            cache: "no-store",
          });
          const body = (await response.json()) as {
            item?: { state?: { watchlisted_at?: string | null } | null };
          };
          return body.item?.state?.watchlisted_at ?? null;
        },
        { user: userId, movie: movieId },
      ),
    )
    .toBeNull();
});

/**
 * Quick Picks against the same seeded stack.
 *
 * A fixture can prove the deck behaves; only this can prove the decision
 * reached serving. The sequence is the one ADR 0012 pins: a dismissal excludes
 * the title from the next recommendation set, undo restores its eligibility,
 * and a watched signal moves the cold-start count only once the API has
 * committed it.
 *
 * Spending that signal is the point of the journey, so putting it back is not
 * optional bookkeeping: the persona's exit state is zero positive signals, and
 * the restore is in a `finally` because a failure half way through the deck
 * would otherwise leave the signal behind.
 */
test("Quick Picks decisions change what serving returns", async ({ page }) => {
  test.slow();
  const userId = COLD_START;
  await signInThroughKeycloak(page);

  async function recommendedIds(): Promise<number[]> {
    return page.evaluate(async (id) => {
      const payload = (await fetch(`/api/users/${id}`, { cache: "no-store" }).then((response) =>
        response.json(),
      )) as { recommendations: { items: { movie_id: number }[] } };
      return payload.recommendations.items.map((item) => item.movie_id);
    }, userId);
  }

  async function signalCount(): Promise<number> {
    const copy = (await page.locator(".quick-pick-progress-count").textContent()) ?? "";
    return Number(copy.trim().split(" ")[0]);
  }

  await page.goto(`/quick-picks?user=${userId}`);
  // The controls are inert markup until React attaches.
  await expect(page.locator(".quick-picks-page")).toHaveAttribute("data-interactive", "true");

  const movieId = Number(await page.locator(".quick-pick-card").getAttribute("data-movie-id"));
  const title = (await page.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(movieId).toBeGreaterThan(0);
  expect(await recommendedIds()).toContain(movieId);

  const before = await signalCount();

  let restored: number[] = [];
  try {
    // The journeys above mutate this same persona, so the top pick may already
    // carry a state row the queue cannot see — a recommendation carries no
    // revision, so a first write can only assert zero. That conflict is what
    // the deck is built to correct: it re-reads the canonical record, and the
    // next attempt asserts a revision the server issued. Retrying once
    // exercises that path rather than papering over it.
    const notForMe = page.getByRole("button", { name: /Not for me/ });
    const status = page.getByRole("status");
    await notForMe.click();
    await expect(status).not.toBeEmpty();
    if (!((await status.textContent()) ?? "").includes(`${title}: not for me saved.`)) {
      await expect(page.locator(".quick-picks-error")).toContainText("try again");
      await notForMe.click();
    }
    await expect(status).toContainText(`${title}: not for me saved.`);
    expect(await recommendedIds()).not.toContain(movieId);
    // A dismissal is an exclusion, never a positive signal.
    expect(await signalCount()).toBe(before);

    // Located by class: a MovieLens title carries parentheses, which a name
    // pattern would read as a group.
    await page.locator("button.quick-picks-undo").click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    expect(await recommendedIds()).toContain(movieId);
    expect(await signalCount()).toBe(before);

    // The panel caps its display at the threshold, so the expectation does too.
    const expected = Math.min(before + 1, 5);
    await page.getByRole("button", { name: /^Watched/ }).click();
    await expect(page.locator(".quick-pick-progress-count")).toHaveText(
      `${expected} of 5 positive watched signals`,
    );
    expect(await recommendedIds()).not.toContain(movieId);
  } finally {
    // Undo already cleared the dismissal on the way through; this covers the
    // runs that failed before reaching it.
    await clearDismissal(page, movieId);
    restored = await resetColdStart(page);
  }
  // Zero positive signals is the persona's exit state, and the title this
  // journey deliberately watched is the one the restore had to find.
  expect(restored, "the Cold Start restore did not remove the watched pick").toContain(
    movieId,
  );
});
