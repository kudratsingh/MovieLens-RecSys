import { expect, test, type Page } from "@playwright/test";

/** Bypass-disabled sign-in through the real Keycloak demo realm. */
async function signInThroughKeycloak(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();

  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();

  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

test("real Keycloak PKCE session reaches the role-gated demo API and logs out", async ({
  page,
}) => {
  await signInThroughKeycloak(page);

  await expect(page.getByRole("button", { name: "Action Fan" })).toBeVisible();

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

  const durableMutation = await page.evaluate(async () => {
    const userId = 900000104;
    const csrfToken = await fetch("/api/auth/csrf", { cache: "no-store" })
      .then((response) => response.json())
      .then((body: { csrfToken: string }) => body.csrfToken);
    const headers = { "Content-Type": "application/json", "x-csrf-token": csrfToken };
    await fetch(`/api/users/${userId}/ratings`, { method: "DELETE", headers });
    const mutation = await fetch(`/api/users/${userId}/ratings`, {
      method: "POST",
      headers,
      body: JSON.stringify({ movie_id: 1, rating: 4 }),
    });
    const [movieDetail, immediateRead] = await Promise.all([
      fetch(`/api/users/${userId}/movies/1`, { cache: "no-store" }).then((response) =>
        response.json(),
      ),
      fetch(`/api/users/${userId}`, { cache: "no-store" }).then((response) =>
        response.json(),
      ),
    ]);
    await fetch(`/api/users/${userId}/ratings`, { method: "DELETE", headers });
    return {
      mutationStatus: mutation.status,
      rating: movieDetail.item.state?.rating,
      historyContainsMovie: immediateRead.history.items.some(
        (item: { movie_id: number }) => item.movie_id === 1,
      ),
    };
  });
  expect(durableMutation).toEqual({
    mutationStatus: 200,
    rating: 4,
    historyContainsMovie: true,
  });

  const rejectedMutation = await page.evaluate(async () => {
    const response = await fetch("/api/users/900000101/ratings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ movie_id: 1, rating: 4 }),
    });
    return response.status;
  });
  expect(rejectedMutation).toBe(403);

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
 */
test("a rating created on detail is findable, editable, and removable in Library", async ({
  page,
}) => {
  test.slow();
  const userId = 900000104;
  const movieId = 1;

  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();

  // Start from a known state: the journey creates the interaction it asserts on.
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
  await page.getByRole("button", { name: "4 stars" }).click();
  await expect(page.getByText("Rating saved.")).toBeVisible();

  // Rated: the new rating is there and can be edited in place.
  await page.goto(`/library?userId=${userId}&tab=rated&q=${encodeURIComponent(needle)}`);
  await expect(page.getByRole("tab", { name: /^Rated/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const ratedRow = page.getByRole("listitem").filter({ hasText: fullTitle });
  await expect(ratedRow).toHaveCount(1);
  await expect(ratedRow.getByText(/Rated 4\.0 of 5/)).toBeVisible();

  await ratedRow.getByRole("combobox").selectOption("3");
  await expect(ratedRow.getByText(/Rated 3\.0 of 5/)).toBeVisible();

  // History: the same interaction, reached from its own collection.
  await page.goto(`/library?userId=${userId}&tab=history&q=${encodeURIComponent(needle)}`);
  const historyRow = page.getByRole("listitem").filter({ hasText: fullTitle });
  await expect(historyRow).toHaveCount(1);
  await expect(historyRow.getByText(/Rated 3\.0 of 5/)).toBeVisible();

  // Deleting the star is not removing the watched interaction.
  await historyRow.getByRole("button", { name: "Remove rating" }).click();
  await expect(historyRow.getByText(/Not rated/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("listitem").filter({ hasText: fullTitle })).toHaveCount(1);

  // Removing history is confirmed, and only then does the movie leave.
  const survivingRow = page.getByRole("listitem").filter({ hasText: fullTitle });
  await survivingRow.getByRole("button", { name: "Remove from history" }).click();
  const confirm = page.getByRole("group", { name: /^Confirm removing/ });
  await expect(confirm).toContainText("deletes the watched interaction and its rating");
  await confirm.getByRole("button", { name: "Remove from history" }).click();
  await expect(survivingRow.getByText(/No longer watched/)).toBeVisible();

  await page.reload();
  await expect(page.getByRole("listitem").filter({ hasText: fullTitle })).toHaveCount(0);
});

/**
 * The Bundle 7 journey step for Browse and detail, run against the seeded
 * Compose stack rather than a fixture: search the catalog, continue the
 * cursor, open a movie, save it, and come back to the same query, the same
 * loaded window, and the same position.
 *
 * The persona is left as it was found, so the job can run repeatedly.
 */
test("search, cursor continuation, detail, and watchlist survive a round trip", async ({
  page,
}) => {
  const userId = 900000104;
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

  await page.getByRole("button", { name: "Clear the title search" }).click();
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
  // The state committed on detail is reflected on the restored card.
  const restoredCard = page.getByRole("listitem").last();
  const savedToggle = restoredCard.getByRole("button", {
    name: `Remove ${title} from watchlist`,
  });
  await expect(savedToggle).toBeVisible();

  await savedToggle.click();
  await expect(
    restoredCard.getByRole("button", { name: `Add ${title} to watchlist` }),
  ).toBeVisible();
});

/**
 * Quick Picks against the same seeded stack.
 *
 * A fixture can prove the deck behaves; only this can prove the decision
 * reached serving. The sequence is the one ADR 0012 pins: a dismissal excludes
 * the title from the next recommendation set, undo restores its eligibility,
 * and a watched signal moves the cold-start count only once the API has
 * committed it.
 */
test("Quick Picks decisions change what serving returns", async ({ page }) => {
  test.slow();
  const userId = 900000104;
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

  // The journeys above mutate this same persona, so the top pick may already
  // carry a state row the queue cannot see — a recommendation carries no
  // revision, so a first write can only assert zero. That conflict is what the
  // deck is built to correct: it re-reads the canonical record, and the next
  // attempt asserts a revision the server issued. Retrying once exercises that
  // path rather than papering over it.
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

  // Leave the persona as it was found so the journey can be run again.
  const cleanup = await page.evaluate(
    async ([user, movie]) => {
      const csrfToken = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      const response = await fetch(`/api/users/${user}/movies/${movie}/watched`, {
        method: "DELETE",
        headers: { "x-csrf-token": csrfToken, "Idempotency-Key": crypto.randomUUID() },
      });
      return response.status;
    },
    [userId, movieId],
  );
  expect(cleanup).toBe(200);
});
