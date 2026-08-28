import { expect, test, type Page } from "@playwright/test";

import { posterInitials } from "@/lib/movie-types";

/**
 * What every poster-bearing surface does when the artwork does not arrive.
 *
 * This runs in a browser rather than jsdom because the failure it models is a
 * real network one: the fixture catalog points at paths that can rot, and three
 * of them already had. Aborting the image requests reproduces that exactly,
 * which no component test can — jsdom never fetches the poster at all.
 *
 * The three viewports come from the projects in `playwright.ui.config.ts`, so
 * each assertion below is made at 390, 768, and 1440.
 */

/** A dead poster path, modelled the way the catalog produces one: a 404. */
async function breakPosters(page: Page) {
  await page.route("**/_next/image**", (route) => route.abort());
  await page.route("**/posters/**", (route) => route.abort());
}

/**
 * Posters below the fold are lazy, so at 390 most of the grid never requests an
 * image at all. Walking to the bottom is what turns "not loaded yet" into the
 * failure this spec is about.
 */
async function revealEveryPoster(page: Page) {
  await page.evaluate(async () => {
    const step = window.innerHeight;
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((resolve) => setTimeout(resolve, 80));
    }
    window.scrollTo(0, 0);
  });
}

/** What each frame is actually showing, from the browser's own view of it. */
async function frameStates(page: Page, cardSelector: string) {
  return page.$$eval(`${cardSelector} .poster-frame`, (frames) =>
    frames.map((frame) => {
      const image = frame.querySelector("img");
      return {
        marked: Boolean(frame.querySelector('[data-testid="poster-fallback"]')),
        // `complete` with no intrinsic width is the broken-image state, and the
        // only one this product must never render.
        broken: Boolean(image && image.complete && image.naturalWidth === 0),
      };
    }),
  );
}

/** The mark, and the title the surface shows beside it, for every card. */
async function marksWithTitles(page: Page, cardSelector: string) {
  return page.$$eval(
    cardSelector,
    (cards) =>
      cards.map((card) => ({
        mark: card.querySelector('[data-testid="poster-fallback"] span')?.textContent ?? null,
        title: card.querySelector(".poster-title, h1")?.textContent?.trim() ?? "",
      })),
  );
}

test("a poster grid names every gap instead of leaving empty frames", async ({ page }) => {
  await breakPosters(page);
  await page.goto("/ui-preview/browse");
  await expect(page.getByRole("list", { name: "Browse results" })).toBeVisible();
  await revealEveryPoster(page);

  const frames = await frameStates(page, ".catalog-cell");
  expect(frames.length).toBeGreaterThan(0);
  // Not one broken-image icon anywhere: that is the state Quick Picks used to
  // ship and every other surface avoided. A frame that has not requested its
  // poster yet is neither — it is simply still lazy.
  expect(frames.filter((frame) => frame.broken)).toEqual([]);
  expect(frames.some((frame) => frame.marked)).toBe(true);
});

test("the mark is derived from the title the card displays", async ({ page }) => {
  await breakPosters(page);
  await page.goto("/ui-preview/browse");
  await expect(page.locator(".catalog-cell").first()).toBeVisible();
  await revealEveryPoster(page);

  const cards = (await marksWithTitles(page, ".catalog-cell")).filter((card) => card.mark);
  expect(cards.length).toBeGreaterThan(0);
  for (const { mark, title } of cards) {
    expect(mark).toBe(posterInitials(title));
    // A bracket or a lowercase glyph means the raw MovieLens title reached the
    // mark: "Babe (1995)" became `B(` before the rule was shared.
    expect(mark).not.toMatch(/[()[\]{}]/);
    expect(mark).toBe(mark?.toUpperCase() ?? null);
  }
});

test("Discover's featured slot and rails fall back to the same mark", async ({ page }) => {
  await breakPosters(page);
  await page.goto("/ui-preview/discover");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  await expect(page.locator('.featured-poster [data-testid="poster-fallback"]')).toBeVisible();
  const railCards = await marksWithTitles(page, ".rail-item");
  expect(railCards.length).toBeGreaterThan(0);
  for (const { mark, title } of railCards) {
    expect(mark).toBe(posterInitials(title));
  }
});

test("Quick Picks names the gap and stays decidable", async ({ page }) => {
  await breakPosters(page);
  await page.goto("/ui-preview/quick-picks");
  await expect(page.locator(".quick-picks-page")).toHaveAttribute("data-interactive", "true");

  const fallback = page.locator('.quick-pick-poster [data-testid="poster-fallback"]');
  await expect(fallback).toBeVisible();
  await expect(fallback).toContainText("Artwork unavailable");
  await expect(fallback).toContainText("PB");

  // The decision is what this route is for; a missing poster must not stop it.
  const before = await page.getByRole("heading", { level: 1 }).textContent();
  await page.getByRole("button", { name: /Not for me/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).not.toHaveText(before ?? "");
  // The next card's artwork is not tarred with the previous card's failure.
  await expect(page.locator('.quick-pick-poster [data-testid="poster-fallback"]')).toBeVisible();
});

test("movie detail shows the mark at poster scale", async ({ page }) => {
  await breakPosters(page);
  await page.goto("/ui-preview/movies/101");

  const fallback = page.locator('.detail-poster [data-testid="poster-fallback"]');
  await expect(fallback).toBeVisible();
  const title = await page.getByRole("heading", { level: 1 }).textContent();
  await expect(fallback.locator("span").first()).toHaveText(posterInitials(title?.trim() ?? ""));
});

test("a poster card is one link, and the rail track is a named group", async ({ page }) => {
  await page.goto("/ui-preview/discover");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // Two anchors to the same movie cost a keyboard viewer a stop per card.
  const linksPerCard = await page.$$eval(".rail-item .poster-card", (cards) =>
    cards.map((card) => card.querySelectorAll("a").length),
  );
  expect(linksPerCard.length).toBeGreaterThan(0);
  for (const count of linksPerCard) expect(count).toBe(1);

  await expect(page.getByRole("group", { name: /movies$/ }).first()).toBeVisible();

  // The featured slot prints its own title beside the poster and hides the
  // card's caption; the card must not start showing a second one.
  await expect(page.locator(".featured-poster .poster-title")).toBeHidden();
});
