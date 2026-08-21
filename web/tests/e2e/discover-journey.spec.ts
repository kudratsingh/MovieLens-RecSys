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

async function removeWatched(page: Page, movieId: number) {
  await page.evaluate(
    async ({ userId, movie }) => {
      const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${userId}/movies/${movie}/watched`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
          "x-csrf-token": csrf,
        },
      });
    },
    { userId: PERSONA_ID, movie: movieId },
  );
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
    await page.getByRole("button", { name: "Mark watched" }).first().click();

    await expect(page.getByText(/Refreshing recommendations/)).toBeVisible();
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
    await expect(page.getByText(/Recommendations refreshed/)).toBeVisible();
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

test("Discover is not reachable without a session", async ({ page }) => {
  await page.goto(DISCOVER);

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Continue with Keycloak" })).toBeVisible();
});
