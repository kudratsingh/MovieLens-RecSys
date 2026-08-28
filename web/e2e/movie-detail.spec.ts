import { expect, test, type Page } from "@playwright/test";

/**
 * The enriched movie detail and the rating interaction, in the isolated
 * preview at 390, 768, and 1440.
 *
 * `browse-detail.spec.ts` already covers this route's place in the Browse
 * journey — opening a card, coming back, the not-found state. What is here is
 * what only a real browser can settle: that no request reaches a third party
 * before the trailer is pressed, that a cast row scrolls instead of pushing the
 * page sideways, and that the rating collapses into a chip that a viewer can
 * get back out of.
 */

const HANDMAIDEN = "The Handmaiden";

/** Full enrichment: backdrop, tagline, runtime, score, six cast, a trailer. */
const ENRICHED = "/ui-preview/movies/101";
/** Enriched, no trailer, no backdrop, and already rated 4.5. */
const NO_TRAILER = "/ui-preview/movies/103";
/** No enriched block at all: the page this route rendered before. */
const PLAIN = "/ui-preview/movies/111";

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("the hero leads with the movie and carries the enriched record", async ({ page }) => {
  await page.goto(ENRICHED);

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(page.getByText("Two women. Two cons. One estate that keeps them both.")).toBeVisible();
  await expect(page.getByText("2016 · 2h 25m · Thriller · Drama")).toBeVisible();
  await expect(page.getByText("8.1 / 10 · 4,812 ratings")).toBeVisible();
  await expect(page.getByText(/Details from TMDB/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Top-billed cast" })).toBeVisible();

  // The two controls the route has always owed the viewer are still here.
  await expect(page.getByRole("button", { name: "Record details" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Back to/ })).toBeVisible();

  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("nothing reaches YouTube until the trailer is pressed", async ({ page }) => {
  const thirdParty: string[] = [];
  page.on("request", (request) => {
    const host = new URL(request.url()).host;
    if (host.includes("youtube") || host.includes("ytimg") || host.includes("google")) {
      thirdParty.push(request.url());
    }
  });

  await page.goto(ENRICHED);
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  // The plate is drawn from artwork this page already holds, so the promise is
  // checkable two ways: no frame, and no request to the embed host either. The
  // usual "lite embed" trick fails the second one, because a YouTube thumbnail
  // is still a YouTube request.
  await expect(page.locator("iframe")).toHaveCount(0);
  expect(thirdParty, "a third-party request was made before the press").toEqual([]);

  const play = page.getByRole("button", { name: /Play trailer/ });
  await expect(play).toBeVisible();
  await play.click();

  const frame = page.locator("iframe");
  await expect(frame).toHaveCount(1);
  await expect(frame).toHaveAttribute(
    "src",
    "https://www.youtube-nocookie.com/embed/T7kfW4trvUM?autoplay=1&rel=0",
  );
  // A frame with no accessible name is an unlabelled region a reader lands in
  // and cannot identify.
  await expect(frame).toHaveAttribute("title", "Official Trailer — The Handmaiden");

  // And it puts itself away, returning focus to the control that opened it.
  await page.getByRole("button", { name: "Close trailer" }).click();
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(play).toBeFocused();
});

test("the trailer control is keyboard operable and closes on Escape", async ({ page }) => {
  await page.goto(ENRICHED);
  const play = page.getByRole("button", { name: /Play trailer/ });
  await play.focus();
  await page.keyboard.press("Enter");

  await expect(page.locator("iframe")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(play).toBeFocused();
});

test("a record with no trailer offers no player and no empty frame", async ({ page }) => {
  await page.goto(NO_TRAILER);

  await expect(page.getByRole("heading", { level: 1, name: "Memories of Murder" })).toBeVisible();
  await expect(page.getByText("8.0 / 10 · 1,937 ratings")).toBeVisible();
  await expect(page.getByRole("button", { name: /Play trailer/ })).toHaveCount(0);
  await expect(page.locator(".movie-trailer")).toHaveCount(0);
  // The backdrop is absent too, so the hero degrades to the poster-left layout.
  await expect(page.locator(".movie-detail-backdrop")).toHaveCount(0);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a record with no enriched block renders the page it always did", async ({ page }) => {
  await page.goto(PLAIN);

  await expect(page.getByRole("heading", { level: 1, name: "Children of Men" })).toBeVisible();
  await expect(page.locator(".movie-detail-backdrop")).toHaveCount(0);
  await expect(page.locator(".movie-trailer")).toHaveCount(0);
  await expect(page.locator(".movie-credits")).toHaveCount(0);
  await expect(page.getByText(/Details from TMDB/)).toHaveCount(0);
  // The parts that never depended on enrichment are untouched.
  await expect(page.getByRole("group", { name: "Your rating" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Record details" })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("the cast row scrolls itself rather than the page", async ({ page }) => {
  await page.goto(ENRICHED);
  const cast = page.getByRole("list", { name: "Top-billed cast" });
  await expect(cast).toBeVisible();

  // Focusable, because a scrollable region no key can reach is a section a
  // keyboard viewer simply cannot read.
  await expect(cast).toHaveAttribute("tabindex", "0");
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a rating commits, collapses into a chip, and reopens pre-filled", async ({
  page,
}, testInfo) => {
  await page.goto(ENRICHED);
  const panel = page.getByRole("group", { name: "Your rating" });
  await expect(panel).toBeVisible();

  const fourStars = panel.getByRole("button", { name: `4 stars for ${HANDMAIDEN}` });
  // The star is a real target on the mobile profile, not a 20px glyph.
  const box = await fourStars.boundingBox();
  expect(box?.width ?? 0, "star target width").toBeGreaterThanOrEqual(44);
  expect(box?.height ?? 0, "star target height").toBeGreaterThanOrEqual(44);
  if (testInfo.project.name === "desktop-1440") {
    // The glyph itself grows on a pointer-first viewport; the target does not
    // shrink to match on touch.
    const glyph = await fourStars.locator("svg").boundingBox();
    expect(glyph?.width ?? 0).toBeGreaterThanOrEqual(30);
  }

  await fourStars.click();

  // The preview writes nothing, so the chip is the acknowledgement itself.
  await expect(page.getByText("You rated 4/5")).toBeVisible();
  await expect(
    panel.getByRole("button", { name: `4 stars for ${HANDMAIDEN}` }),
  ).toHaveCount(0);

  const change = page.getByRole("button", { name: `Change rating for ${HANDMAIDEN}` });
  await expect(change).toBeVisible();
  await change.click();

  const reopened = panel.getByRole("button", { name: `4 stars for ${HANDMAIDEN}` });
  await expect(reopened).toHaveAttribute("aria-pressed", "true");
  // `Clear rating` lives one deliberate step away from a recorded value.
  await expect(panel.getByRole("button", { name: "Clear rating" })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("an already rated movie opens collapsed", async ({ page }) => {
  await page.goto(NO_TRAILER);

  // Half-star values come from the Library's editor and have to read back here.
  await expect(page.getByText("You rated 4.5/5")).toBeVisible();
  await expect(page.getByRole("button", { name: /stars for/ })).toHaveCount(0);
});
