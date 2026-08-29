import axe from "axe-core";
import { expect, type Page } from "@playwright/test";

/**
 * Shared machinery for the Bundle 7A finish gate.
 *
 * The component suites already run axe against isolated trees. What they
 * cannot see is the assembled document: the shell's landmarks around a route's
 * own, the heading order once a route header and a region header sit together,
 * a focus ring against the real background, and a control row that only
 * overflows once it is inside the page it ships in. That is what this file
 * exists to check, and it checks it in a browser rather than jsdom because
 * three of the gate's criteria — visible focus, forced colours, and horizontal
 * overflow — have no meaning without layout and a cascade.
 *
 * axe is injected from the installed `axe-core` package rather than pulled
 * from a CDN, so the gate runs offline and pins the same rule set the
 * component tests use.
 */

export type AxeViolation = {
  id: string;
  impact: string | null;
  help: string;
  nodes: string[];
};

/**
 * Runs axe against the whole document and returns only the impacts the
 * accessibility gate treats as blocking.
 *
 * Minor and moderate findings are returned separately rather than dropped: the
 * gate does not fail on them, but a review that never sees them cannot say
 * whether they are acceptable.
 */
export async function auditPage(page: Page): Promise<{
  blocking: AxeViolation[];
  advisory: AxeViolation[];
}> {
  await page.evaluate(axe.source);
  const violations = await page.evaluate(async () => {
    const results = await window.axe.run(document, {
      resultTypes: ["violations"],
      // Colour contrast needs real pixels, and these pages are dark-first with
      // an amber accent, so it is exactly the rule worth keeping on.
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
    });
    return results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact ?? null,
      help: violation.help,
      nodes: violation.nodes.slice(0, 4).map((node) => node.html),
    }));
  });

  const blockingImpacts = new Set(["critical", "serious"]);
  return {
    blocking: violations.filter((violation) => blockingImpacts.has(violation.impact ?? "")),
    advisory: violations.filter((violation) => !blockingImpacts.has(violation.impact ?? "")),
  };
}

/** Formats violations so a CI failure names the rule and the element. */
export function describeViolations(violations: AxeViolation[]): string {
  return violations
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.join("\n  ")}`)
    .join("\n");
}

export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

/**
 * The document has stopped moving.
 *
 * `html` carries `scroll-behavior: smooth`, so a scroll a route starts on the
 * viewer's behalf — a trailer opening, a decision handing the page back to the
 * featured movie — is still animating when the next line runs. Two consecutive
 * frames at the same offset is the stability rule Playwright applies before it
 * computes a click point, asked here as a wait of its own, for the two callers
 * that need it: a press aimed at a control that is still travelling, and an
 * audit whose colour-contrast pass resolves backgrounds by hit-testing and so
 * wants the viewport to hold still across its run.
 */
export async function scrollingHasSettled(page: Page): Promise<void> {
  await page.waitForFunction(
    () =>
      new Promise<boolean>((resolve) => {
        const start = window.scrollY;
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(window.scrollY === start)));
      }),
    undefined,
    { polling: 100 },
  );
}

/**
 * The document outline as a reader hears it.
 *
 * `visually-hidden` headings count — they are headings — but `aria-hidden`
 * subtrees do not, because a reader never reaches them.
 */
export async function headingOutline(page: Page): Promise<{ level: number; text: string }[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("h1, h2, h3, h4, h5, h6")]
      .filter((heading) => !heading.closest("[aria-hidden='true']"))
      .map((heading) => ({
        level: Number(heading.tagName.slice(1)),
        text: (heading.textContent ?? "").trim().slice(0, 60),
      })),
  );
}

/** Reports the first place the outline jumps more than one level. */
export function outlineSkip(
  outline: { level: number; text: string }[],
): string | null {
  for (let index = 1; index < outline.length; index += 1) {
    const previous = outline[index - 1];
    const current = outline[index];
    if (current.level > previous.level + 1) {
      return `h${previous.level} "${previous.text}" is followed by h${current.level} "${current.text}"`;
    }
  }
  return null;
}

/**
 * Asserts a control shows a focus indicator that a sighted keyboard user can
 * actually see: an outline or a shadow that is not there when the control is
 * unfocused. Comparing focused against unfocused is the point — a global
 * `outline: none` and a global 3px ring both produce "an outline value", and
 * only the difference distinguishes them.
 */
export async function expectVisibleFocus(page: Page, selector: string) {
  const style = await page.evaluate((target) => {
    const element = document.querySelector<HTMLElement>(target);
    if (!element) return null;
    const read = () => {
      const computed = getComputedStyle(element);
      return {
        outline: `${computed.outlineStyle} ${computed.outlineWidth} ${computed.outlineColor}`,
        shadow: computed.boxShadow,
      };
    };
    element.blur();
    const blurred = read();
    // `focus-visible` only matches a keyboard-initiated focus, which is what a
    // programmatic `.focus()` counts as in Chromium.
    element.focus();
    const focused = read();
    return { blurred, focused };
  }, selector);

  expect(style, `no element matched ${selector}`).not.toBeNull();
  const changed =
    style!.blurred.outline !== style!.focused.outline ||
    style!.blurred.shadow !== style!.focused.shadow;
  expect(
    changed,
    `${selector} looks identical focused and unfocused: ${JSON.stringify(style)}`,
  ).toBe(true);
  expect(style!.focused.outline, `${selector} focus ring is suppressed`).not.toContain("none");
}

declare global {
  interface Window {
    axe: typeof axe;
  }
}
