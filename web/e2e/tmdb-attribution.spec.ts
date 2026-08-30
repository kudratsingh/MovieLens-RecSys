import { expect, test, type Page } from "@playwright/test";

import { auditPage, describeViolations, scrollingHasSettled } from "./finish-gate-support";

/**
 * The TMDB notice is a property of the product, not of one route.
 *
 * TMDB's terms require the mark and the non-endorsement sentence wherever
 * their data or images appear. Every product surface shows TMDB posters, and
 * movie detail adds a backdrop, an aggregate score, cast portraits and a
 * trailer — yet the notice lived in exactly one place, the pre-redesign
 * dashboard at `/legacy`, which is scheduled for retirement. Retiring it would
 * have deleted the app's only copy.
 *
 * It sits in `AppShell` now, so this file's job is to prove that "in the shell"
 * really does mean every route, at every width, and that a phone's fixed bottom
 * navigation does not park itself on top of the one line the product is
 * required to show. The three Playwright projects supply 390x844, 768x1024, and
 * 1440x1000, so each route below is checked at all three without a loop here.
 */

const NOTICE =
  "This product uses the TMDB API but is not endorsed or certified by TMDB.";

/** The five product routes, plus the live route in fixture mode. */
const PRODUCT_ROUTES = [
  "/ui-preview/discover",
  "/ui-preview/browse",
  "/ui-preview/library",
  "/ui-preview/movies/101",
  "/ui-preview/quick-picks",
  "/discover?demo=learned",
] as const;

/**
 * The signed-out door renders no shell, so its copy sits at the foot of the
 * sign-in card. It shows no TMDB artwork of its own — the notice is here
 * because this is the product's only unauthenticated page, and a required line
 * that is on every page but the front one is the gap this work closes.
 */
const DOOR = "/";

const ALL_SURFACES = [...PRODUCT_ROUTES, DOOR];

type Box = { bottom: number; height: number; left: number; right: number; top: number };

/**
 * Where the notice sits relative to the viewport, and the fixed bottom
 * navigation it must not sit under.
 *
 * Both rectangles are read in one `evaluate` rather than through two
 * `boundingBox()` calls. Discover re-renders after hydration, and a Playwright
 * element handle resolved just before that returns `null` for its box once the
 * node it points at has been replaced — a staleness failure that reads exactly
 * like a missing notice.
 */
async function geometry(page: Page): Promise<{ navigation: Box | null; notice: Box }> {
  await expect(page.locator(".tmdb-attribution")).toBeVisible();
  const measured = await page.evaluate(() => {
    const read = (node: Element | null): Box | null => {
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      return {
        bottom: rect.bottom,
        height: rect.height,
        left: rect.left,
        right: rect.right,
        top: rect.top,
      };
    };
    const notice = read(document.querySelector(".tmdb-attribution"));
    if (!notice) return null;
    return {
      navigation: read(document.querySelector('nav[aria-label="Primary mobile"]')),
      notice,
    };
  });

  expect(measured, "the TMDB notice is not laid out").not.toBeNull();
  return measured!;
}

for (const path of ALL_SURFACES) {
  test(`${path} carries the TMDB notice`, async ({ page }) => {
    await page.goto(path);

    const attribution = page.locator(".tmdb-attribution");
    await expect(attribution).toHaveCount(1);
    await expect(attribution).toBeVisible();
    // Verbatim. The wording is an obligation, so a paraphrase is a failure
    // even though it would read perfectly well.
    await expect(attribution).toContainText(NOTICE);

    const link = attribution.getByRole("link", { name: "TMDB" });
    await expect(link).toHaveAttribute("href", "https://www.themoviedb.org");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(attribution.getByRole("img", { name: "TMDB" })).toBeVisible();

    // Inside the document rather than merely present: a notice pushed off the
    // right edge is one nobody reads.
    const { notice } = await geometry(page);
    const width = page.viewportSize()?.width ?? 0;
    expect(notice.left, `${path}: notice starts left of the viewport`).toBeGreaterThanOrEqual(-1);
    expect(notice.right, `${path}: notice overruns the viewport`).toBeLessThanOrEqual(width + 1);
  });
}

test("the product routes keep the notice clear of the mobile bottom navigation", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "The bottom navigation is a phone surface");

  const collisions: string[] = [];
  for (const path of PRODUCT_ROUTES) {
    await page.goto(path);
    // The notice is the last thing on the page, so the collision — if there is
    // one — only exists once the document is scrolled all the way down.
    //
    // Re-scrolled on every poll rather than once: a route whose posters are
    // still arriving is not yet as tall as it will be, so a single scroll aimed
    // at `scrollHeight` lands at the foot of a document that then grows under
    // it — which on Discover means it lands at 0 and the check silently
    // measures the top of the page. `instant` because `html` carries
    // `scroll-behavior: smooth`.
    await expect
      .poll(
        async () => {
          await page.evaluate(() =>
            window.scrollTo({ behavior: "instant", top: document.documentElement.scrollHeight }),
          );
          await scrollingHasSettled(page);
          return page.evaluate(() => {
            const document_ = document.documentElement;
            const furthest = document_.scrollHeight - window.innerHeight;
            return furthest > 0 && Math.abs(window.scrollY - furthest) < 2;
          });
        },
        { message: `${path} never reached the foot of the document` },
      )
      .toBe(true);

    const { navigation, notice } = await geometry(page);
    expect(navigation, `${path}: no bottom navigation`).not.toBeNull();

    if (notice.bottom > navigation!.top) {
      collisions.push(
        `${path}: notice ends at ${notice.bottom}, navigation starts at ${navigation!.top}`,
      );
    }
  }
  expect(collisions.join("\n")).toBe("");
});

test("the notice survives a 320px viewport on every surface", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "One narrow-width sweep is enough");

  // The narrowest width the product supports (`body { min-width: 320px }`).
  await page.setViewportSize({ width: 320, height: 640 });

  const offenders: string[] = [];
  for (const path of ALL_SURFACES) {
    await page.goto(path);

    const { notice } = await geometry(page);
    if (notice.left < -1 || notice.right > 321) {
      offenders.push(`${path}: ${notice.left} → ${notice.right}`);
    }
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    if (overflow > 1) offenders.push(`${path}: document overflows by ${overflow}px`);
  }
  expect(offenders.join("\n")).toBe("");
});

test("the notice reads at AA against whatever it sits on", async ({ page }) => {
  // A quiet line is still a line that has to be readable. The muted text role
  // is the quietest one that clears 4.5:1 on every surface token, and this is
  // the assertion that keeps a later "make it quieter" honest.
  const measurements: string[] = [];
  for (const path of ["/ui-preview/discover", DOOR]) {
    await page.goto(path);
    const ratio = await page.evaluate(() => {
      const notice = document.querySelector<HTMLElement>(".tmdb-attribution");
      if (!notice) return null;
      const parse = (value: string) =>
        (value.match(/[\d.]+/g) ?? []).slice(0, 4).map(Number);
      const relative = (rgb: number[]) => {
        const channel = (raw: number) => {
          const c = raw / 255;
          return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
      };

      // The first ancestor that actually paints. A transparent parent tells
      // you nothing about what the text is read against.
      let node: HTMLElement | null = notice;
      let background: number[] | null = null;
      while (node && !background) {
        const parsed = parse(getComputedStyle(node).backgroundColor);
        if (parsed.length >= 3 && (parsed[3] ?? 1) > 0) background = parsed;
        node = node.parentElement;
      }
      if (!background) return null;

      const foreground = parse(getComputedStyle(notice).color);
      const [lighter, darker] = [relative(foreground), relative(background)].sort(
        (a, b) => b - a,
      );
      return (lighter + 0.05) / (darker + 0.05);
    });

    expect(ratio, `${path}: could not resolve the notice's colours`).not.toBeNull();
    measurements.push(`${path}: ${ratio!.toFixed(2)}:1`);
    expect(ratio!, `${path} contrast: ${measurements.join(", ")}`).toBeGreaterThanOrEqual(4.5);
  }
});

test("adding the notice leaves the door's accessibility audit clean", async ({ page }) => {
  // The door is the surface the notice changed structurally — it grew a ruled
  // block inside the sign-in card rather than a footer — and it is the one
  // page in the finish-gate matrix a reviewer reaches without a session.
  await page.goto(DOOR);
  await expect(page.getByRole("heading", { level: 1, name: /Sign in to explore/ })).toBeVisible();

  const { blocking } = await auditPage(page);
  expect(describeViolations(blocking), "axe violations on the signed-out door").toBe("");
});
