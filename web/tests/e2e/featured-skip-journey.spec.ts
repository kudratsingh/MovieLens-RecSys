import { expect, test, type Page } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";

/**
 * The service-backed `Featured picks` journey.
 *
 * Everything here needs a real revision counter and a real committed state, so
 * none of it is reachable from the fixture harness: a title has to be
 * watchlisted through the UI, survive a reload as the API's own per-item state,
 * and then be held back by a preference the API stored. The fixture specs cover
 * the shapes; this covers that they are the shapes the stack actually produces.
 *
 * This journey shares **Drama Fan (900000102)** with `discover-journey.spec.ts`
 * — the same persona that file owns, because the setting under test belongs to
 * Discover and Discover's persona is Drama Fan. The run is serialized
 * (`workers: 1` in `playwright.config.ts`), so the two never overlap, and this
 * file restores both halves of what it changes in a `finally`: the preference
 * back to its default, and the watchlist entry removed. `persona-hygiene.spec.ts`
 * runs last and fails the run if either is left behind.
 */

const PERSONA_ID = 900000102;
const DISCOVER = `/discover?userId=${PERSONA_ID}`;

async function featuredMovieId(page: Page): Promise<number> {
  const href = await page
    .getByRole("link", { name: /Open movie/ })
    .getAttribute("href");
  const match = /\/movies\/(\d+)/.exec(href ?? "");
  expect(match, `expected a movie link, got ${href}`).not.toBeNull();
  return Number(match?.[1]);
}

/** Undoes the watchlist entry whatever state the run left it in. */
async function clearWatchlist(page: Page, movieId: number) {
  await page.evaluate(
    async ({ userId, movie }) => {
      const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      await fetch(`/api/users/${userId}/movies/${movie}/watchlist`, {
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

/** Puts the preference back to the documented default, whatever it is now. */
async function restoreFeaturedPreference(page: Page) {
  await page.evaluate(
    async ({ userId }) => {
      const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
        .then((response) => response.json())
        .then((body: { csrfToken: string }) => body.csrfToken);
      // No `expected_revision`: this is a restore, and it has to succeed
      // against whatever revision the run happens to have left behind.
      await fetch(`/api/users/${userId}/preferences`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ feature_watchlisted_titles: true }),
      });
    },
    { userId: PERSONA_ID },
  );
}

test("a watchlisted title can be skipped, then held back from the featured slot", async ({
  page,
}) => {
  await signInThroughKeycloak(page);
  await page.goto(DISCOVER);
  await restoreFeaturedPreference(page);
  await page.reload();

  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toBeVisible();
  const savedTitle = (await heading.textContent())?.trim() ?? "";
  const savedId = await featuredMovieId(page);
  expect(savedTitle).not.toBe("");

  try {
    // 1. Save the featured title. Watchlist is organizational, so the API keeps
    //    returning it — which is the whole reason the featured slot needed a
    //    way past it.
    await page.getByRole("button", { name: "Watchlist" }).first().click();
    await expect(page.getByText(/saved to watchlist/)).toBeVisible();

    // 2. After a reload the route knows the title is saved, because the
    //    recommendation response carries the state the API committed — not
    //    because anything was cached in the tab.
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: savedTitle })).toBeVisible();
    const featured = page.getByRole("region", { name: savedTitle });
    await expect(featured.getByText("On your watchlist")).toBeVisible();
    const skip = featured.getByRole("button", { name: "Skip" });
    await expect(skip).toBeVisible();

    // 3. Skipping advances the slot and records nothing: the announcement says
    //    the title is still on the watchlist, and it still is.
    await skip.click();
    await expect(
      page.getByText(`Skipped ${savedTitle} — still on your watchlist.`),
    ).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).not.toHaveText(savedTitle);
    const rail = page.getByRole("region", { name: "Next in this ranked set" });
    await expect(rail.getByRole("link", { name: new RegExp(escape(savedTitle)) })).toBeVisible();

    // 4. Turn featuring off through the permanent setting, and read back what
    //    the API stored rather than what the button was clicked to.
    await page.getByRole("button", { name: "Feature watchlisted titles" }).click();
    await expect(
      page.getByText("Watchlisted titles will not be featured. They stay in the ranked list below."),
    ).toBeVisible();

    // 5. It survives the reload, and the title keeps its rail card.
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).not.toHaveText(savedTitle);
    await expect(
      page.getByRole("button", { name: "Feature watchlisted titles" }),
    ).toHaveAttribute("aria-pressed", "false");
    const heldRail = page.getByRole("region", { name: "Next in this ranked set" });
    const heldCard = heldRail.getByRole("link", { name: new RegExp(escape(savedTitle)) });
    await expect(heldCard).toBeVisible();
    // Held back from the slot, not removed from the set — and still reported as
    // watchlisted, because nothing about the preference touched the state.
    await expect(heldRail.getByRole("button", { name: "In watchlist" }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Skip" })).toHaveCount(0);
  } finally {
    await restoreFeaturedPreference(page);
    await clearWatchlist(page, savedId);
  }

  // The persona is handed on as it was found.
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Feature watchlisted titles" }),
  ).toHaveAttribute("aria-pressed", "true");
});

/** Escapes a movie title for use inside an accessible-name regular expression. */
function escape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
