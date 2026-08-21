import { expect, test, type Page } from "@playwright/test";

/**
 * Quick Picks in a real browser, against the recorded queue.
 *
 * The point of these is parity: a decision made with a button, a key, and a
 * swipe has to leave the page in the same state. Anything a fixture cannot
 * prove — that the exclusion actually reached serving — belongs to the
 * service-backed journey in `tests/e2e/browser-auth.spec.ts`.
 */

const ROUTE = "/ui-preview/quick-picks";

/** Waits for hydration; before it, the controls are inert markup. */
async function openQuickPicks(page: Page, url: string = ROUTE) {
  await page.goto(url);
  await expect(page.locator(".quick-picks-page")).toHaveAttribute(
    "data-interactive",
    "true",
  );
}

function status(page: Page) {
  return page.getByRole("status");
}

async function currentTitle(page: Page) {
  return page.getByRole("heading", { level: 1 }).textContent();
}

async function swipeLeft(page: Page) {
  const poster = page.locator(".quick-pick-poster");
  const box = await poster.boundingBox();
  if (!box) throw new Error("The swipe surface has no box");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width * 0.8, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.1, y, { steps: 8 });
  await page.mouse.up();
}

test("a button, a key, and a swipe reach the same canonical outcome", async ({ page }) => {
  const outcomes: { title: string | null; status: string | null }[] = [];

  for (const decide of [
    async () => page.getByRole("button", { name: /Not for me/ }).click(),
    async () => page.keyboard.press("j"),
    async () => swipeLeft(page),
  ]) {
    await openQuickPicks(page);
    await expect(page.getByRole("heading", { level: 1, name: "Perfect Blue" })).toBeVisible();
    await decide();
    await expect(
      page.getByRole("heading", { level: 1, name: "In the Mood for Love" }),
    ).toBeVisible();
    outcomes.push({ title: await currentTitle(page), status: await status(page).textContent() });
  }

  expect(outcomes[1]).toEqual(outcomes[0]);
  expect(outcomes[2]).toEqual(outcomes[0]);
  expect(outcomes[0].status).toContain("Perfect Blue: not for me saved.");
});

test("undo returns the dismissed title to the front of the queue", async ({ page }) => {
  await openQuickPicks(page);
  await page.getByRole("button", { name: /Not for me/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "In the Mood for Love" })).toBeVisible();

  await page.getByRole("button", { name: /Undo not for me for Perfect Blue/ }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Perfect Blue" })).toBeVisible();
  await expect(status(page)).toContainText("back in the queue");
});

test("watchlist leaves the signal count alone and watched moves it", async ({ page }) => {
  await openQuickPicks(page);
  await expect(page.getByText("2 of 5 positive watched signals")).toBeVisible();

  await page.getByRole("button", { name: /^Watchlist/ }).click();
  await expect(status(page)).toContainText("watchlist saved");
  await expect(page.getByText("2 of 5 positive watched signals")).toBeVisible();

  await page.getByRole("button", { name: /^Watched/ }).click();
  await expect(page.getByText("3 of 5 positive watched signals")).toBeVisible();
});

test("a failed decision keeps the card and restores the controls", async ({ page }) => {
  await openQuickPicks(page, `${ROUTE}?fail=commit`);
  const notForMe = page.getByRole("button", { name: /Not for me/ });
  await notForMe.click();

  await expect(page.locator(".quick-picks-error")).toHaveText(
    "The recommendation API could not save that decision.",
  );
  await expect(page.getByRole("heading", { level: 1, name: "Perfect Blue" })).toBeVisible();
  await expect(notForMe).toBeEnabled();
  await expect(notForMe).toBeFocused();
  await expect(status(page)).toContainText("The card is unchanged.");
});

test("a failed queue read offers a retry instead of an empty deck", async ({ page }) => {
  await openQuickPicks(page, `${ROUTE}?fail=queue`);

  await expect(page.getByRole("alert", { name: /Quick picks/ })).toContainText(
    "The recommendation API did not answer in time.",
  );
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("the queue exhausts into Browse and restart paths", async ({ page }) => {
  await openQuickPicks(page);
  const notForMe = page.getByRole("button", { name: /Not for me/ });

  // Six of the seven recorded picks, each confirmed before the next decision.
  for (let index = 0; index < 6; index += 1) {
    const title = await currentTitle(page);
    await notForMe.click();
    await expect(status(page)).toContainText(`${title}: not for me saved.`);
  }
  await notForMe.click();

  await expect(
    page.getByRole("heading", { name: "That is every pick we have for now" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Get more picks" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
});

test("learned copy appears only when the policy reports learned serving", async ({ page }) => {
  await openQuickPicks(page);
  await expect(page.getByRole("heading", { name: "Popular while we learn" })).toBeVisible();

  await openQuickPicks(page, `${ROUTE}?policy=learned`);
  await expect(
    page.getByRole("heading", { name: "Picked from your watched history" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Why this?" }).click();
  await expect(page.getByText(/Retrieved as similar to Memories of Murder/)).toBeVisible();
});

test("every decision control clears the 44px mobile target size", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile target sizing");
  await openQuickPicks(page);

  const controls = page.locator(
    ".quick-pick-actions button, .movie-rating-stars button, .quick-picks-header a",
  );
  const count = await controls.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    const box = await controls.nth(index).boundingBox();
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
});

test("reduced motion drops the card fling without losing the gesture", async ({ page }) => {
  await openQuickPicks(page);
  await expect(page.locator(".quick-pick-poster")).toHaveAttribute("data-motion", "fling");

  // Emulated after load so this also proves the deck reacts to the preference
  // changing under it, not only to its value at mount.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".quick-pick-poster")).toHaveAttribute("data-motion", "none");

  await swipeLeft(page);

  await expect(
    page.getByRole("heading", { level: 1, name: "In the Mood for Love" }),
  ).toBeVisible();
});
