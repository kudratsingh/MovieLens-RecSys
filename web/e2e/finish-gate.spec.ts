import { expect, test, type Page } from "@playwright/test";

import {
  auditPage,
  describeViolations,
  expectVisibleFocus,
  headingOutline,
  horizontalOverflow,
  outlineSkip,
} from "./finish-gate-support";

/**
 * The Bundle 7A visual and accessibility gate.
 *
 * One file, one job: walk the named finish-gate state matrix in a real browser
 * and assert the criteria the handoff lists — zero critical or serious axe
 * violations, a logical outline and named landmarks, visible focus, keyboard
 * completeness, 44px mobile targets, semantic state text, the poster
 * alternative-text policy, focus restoration, reduced motion, forced-colours
 * usability, and no horizontal overflow at 320px.
 *
 * Every state here is produced by the isolated fixture harness or by explicit
 * failure injection, which is what makes the matrix reproducible: `loading`
 * and `upstream-error` are not states a healthy stack can be asked to hold
 * still in. The service-backed half of the gate — that these states are the
 * ones the real stack actually produces — lives in
 * `web/tests/e2e/finish-gate-journey.spec.ts`.
 *
 * The three Playwright projects supply 390x844, 768x1024, and 1440x1000, so
 * every state below is exercised at all three widths without a loop here.
 */

type MatrixState = {
  /** Matches the screenshot name in `docs/frontend/evidence/bundle-7a`. */
  id: string;
  path: string;
  /** Resolves once the state is on screen and has stopped moving. */
  settle: (page: Page) => Promise<void>;
  /** A state that renders no route content has no `h1` to require. */
  headless?: boolean;
};

const heading = (name: string | RegExp) => async (page: Page) => {
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
};

const HANDMAIDEN = "The Handmaiden";

const MATRIX: MatrixState[] = [
  {
    // The front door, signed out. The 7d cutover made `/` a redirect for a
    // signed-in viewer and left this as the only thing it renders on its own,
    // so it is the one changed surface the isolated harness can reach: the
    // fixture server holds no session, and every authenticated route lands
    // here.
    id: "sign-in-door",
    path: "/",
    settle: heading(/Sign in to explore/),
  },
  { id: "discover-learned", path: "/discover?demo=learned", settle: heading(HANDMAIDEN) },
  { id: "discover-fallback", path: "/discover?demo=fallback", settle: heading(HANDMAIDEN) },
  {
    id: "discover-loading",
    path: "/discover?demo=loading",
    headless: true,
    settle: async (page) => {
      // The rail skeleton and the history skeleton both announce themselves.
      await expect(page.getByText("Loading movies").first()).toBeAttached();
    },
  },
  {
    id: "discover-empty",
    path: "/discover?demo=empty",
    headless: true,
    settle: async (page) => {
      await expect(page.getByText("No recommendations right now")).toBeVisible();
    },
  },
  {
    id: "discover-upstream-error",
    path: "/discover?demo=recommendations-error",
    headless: true,
    settle: async (page) => {
      await expect(
        page.getByRole("alert", { name: /Recommendations could not be loaded/ }),
      ).toBeVisible();
    },
  },
  {
    id: "discover-poster-error",
    path: "/discover?demo=poster-failure",
    settle: async (page) => {
      await heading(HANDMAIDEN)(page);
      await expect(page.getByTestId("poster-fallback").first()).toBeVisible();
    },
  },
  {
    id: "discover-auth-required",
    path: "/discover?demo=auth-expired",
    headless: true,
    settle: async (page) => {
      await expect(page.getByRole("link", { name: "Sign in again" }).first()).toBeVisible();
    },
  },
  {
    id: "browse-default",
    path: "/ui-preview/browse",
    settle: async (page) => {
      await expect(page.getByRole("list", { name: "Browse results" })).toBeVisible();
    },
  },
  {
    id: "library-populated",
    path: "/ui-preview/library",
    settle: async (page) => {
      await expect(page.getByRole("list", { name: "Rated movies" })).toBeVisible();
    },
  },
  {
    // The Seen tab, which is the one state in the matrix that puts a spotlight
    // above a list: two decision surfaces for the same movie inside one panel,
    // and the widest control row the product has.
    id: "library-seen",
    path: "/ui-preview/library?tab=history",
    settle: async (page) => {
      await expect(page.getByRole("region", { name: "Seen spotlight" })).toBeVisible();
      await expect(page.getByRole("list", { name: "Seen movies" })).toBeVisible();
    },
  },
  {
    id: "library-seen-filtered-empty",
    path: "/ui-preview/library?tab=history&year_from=1900&year_to=1910",
    settle: async (page) => {
      await expect(page.getByText("Nothing in Seen matches these filters.")).toBeVisible();
    },
  },
  {
    id: "library-empty",
    path: "/ui-preview/library?tab=watchlist&empty=watchlist",
    settle: async (page) => {
      await expect(page.getByText("Nothing saved yet")).toBeVisible();
    },
  },
  {
    id: "movie-detail",
    path: "/ui-preview/movies/101",
    settle: heading(HANDMAIDEN),
  },
  {
    id: "quick-picks",
    path: "/ui-preview/quick-picks",
    settle: async (page) => {
      await expect(page.locator(".quick-picks-page")).toHaveAttribute("data-interactive", "true");
      await heading("Perfect Blue")(page);
    },
  },
];

for (const state of MATRIX) {
  test(`${state.id} has an accessible, non-overflowing document`, async ({ page }) => {
    await page.goto(state.path);
    await state.settle(page);

    const { blocking } = await auditPage(page);
    expect(describeViolations(blocking), `axe violations on ${state.id}`).toBe("");

    expect(await horizontalOverflow(page), `${state.id} overflows horizontally`).toBeLessThanOrEqual(1);

    // Landmarks: one main, and every navigation distinguishable by name. Two
    // unnamed navs is the failure this catches — a screen-reader user hears
    // "navigation" twice and cannot tell the shell from the route.
    await expect(page.getByRole("main")).toHaveCount(1);
    const navNames = await page
      .getByRole("navigation")
      .evaluateAll((navs) =>
        navs.map((nav) => nav.getAttribute("aria-label") ?? nav.getAttribute("aria-labelledby") ?? ""),
      );
    expect(navNames.every((name) => name.length > 0), `unnamed navigation on ${state.id}`).toBe(true);
    expect(new Set(navNames).size, `duplicate navigation names on ${state.id}`).toBe(navNames.length);

    const outline = await headingOutline(page);
    const h1s = outline.filter((entry) => entry.level === 1);
    expect(h1s.length, `${state.id} should have ${state.headless ? "no" : "one"} h1`).toBe(
      state.headless ? 0 : 1,
    );
    expect(outlineSkip(outline), `heading level skipped on ${state.id}`).toBeNull();
  });
}

/**
 * A deliberately wide face, applied to text *and* form controls.
 *
 * The 320px sweep is a font-metric test whether or not it admits it: a row that
 * fits on a developer's machine can overflow on a runner whose system font is
 * wider, and that is exactly how the Library filter row shipped a 10px overflow
 * that only CI saw. Overriding the face makes the check describe the layout
 * rather than the machine — a row that survives a monospace at 320px has real
 * slack, and one that does not is one system-font change away from breaking.
 */
const WIDE_FACE = `*, input, select, button, textarea {
  font-family: "Courier New", "DejaVu Sans Mono", monospace !important;
}`;

test("no state overflows a 320px viewport, on narrow and wide font metrics", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "One narrow-width sweep is enough");
  test.slow();

  const offenders: string[] = [];
  await page.setViewportSize({ width: 320, height: 640 });
  for (const face of ["system", "wide"] as const) {
    for (const state of MATRIX) {
      await page.goto(state.path);
      await state.settle(page);
      if (face === "wide") {
        await page.addStyleTag({ content: WIDE_FACE });
        // Re-layout under the new metrics before measuring.
        await expect
          .poll(async () => page.evaluate(() => document.fonts.status))
          .toBe("loaded");
      }
      const overflow = await horizontalOverflow(page);
      if (overflow > 1) offenders.push(`${state.id} (${face} font): ${overflow}px`);
    }
  }
  expect(offenders.join("\n")).toBe("");
});

test("forced colours keep the movie decision and its controls usable", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/discover?demo=learned");
  await heading(HANDMAIDEN)(page);

  // The title still reads, and it is not painted in the same colour as the
  // surface behind it — the classic forced-colours failure is a value baked
  // into a component that the system palette never gets to replace.
  const contrast = await page.evaluate(() => {
    const title = document.querySelector<HTMLElement>("h1");
    if (!title) return null;
    return {
      color: getComputedStyle(title).color,
      background: getComputedStyle(document.body).backgroundColor,
    };
  });
  expect(contrast).not.toBeNull();
  expect(contrast!.color).not.toBe(contrast!.background);

  // A forced-colours palette removes the background that used to distinguish a
  // button from the page, so the contract is that each one carries a border.
  const borders = await page
    .locator(".button-primary, .button-secondary, .icon-button")
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        label: (node.textContent ?? "").trim().slice(0, 30),
        width: Number.parseFloat(getComputedStyle(node).borderTopWidth),
      })),
    );
  expect(borders.length).toBeGreaterThan(0);
  expect(borders.filter((entry) => !(entry.width >= 1))).toEqual([]);

  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(1);
  await expect(page.getByRole("button", { name: "Why this?" })).toBeVisible();
});

test("the core Discover actions are reachable and operable from the keyboard", async ({ page }) => {
  await page.goto("/discover?demo=learned");
  await heading(HANDMAIDEN)(page);

  // Walk the tab ring from the top of the document and record what it reaches.
  // The set matters more than the order: a viewer must be able to open the
  // movie, save it, mark it watched, and ask why, without a pointer.
  const reached = new Set<string>();
  await page.locator("body").press("Tab");
  for (let step = 0; step < 40; step += 1) {
    const label = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      if (!active || active === document.body) return "";
      return (active.getAttribute("aria-label") ?? active.textContent ?? "").trim();
    });
    if (label) reached.add(label.replace(/\s+/g, " "));
    await page.keyboard.press("Tab");
  }

  const required = ["Open movie", "Watchlist", "Mark watched", "Not for me", "Why this?"];
  const missing = required.filter(
    (name) => ![...reached].some((label) => label.includes(name)),
  );
  expect(missing, `unreachable by keyboard: ${[...reached].join(" | ")}`).toEqual([]);

  // Operable, not merely reachable: the evidence drawer opens on Enter and
  // hands focus back to its trigger on Escape.
  const trigger = page.getByRole("button", { name: "Why this?" });
  await trigger.focus();
  await expectVisibleFocus(page, ".featured-copy button[aria-expanded]");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("state is carried by text and semantics, not only by colour", async ({ page }) => {
  await page.goto("/ui-preview/library");
  await expect(page.getByRole("list", { name: "Rated movies" })).toBeVisible();

  // The selected tab announces itself rather than relying on its underline.
  await expect(page.getByRole("tab", { name: /^Rated/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: /^Watchlist/ })).toHaveAttribute("aria-selected", "false");

  await page.goto("/ui-preview/movies/101");
  await heading(HANDMAIDEN)(page);
  // Watchlist is a two-state control: the pressed state is in the accessibility
  // tree and in the label, so it survives greyscale and forced colours.
  const watchlist = page.getByRole("button", { name: /^(Watchlist|In watchlist)$/ });
  await expect(watchlist).toHaveAttribute("aria-pressed", /true|false/);

  // A failure region says what happened in words, not with a red border.
  await page.goto("/ui-preview/browse?fail=catalog");
  const alert = page.getByRole("alert", { name: "Catalog could not be loaded" });
  await expect(alert).toContainText("Catalog could not be loaded");
  await expect(alert).toContainText("The recommendation API returned an error.");
});

test("posters follow the alternative-text policy", async ({ page }) => {
  for (const path of ["/discover?demo=learned", "/ui-preview/browse", "/ui-preview/movies/101"]) {
    await page.goto(path);
    await expect(page.locator("img").first()).toBeVisible();

    const posters = await page.locator("img").evaluateAll((images) =>
      images.map((image) => {
        const alt = image.getAttribute("alt");
        const frame = image.closest(".poster-frame, .movie-detail-poster");
        const card = image.closest("article, .movie-detail-hero, .featured-movie");
        return {
          alt,
          isPoster: Boolean(frame),
          // An empty alt is only correct when the title is already adjacent in
          // text; otherwise the movie loses its identity for a reader.
          hasAdjacentTitle: Boolean(card?.querySelector(".poster-title, h1, h2, h3")),
          src: (image as HTMLImageElement).getAttribute("src")?.slice(0, 60) ?? "",
        };
      }),
    );

    for (const poster of posters) {
      // Every image declares an alt: a missing attribute is the one thing axe
      // and a reader both treat as broken.
      expect(poster.alt, `missing alt on ${path} (${poster.src})`).not.toBeNull();
      if (poster.isPoster && poster.alt === "") {
        expect(
          poster.hasAdjacentTitle,
          `decorative poster with no adjacent title on ${path} (${poster.src})`,
        ).toBe(true);
      }
      // A filename or the word "image" is alt text that says nothing.
      expect(poster.alt?.toLowerCase()).not.toMatch(/^(image|photo|poster)$|\.(jpg|jpeg|png|webp)$/);
    }
  }
});

test("primary mobile targets clear 44x44", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Touch-target sizing is a mobile criterion");

  const surfaces = [
    { path: "/discover?demo=learned", selector: ".featured-actions a, .featured-actions button" },
    { path: "/ui-preview/browse", selector: ".browse-toolbar button, .browse-active button" },
    { path: "/ui-preview/movies/101", selector: ".movie-state-row button" },
  ];

  const undersized: string[] = [];
  for (const surface of surfaces) {
    await page.goto(surface.path);
    const controls = page.locator(surface.selector);
    const count = await controls.count();
    expect(count, `no controls matched ${surface.selector}`).toBeGreaterThan(0);
    for (let index = 0; index < count; index += 1) {
      const control = controls.nth(index);
      if (!(await control.isVisible())) continue;
      const box = await control.boundingBox();
      const label = ((await control.textContent()) ?? "").trim().slice(0, 30);
      if ((box?.width ?? 0) < 44 || (box?.height ?? 0) < 44) {
        undersized.push(`${surface.path} "${label}" ${box?.width}x${box?.height}`);
      }
    }
  }
  expect(undersized.join("\n")).toBe("");
});

test("reduced motion is honoured across the animated surfaces", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });

  await page.goto("/ui-preview/quick-picks");
  await expect(page.locator(".quick-picks-page")).toHaveAttribute("data-interactive", "true");
  // The deck's fling is the one motion that carries meaning, so it degrades
  // rather than disappearing silently.
  await expect(page.locator(".quick-pick-poster")).toHaveAttribute("data-motion", "none");

  await page.goto("/discover?demo=learned");
  await heading(HANDMAIDEN)(page);
  const durations = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("a, button, article, section")]
      .map((node) => getComputedStyle(node).transitionDuration)
      .filter((duration) => duration !== "0s")
      // The global reduce rule collapses transitions to 0.01ms; anything longer
      // is a declaration that escaped it.
      .filter((duration) => Number.parseFloat(duration) > 0.001),
  );
  expect(durations).toEqual([]);
});
