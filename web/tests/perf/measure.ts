import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import type { Locator, Page } from "@playwright/test";

/**
 * Instrumentation for the browser-timing gate.
 *
 * Everything here measures inside the page rather than around it. Timing a
 * Playwright call measures Playwright's round trip as much as the application:
 * the number that matters is the one the browser's own clock records between an
 * input event and the pixel that answers it, which is what `performance.now()`
 * inside a capture-phase listener and a MutationObserver give.
 */

/** Targets from the frontend testing strategy's performance gate. */
export const BUDGETS = {
  lcpMs: 2500,
  cls: 0.1,
  ackMs: 100,
} as const;

/**
 * Which budgets fail the run, and which are only reported.
 *
 * Three different answers because the three measures degrade differently under
 * a busy runner:
 *
 *   CLS   always enforced. It is a property of the markup, and no amount of CPU
 *         contention can make a box with a reserved aspect ratio shift.
 *   LCP   enforced. The worst local reading across three runs under a 4x CPU
 *         throttle was 392 ms against a 2 500 ms budget, and LCP here is
 *         dominated by the server render and one poster rather than by a long
 *         JavaScript task, so it degrades gradually rather than off a cliff.
 *   ack   advisory on first landing. The thinnest margin of the three (worst
 *         local reading 16.9 ms) and the most CPU-sensitive, because it is a
 *         React state update racing a 100 ms budget. Promote it once three
 *         consecutive `browser-auth-e2e` runs on the 4-vCPU runner record it
 *         under 50 ms; ADR 0010 carries the rule.
 *
 * Both switches exist so a promotion is a one-line change with a recorded
 * reason rather than an edit to an assertion.
 */
export const ENFORCE_LCP = process.env.PERF_ENFORCE_LCP !== "false";
export const ENFORCE_ACK = process.env.PERF_ENFORCE_ACK === "true";

export const CPU_THROTTLE = Number(process.env.PERF_CPU_THROTTLE ?? 4);

const REPORT_PATH =
  process.env.BROWSER_TIMING_OUTPUT ?? "../artifacts/browser-timing/browser-timing.json";

export function reportPath(): string {
  return resolve(process.cwd(), REPORT_PATH);
}

/** Drop any previous run's report. Called once, from the config's globalSetup. */
export function resetReport(): void {
  rmSync(reportPath(), { force: true });
}

export interface StructuralClaim {
  ok: boolean;
  detail: string;
}

export interface RequestTally {
  total: number;
  tmdbApi: string[];
  tmdbImage: string[];
  sameOriginImage: number;
}

export interface RouteMeasurement {
  route: string;
  path: string;
  skipped?: string;
  lcpMs?: number;
  cls?: number;
  shifts?: LayoutShiftRecord[];
  acknowledgement?: { label: string; ms: number };
  structural: Record<string, StructuralClaim>;
  requests?: RequestTally;
}

export interface LayoutShiftRecord {
  value: number;
  startMs: number;
  source: string;
}

interface Vitals {
  lcp: number;
  cls: number;
  shifts: LayoutShiftRecord[];
  ackStart: number | null;
  ackEnd: number | null;
}

const results: RouteMeasurement[] = [];

export function record(measurement: RouteMeasurement): void {
  results.push(measurement);
}

/**
 * Start collecting LCP and CLS before the document exists.
 *
 * `buffered: true` on both observers matters: the largest paint and the first
 * shifts routinely happen before any script this file adds could have attached,
 * and an observer without it silently reports zero.
 */
export async function installVitals(page: Page): Promise<void> {
  await page.addInitScript(() => {
    interface LayoutShiftEntry extends PerformanceEntry {
      value: number;
      hadRecentInput: boolean;
      sources?: { node?: Node | null }[];
    }
    const state = {
      lcp: 0,
      cls: 0,
      shifts: [] as { value: number; startMs: number; source: string }[],
      ackStart: null as number | null,
      ackEnd: null as number | null,
    };
    (window as unknown as { __perf: typeof state }).__perf = state;

    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        // The largest contentful paint can be superseded as bigger elements
        // render, so the last entry before interaction is the real one.
        state.lcp = entry.startTime;
      }
    }).observe({ type: "largest-contentful-paint", buffered: true });

    // The web-vitals session-window definition, not a naive sum: CLS is the
    // largest burst of shifts within a 5 s window with gaps under 1 s. A sum
    // would punish a long page for shifts a reader never saw together.
    let sessionValue = 0;
    let sessionStart = 0;
    let sessionLast = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as LayoutShiftEntry[]) {
        // A shift the reader caused by tapping is not instability.
        if (entry.hadRecentInput) {
          continue;
        }
        const withinSession =
          sessionValue > 0 &&
          entry.startTime - sessionLast < 1000 &&
          entry.startTime - sessionStart < 5000;
        if (withinSession) {
          sessionValue += entry.value;
          sessionLast = entry.startTime;
        } else {
          sessionValue = entry.value;
          sessionStart = entry.startTime;
          sessionLast = entry.startTime;
        }
        state.cls = Math.max(state.cls, sessionValue);
        const node = entry.sources?.[0]?.node as Element | null | undefined;
        state.shifts.push({
          value: entry.value,
          startMs: entry.startTime,
          source: node
            ? `${node.nodeName.toLowerCase()}${node.className ? `.${String(node.className).split(" ")[0]}` : ""}`
            : "unknown",
        });
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

/** Emulate a mid-range phone's CPU. Must be applied before navigation. */
export async function throttleCpu(page: Page, rate: number): Promise<void> {
  if (rate <= 1) {
    return;
  }
  const session = await page.context().newCDPSession(page);
  await session.send("Emulation.setCPUThrottlingRate", { rate });
}

export async function readVitals(page: Page): Promise<Vitals> {
  return page.evaluate(() => (window as unknown as { __perf: Vitals }).__perf);
}

/**
 * Wait until the page has stopped changing on its own.
 *
 * LCP keeps being revised while content arrives, so reading it too early
 * reports an early paint as the largest one. Network idle plus two animation
 * frames is the cheapest honest "it has settled".
 */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(
    () =>
      new Promise<void>((done) => {
        requestAnimationFrame(() => requestAnimationFrame(() => done()));
      }),
  );
}

/**
 * Arm the acknowledgement stopwatch on one element's visible text.
 *
 * "Visible acknowledgement" is defined here as: the text under `selector`
 * differs from what it said when the reader acted. That covers an optimistic
 * label flip, a status line appearing, and a busy state — every shape the
 * product actually uses — without asserting which one a given control chose.
 *
 * The clock starts on the input event itself, in the capture phase, so the
 * measurement includes the application's own dispatch and render cost and
 * excludes Playwright's round trip. Several event types are listened for
 * because not every control is a button: a `<select>` never sees a click that
 * Playwright's `selectOption` would produce.
 */
export async function armAcknowledgement(page: Page, selector: string): Promise<void> {
  await page.evaluate((target) => {
    const state = (
      window as unknown as {
        __perf: { ackStart: number | null; ackEnd: number | null };
      }
    ).__perf;
    state.ackStart = null;
    state.ackEnd = null;
    const before = document.querySelector(target)?.textContent ?? null;
    const check = () => {
      if (state.ackStart === null || state.ackEnd !== null) {
        return;
      }
      const now = document.querySelector(target)?.textContent ?? null;
      if (now !== before) {
        state.ackEnd = performance.now();
      }
    };
    const start = () => {
      if (state.ackStart === null) {
        state.ackStart = performance.now();
      }
    };
    for (const type of ["pointerdown", "click", "keydown", "change"]) {
      document.addEventListener(type, start, { capture: true });
    }
    new MutationObserver(check).observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
    });
  }, selector);
}

/** Perform one interaction and return the milliseconds until it was acknowledged. */
export async function measureAcknowledgement(
  page: Page,
  ackSelector: string,
  act: () => Promise<void>,
): Promise<number> {
  await armAcknowledgement(page, ackSelector);
  await act();
  await page.waitForFunction(
    () => (window as unknown as { __perf: { ackEnd: number | null } }).__perf.ackEnd !== null,
    undefined,
    { polling: "raf" },
  );
  const vitals = await readVitals(page);
  return (vitals.ackEnd ?? 0) - (vitals.ackStart ?? 0);
}

export async function clickAndMeasureAcknowledgement(
  page: Page,
  trigger: Locator,
  ackSelector: string,
): Promise<number> {
  return measureAcknowledgement(page, ackSelector, () => trigger.click());
}

/**
 * Count what the page fetched, split by who serves it.
 *
 * The claim under test is that a catalog page never fans out to TMDB per
 * visible card. Poster *artwork* is a TMDB CDN URL by design, but it must reach
 * the browser through this origin's image optimizer — a direct `image.tmdb.org`
 * request or any `api.themoviedb.org` request means metadata is being resolved
 * client-side, one call per card.
 */
export function trackRequests(page: Page): RequestTally {
  const tally: RequestTally = { total: 0, tmdbApi: [], tmdbImage: [], sameOriginImage: 0 };
  page.on("request", (request) => {
    tally.total += 1;
    // Classified by host, never by substring: the optimizer's own URL carries
    // the TMDB origin percent-encoded in its `url` parameter, so a substring
    // match would report every correctly-proxied poster as a direct fan-out.
    const { hostname, pathname } = new URL(request.url());
    if (hostname === "api.themoviedb.org") {
      tally.tmdbApi.push(request.url());
    } else if (hostname === "image.tmdb.org") {
      tally.tmdbImage.push(request.url());
    } else if (pathname.startsWith("/_next/image")) {
      tally.sameOriginImage += 1;
    }
  });
  return tally;
}

/** URLs matching a substring, in arrival order — used for deferred-load proofs. */
export function trackMatching(page: Page, needle: string): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes(needle)) {
      seen.push(request.url());
    }
  });
  return seen;
}

/**
 * Poster boxes reserve their space before the image loads.
 *
 * Checked structurally rather than only through CLS: a run that happens to load
 * every poster from cache would show CLS 0 while the markup was still capable
 * of shifting. Reserved space is either explicit width/height attributes or a
 * container with a fixed aspect ratio, which is what this app uses.
 */
export async function posterReservation(page: Page): Promise<StructuralClaim> {
  const report = await page.evaluate(() => {
    const images = Array.from(document.querySelectorAll("img"));
    let reserved = 0;
    const unreserved: string[] = [];
    for (const image of images) {
      const hasAttributes = image.hasAttribute("width") && image.hasAttribute("height");
      const frame = image.closest(".poster-frame") ?? image.parentElement;
      const ratio = frame ? getComputedStyle(frame).aspectRatio : "auto";
      const hasRatio = ratio !== "auto" && ratio !== "";
      if (hasAttributes || hasRatio) {
        reserved += 1;
      } else {
        unreserved.push(image.getAttribute("src")?.slice(0, 60) ?? "(no src)");
      }
    }
    return { total: images.length, reserved, unreserved: unreserved.slice(0, 5) };
  });
  return {
    // A page with no images passes vacuously, and says so: Library rows carry
    // initials rather than posters, and a movie with no artwork renders the
    // deterministic fallback mark inside the box the poster would have had.
    ok: report.reserved === report.total,
    detail:
      report.total === 0
        ? "no images on this route (initials or fallback marks, both inside reserved boxes)"
        : `${report.reserved}/${report.total} images sit in a reserved box` +
          (report.unreserved.length ? `; unreserved: ${report.unreserved.join(", ")}` : ""),
  };
}

/**
 * Images below the fold defer their download.
 *
 * The first cards are allowed to be eager — they are the LCP candidate — so
 * this asks a narrower question: is anything below the initial viewport being
 * loaded eagerly?
 */
export async function belowFoldLazyLoading(page: Page): Promise<StructuralClaim> {
  const report = await page.evaluate(() => {
    const images = Array.from(document.querySelectorAll("img"));
    const viewportHeight = window.innerHeight;
    let belowFold = 0;
    let eagerBelowFold = 0;
    let eagerAboveFold = 0;
    for (const image of images) {
      const top = image.getBoundingClientRect().top + window.scrollY;
      const lazy = image.getAttribute("loading") === "lazy";
      if (top >= viewportHeight) {
        belowFold += 1;
        if (!lazy) {
          eagerBelowFold += 1;
        }
      } else if (!lazy) {
        eagerAboveFold += 1;
      }
    }
    return { total: images.length, belowFold, eagerBelowFold, eagerAboveFold };
  });
  return {
    ok: report.eagerBelowFold === 0,
    detail:
      `${report.belowFold}/${report.total} images start below the fold; ` +
      `${report.eagerBelowFold} of those load eagerly ` +
      `(${report.eagerAboveFold} eager above the fold, which is the LCP candidate)`,
  };
}

export function tmdbFanOut(tally: RequestTally, cards: number): StructuralClaim {
  const ok = tally.tmdbApi.length === 0 && tally.tmdbImage.length === 0;
  return {
    ok,
    detail:
      `${cards} cards rendered; ${tally.tmdbApi.length} api.themoviedb.org and ` +
      `${tally.tmdbImage.length} direct image.tmdb.org requests; ` +
      `${tally.sameOriginImage} same-origin optimized images`,
  };
}

export function boundedPage(cards: number, ceiling: number): StructuralClaim {
  return {
    ok: cards > 0 && cards <= ceiling,
    detail: `${cards} cards rendered against a page ceiling of ${ceiling}`,
  };
}

// --- reporting --------------------------------------------------------------

/**
 * Write the report, merging anything an earlier worker already recorded.
 *
 * Playwright retires a worker when a test fails and starts a fresh one, which
 * takes this module's in-memory results with it. Merging on write means a run
 * with one failing route still reports every route it measured — which is
 * exactly the run where the numbers are worth having. `resetReport()` clears
 * the file once per run so nothing leaks in from the last one.
 */
export function writeReport(): void {
  const path = reportPath();
  mkdirSync(dirname(path), { recursive: true });
  const merged = [
    ...previousResults(path).filter(
      (earlier) => !results.some((current) => current.route === earlier.route),
    ),
    ...results,
  ];
  const payload = {
    profile: {
      viewport: "390x844",
      device_scale_factor: 3,
      mobile: true,
      cpu_throttle: CPU_THROTTLE,
      network_throttle: "none (loopback stack; see playwright.perf.config.ts)",
    },
    budgets: BUDGETS,
    enforcement: {
      cls: "enforced",
      lcp: ENFORCE_LCP ? "enforced" : "advisory",
      acknowledgement: ENFORCE_ACK ? "enforced" : "advisory",
      structural: "enforced",
    },
    routes: merged,
    skipped: merged.filter((entry) => entry.skipped).map((entry) => entry.route),
  };
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`);
  renderTable(path, merged);
}

function previousResults(path: string): RouteMeasurement[] {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
    const routes = (parsed as { routes?: unknown }).routes;
    return Array.isArray(routes) ? (routes as RouteMeasurement[]) : [];
  } catch {
    return [];
  }
}

function renderTable(path: string, entries: RouteMeasurement[]): void {
  const header = [
    pad("route", 14),
    pad("LCP ms", 9),
    pad("CLS", 8),
    pad("ack ms", 8),
    "structural",
  ].join(" ");
  const lines = [
    "",
    `[browser-timing] mobile 390x844, DPR 3, CPU throttle ${CPU_THROTTLE}x`,
    `[browser-timing] budgets: LCP <= ${BUDGETS.lcpMs} ms, CLS <= ${BUDGETS.cls}, ` +
      `acknowledgement <= ${BUDGETS.ackMs} ms ` +
      `(CLS and structural enforced; LCP ${ENFORCE_LCP ? "enforced" : "advisory"}; ` +
      `acknowledgement ${ENFORCE_ACK ? "enforced" : "advisory"})`,
    header,
    "-".repeat(header.length),
  ];
  for (const entry of entries) {
    if (entry.skipped) {
      lines.push(`${pad(entry.route, 14)} skipped — ${entry.skipped}`);
      continue;
    }
    const claims = Object.entries(entry.structural);
    const failed = claims.filter(([, claim]) => !claim.ok).map(([name]) => name);
    lines.push(
      [
        pad(entry.route, 14),
        pad(entry.lcpMs === undefined ? "-" : entry.lcpMs.toFixed(0), 9),
        pad(entry.cls === undefined ? "-" : entry.cls.toFixed(4), 8),
        pad(
          entry.acknowledgement === undefined ? "-" : entry.acknowledgement.ms.toFixed(1),
          8,
        ),
        failed.length === 0 ? `${claims.length}/${claims.length} ok` : `FAILED: ${failed.join(", ")}`,
      ].join(" "),
    );
  }
  for (const entry of entries) {
    for (const [name, claim] of Object.entries(entry.structural)) {
      lines.push(`  ${claim.ok ? "ok  " : "FAIL"} ${entry.route}/${name}: ${claim.detail}`);
    }
  }
  lines.push(`[browser-timing] report written to ${path}`, "");
  console.log(lines.join("\n"));
}

function pad(value: string, width: number): string {
  return value.length >= width ? value : `${value}${" ".repeat(width - value.length)}`;
}
