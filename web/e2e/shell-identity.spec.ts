import { expect, test, type Page } from "@playwright/test";

/**
 * The shell names both identities at every width.
 *
 * `shell.css` used to hide the actor line and the persona name below 1050px.
 * `display: none` removes an element from the accessibility tree as well as
 * from the screen, so on a phone the only identity signal left was an
 * `aria-hidden` two-letter dot — and Browse and movie detail restate the
 * persona nowhere else on the page, so those two routes never said whose data
 * was on screen. The design contract asks for "authenticated actor plus an
 * explicitly labeled selected demo persona", with no width qualifier.
 *
 * The three Playwright projects supply 390x844, 768x1024, and 1440x1000, so
 * every route below is checked at all three without a loop here; the 320 sweep
 * at the end is the width where the header has the least room to give.
 *
 * Fixture mode is the right harness for this: the shell's identity block is
 * pure props and layout, and the isolated preview holds the states still. What
 * it cannot show is `Sign out` — the preview renders `Exit preview` in its
 * place — so that half lives in the service-backed suite.
 */

const ACTOR_LABEL = "Isolated mode";
const ACTOR_NAME = "Fixture reviewer";
const PERSONA_NAME = "Action Fan";

const ROUTES = [
  { path: "/ui-preview/discover", personaLabel: "Recorded persona" },
  { path: "/ui-preview/browse", personaLabel: "Recorded persona" },
  { path: "/ui-preview/library", personaLabel: "Recorded persona" },
  { path: "/ui-preview/movies/101", personaLabel: "Recorded persona" },
  { path: "/ui-preview/quick-picks", personaLabel: "Recorded persona" },
  // The live route in fixture mode, which is where the product wording lives.
  { path: "/discover?demo=learned", personaLabel: "Exploring as" },
] as const;

async function expectNamedIdentities(page: Page, personaLabel: string) {
  const header = page.locator(".shell-header");
  const actor = header.locator(".actor-copy");
  const persona = header.locator(".persona-cluster");

  // `toBeVisible` is the accessibility-tree assertion that matters here: the
  // regression being guarded against was `display: none`, which fails it.
  await expect(actor).toBeVisible();
  await expect(actor).toContainText(ACTOR_LABEL);
  await expect(actor).toContainText(ACTOR_NAME);

  await expect(persona).toBeVisible();
  await expect(persona).toContainText(personaLabel);
  await expect(persona).toContainText(PERSONA_NAME);

  // Two identities, never one. Conflating them is the single thing the design
  // contract forbids outright.
  await expect(actor).not.toContainText(PERSONA_NAME);
  await expect(persona).not.toContainText(ACTOR_NAME);

  // The initials stay decorative, because the name is right beside them.
  await expect(header.locator(".persona-dot")).toHaveAttribute("aria-hidden", "true");
}

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

for (const route of ROUTES) {
  test(`${route.path} names the actor and the persona`, async ({ page }) => {
    await page.goto(route.path);
    await expect(page.locator(".shell-header")).toBeVisible();

    await expectNamedIdentities(page, route.personaLabel);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
  });
}

test("every route keeps one main landmark and a skip link into it", async ({ page }) => {
  // Quick Picks ran outside the shell until this sweep, so it had neither, and
  // axe reported `landmark-one-main` on it at both viewports.
  for (const route of ROUTES) {
    await page.goto(route.path);
    await expect(page.getByRole("main"), route.path).toHaveCount(1);
    await expect(page.getByRole("main"), route.path).toHaveAttribute(
      "id",
      "main-content",
    );
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip, route.path).toHaveAttribute("href", "#main-content");
  }
});

test("Quick Picks carries the product navigation it used to run without", async ({
  page,
}, testInfo) => {
  await page.goto("/ui-preview/quick-picks");

  await expect(page.getByRole("navigation", { name: "Primary" })).toBeAttached();
  const mobileNavigation = page.getByRole("navigation", { name: "Primary mobile" });
  if (testInfo.project.name === "mobile-390") {
    // The bottom navigation is the small-screen half of the contract, and it
    // is the one Quick Picks lost entirely by rendering outside the shell.
    await expect(mobileNavigation).toBeVisible();
    await expect(mobileNavigation.getByRole("link")).toHaveCount(3);
  } else {
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  }
});

test("both identities survive a 320px viewport", async ({ page }) => {
  // The narrowest width the product supports (`body { min-width: 320px }`).
  // The header gives up the wordmark's copy here rather than an identity.
  await page.setViewportSize({ width: 320, height: 640 });

  for (const route of ROUTES) {
    await page.goto(route.path);
    await expectNamedIdentities(page, route.personaLabel);
    expect(await horizontalOverflow(page), route.path).toBeLessThanOrEqual(1);
  }
});
