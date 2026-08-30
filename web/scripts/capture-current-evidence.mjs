import { execFileSync } from "node:child_process";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

/**
 * The current build's screenshot matrix.
 *
 * The evidence folder had grown thirteen sets deep and not one of them was a
 * picture of the product as it stands: the bundle folders are differential by
 * design, and the newest complete set predates the featured queue, the enriched
 * movie page, the ranked rail, the featured-skip preference, and the Seen tab.
 * A document that wants to show what this looks like today had nothing to link.
 *
 * So this set is complete rather than differential, and its output directory is
 * a **stable name** — `docs/frontend/evidence/current/` — overwritten in place
 * on every re-shoot. The dated folders remain the historical record; this one is
 * the answer to "what does it look like now", and a link to
 * `current/discover-desktop-1440.png` keeps meaning that after the next round.
 *
 * Every capture is service-backed: the seeded Compose stack with
 * `DEV_AUTH_BYPASS=false`, real Keycloak, real FastAPI, real RLS, the local
 * catalog snapshot, the feature and model servers, and the web BFF.
 *
 * **Persona ownership is the journeys' table, and this script only reads.**
 * Discover as Drama Fan, Browse as Eclectic Viewer, Library and movie detail as
 * Action Fan, Quick Picks as Cold Start. Nothing here presses a decision
 * control. Opening a disclosure is a read; marking a movie watched is not, and
 * a capture that spent one of the signals Cold Start is meant to have none of
 * would leave the persona
 * dirty for the journeys, the k6 page workload, and every later run.
 *
 * The run writes its own README from what it observed — the commit, the serving
 * policy each persona was actually served, the catalog's poster coverage, the
 * enrichment behind the movie page. Provenance is what makes these evidence
 * rather than pictures, and a hand-written table drifts from the pictures it
 * describes on the first re-shoot.
 *
 * Usage:
 *   MOVIELENS_DEMO_URL=http://localhost:3001 node scripts/capture-current-evidence.mjs
 */

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../..");
const output = resolve(repoRoot, "docs/frontend/evidence/current");

const BASE = process.env.MOVIELENS_DEMO_URL ?? "http://localhost:3001";

const MOBILE = { suffix: "mobile-390", width: 390, height: 844 };
const TABLET = { suffix: "tablet-768", width: 768, height: 1024 };
const DESKTOP = { suffix: "desktop-1440", width: 1440, height: 1000 };
const ALL = [MOBILE, TABLET, DESKTOP];

const ACTION_FAN = 900000101;
const DRAMA_FAN = 900000102;
const ECLECTIC = 900000103;
const COLD_START = 900000104;

const PERSONA_NAMES = {
  [ACTION_FAN]: "Action Fan",
  [DRAMA_FAN]: "Drama Fan",
  [ECLECTIC]: "Eclectic Viewer",
  [COLD_START]: "Cold Start",
};

/**
 * The movie page's subject: The Matrix, which Action Fan has in seeded history
 * and which the enrichment gave a backdrop, a tagline, a runtime and six cast
 * members. A title with no `details` would photograph the degraded page and
 * call it the product.
 */
const DETAIL_MOVIE = 2571;

/** Enough windows to walk the whole 120-title fixture and notice if it grew. */
const COVERAGE_PAGE_LIMIT = 20;

/**
 * The per-file budget. A poster-dense page at 1440 lands between 200 and 600 KB
 * at scale factor 1; the one or two that carry a full-bleed backdrop go over, so
 * they are re-encoded through a palette rather than left to bloat the repo. The
 * quantisation happens here rather than afterwards so the size this run reports
 * is the size that ships.
 */
const MAX_BYTES = 700 * 1024;

const SIGNED_OUT = [
  {
    name: "sign-in-door",
    path: "/",
    persona: null,
    note: "The only unauthenticated screen in the product.",
    viewports: [MOBILE, DESKTOP],
    settle: (page) =>
      page.getByRole("button", { name: "Continue with Keycloak" }).waitFor(),
  },
];

const SIGNED_IN = [
  {
    name: "discover",
    path: `/discover?userId=${DRAMA_FAN}`,
    persona: DRAMA_FAN,
    note: "The front door for a signed-in viewer: the featured queue position, the ranked rail, and the watch history beside them.",
    viewports: ALL,
    settle: (page) => page.locator("section.featured-movie h1").waitFor(),
  },
  {
    name: "discover-why-this",
    path: `/discover?userId=${DRAMA_FAN}`,
    persona: DRAMA_FAN,
    note: "Both disclosure steps open, scrolled to the second: the recorded prediction audit — policy, model and feature versions, feature event time, input-state revision and hash, request id, per-stage latency — and the feature values behind the rank score.",
    viewports: [DESKTOP],
    settle: openTechnicalEvidence,
  },
  {
    name: "browse",
    path: `/browse?user=${ECLECTIC}`,
    persona: ECLECTIC,
    note: "The catalog on its default Most watched here ordering, with the shared control family on every card.",
    viewports: ALL,
    settle: (page) => page.getByRole("list", { name: "Browse results" }).waitFor(),
  },
  {
    name: "movie-detail",
    path: `/movies/${DETAIL_MOVIE}?user=${ACTION_FAN}`,
    persona: ACTION_FAN,
    note: "An enriched title: backdrop, tagline, runtime, crowd score, cast, and the shared rating control.",
    viewports: ALL,
    settle: (page) => page.locator("h1#movie-title").waitFor(),
  },
  {
    name: "library",
    path: `/library?userId=${ACTION_FAN}&tab=rated`,
    persona: ACTION_FAN,
    note: "The Rated collection, which is the Library's default tab.",
    viewports: ALL,
    settle: (page) =>
      page.getByRole("tab", { name: /Rated/, selected: true }).waitFor(),
  },
  {
    name: "library-seen",
    path: `/library?userId=${ACTION_FAN}&tab=history`,
    persona: ACTION_FAN,
    note: "The Seen tab: search, genre and year filters, five rankings, and the spotlight walking the filtered list above it.",
    viewports: ALL,
    settle: async (page) => {
      await page.getByRole("tab", { name: /Seen/, selected: true }).waitFor();
      // The spotlight is the half of this tab that a picture of the list alone
      // would miss, and it mounts only once a row has arrived to feature.
      await page.locator("#library-spotlight").waitFor();
    },
    measure: measureFilterRow,
  },
  {
    name: "quick-picks",
    path: `/quick-picks?user=${COLD_START}`,
    persona: COLD_START,
    note: "One decision at a time, photographed at zero signals. No control on this page is pressed.",
    viewports: ALL,
    // The card, not the deck wrapper: the queue arrives from the API and the
    // wrapper is on screen before it does.
    settle: (page) => page.locator(".quick-pick-card h1").waitFor(),
  },
];

/**
 * Walks the two deliberate actions the route contract puts between a viewer and
 * the model evidence, and leaves the second one's tables in view.
 *
 * Both are reads. The drawer renders from the response already on screen; the
 * button inside it issues the audit and online-feature GETs. Neither writes.
 */
async function openTechnicalEvidence(page) {
  await page.locator("section.featured-movie h1").waitFor();
  await page.getByRole("button", { name: "Why this?" }).first().click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("button", { name: "Show prediction audit" }).click();

  // Ready, not merely open: both regions have to be past their loading state,
  // or the capture is a picture of two skeletons.
  await page.waitForFunction(
    () =>
      Boolean(
        document.querySelector('section[aria-labelledby="audit-heading"] dl') &&
          document.querySelector('section[aria-labelledby="features-heading"] dl'),
      ),
    null,
    { timeout: 30_000 },
  );

  // The drawer is taller than its panel and the audit is the point of this
  // capture, so put the audit heading at the top of the panel. `scrollIntoView`
  // rather than `scrollIntoViewIfNeeded`: the heading starts one pixel inside
  // the panel's bottom edge, which counts as visible, so "if needed" did
  // nothing and the capture was of an empty scroll position.
  await page.evaluate(() =>
    document
      .querySelector("#audit-heading")
      ?.scrollIntoView({ behavior: "instant", block: "start" }),
  );
}

/**
 * The geometry of the collection filter row, measured rather than eyeballed.
 *
 * The Seen tab puts a search field, two year bounds and a submit button in one
 * form, and at the two narrower viewports the form is handed less width than
 * its content needs. A picture shows that something is wrong; these four
 * measurements say what, and — because they are re-taken on every re-shoot —
 * they stop saying it the moment the layout is fixed, which a paragraph in a
 * README would not.
 */
async function measureFilterRow(page) {
  return page.evaluate(() => {
    const form = document.querySelector(".library-filter");
    const input = form?.querySelector(".library-input");
    const button = form?.querySelector("button");
    if (!form || !input || !button) return null;
    return {
      formWidth: Math.round(form.clientWidth),
      contentWidth: Math.round(form.scrollWidth),
      inputWidth: Math.round(input.getBoundingClientRect().width),
      buttonOverhang: Math.round(
        button.getBoundingClientRect().right - form.getBoundingClientRect().right,
      ),
    };
  });
}

/**
 * Blocks until every `<img>` the screenshot will contain has finished, then
 * refuses to continue if any of them finished badly.
 *
 * A poster that is still decoding photographs as an empty reserved box, and one
 * that failed photographs as a broken frame. Both look like product defects in
 * a document that is meant to show the product working, and both are invisible
 * to a screenshot call that only waits for the network to go quiet.
 *
 * Scoped to the viewport on purpose. These are viewport captures, and the rails
 * and grids below the fold are deliberately lazy — an off-screen `loading=lazy`
 * image never reports `complete`, so waiting on the whole document waits
 * forever for artwork that is not in the picture. The count is returned so the
 * README can state it rather than imply it.
 */
async function imagesSettled(page) {
  const visible = `() => {
    const inViewport = (image) => {
      const box = image.getBoundingClientRect();
      return (
        box.width > 0 &&
        box.height > 0 &&
        box.bottom > 0 &&
        box.right > 0 &&
        box.top < window.innerHeight &&
        box.left < window.innerWidth
      );
    };
    return Array.from(document.images).filter(inViewport);
  }`;

  await page.waitForFunction(
    `(${visible})().every((image) => image.complete)`,
    null,
    { timeout: 30_000 },
  );
  const seen = await page.evaluate(`(() => {
    const images = (${visible})();
    return {
      total: images.length,
      decoded: images.filter((image) => image.naturalWidth > 0).length,
      broken: images
        .filter((image) => image.naturalWidth === 0)
        .map((image) => image.currentSrc || image.src),
    };
  })()`);
  if (seen.broken.length) {
    throw new Error(
      `${seen.broken.length} image(s) failed to load and would photograph as a broken frame:\n  ${seen.broken.join("\n  ")}`,
    );
  }
  return seen;
}

async function capture(page, item) {
  const files = [];
  const measurements = {};
  for (const viewport of item.viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto(`${BASE}${item.path}`, { waitUntil: "networkidle" });
    await item.settle?.(page);
    const images = await imagesSettled(page);
    if (item.measure) measurements[viewport.suffix] = await item.measure(page);
    const path = resolve(output, `${item.name}-${viewport.suffix}.png`);
    await page.screenshot({ animations: "disabled", path });
    const size = await enforceBudget(path);
    files.push({ name: basename(path), viewport: viewport.suffix, size });
    console.log(
      `captured ${item.name}-${viewport.suffix} (${kb(size)}, ${images.decoded}/${images.total} in-viewport images decoded)`,
    );
  }
  return { ...item, files, measurements };
}

function kb(bytes) {
  return `${Math.round(bytes / 1024)} KB`;
}

/**
 * Re-encodes a capture through a palette if it came out over budget, and
 * returns the size that is actually on disk afterwards.
 *
 * `sharp` is Next's own dependency rather than one added for this, and it is
 * imported only when a file needs it, so a tree without it still produces the
 * matrix — just with the oversized file left as it is and a line saying so.
 */
async function enforceBudget(path) {
  let { size } = await stat(path);
  if (size <= MAX_BYTES) return size;

  let sharp;
  try {
    ({ default: sharp } = await import("sharp"));
  } catch {
    console.warn(`${basename(path)} is ${kb(size)}, over the ${kb(MAX_BYTES)} budget, and sharp is unavailable to reduce it`);
    return size;
  }

  const original = size;
  // Read first: sharp would otherwise be reading the file this is about to
  // overwrite.
  const encoded = await sharp(await readFile(path))
    .png({ palette: true, effort: 10 })
    .toBuffer();
  await writeFile(path, encoded);
  ({ size } = await stat(path));
  console.log(`  quantised ${basename(path)}: ${kb(original)} -> ${kb(size)}`);
  return size;
}

/** The serving policy the API actually applied, read through the BFF. */
async function servingPolicy(page, userId) {
  return page.evaluate(async (id) => {
    const body = await fetch(`/api/users/${id}/recommendations?limit=10`, {
      cache: "no-store",
    }).then((response) => response.json());
    return body.serving_policy ?? null;
  }, userId);
}

/**
 * Poster coverage over the whole catalog rather than the first window.
 *
 * The 2026-08-28 enrichment exists because 96 of 120 titles had no artwork, and
 * the first Browse window was never where that showed — it is sorted by
 * popularity, which is exactly where the posters already were. So this walks
 * every cursor page.
 */
async function catalogCoverage(page, userId) {
  return page.evaluate(
    async ({ id, pageLimit }) => {
      let cursor = null;
      let pages = 0;
      let total = 0;
      let withPoster = 0;
      let withOverview = 0;
      do {
        const query = new URLSearchParams({ limit: "48", sort: "title" });
        if (cursor) query.set("cursor", cursor);
        const body = await fetch(`/api/users/${id}/catalog?${query}`, {
          cache: "no-store",
        }).then((response) => response.json());
        const items = body.items ?? [];
        total += items.length;
        withPoster += items.filter((item) => item.poster_url).length;
        withOverview += items.filter((item) => item.overview).length;
        pages += 1;
        cursor = body.page?.has_more ? body.page.next_cursor : null;
      } while (cursor && pages < pageLimit);
      return { pages, total, withPoster, withOverview, truncated: Boolean(cursor) };
    },
    { id: userId, pageLimit: COVERAGE_PAGE_LIMIT },
  );
}

/** What the movie page had to work with, so the picture of it can be read. */
async function detailEnrichment(page, userId, movieId) {
  return page.evaluate(
    async ({ id, movie }) => {
      const body = await fetch(`/api/users/${id}/movies/${movie}`, {
        cache: "no-store",
      }).then((response) => response.json());
      const item = body.item ?? {};
      const details = item.details ?? null;
      return {
        title: item.title ?? null,
        releaseYear: item.release_year ?? null,
        poster: Boolean(item.poster_url),
        backdrop: Boolean(details?.backdrop_url),
        tagline: Boolean(details?.tagline),
        runtime: details?.runtime_minutes ?? null,
        // `tmdb_rating` is `{ average, count }`, and the page prints both.
        tmdbRating: details?.tmdb_rating
          ? `${details.tmdb_rating.average}/10 from ${details.tmdb_rating.count} ratings`
          : null,
        cast: (details?.cast ?? []).length,
        trailer: Boolean(details?.trailer),
      };
    },
    { id: userId, movie: movieId },
  );
}

async function signIn(page) {
  await page.goto(`${BASE}/`);
  await page.getByRole("button", { name: "Continue with Keycloak" }).click();
  await page.waitForURL(/\/realms\/demo\/protocol\/openid-connect\/auth/, {
    timeout: 60_000,
  });
  await page.locator("#username").fill("demo");
  await page.locator("#password").fill("demo");
  await page.locator("#kc-login").click();
  await page.getByRole("button", { name: "Sign out" }).waitFor({ timeout: 60_000 });
}

function git(...args) {
  return execFileSync("git", args, { cwd: repoRoot, encoding: "utf8" }).trim();
}

function policyLine(policy) {
  if (!policy) return "no serving policy reported";
  const learned = policy.learned ? "`learned: true`" : "`learned: false`";
  const signals = `${policy.positive_signal_count} positive signal${policy.positive_signal_count === 1 ? "" : "s"}`;
  return `\`${policy.name}\`, ${learned}, ${signals}, reason \`${policy.reason}\`, filter \`${policy.filter_policy}\``;
}

/**
 * Reports the filter-row geometry, and only when it is wrong.
 *
 * A set that photographs a defect and says nothing about it invites the reader
 * to assume the capture is at fault. A set that describes one in prose keeps
 * describing it after it is fixed. So the finding is emitted from the numbers
 * this run measured, and disappears on the re-shoot that no longer measures
 * them.
 */
function filterRowFinding(captures) {
  const seen = captures.find((item) => item.name === "library-seen");
  const rows = Object.entries(seen?.measurements ?? {}).filter(([, value]) => value);
  // Two different failures, and a viewport can have either: the form overflows
  // its own box, or it fits only because the search field has been crushed to a
  // width nobody can type into.
  const broken = rows.filter(
    ([, value]) =>
      value.contentWidth > value.formWidth ||
      value.buttonOverhang > 0 ||
      value.inputWidth < 80,
  );
  if (!broken.length) return "";

  const table = rows
    .map(
      ([viewport, value]) =>
        `| \`${viewport}\` | ${value.formWidth} px | ${value.contentWidth} px | ${value.inputWidth} px | ${value.buttonOverhang > 0 ? `${value.buttonOverhang} px past the form` : "none"} |`,
    )
    .join("\n");

  return `
## A defect these pictures record

The Seen tab's filter row does not fit at the two narrower viewports, and the
captures show it rather than hide it. Measured by this run, on the same page
load that was photographed:

| Viewport | Form width | Content width | Search field | Button overhang |
|---|---|---|---|---|
${table}

Where the content is wider than the form, the \`Filter\` button is painted
outside its own form and lands on the \`Genre\` control beside it; where the
search field is a few dozen pixels wide, it is a text input nobody can read what
they typed into. The Rated and Watchlist tabs are unaffected — they carry the
same form without the year bounds, which is the part that will not shrink.

This is a product defect on \`main\`, not a capture artifact: it reproduces on
reload, at every width below the point where \`.library-filter\` reaches its
\`max-width\`, and the same row is correct at \`desktop-1440\`. It belongs to the
Seen work rather than to this evidence set, so it is recorded here and fixed
elsewhere. These four numbers are re-measured on every re-shoot, so this section
goes away on its own when the layout is fixed.
`;
}

function readme({ capturedAt, commit, dirty, policies, coverage, detail, captures }) {
  const totalBytes = captures.flatMap((item) => item.files).reduce((sum, file) => sum + file.size, 0);
  const rows = captures
    .map((item) => {
      const persona = item.persona
        ? `${PERSONA_NAMES[item.persona]} (${item.persona})`
        : "signed out";
      const widths = item.files.map((file) => `\`${file.viewport}\``).join(", ");
      return `| \`${item.name}-*\` | ${persona} | ${widths} | ${item.note} |`;
    })
    .join("\n");

  const policyRows = Object.entries(policies)
    .map(([userId, policy]) => `| ${PERSONA_NAMES[userId]} (${userId}) | ${policyLine(policy)} |`)
    .join("\n");

  const fileRows = captures
    .flatMap((item) => item.files)
    .map((file) => `| \`${file.name}\` | ${kb(file.size)} |`)
    .join("\n");

  return `# Current build evidence

The product as it stands on \`main\`, at the three contracted viewports, plus the
signed-out door and the model-evidence disclosure.

This directory has a **stable name and is overwritten in place** on every
re-shoot, so a document can link \`current/discover-desktop-1440.png\` and keep
meaning the current build. The dated folders beside it stay as they are: each one
is the record of what a particular bundle changed, and none of them is a picture
of the whole product. This one is.

## Provenance

| | |
|---|---|
| Captured | ${capturedAt} |
| Commit | \`${commit}\`${dirty ? ", with uncommitted changes under `web/`, `src/`, `synthetic/` or `infra/` — the images were built from that tree rather than from the commit alone" : ""} |
| Stack | \`docker-compose.yml\` + \`docker-compose.demo.yml\`, project \`movielens-demo\`, images built from this tree (\`make demo-up\`) and seeded (\`make demo-seed\`) |
| Smoke | \`make demo-smoke\` passed before the first capture |
| Auth | Real Keycloak, \`DEV_AUTH_BYPASS=false\`, authorization code + PKCE through the Next BFF, \`demo\`/\`demo\` |
| Viewports | 390×844, 768×1024, 1440×1000 · \`colorScheme: dark\` · device scale factor 1 · \`prefers-reduced-motion: reduce\` |
| Catalog | ${coverage.total} rows over ${coverage.pages} cursor page${coverage.pages === 1 ? "" : "s"}: **${coverage.withPoster}/${coverage.total} posters**, **${coverage.withOverview}/${coverage.total} overviews**${coverage.truncated ? " (walk stopped at the page cap)" : ""} |
| Images | Every capture waited for every in-viewport \`<img>\` to finish decoding; a broken frame fails the run rather than shipping. The rails and grids below the fold stay lazy, which is the behaviour under test elsewhere |

Reduced motion is emulated because the product honours it by shortening
transitions and nothing else — \`globals.css\` sets durations to \`0.01ms\` and
leaves every layout rule alone — so it removes capture flake without changing
what is being photographed.

### Serving policy each persona was actually served

Printed by the capture run from a live \`GET /users/{id}/recommendations\`, not
asserted from the design contract.

| Persona | \`serving_policy\` |
|---|---|
${policyRows}

Cold Start reporting a fallback policy is the correct answer, not a defect: the
persona is seeded empty and the threshold is ten positive signals.

### The movie page's subject

\`${detail.title}\`, movie ${DETAIL_MOVIE}, chosen because it is in Action Fan's
seeded history *and* carries enrichment — a title without \`details\` would
photograph the degraded page and pass it off as the product. What the API
returned for it at capture time:

- poster: ${detail.poster ? "present" : "missing"}
- backdrop: ${detail.backdrop ? "present" : "missing"}
- tagline: ${detail.tagline ? "present" : "missing"}
- runtime: ${detail.runtime !== null ? `${detail.runtime} min` : "missing"}
- TMDB score: ${detail.tmdbRating ?? "missing"}
- cast: ${detail.cast} named
- trailer: ${detail.trailer ? "present" : "missing"}

## Capture command

\`\`\`bash
make demo-up          # build api + web from this tree
make demo-seed        # seed the reviewed 120-title fixture
make demo-smoke       # must pass: warm personas have to report learned: true
cd web
MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:current
\`\`\`

\`web/scripts/capture-current-evidence.mjs\` signs in through Keycloak, records the
policies and coverage above, captures each surface, and rewrites this file from
what it observed. It **only reads**: no capture presses a decision control, so
Quick Picks is photographed as Cold Start without spending one of that persona's
signals. Persona assignment follows the ownership table in
\`web/tests/e2e/browser-auth.spec.ts\`.

## The matrix

| Surface | Persona | Widths | What it shows |
|---|---|---|---|
${rows}

## Files

${captures.flatMap((item) => item.files).length} PNGs, ${kb(totalBytes)} total.

| File | Size |
|---|---|
${fileRows}
${filterRowFinding(captures)}
## What these pictures are not

They are evidence, not the gate. A screenshot proves a surface rendered once on
one stack; what actually holds these surfaces is
\`web/e2e/finish-gate.spec.ts\` (the named state matrix with axe at 390/768/1440
plus a 320px sweep), the fixture-mode route specs beside it, the serialized
service-backed journeys in \`web/tests/e2e/\`,
\`web/tests/perf/browser-timing.spec.ts\` for LCP, CLS and the structural
promises, and \`make catalog-verify\` for the stored poster URLs.

Nor are they a substitute for the moderated sessions
\`docs/frontend/finish-gate-review.md\` is still holding on. Pictures show what
the product looks like; they say nothing about whether anyone can use it.
`;
}

await mkdir(output, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "dark", deviceScaleFactor: 1 });
await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });

const captures = [];
for (const item of SIGNED_OUT) captures.push(await capture(page, item));

await signIn(page);

const policies = {};
for (const userId of [DRAMA_FAN, ACTION_FAN, ECLECTIC, COLD_START]) {
  policies[userId] = await servingPolicy(page, userId);
  console.log(`${PERSONA_NAMES[userId]} (${userId}) served ${JSON.stringify(policies[userId])}`);
}

/*
 * The one precondition worth refusing to capture without.
 *
 * A stack whose model sidecar is down, or whose features never materialized,
 * still serves every one of these pages — it just serves the popularity
 * fallback, and the pictures come out looking like a product that has no
 * learned path. That is the single most misleading thing this set could
 * publish, and it is invisible in a screenshot: the pages are pretty either
 * way. So it is checked here rather than left to whoever remembers to run
 * `make demo-smoke` first.
 *
 * Cold Start is checked from the other direction. Its capture is *about* the
 * zero-signal state, so a persona some earlier run left dirty would make the
 * Quick Picks frame a picture of nothing in particular.
 */
const notLearned = [DRAMA_FAN, ACTION_FAN, ECLECTIC].filter(
  (userId) => !policies[userId]?.learned,
);
if (notLearned.length) {
  await browser.close();
  throw new Error(
    `Refusing to capture: ${notLearned
      .map((userId) => `${PERSONA_NAMES[userId]} was served ${policies[userId]?.name ?? "nothing"}`)
      .join(", ")}. A warm persona on the fallback usually means the model sidecar or the materialized features are missing — run \`make demo-seed && make demo-smoke\` and diagnose before re-shooting.`,
  );
}
if (policies[COLD_START]?.positive_signal_count !== 0) {
  await browser.close();
  throw new Error(
    `Refusing to capture: Cold Start is carrying ${policies[COLD_START]?.positive_signal_count} positive signal(s) and has to be handed on at zero. Re-seed before re-shooting.`,
  );
}

const coverage = await catalogCoverage(page, ECLECTIC);
console.log(
  `catalog coverage: ${coverage.withPoster}/${coverage.total} posters, ${coverage.withOverview}/${coverage.total} overviews over ${coverage.pages} page(s)`,
);

const detail = await detailEnrichment(page, ACTION_FAN, DETAIL_MOVIE);
console.log(`movie ${DETAIL_MOVIE} enrichment: ${JSON.stringify(detail)}`);

for (const item of SIGNED_IN) captures.push(await capture(page, item));

await browser.close();

await writeFile(
  resolve(output, "README.md"),
  readme({
    capturedAt: new Date().toISOString().slice(0, 10),
    commit: git("rev-parse", "HEAD"),
    // Scoped to the trees the demo images are built from. The whole working
    // tree is dirty by definition once this run has written its own PNGs, so
    // asking about all of it would answer "yes" every time and mean nothing.
    dirty: git("status", "--porcelain", "--", "web", "src", "synthetic", "infra").length > 0,
    policies,
    coverage,
    detail,
    captures,
  }),
  "utf8",
);
console.log(`wrote ${resolve(output, "README.md")}`);
