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
});
