import { expect, test } from "@playwright/test";

test("real Keycloak PKCE session reaches the role-gated demo API and logs out", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();

  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();

  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
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
