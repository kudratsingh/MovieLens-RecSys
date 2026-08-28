import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";

/**
 * The Seen tab against the seeded Compose stack: real Keycloak, real FastAPI,
 * real RLS, and a rating that has to survive a round trip.
 *
 * **Persona: Action Fan (900000101)**, per the ownership table in
 * `browser-auth.spec.ts`. This is the persona that owns Library's rating and
 * watched-history writes, and the one write here is a star value on a title
 * that is already in its seeded history — put back, exactly, before the spec
 * ends. Nothing here touches Cold Start (900000104).
 *
 * What the fixture harness cannot show is the part this spec exists for: the
 * spotlight's rating is committed by the API before the chip collapses, and the
 * row for the same movie is reconciled from that committed response rather than
 * from a refetch. Two surfaces, one write path, one answer.
 */

const USER_ID = 900000101;

/** The stored value, read from the row rather than from the control. */
async function ratingOf(page: Page, movieId: number): Promise<number | null> {
  const state = await page
    .getByRole("listitem")
    .filter({ has: page.locator(`#library-movie-${movieId}`) })
    .locator(".library-row-state")
    .innerText();
  const match = /Rated (\d+(?:\.\d+)?) of 5/.exec(state);
  return match ? Number(match[1]) : null;
}

/**
 * Puts the star back exactly as it was found.
 *
 * Through the BFF rather than through the control, and deliberately: the stored
 * constraint is half-star, the spotlight's row is whole stars, and a seeded
 * 4.5 is a value the control cannot express. Restoring what was actually there
 * matters more than restoring it by hand.
 */
async function restoreRating(page: Page, movieId: number, rating: number | null) {
  await page
    .evaluate(
      async ({ userId, movie, value }) => {
        const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
          .then((response) => response.json())
          .then((body: { csrfToken: string }) => body.csrfToken);
        const headers = {
          "content-type": "application/json",
          "x-csrf-token": csrf,
          "Idempotency-Key": crypto.randomUUID(),
        };
        await fetch(`/api/users/${userId}/movies/${movie}/rating`, {
          method: value === null ? "DELETE" : "PUT",
          headers,
          body: value === null ? undefined : JSON.stringify({ rating: value }),
        });
      },
      { userId: USER_ID, movie: movieId, value: rating },
    )
    .catch(() => {});
}

test("a rating changed in the Seen spotlight lands on the row it belongs to", async ({
  page,
}) => {
  test.slow();
  await signInThroughKeycloak(page);

  await page.goto(`/library?userId=${USER_ID}&tab=history`);
  await expect(page.getByRole("tab", { name: /^Seen/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  const spotlight = page.getByRole("region", { name: "Seen spotlight" });
  await expect(spotlight).toBeVisible();
  const title = (await spotlight.getByRole("heading", { level: 3 }).innerText()).trim();
  // The spotlight is the list's first row, so the movie it features is the one
  // whose row has to agree with it.
  const movieId = Number(
    await page
      .getByRole("list", { name: "Seen movies" })
      .getByRole("listitem")
      .first()
      .locator("[id^='library-movie-']")
      .getAttribute("id")
      .then((id) => id?.replace("library-movie-", "")),
  );
  expect(movieId).toBeGreaterThan(0);

  const original = await ratingOf(page, movieId);
  // A different whole star than whatever is stored, so the write is a change
  // rather than a re-confirmation of the same value.
  const target = original !== null && Math.round(original) === 4 ? 3 : 4;
  const rating = spotlight.getByRole("group", { name: "Your rating" });

  try {
    if (original !== null) {
      await rating.getByRole("button", { name: /^Change rating for / }).click();
    }
    await rating.getByRole("button", { name: `${target} stars for ${title}` }).click();

    // The chip collapses only after the API answered, so it *is* the evidence
    // that a revision was written.
    await expect(rating.getByText(`You rated ${target}/5`)).toBeVisible();
    await expect(page.getByText(/Rating saved for .+ library/)).toBeAttached();
    await expect
      .poll(() => ratingOf(page, movieId), { message: "the row follows the spotlight" })
      .toBe(target);

    // And the record survives the round trip rather than only the render.
    await page.reload();
    await expect(
      page
        .getByRole("region", { name: "Seen spotlight" })
        .getByText(`You rated ${target}/5`),
    ).toBeVisible();
    expect(await ratingOf(page, movieId)).toBe(target);
  } finally {
    await restoreRating(page, movieId, original);
  }

  await page.reload();
  expect(await ratingOf(page, movieId)).toBe(original);
});
