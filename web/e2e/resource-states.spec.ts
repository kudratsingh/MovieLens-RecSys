import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The shared empty and failure blocks, at every width.
 *
 * These are one component used by five routes, so their layout is checked once
 * here rather than being re-asserted per route. The reachable instances are
 * Discover's empty ranked set and Browse's failed catalog read, both of which
 * the fixture harness can produce without a session or a write.
 */

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

async function trackCount(block: Locator): Promise<number> {
  return block.evaluate(
    (node) => getComputedStyle(node).gridTemplateColumns.split(" ").filter(Boolean).length,
  );
}

test("the empty ranked set keeps its ways out in one row, in the offered order", async ({
  page,
}, testInfo) => {
  await page.goto("/discover?demo=empty");

  // The route has two empty regions when nothing is ranked — the recommendation
  // set and the watch history below it. This one is the ranked set.
  const block = page
    .locator(".resource-empty")
    .filter({ hasText: "No recommendations right now" });
  await expect(block).toBeVisible();
  const actions = block.locator(".resource-state-actions");

  // Both ways out belong to the action row. Auto-placing them into the block's
  // own grid put the first one in the mark's narrow column and the second
  // beside it, so the primary action read second and sat about 40px wide.
  await expect(actions.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
  await expect(actions.getByRole("link", { name: /Quick picks/ })).toBeVisible();

  const primary = await actions.getByRole("link").first().boundingBox();
  const secondary = await actions.getByRole("link").nth(1).boundingBox();
  expect(primary).not.toBeNull();
  expect(secondary).not.toBeNull();
  // Reading order follows DOM order whether the row fits on one line or wraps.
  const sameRow = Math.abs(primary!.y - secondary!.y) < primary!.height;
  expect(sameRow ? primary!.x < secondary!.x : primary!.y < secondary!.y).toBe(true);

  // A phone gets one column: the mark above the copy, not beside it.
  const isPhone = testInfo.project.name === "mobile-390";
  expect(await trackCount(block)).toBe(isPhone ? 1 : 2);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed read stacks the same way and names itself in plain words", async ({
  page,
}, testInfo) => {
  await page.goto("/ui-preview/browse?fail=catalog");

  const block = page.locator(".resource-error");
  await expect(block).toBeVisible();
  // The accessible name is the headline a sighted viewer reads, not the
  // transport status the logs carry.
  await expect(block).toHaveAttribute("aria-label", "Catalog could not be loaded");
  await expect(
    block.locator(".resource-state-actions").getByRole("button", { name: "Try again" }),
  ).toBeVisible();

  expect(await trackCount(block)).toBe(testInfo.project.name === "mobile-390" ? 1 : 2);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
});
