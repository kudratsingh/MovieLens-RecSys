import { expect, test } from "@playwright/test";

/**
 * Responsive coverage for the live `/discover` route, driven through the
 * isolated recorded-scenario harness so every state is reachable without a
 * backend. Each Playwright project runs the file at 390, 768, and 1440 widths.
 */

const HANDMAIDEN = "The Handmaiden";

async function pageOverflow(page: import("@playwright/test").Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("the primary movie is the first read and the policy label follows the response", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(
    page.getByRole("region", { name: HANDMAIDEN }).getByText("Ranked by the learned model"),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: /Primary/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the whole catalog" })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a fallback response says so and never borrows learned copy", async ({ page }) => {
  await page.goto("/discover?demo=fallback");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(page.getByText("Popular while we learn").first()).toBeVisible();
  await expect(page.getByText("Ranked by the learned model")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("technical evidence is two deliberate actions away and does not block the movie", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("LightGBM rank over learned item-item candidates")).toBeVisible();
  await expect(page.getByTestId("technical-evidence")).toHaveCount(0);

  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(page.getByTestId("technical-evidence")).toBeVisible();
  await expect(drawer.getByText("item-item-v3", { exact: true })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
});

test("a failed history region leaves the movie decision usable", async ({ page }) => {
  await page.goto("/discover?demo=history-error");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(page.getByRole("button", { name: "Why this?" })).toBeVisible();
  await expect(page.getByRole("alert", { name: /Watch history could not be loaded/ })).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed recommendation region leaves watch history readable", async ({ page }) => {
  await page.goto("/discover?demo=recommendations-error");

  await expect(page.getByRole("alert", { name: /Recommendations could not be loaded/ })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /has watched/ })).toBeVisible();

  // A history row is a link with artwork, not a dead line of text: the read
  // model carries `poster_url` and `release_year`, so the title prints once
  // without its year and the row opens the movie.
  const heat = page.getByRole("link", { name: /^Heat/ });
  await expect(heat).toBeVisible();
  await expect(heat).toHaveAttribute("href", /^\/movies\/6\?/);
  await expect(heat.locator("img")).toBeVisible();
  await expect(page.getByText("Heat (1995)")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed evidence region still lets the reason and policy be read", async ({ page }) => {
  await page.goto("/discover?demo=evidence-error");

  await page.getByRole("button", { name: "Why this?" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("LightGBM rank over learned item-item candidates")).toBeVisible();

  await drawer.getByRole("button", { name: "Show prediction audit" }).click();
  await expect(drawer.getByRole("alert", { name: /Prediction audits/ })).toBeVisible();
  await expect(drawer.getByRole("alert", { name: /Online features/ })).toBeVisible();
});

test("an empty ranked set offers a way forward instead of an error", async ({ page }) => {
  await page.goto("/discover?demo=empty");

  await expect(page.getByText("No recommendations right now")).toBeVisible();
  await expect(page.getByRole("link", { name: "Browse the catalog" }).first()).toBeVisible();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("loading announces itself without claiming a failure", async ({ page }) => {
  await page.goto("/discover?demo=loading");

  // Two nodes can carry this during streaming: the route-level `loading.tsx`
  // and the region skeleton underneath it. Either one is the announcement this
  // test is about, so match the first rather than assert there is only one.
  await expect(page.getByText("Loading movies").first()).toBeAttached();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("an expired session offers a reauthentication path", async ({ page }) => {
  await page.goto("/discover?demo=auth-expired");

  // Both regions fail the same way, so the name is not unique on the page.
  await expect(
    page.getByRole("alert", { name: /Your session expired/ }).first(),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in again" }).first()).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a failed poster keeps the movie identity and does not move the layout", async ({
  page,
}) => {
  await page.goto("/discover?demo=poster-failure");

  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
  await expect(
    page.getByRole("region", { name: HANDMAIDEN }).getByTestId("poster-fallback"),
  ).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a live read with no API reachable fails visibly instead of showing recorded data", async ({
  page,
}) => {
  // No `demo` parameter, so the route reads live even inside the isolated mode.
  await page.goto("/discover");

  // The isolated harness carries no session, so a live read fails at the auth
  // boundary — which is the point: it fails visibly rather than quietly
  // answering with recorded data.
  await expect(
    page.getByRole("alert", { name: /Your session expired/ }).first(),
  ).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toHaveCount(0);
  await expect(page.getByText("Recorded contract fixtures")).toHaveCount(0);
});

test("mobile keeps the primary decision and the bottom navigation reachable", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile shell assertion");
  await page.goto("/discover?demo=learned");

  const bottom = page.getByRole("navigation", { name: "Primary mobile" });
  await expect(bottom).toBeVisible();
  await expect(bottom.getByRole("link", { name: "Browse" })).toHaveAttribute(
    "href",
    "/browse?user=900000101",
  );
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();
});

/**
 * Where a control's label actually starts drawing.
 *
 * A stretched grid item's *box* always begins at its column edge, so a bounding
 * box cannot see this defect at all: what was indented was the centred text
 * inside a button with no visible border. The text node's own rectangle is what
 * a reader sees.
 */
async function labelLeft(locator: import("@playwright/test").Locator) {
  return locator.evaluate((element) => {
    const label = Array.from(element.childNodes).find(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
    );
    if (!label) return null;
    const range = document.createRange();
    range.selectNodeContents(label);
    return range.getBoundingClientRect().left;
  });
}

test("the three decisions line up on the mobile action strip", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile layout assertion");
  await page.goto("/discover?demo=learned");

  const actions = page.locator(".featured-actions");
  await expect(actions).toBeVisible();

  const watchlist = actions.getByRole("button", { name: "Watchlist" });
  const watched = actions.getByRole("button", { name: "Mark watched" });
  const dismiss = actions.getByRole("button", { name: "Not for me" });

  const [watchlistBox, watchedBox, dismissBox] = await Promise.all([
    watchlist.boundingBox(),
    watched.boundingBox(),
    dismiss.boundingBox(),
  ]);

  // The two bordered buttons share a row; the quiet one starts the next.
  expect(watchedBox?.y ?? 0).toBeCloseTo(watchlistBox?.y ?? 0, 0);
  expect(dismissBox?.y ?? 0).toBeGreaterThan(watchlistBox?.y ?? 0);

  // `Not for me` is the one quiet control in the strip, so it has no visible
  // box to align to — only its label. Stretched across its grid column like the
  // two bordered buttons above it, that centred label started ~52px right of
  // their left edge (~43px right of their own labels) and read as an indent
  // rather than as the third action. It now starts where their borders do,
  // within a pixel or two of border width and sub-pixel rounding.
  const dismissLabelLeft = await labelLeft(dismiss);
  expect(dismissLabelLeft).not.toBeNull();
  expect(Math.abs((dismissLabelLeft ?? 0) - (watchlistBox?.x ?? 0))).toBeLessThanOrEqual(2);
});

test("Quick Picks is reachable from Discover without a fourth navigation slot", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");

  const entry = page.getByRole("link", { name: /Quick picks/ });
  await expect(entry).toBeVisible();
  await expect(entry).toHaveAttribute("href", "/quick-picks?user=900000101");
  // The route contract keeps the shell at three primary routes.
  await expect(
    page.getByRole("navigation", { name: /Primary/ }).first().getByRole("link", {
      name: /Quick/,
    }),
  ).toHaveCount(0);
});

test("the Quick Picks entry stays a thumb-sized target", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile touch-target assertion");
  await page.goto("/discover?demo=learned");

  const box = await page.getByRole("link", { name: /Quick picks/ }).boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
});

test("the rail starts after the featured movie and never repeats it", async ({ page }) => {
  await page.goto("/discover?demo=learned");

  const featured = page.getByRole("heading", { level: 1 });
  const title = (await featured.textContent()) ?? "";
  expect(title.length).toBeGreaterThan(0);

  // The featured slot is a queue position and the rail is what is still ahead
  // of it, so no title is ever both the decision and part of what follows it.
  const rail = page.getByRole("region", { name: /ranked set/ });
  await expect(rail.getByText(title, { exact: true })).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("the evidence drawer answers in a sentence before it answers in a table", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  const opening = drawer.getByText(/^Picked from/);
  await expect(opening).toBeVisible();
  // Plain language first; the identifiers keep their place under a heading.
  await expect(opening).not.toContainText("lightgbm");
  await expect(drawer.getByRole("heading", { name: "Model evidence" })).toBeVisible();
  await expect(drawer.getByText("item-item-lightgbm")).toBeVisible();
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

test("a fallback drawer says what it is without borrowing learned phrasing", async ({
  page,
}) => {
  await page.goto("/discover?demo=fallback");
  await page.getByRole("button", { name: "Why this?" }).click();

  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText(/watched most across this tenant/)).toBeVisible();
  await expect(drawer.getByText(/^Picked from/)).toHaveCount(0);
  expect(await pageOverflow(page)).toBeLessThanOrEqual(1);
});

/**
 * How many lines an element's own label actually occupies.
 *
 * A bounding box cannot answer this: a clamped title and a wrapped pill label
 * both leave the element's box exactly where the layout put it. Only the text
 * is measured, and only its distinct top edges count — a pill holds an icon
 * beside its label, and two rectangles on one line are still one line.
 */
async function textLines(locator: import("@playwright/test").Locator) {
  return locator.evaluate((element) => {
    const text = [...element.childNodes].filter(
      (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
    );
    if (text.length === 0) return 0;
    const range = document.createRange();
    range.setStartBefore(text[0]);
    range.setEndAfter(text[text.length - 1]);
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
  });
}

test("every rail card's controls sit on one baseline, whatever the title does", async ({
  page,
}) => {
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const cards = page.locator(".rail-item");
  const count = await cards.count();
  expect(count).toBeGreaterThan(2);

  const rows: { title: string; lines: number; y: number }[] = [];
  for (let index = 0; index < count; index += 1) {
    const card = cards.nth(index);
    const title = card.locator(".poster-title");
    const box = await card.locator(".movie-state-row").boundingBox();
    rows.push({
      title: (await title.textContent()) ?? "",
      lines: await textLines(title),
      y: box?.y ?? -1,
    });
  }

  // The test only means something if the rail is actually mixed: a set of
  // uniformly short titles would pass a ragged layout too.
  expect(rows.some((row) => row.lines === 1)).toBe(true);
  expect(rows.some((row) => row.lines === 2)).toBe(true);

  // Reserving two title lines and pinning the control row to the bottom of the
  // card puts every card's controls on the same line, so `Toy Story` and
  // `To Kill a Mockingbird` no longer offer their decisions at two heights.
  const tops = rows.map((row) => Math.round(row.y));
  expect(new Set(tops).size, JSON.stringify(rows)).toBe(1);
});

test("no rail control wraps its label onto a second line", async ({ page }) => {
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const controls = page.locator(".rail-item .movie-state-row button");
  const count = await controls.count();
  expect(count).toBeGreaterThan(0);

  const wrapped: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const control = controls.nth(index);
    // `Mark watched` wrapped inside its own pill at rail width, which is what
    // pushed one card's third action a line below its neighbour's.
    if ((await textLines(control)) > 1) {
      wrapped.push((await control.getAttribute("aria-label")) ?? (await control.innerText()));
    }
    // A label kept on one line by clipping is not kept on one line.
    const clipped = await control.evaluate((node) => node.scrollWidth - node.clientWidth);
    expect(clipped, `${await control.innerText()} is clipped by ${clipped}px`).toBeLessThanOrEqual(0);
  }
  expect(wrapped.join(", ")).toBe("");
});

test("a rail card spends most of its height on the poster", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1440", "Desktop density assertion");
  await page.goto("/discover?demo=learned");
  await expect(page.getByRole("heading", { level: 1, name: HANDMAIDEN })).toBeVisible();

  const card = page.locator(".rail-item").first();
  const poster = await card.locator(".poster-frame").boundingBox();
  const row = await card.locator(".movie-state-row").boundingBox();
  const caption = (row?.y ?? 0) + (row?.height ?? 0) - ((poster?.y ?? 0) + (poster?.height ?? 0));

  // The caption and its controls used to run to 69% of the poster's height —
  // three full-width ovals, one of them two lines tall. The floor here is the
  // regression guard, not the target: the target is the poster staying the
  // thing a viewer is looking at.
  expect(caption / (poster?.height ?? 1)).toBeLessThan(0.55);
});
