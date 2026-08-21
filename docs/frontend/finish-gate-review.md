# Movie-discovery frontend: UI finish-gate review

**Verdict: HOLD — and moderated research with real participants is the only
thing standing between this document and PASS.** Every criterion a reviewer can
settle is settled and passing after the 7d cutover; see
[§10](#10-re-run-after-cutover-7d), which supersedes the verdict below.

This document is written in two dated passes. Sections 1–9 are the **7A**
pass and are left as the record of what was true then, including its HOLD and
its three blocking items. [Section 10](#10-re-run-after-cutover-7d) is the
**7D** re-run after the cutover that cleared them, and is the current verdict.

**Reviewed:** 2026-08-21 · **Bundle:** 7A · **Base:** `876fd36` (7b merged)

**Reviewer:** one engineer, against a locally seeded Compose stack. Where a
criterion requires participants rather than a reviewer, this document says so
and marks the criterion accordingly rather than substituting an opinion for
evidence.

This is the written gate the [testing strategy](testing-strategy.md#ui-finish-gate)
and the [Bundles 5–7 handoff](bundles-5-7-handoff.md#bundle-7--finish-gate-and-cutover)
require before the legacy dashboard is removed. It records what was run, what
was observed, a verdict per criterion, and the exact work that would flip each
open item.

---

## 1. Verdict

**HOLD.** Three blocking items, listed in [§7](#7-blocking-items). None of them
is inside the new movie-discovery routes: `/discover`, `/browse`,
`/movies/[movieId]`, `/library`, and `/quick-picks` pass every criterion this
gate applies to them, service-backed, at all three widths. What fails is the
product they are assembled into — its front door is still the pre-redesign
dashboard, that dashboard states a serving policy the deployed router
contradicts, `/discover` has no inbound link from anywhere, and two of the five
routes run a different shell that drops the contracted mobile navigation.

All three are cutover work, and all three are small.

### A note on sequencing, because this looks circular

The handoff says to keep `/legacy` "until the new journey passes" and to remove
it "only after PASS". Read strictly against a HOLD, that deadlocks: the cutover
waits for PASS, and PASS waits for the cutover.

It is not circular, because the two halves of the cutover are separable. The
handoff's rule protects a **rollback**, and nothing here proposes removing it.
The implementation plan is the more precise statement of intent:

> The current dashboard remains available **behind a temporary legacy route or
> feature flag** until the authenticated Discover → Library → feedback →
> refreshed prediction loop passes browser and load gates.

The loop now passes its browser gate ([§3](#3-what-was-run)). The dashboard is
not behind a temporary legacy route: it *is* `/`, and `/legacy` is an alias of
it rather than its new home. So the honest reading of this HOLD is:

- **The new journey has passed.** Nothing below asks for more work inside the
  five product routes.
- **The cutover may proceed**, and it is what clears every blocking item.
  `/legacy` stays as the rollback.
- **Re-run this gate after the cutover**; on the evidence here it flips to PASS.

---

## 2. What this gate covers, and what it does not

| Area | Owner | Status here |
|---|---|---|
| Service-backed ten-step browser journey | This PR | Run, green, three consecutive local passes |
| Visual and accessibility gate | This PR | Automated and green; screenshot matrix captured |
| Product finish review | This PR | Below |
| Page-shaped load, browser timing (LCP/CLS/ack) | PR 7b, merged | Cited; the browser half re-run on this branch — see [§5](#performance-gate--pass-carried-from-7b) |
| Legacy removal and cutover | PR 7d | Cutover done, `/legacy` retained as the rollback — see [§10](#10-re-run-after-cutover-7d) |
| Moderated research with real participants | Not scheduled | **Not met** — see [§4.2](#42-moderated-tasks) |

---

## 3. What was run

### 3.1 Local commands and results

The stack: `docker compose -p movielens-demo -f docker-compose.yml -f
docker-compose.demo.yml -f <local port override> …`, mirroring `make demo-up` /
`make demo-seed`. `DEV_AUTH_BYPASS=false`, real Keycloak with the imported
`demo` realm, forced RLS, the local catalog snapshot, Feast's feature server,
the model server, and the web BFF. The only local deviation is the published
port of the API container, moved off `8000` because another project holds it;
nothing in the stack reaches the API through the host.

| Command | Result |
|---|---|
| `npm ci` | clean |
| `npm run api:types:check` | pass — no OpenAPI/TypeScript drift |
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm test` | 43 files, 399 tests, pass |
| `npm run build` | pass |
| `MOVIELENS_UI_PORT=3113 npm run test:e2e:ui` | 201 across `mobile-390`, `tablet-768`, `desktop-1440` — 187 run, 14 skipped by project, pass |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e` | 7 tests, pass, 13.9–15.2 s (three consecutive runs) |
| `npx playwright test tests/e2e/finish-gate-journey.spec.ts` | pass ×3 standalone (7.4 s, 6.5 s, 6.6 s) |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:perf` | 5 tests, pass — 7b's gate re-run on this branch, [numbers below](#performance-gate--pass-carried-from-7b) |

Every row above was re-run after rebasing onto `876fd36` (PR #62, 7b).

Seeded state at capture time, read from the API in the browser's own session:

```json
{"name":"item-item-cosine+lightgbm","learned":true,"positive_signal_count":8,
 "threshold":5,"reason":"learned-two-stage: item-item-cosine retrieval over 0
 positive seeds, ranked by demo-lgbm-v1","score_scale":"lightgbm-rank-score",
 "filter_policy":"watched-and-dismissed-excluded-v1","excluded_count":8}
```

So the learned path was genuinely exercised, not assumed: the warm persona was
above the five-signal threshold and the response reported learned serving.

### 3.2 CI

The same gates on a clean runner, against a Compose stack built from scratch:
[PR #63](https://github.com/kudratsingh/MovieLens-RecSys/pull/63) —
[all checks](https://github.com/kudratsingh/MovieLens-RecSys/pull/63/checks).
The `frontend` job carries the visual and accessibility gate; `browser-auth-e2e`
carries the service-backed journey and then 7b's browser timing;
`synthetic-load-smoke` carries the unchanged direct-API p99 gate.

Worth recording because it is the point of running a gate on a machine that is
not the author's: the service-backed journey passed on the first clean-runner
attempt ([run 32524174806](https://github.com/kudratsingh/MovieLens-RecSys/actions/runs/32524174806)),
and the accessibility gate failed it — the Library filter row overflowed 320px
by 10px on the runner's fonts and not on macOS. That defect and the change it
forced in the check are described under
[implementation fidelity](#6-implementation-fidelity--hold).

### 3.3 The service-backed journey

`web/tests/e2e/finish-gate-journey.spec.ts` runs the handoff's ten steps end to
end in one browser session. It honours the persona ownership table the run
already uses — Action Fan for the movie-state and Library work, Drama Fan for
Discover, Eclectic Viewer for Browse, Cold Start for the fallback label and the
dismissal — and reverses every write it makes.

| Step | What it proves |
|---|---|
| 1 | Keycloak PKCE sign-in; the four named personas are offered; the shell labels the selected persona separately from the signed-in actor |
| 2 | Copy follows the reported policy in both directions: learned copy only when `serving_policy.learned` is true, and Cold Start below threshold never borrows it |
| 3 | `Why this?` quotes the response; the audit read is one further action and does not stand in front of the movie; no percentage match is claimed |
| 4 | Browse search, a genre filter that invalidates the cursor, cursor continuation with no repeated title, detail, and a return to the same query, window, and scroll position |
| 5 | Watchlist saved and retrievable from the Library tab; watched; rated; canonical committed state read back independently with a server-issued revision, and the documented watchlist-clearing watched transition |
| 6 | The rating is findable and editable in Rated and History; deleting the star leaves the interaction; removing history is confirmed and takes the rating with it |
| 7 | `Refreshing recommendations` before the refetch answers and `Recommendations refreshed` only after; the watched title leaves the next ranked set; no retraining claim anywhere |
| 8 | A Quick Picks dismissal is honoured by serving, undo restores eligibility, and neither moves the positive signal count |
| 9 | An injected 503, an injected 401, a real cleared session, and aborted poster requests — each with its own recovery |
| 10 | Sign out; every product route redirects to the door; an authenticated BFF read answers 401 |

Failure injection is Playwright `route` interception at the BFF boundary, so the
page, the components, and the resource state machine are the shipped ones and
only the bytes the BFF returns are replaced. No production route was given a
demo mode to make this possible.

### 3.4 CI wiring, and why

The journey was appended to the existing serialized `npm run test:e2e` set
rather than given its own job. Measured: it adds **6.1 s locally** to a set that
now runs in 13.9 s, against a "≤ ~2 min" bar. A separate `finish-gate` job would
have re-paid the whole Compose bring-up — several minutes — to save nothing, and
would have run a second writer against the same seeded database, which is the
exact hazard the serialized single-worker configuration exists to prevent.

The accessibility gate lives at `web/e2e/finish-gate.spec.ts`, inside the
`playwright.ui.config.ts` project set that the `frontend` job already runs via
`npm run test:e2e:ui`. No workflow change was needed for it either.

One pre-existing spec was made re-runnable in passing: `discover-journey.spec.ts`
asserted a first write against revision `0`, which is only true the first time
that journey is ever run — removing a watched interaction leaves the state row
behind at a higher revision. It now retries once through the conflict re-read,
the same correction the Quick Picks deck relies on. Without it the service-backed
set could not be run twice against one stack, which is how it was verified here.

---

## 4. Research validation

### 4.1 Five-second test

Protocol: 390×844, first stable render, three questions.

**On `/discover` — PASS.**

| Question | Answer |
|---|---|
| What is this? | A movie recommender. A poster, a title, a year, genres, and a one-line reason. |
| Is it for you? | Yes — it names the persona it is exploring as, and the reason is about that persona's history. |
| What should you do first? | `Open movie`, the one filled button on the screen. |

Evidence: `evidence/bundle-7a/discover-learned-mobile.png`,
`discover-fallback-mobile.png`.

**On `/` after signing in — HOLD.**

| Question | Answer |
|---|---|
| What is this? | An ML systems demo. The first words are `TWO-STAGE RECOMMENDER` and `Movies selected with the system visible.` |
| Is it for you? | Unclear. The first panel is a `SERVING CONTRACT` table of candidate policy, isolation, and target latency. |
| What should you do first? | Unclear. The first interactive controls are four persona chips and a numeric user-ID field. |

The testing strategy is explicit that "identifying the page only as an ML demo
is a HOLD", and this is the screen a signed-in viewer actually meets first.
Evidence: `evidence/bundle-7a/landing-after-sign-in-mobile.png`.

### 4.2 Moderated tasks

**Not met, and not meetable from this PR.** The protocol calls for four to five
movie-focused participants and three to four technical reviewers, with
keyboard-only and small-screen coverage in the mix, and it states that persona
simulations "do not count as validation data". One reviewer walking the tasks is
an expert walkthrough. Recorded below as exactly that.

Task success is reported as observed; **time on task, abandonment, scan counts,
comprehension, and confidence are not reported, because they require a
participant and inventing them would be worse than leaving them blank.**

| Discovery task | Completed | Path taken | Friction observed |
|---|---|---|---|
| 1. Find a movie to watch tonight | Yes | `/discover` → primary movie → `Open movie` | The learned persona's #1 pick has no artwork ([N1](#n1-the-first-learned-recommendation-has-no-poster)) |
| 2. Mark three movies watched and rate them | Partly — two, deliberately | Detail and Discover both; rating is one press on detail, and `Watched` reveals the stars on Discover | None. A third repeat adds no new path, and every write here has to be reversed to leave the personas as the ownership table expects. The star note is explicit that magnitude is display feedback |
| 3. Find and change one of those ratings | Yes | `/library?tab=rated`, filter by title, edit in the row | None |
| 4. Save a movie and retrieve it from the watchlist | Yes | Detail `Watchlist` → Library `Watchlist` tab | None. Marking watched later consumes the entry, which the contract intends but the UI does not say out loud |
| 5. Explain why one recommendation appeared | Yes | `Why this?` → reason, policy, versions, request ID | None |
| 6. Start from Cold Start and build a useful state | Partly | `/quick-picks?user=900000104` — one card, three equal actions, keyboard shortcuts, progress toward five. A dismissal and its undo were driven through serving here; that a committed watched signal moves the counter is proven in `browser-auth.spec.ts` | Cold Start was **not** driven to five signals: the ownership table requires it be handed on at zero, so the learned transition itself is unproven from the UI and is asserted at the serving layer instead. Quick Picks is also reachable only from `/discover`, which is itself unreachable by navigation ([B2](#b2-discover-has-no-inbound-link)) |
| 7. Find the serving policy and model version | Yes | `Why this?` → `Show prediction audit`. Two deliberate actions | None |

Technical-reviewer walkthrough: the policy label, the reason string, the
candidate and ranker versions, the feature freshness, the request ID, and the
prediction audit are all reachable in two actions from the primary movie, and
the request ID on screen matches the one the BFF propagated. The one thing a
reviewer will notice and should: the learned response's own reason says
"item-item-cosine retrieval over **0 positive seeds**" while the policy reports
learned serving. The UI quotes it verbatim rather than smoothing it, which is
the correct behaviour — see [N2](#n2-learned-serving-reports-zero-positive-seeds).

---

## 5. UI finish-gate criteria

Applied in the documented order.

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Product legibility | **HOLD** | [B1](#b1-the-authenticated-front-door-is-the-pre-redesign-dashboard), [B2](#b2-discover-has-no-inbound-link) · `landing-after-sign-in-*.png` vs `discover-learned-*.png` |
| 2 | Hierarchy | PASS | `discover-*`, `library-populated-*`, `quick-picks-*`, `movie-detail-*` |
| 3 | Pattern fit | PASS | `web/e2e/*.spec.ts`, `finish-gate-journey.spec.ts` steps 4 and 8 |
| 4 | States | PASS | `discover-{loading,empty,upstream-error,poster-error}-*.png`, `library-empty-*`, journey step 9 |
| 5 | Responsive behaviour | **HOLD** | [B3](#b3-browse-and-movie-detail-run-a-second-shell) · `movie-detail-mobile.png` |
| 6 | Implementation fidelity | **HOLD** | [B3](#b3-browse-and-movie-detail-run-a-second-shell) |
| 7 | Truthfulness | **HOLD** | [B1](#b1-the-authenticated-front-door-is-the-pre-redesign-dashboard) · inside the product routes: PASS |
| — | Accessibility gate | PASS | `web/e2e/finish-gate.spec.ts`, all three projects |
| — | Performance and reliability gate | PASS (carried from 7b) | [below](#performance-gate--pass-carried-from-7b) |

### 1. Product legibility — HOLD

On `/discover`, a movie and a movie decision are the first and only things in
the first viewport, at every width. On the route a signed-in viewer actually
lands on, the first viewport is an architecture hero and a serving-contract
table. Both blocking items are in [§7](#7-blocking-items).

### 2. Hierarchy — PASS

Visual weight follows the decision: poster, title, year and genres, the reason,
then one filled primary action and two secondary ones, then the status line,
then `Why this?`, then the rail. Technical evidence is never above the movie —
`finish-gate-journey.spec.ts` step 3 asserts the audit region is absent from the
document until it is asked for.

Two non-blocking observations: [N3](#n3-the-discover-empty-state-stacks-awkwardly-at-390px)
and [N4](#n4-library-spends-the-first-mobile-viewport-on-identity-copy).

### 3. Pattern fit — PASS

Every rail, grid, tab, drawer, and gesture serves a named job. The rail has
keyboard controls and a `See all`; the Quick Picks swipe is an enhancement with
button and keyboard equivalents proven to reach identical outcomes
(`web/e2e/quick-picks.spec.ts`); the cursor grid never claims a total it cannot
compute; Quick Picks is a deliberate entry point from Discover rather than a
fourth navigation slot. No forbidden default appears inside the product routes.

### 4. States — PASS

Loading, empty, upstream-error, auth-expired, not-found, poster-failure,
pending, disabled, focus, and confirmation states are all intentional, all
captured, and all asserted. Regions fail independently: journey step 9 kills the
catalog read and leaves the search box, the filters, and the shell usable; the
fixture matrix shows a dead recommendation region beside a readable watch
history. A failed poster does not move the layout — asserted at ≤ 1px of
horizontal delta, on a page first proven to be serving optimized posters so the
injection could not be vacuous.

### 5. Responsive behaviour — HOLD

Mobile preserves the job on Discover, Library, and Quick Picks: one primary
decision, thumb-reachable actions, a persistent bottom navigation, and no
horizontal overflow at 390px or at 320px on any state in the matrix.

It does not on Browse and movie detail, which render a different header with no
bottom navigation at all — see [B3](#b3-browse-and-movie-detail-run-a-second-shell).
Browse is one of the three primary routes the design contract puts in that
navigation.

### 6. Implementation fidelity — HOLD

Tokens, the movie-state control family, the write path, and the resource state
model are shared and consistent — that was 7c's work and it holds. The
inconsistency is at the shell: two of five routes run their own header, and it
labels the persona with a raw numeric ID where the others resolve the display
name. [B3](#b3-browse-and-movie-detail-run-a-second-shell).

Two fidelity defects were found by this gate and fixed here rather than filed.

**Muted text failed WCAG AA.** `--text-muted` cleared 4.5:1 against the page
canvas but not against the surfaces the muted role is actually used on — 4.40 on
`--surface-base`, 4.13 behind a Library tab count, 3.55 on a poster-fallback
thumbnail — which put six serious axe violations across request IDs, tab counts,
and freshness notes. The token now clears 4.5:1 against every surface token.

**Library overflowed a 320px viewport by 10px**, and only CI saw it. The filter
row is a flex item at its default `min-width: auto`, so its min-content width —
the input's minimum plus the whole `Filter` button — was a floor it would not go
below, and the button was pushed off-screen. The input inside already set
`min-width: 0`, which only lets it shrink within a row the form had already
refused to narrow. It fits on macOS and not on a Linux runner, because the two
have different system fonts and this row had no slack.

That second one changed the check as well as the code. The 320px sweep is a
font-metric test whether or not it admits it, so it now runs twice — once on the
platform's own fonts and once with a deliberately wide monospace forced onto
text *and* form controls. A row that survives that has real slack; one that does
not is one system-font change away from breaking, wherever it happens to be
measured. Both fixes are guarded by `web/e2e/finish-gate.spec.ts`.

### 7. Truthfulness — HOLD

Inside the product routes, every personalization statement matches tested
backend behaviour, and the journey asserts it rather than asserting the copy:

- learned copy appears only when `serving_policy.learned` is true, in both
  directions, against a live response;
- no `Because you liked`, and no percentage match, anywhere;
- `Recommendations refreshed` is said only after the refetch resolves, proven by
  holding the refetch open and asserting the intermediate frame;
- the rating note states that a 1 and a 5 are the same learned signal today;
- watchlist copy says it changes no recommendation input;
- dismissal is proven to be an exclusion and proven not to move the positive
  signal count;
- the Library summary is labelled `live-ratings-v1` and disclaims being a
  deployed-model explanation.

The failure is outside them: `/` asserts `Candidate policy: Popularity baseline`
in the same session in which the API reports `item-item-cosine+lightgbm` with
`learned: true`. [B1](#b1-the-authenticated-front-door-is-the-pre-redesign-dashboard).

### Accessibility gate — PASS

`web/e2e/finish-gate.spec.ts`, run at 390×844, 768×1024, and 1440×1000 over the
twelve named states:

| Requirement | How it is checked |
|---|---|
| Zero critical or serious axe violations | axe-core injected into the assembled document, `wcag2a/aa`, `wcag21a/aa`, `best-practice`; minor and moderate findings are surfaced, not silently dropped |
| One logical heading hierarchy | Exactly one `h1` on a content state, none on a headless one, and no skipped level anywhere in the outline |
| Named landmarks and navigation | Exactly one `main`; every `nav` named, and no two named the same |
| Visible focus | Computed outline and shadow compared focused against unfocused, so a global `outline: none` and a real ring cannot both pass |
| Keyboard completeness | The tab ring is walked from the top of the document and must reach `Open movie`, `Watchlist`, `Mark watched`, `Not for me`, and `Why this?`; the drawer opens on Enter and returns focus to its trigger on Escape |
| 44×44 mobile targets | Every visible control in the Discover action row, the Browse toolbar, and the detail state row |
| State by text and semantics | `aria-selected` on tabs, `aria-pressed` on the watchlist toggle, and failure regions that say what happened in words |
| Poster alternative text | Every image declares an `alt`; an empty one is only allowed where the title is adjacent in text; filenames and the bare word "image" are rejected |
| Focus restoration | Drawer close, and the Library and detail confirmation cancel paths |
| Reduced motion | The Quick Picks fling degrades under `prefers-reduced-motion`, and no transition on Discover survives the global reduce rule |
| Forced colours | `forced-colors: active` emulated: the title keeps a colour distinct from the surface, every button carries a border, nothing overflows |
| No horizontal overflow at 320px | Every state in the matrix, swept at 320×640 twice: on the platform's own fonts and with a wide monospace forced onto text and form controls, so the result describes the layout rather than the runner |

### Performance gate — PASS (carried from 7b)

Cited rather than re-measured. PR #62 delivers this half of the gate and it is
on `main` ahead of this branch:

- `synthetic/load/pages.js` and `page_thresholds.js` — page-shaped workloads for
  the recommendation/history/catalog BFF fan-out, cursor continuation, Library
  reads, mutation-plus-immediate-read, and Quick Picks actions, run by
  `make demo-load-pages`. Advisory on PR CI, enforced nightly.
- `web/tests/perf/browser-timing.spec.ts` — `npm run test:perf`, run in
  `browser-auth-e2e` after the journeys on a written mobile profile. LCP and CLS
  are enforced; the 100 ms acknowledgement budget is advisory.
- `synthetic/load/reliability.py` — ten reliability facts, including request-ID
  traceability, readiness, the auth boundary, and degraded metadata operation.
- The direct-API p99 gate is unchanged and still enforced by
  `synthetic-load-smoke`.

Two things about that gate belong in this review rather than only in 7b's.

**Rate limiting is recorded as not implemented.** The handoff's reliability list
names "rate-limit behavior" as something to verify, and 7b's fact table records
that there is nothing to verify yet. That is the honest outcome and it is not
counted as a pass here: it is an open gap, non-blocking for this gate because
no product claim depends on it, and it needs a decision — implement per-tenant
limits against the Phase 3 tenant-config row, or record the omission in an ADR.

**The browser half was re-run on this branch**, because a token change to
`--text-muted` touches every route 7b measures and a green gate elsewhere is not
evidence about this tree. `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run
test:perf`, on 7b's pinned mobile profile (390×844, DPR 3, 4× CPU throttle):

| Route | LCP | CLS | Acknowledgement | Structural |
|---|---:|---:|---:|---|
| discover | 120 ms | 0.0000 | 10.4 ms | 5/5 |
| browse | 116 ms | 0.0000 | 15.9 ms | 5/5 |
| movie-detail | 84 ms | 0.0000 | 7.7 ms | 2/2 |
| library | 100 ms | 0.0000 | 13.8 ms | 2/2 |
| quick-picks | 100 ms | 0.0000 | — | 2/2 |

These are a warm loopback stack on a developer machine, not the budget. They are
recorded to show the accessibility fix did not regress 7b's gate, and the
enforced thresholds and their artifacts stay with the job that produces them.

---

## 6. Non-blocking findings

### N1. The first learned recommendation has no poster

The warm persona's rank-1 title renders `ARTWORK UNAVAILABLE`. The fallback is
well made — initials, a label, reserved dimensions, no layout movement — and
the identity survives, but "poster-first" is the product's stated image
treatment and the very first read does not get one. This is catalog coverage,
not a UI defect: 24 of the 120 reviewed titles carry complete poster metadata,
which the handoff records as intentional. Worth an offline enrichment pass
before this is shown to anyone as a portfolio surface.

### N2. Learned serving reports zero positive seeds

`reason: "learned-two-stage: item-item-cosine retrieval over 0 positive seeds,
ranked by demo-lgbm-v1"` on a response that reports `learned: true` for a
persona with eight watched titles. The ranker is genuinely learned; the
retrieval stage contributed nothing. The UI quotes the reason verbatim and makes
no claim beyond `learned`, which is the contract, so this is not a frontend
truthfulness defect — but a reviewer reading the audit will see a two-stage
policy whose first stage is empty. It belongs in a serving follow-up: either the
seeded personas' history is outside the item-item index, or the seeds are being
dropped before retrieval.

Resolved in PR #64 — the seeds were being dropped before retrieval.

### N3. The Discover empty state stacks awkwardly at 390px

The empty state puts its icon in a left column and its copy in a narrow right
one, and the two actions wrap so that the secondary (`Rate a few in Quick picks`)
sits above the primary (`Browse the catalog`). Legible and operable — it passes
axe, overflow, and target sizing — but the reading order inverts the intended
priority. Evidence: `discover-empty-mobile.png`.

### N4. Library spends the first mobile viewport on identity copy

At 390×844 the heading and the four-line actor-versus-persona disclaimer push
the tabs to the fold and the first rated row just past it. The disclaimer is
load-bearing and should stay, but the design contract's first-read object for
this route is the collection, and at this width it is not.

### N5. Browse restores a stored window instead of re-reading

Returning to a Browse URL whose filter set is already in the tab's session
storage restores that window, for up to thirty minutes, without a catalog
request. This is deliberate, documented, bounded to 192 items, and never saved
for a failed window — and it is the only way cursor pagination can survive a
detail visit. Recorded because it changes what "reload the page" means, and
because it is the reason this gate's injected failures target filter sets the
tab has not seen.

---

## 7. Blocking items

### B1. The authenticated front door is the pre-redesign dashboard

`web/app/page.tsx` renders, for any signed-in viewer, an architecture hero
(`Movies selected with the system visible.`), a `SERVING CONTRACT` panel, and
the eighteen-card rating wall. `/legacy` re-exports the same component, so the
dashboard is not behind a legacy route — it is the default route, and `/legacy`
is its alias.

Two forbidden defaults from the design contracts are live on it: *architecture
hero before movie content*, and *model claims that exceed the observed backend
behavior*. The second is concrete and measurable: the panel states
`Candidate policy: Popularity baseline`, while the same session's
`GET /api/users/900000102/recommendations` reports
`{"name":"item-item-cosine+lightgbm","learned":true}`.

*Flips to PASS when:* `/` serves the movie-discovery product — redirecting to
`/discover`, or rendering it — and the static serving-contract panel is gone.
The dashboard keeps its rollback home at `/legacy`.

*Evidence:* `evidence/bundle-7a/landing-after-sign-in-{mobile,tablet,desktop}.png`;
`web/app/page.tsx`; `web/app/legacy/page.tsx`; the policy JSON in
[§3.1](#31-local-commands-and-results).

### B2. `/discover` has no inbound link

The product's primary route cannot be reached by clicking anything.

- `/` links `Discover → /` (itself), `Browse → /browse`, `Library → /library`,
  and hides that navigation entirely below 768px.
- `CatalogRouteHeader`, the header on `/browse` and `/movies/[movieId]`, also
  links `Discover → /`.
- Only `AppShell`, used by `/discover` and `/library`, links `For you →
  /discover`.

So a viewer reaches Discover by typing a URL, or by going to Library first and
noticing that its navigation is different. Quick Picks, whose only entry point
is Discover, inherits the problem.

*Flips to PASS when:* every authenticated surface's primary navigation points at
`/discover`, and the mobile navigation exists on all of them.

*Evidence:* `web/app/page.tsx`; `web/components/browse/route-header.tsx`;
`web/lib/navigation.ts`; `evidence/bundle-7a/landing-after-sign-in-mobile.png`,
`movie-detail-mobile.png`.

### B3. Browse and movie detail run a second shell

`CatalogRouteHeader` is a parallel header, and its own source comment names it
as interim: "Bundle 4's shell belongs to the recorded preview … so the live
catalog slice carries its own until the shell is promoted onto the authenticated
routes." Two contracted behaviours are missing from it:

1. **No mobile bottom navigation.** The design contract requires "a compact
   header plus bottom navigation for the three primary routes" on small screens.
   Discover and Library have it; Browse — one of those three — and movie detail
   do not, so navigating on a phone means scrolling back to a text link row.
2. **The persona is shown as a number.** `Exploring as persona 900000101`, where
   Discover and Library resolve `Exploring as Action Fan`. The contract's own
   example copy is the name, and the numeric form is the identity the product is
   trying to keep legible.

*Flips to PASS when:* Browse and movie detail render the product shell, with the
mobile bottom navigation and the resolved persona name.

*Evidence:* `evidence/bundle-7a/movie-detail-mobile.png` against
`discover-learned-mobile.png` and `library-populated-mobile.png`;
`web/components/browse/route-header.tsx`.

---

## 8. Handoff acceptance checklist

From [the handoff](bundles-5-7-handoff.md#handoff-acceptance-checklist).

| Item | Status | Evidence |
|---|---|---|
| Bundle 4 squash-merged and the handoff rebased onto it | ✅ | `e4af19c`, and the Bundle 5–7 PRs that followed it |
| The first Bundle 5 PR links the governing route and truthfulness contracts | ✅ | PR #53 and `docs/frontend/frontend-system.md` |
| No production route silently falls back to a recorded fixture | ✅ | `web/lib/resources/fixture-gate.ts` throws outside fixture mode; `web/tests/unit/resources-fixture-lockout.test.ts` asserts the server client imports no fixture; `web/e2e/discover.spec.ts` proves a live read with no API fails visibly instead of showing recorded data |
| No browser request forwards a caller-supplied bearer token | ✅ | `web/lib/resources/browser.ts` raises `ForwardedCredentialError`; `browser-auth.spec.ts` proves the public session carries no access, refresh, or ID token |
| Every mutation reconciles a committed canonical state and revision | ✅ | Journey step 5 reads the record back independently and asserts a server-issued revision above the previous one |
| Every user-scoped endpoint has tenant and actor authorization evidence | ✅ | `tests/tenant_isolation/` in the `tenant-isolation` CI job; journey step 10 proves the BFF answers 401 with no session |
| Fallback/learned labels follow the returned policy and the five-signal threshold | ✅ | Journey step 2, asserted in both directions against live responses |
| Watchlist, watched/rating, and dismissal keep their distinct model meanings | ✅ | Journey steps 5, 6, and 8 |
| Browse, poster, and prediction coverage are reported separately | ✅ | `docs/frontend/catalog-contract.md`; [N1](#n1-the-first-learned-recommendation-has-no-poster) records the coverage gap rather than hiding it |
| Bundle 7 records a written PASS or HOLD before `/legacy` is removed | ✅ | This document. **HOLD.** |

---

## 9. Re-running this gate

```bash
# Service-backed
make demo-up && make demo-seed
cd web && PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e
MODE=service MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7a

# Visual and accessibility
cd web && MOVIELENS_UI_PORT=3113 npm run test:e2e:ui
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3113   # in another terminal, then:
MODE=fixture MOVIELENS_UI_PORT=3113 npm run evidence:bundle7a
```

In CI: the `frontend` job runs the visual and accessibility gate; the
`browser-auth-e2e` job runs the service-backed journey against the
bypass-disabled Compose stack.

---

## 10. Re-run after cutover (7D)

**Verdict: HOLD, on one item that is not a reviewer's to close — moderated
research with real participants.** Every one of the seven finish-gate criteria
now passes, the three blocking items in [§7](#7-blocking-items) are cleared and
asserted against the running stack, and no new blocking item was found. What is
missing is validation data, and a reviewer cannot manufacture it.

**Re-run:** 2026-08-21 · **Bundle:** 7D · **Base:** `25807b2` (7a merged) plus
this branch · **Reviewer:** one engineer, against a locally seeded Compose
stack — the same limitation [§4.2](#42-moderated-tasks) records, and the same
consequence.

### 10.1 What changed

- `/` is the movie-discovery product for a signed-in viewer and the sign-in
  door for everyone else. It redirects rather than rendering Discover under a
  second address, so `/discover?userId=` stays the only URL that carries a
  persona and the navigation's active state stays honest. Both spellings of the
  persona parameter (`userId`, `user`) survive the door.
- The pre-redesign dashboard lives only at `/legacy`, labelled as legacy on
  screen, linked back to the product, authenticated like every other route, and
  reachable from a footer utility link rather than from any primary navigation.
- Its `SERVING CONTRACT` panel reports the `serving_policy` the response
  carried — policy name, learned or not, model version, and the reason string —
  and renders `Not read yet` rather than naming a policy it was not told about.
- Browse, movie detail, and Library render the shared `AppShell`. The two
  parallel headers (`CatalogRouteHeader`, `LibraryShell`) are deleted.
- `/` joined the middleware's personalized-document set, because its answer now
  depends on the session in both directions.

### 10.2 What was run

Local, against the seeded stack: `docker compose -p movielens-demo -f
docker-compose.yml -f docker-compose.demo.yml -f <local port override>`,
mirroring `make demo-up` / `make demo-seed`, with `DEV_AUTH_BYPASS=false`, real
Keycloak on the imported `demo` realm, forced RLS, the local catalog snapshot,
Feast's feature server, the model server, and the web BFF. The only local
deviation is the published host port of the API container, moved off `8000`
because another project holds it; nothing in the stack reaches the API through
the host.

| Command | Result |
|---|---|
| `npm ci` | clean |
| `npm run api:types:check` | pass — no OpenAPI/TypeScript drift |
| `npm run lint` | pass |
| `npm run typecheck` | pass |
| `npm test` | 45 files, 413 tests, pass (399 before; the additions are below) |
| `npm run build` | pass |
| `MOVIELENS_UI_PORT=3114 npm run test:e2e:ui` | 204 across `mobile-390`, `tablet-768`, `desktop-1440` — 190 run, 14 skipped by project, pass ×2. **The first attempt failed**, see [10.5](#105-the-gate-failed-the-first-time-it-was-pointed-at-the-front-door) |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e` | 7 tests, pass, 18.8–22.9 s — ×2 on the final tree, and ×2 more before the legacy contrast fix |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:perf` | 5 tests, pass ×3, [numbers below](#107-performance) |
| `MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7d` | 10 captures, [`evidence/bundle-7d/`](evidence/bundle-7d/README.md) |

Everything above was run twice over, against two stacks, because `main` moved
under this branch while it was in review — see
[10.10](#1010-verified-against-main-after-pr-64). Seeded state at capture time,
read from the API in the browser's own session, on the second of them:

```json
{"name":"item-item-cosine+lightgbm","learned":true,"positive_signal_count":8,
 "threshold":5,"reason":"learned-two-stage: item-item-cosine retrieval over 8
 positive seeds, ranked by demo-lgbm-v1","score_scale":"lightgbm-rank-score",
 "filter_policy":"watched-and-dismissed-excluded-v1","excluded_count":8}
```

That is [PR #64](https://github.com/kudratsingh/MovieLens-RecSys/pull/64)
visible in the response: `over 8 positive seeds` where the 7A run and this
branch's own first run both read `over 0 positive seeds`. It resolves
[N2](#n2-learned-serving-reports-zero-positive-seeds), and it changes which
titles every persona is served — which is why the committed evidence was
recaptured against it.

**CI on this branch: all eight checks pass** —
[run 32530254885](https://github.com/kudratsingh/MovieLens-RecSys/actions/runs/32530254885):
`frontend` (the visual and accessibility gate, including the front door added
below), `browser-auth-e2e` (the service-backed journey and 7b's browser
timing), `demo-compose`, `feature-parity`, `lint`, `synthetic-load-smoke`,
`tenant-isolation`, and `test`. That run is of the branch as pushed, before
#64.

### 10.3 Blocking items

| Item | Status | Evidence |
|---|---|---|
| [B1](#b1-the-authenticated-front-door-is-the-pre-redesign-dashboard) — the front door is the pre-redesign dashboard | **Cleared** | `evidence/bundle-7d/landing-after-sign-in-{mobile,tablet,desktop}.png` against `evidence/bundle-7a/landing-after-sign-in-*.png`; `finish-gate-journey.spec.ts` step 1 asserts the URL and the shell after the Keycloak round trip |
| B1 — the panel's false model claim | **Cleared** | `evidence/bundle-7d/legacy-dashboard-desktop.png` shows `Serving policy: item-item-cosine+lightgbm`, `Learned ranking: Yes`; journey step 1 checks the panel against the response it claims to report, not against a string the test invented; `serving-contract-panel.test.tsx` covers the no-policy and cold-start cases |
| [B2](#b2-discover-has-no-inbound-link) — `/discover` has no inbound link | **Cleared** | Journey step 1 asserts every slot of the primary navigation by `href`; `app-shell.test.tsx` asserts the same set in both the desktop and the mobile navigation. Quick Picks keeps its Discover entry point and is still not a fourth slot |
| [B3](#b3-browse-and-movie-detail-run-a-second-shell) — Browse and detail run a second shell | **Cleared** | `evidence/bundle-7d/{browse-shell,movie-detail-shell,library-shell}-mobile.png`; journey steps 4 and 5 assert the bottom navigation at 390×844 and a resolved persona name with no numeric ID in the header |

B3 is cleared by deletion rather than by convergence: `CatalogRouteHeader` and
`LibraryShell` no longer exist, so there is no second shell left to drift.

### 10.4 Criteria

Applied in the documented order. Rows unchanged from the 7A pass are marked as
carried; the gate was re-run for all of them, not only the three that moved.

| # | Criterion | 7A | Now | Evidence |
|---|---|---|---|---|
| 1 | Product legibility | HOLD | **PASS** | [10.6](#106-five-second-test) · `landing-after-sign-in-*.png` |
| 2 | Hierarchy | PASS | PASS (carried, re-run) | `finish-gate.spec.ts` outline and landmark checks across the matrix |
| 3 | Pattern fit | PASS | PASS (carried, re-run) | Journey steps 4 and 8; the legacy dashboard is a footer utility link, not a navigation slot |
| 4 | States | PASS | PASS (carried, re-run) | Injected matrix unchanged; journey step 9 |
| 5 | Responsive behaviour | HOLD | **PASS** | B3 cleared; 320px sweep now also covers the front door on two font metrics |
| 6 | Implementation fidelity | HOLD | **PASS** | One shell, one persona vocabulary; the front door left the Tailwind-utility island and uses the product's tokens |
| 7 | Truthfulness | HOLD | **PASS** | B1's panel cleared; the product routes' truthfulness findings are unchanged and still pass |
| — | Accessibility gate | PASS | PASS | `finish-gate.spec.ts`, all three projects, now including the signed-out front door |
| — | Performance and reliability | PASS | PASS | [10.7](#107-performance) |
| — | Moderated research | Not met | **Not met — requires participant sessions (owner)** | [10.8](#108-what-the-owner-must-run-to-convert-this-verdict) |

### 10.5 The gate failed the first time it was pointed at the front door

Worth recording, because it is the whole argument for putting a surface in a
gate rather than reasoning about it. Adding `/` to the accessibility matrix
failed four checks immediately, on a screen that had shipped for months:

- **The primary action's label cleared 1.31:1.** `button, input, select { color:
  inherit }` in `globals.css` is unlayered, and Tailwind v4 puts utilities in
  `@layer utilities` — so an unlayered element selector out-ranks every one of
  them. `text-zinc-950` on `Continue with Keycloak` had never applied, and the
  label rendered at `#f4f4f5` on `#ffd230`. This is a structural defect, not a
  colour choice: no utility on that element could have won.
- **The helper text cleared 2.42:1** (`text-zinc-600` on the card).
- **The door overflowed a 320px viewport** by 31px on the platform's fonts and
  48px under the forced wide face. The card sat in a `grid` whose auto track
  floors at the item's min-content width, and the longest word in the heading
  was wider than the viewport could hold.

Fixed rather than filed, by moving the door onto the same `.card-surface` /
`.button-primary` / `.eyebrow` vocabulary as the product it opens: token
colours that are already proven against this gate, `min-width: 0` on the card,
and `overflow-wrap: anywhere` on the title — `anywhere` rather than
`break-word` because only `anywhere` reduces the min-content contribution that
set the floor. Zero axe violations and zero overflow afterwards, at 320, 390,
768, and 1440.

The same structural cause was then visible in the 7D evidence on the retained
dashboard: `Explore` was white text on a white button, and the selected persona
chip and selected star cleared about 1.3:1 on amber. Fixed here too — a
rollback with an unreadable primary control is not a rollback — with one
class-scoped rule in `components/legacy/`, deliberately local. See
[N7](#n7-tailwind-colour-utilities-lose-to-the-unlayered-base-rule).

### 10.6 Five-second test

Protocol unchanged: 390×844, first stable render, three questions.

**On `/` after signing in — PASS.** Evidence:
`evidence/bundle-7d/landing-after-sign-in-mobile.png`.

| Question | Answer |
|---|---|
| What is this? | A movie recommender. A poster, `The Shawshank Redemption`, `1994 / Crime · Drama`, and one line of reason. |
| Is it for you? | Yes — `Similar to movies in this persona's watched history`, with the persona named in the shell separately from the signed-in actor. |
| What should you do first? | `Open movie`, the one filled button on the screen. |

This is the same answer `/discover` already gave in the 7A pass, which is the
point: the route a signed-in viewer actually lands on is now that route. Compare
`evidence/bundle-7a/landing-after-sign-in-mobile.png`, whose first words were
`TWO-STAGE RECOMMENDER` above a `SERVING CONTRACT` table.

**On the signed-out `/` — PASS**, against the same three questions applied to a
door rather than a product: it says what the product is, that a demo persona is
separate from the signed-in actor, and offers exactly one action. Evidence:
`evidence/bundle-7d/sign-in-door-mobile.png`.

### 10.7 Performance

`npm run test:perf` on 7b's pinned mobile profile (390×844, DPR 3, 4× CPU
throttle), three runs, all passing. Representative run:

| Route | LCP | CLS | Acknowledgement | Structural |
|---|---:|---:|---:|---|
| discover | 384 ms | 0.0000 | 11.0 ms | 5/5 |
| browse | 108 ms | 0.0000 | 13.7 ms | 5/5 |
| movie-detail | 72 ms | 0.0000 | 9.1 ms | 2/2 |
| library | 112 ms | 0.0000 | 16.8 ms | 2/2 |
| quick-picks | 112 ms | 0.0000 | — | 2/2 |

Discover's LCP moved from the 120 ms recorded in [§5](#performance-gate--pass-carried-from-7b)
to a reproducible 380–396 ms, and that deserved an explanation rather than a
shrug.

**What is measured.** The route emits two LCP candidates for the warm persona:
the server-rendered `h1` at ~72 ms — which at that moment still holds the
placeholder `Finding a strong first pick…` — and then the route status line at
~380 ms, which only exists after hydration and is the larger of the two
(12617 px² against 10980). The primary movie's poster is never among the
candidates.

**What is ruled out.** Not the cutover: the only change this PR makes to
Discover's document is a footer link that sits below the fold and is not
prefetched — measured, zero `/legacy` requests during the measurement window.
Not catalog coverage either, which was this review's first guess and was wrong:
[N1](#n1-the-first-learned-recommendation-has-no-poster) meant the warm
persona's top pick had no artwork before #64 and does have artwork after it
(`The Shawshank Redemption`, `Get Shorty`), and the LCP element and timing are
unchanged across that — 380 ms without a poster, 384 ms with one. The guess was
made plausible by a Cold Start measurement that did produce an `IMG` LCP at
88 ms, and it does not survive the second stack.

**What is open.** Why the featured poster never becomes an LCP candidate on
this route, when it does on Quick Picks and Cold Start's Discover. Recorded as
a follow-up rather than answered here: the enforced budget is 2500 ms, both
measurements clear it by more than six times, and CLS is 0.0000, so nothing
about this gates the cutover. It is written down because a route whose LCP
element is a hydration-time paragraph will report hydration cost as paint cost
for as long as that is true, and the next person to read a 380 ms number should
know that is what it is.

The direct-API p99 gate and the page-shaped load budgets are unchanged by this
PR and were not re-measured; `synthetic-load-smoke` still owns them, and it
passed on this branch.

Rate limiting is still recorded as not implemented, still non-blocking here,
and still needs the decision [§5](#performance-gate--pass-carried-from-7b)
describes.

### 10.8 What the owner must run to convert this verdict

This is the whole remaining distance to PASS. Nothing in it can be delegated to
a reviewer, and [§4.2](#42-moderated-tasks) already states why: the protocol
says persona simulations "do not count as validation data", and one engineer
walking the tasks is an expert walkthrough.

**Required — moderated sessions.** From
[the testing strategy](testing-strategy.md#moderated-task-protocol):

- **4–5 movie-focused participants** and **3–4 technical reviewers**, with
  keyboard-only and small-screen coverage present in the mix.
- The seven discovery tasks in [§4.2](#42-moderated-tasks), run against the
  cutover build — task 1 now starts at `/` rather than at a typed URL, and task
  6's Quick Picks entry is reachable by clicking for the first time.
- Capture what this document has left blank on purpose: completion and
  abandonment, time on task, errors and recovery, movie scan count before a
  decision, feedback-semantics comprehension, spontaneous comments and
  confidence, and whether the ML evidence is discoverable but non-disruptive.

**Then:** replace §4.2 with the observed data and re-record the verdict. On the
evidence in this section, nothing else is outstanding — if the sessions surface
no new defect, this becomes **PASS**, and legacy removal becomes eligible in
its own PR with the rollback documented in
[`README.md`](README.md#rolling-the-cutover-back).

**Not required for PASS, but open:** the rate-limiting decision, and the two
findings below.

### 10.9 Findings

Carried from [§6](#6-non-blocking-findings): N3, N4, and N5 — all still
non-blocking, none of them touched by this PR.

[N2](#n2-learned-serving-reports-zero-positive-seeds) is **resolved** by
[PR #64](https://github.com/kudratsingh/MovieLens-RecSys/pull/64), which found
the seeds were being dropped before retrieval. The response now reads `over 8
positive seeds`, and a retrieval no seed reached is labelled
`unseeded-retrieval` with `learned: false` rather than borrowing the learned
label — which is the backend making the same commitment the frontend's
truthfulness criterion makes.

[N1](#n1-the-first-learned-recommendation-has-no-poster) is **substantially
improved** by the same PR rather than by any coverage work: the personas are
now served titles that do carry artwork, so the first learned recommendation
has a poster. The underlying coverage gap the finding names — 24 of 120
reviewed titles with complete poster metadata — is unchanged, so a different
persona or a different ranking can still land on the fallback. Recorded as
improved, not closed.

#### N6. The product has no persona picker

The cutover removed the dashboard from the front door, and the four named demo
personas were only ever offered there. The product selects a persona by URL —
`?userId=` on Discover and Library, `?user=` on Browse, movie detail, and Quick
Picks — and the shell names the selected persona without offering a way to
change it. `/legacy` still has the chips, which is one reason it is linked from
the shell's footer rather than hidden.

Not treated as a blocking item: the design contract's shell requirement is
"authenticated actor plus an explicitly labeled selected demo persona", which
is met, and it names no picker. It is recorded because a portfolio walkthrough
wants one, and because building it inside a cutover would have put a new
control into every shell header on the same PR that re-runs the gate over those
headers. It belongs in its own change, with its own accessibility evidence.

#### N7. Tailwind colour utilities lose to the unlayered base rule

`button, input, select { color: inherit }` sits outside any cascade layer in
`globals.css`, so it beats every Tailwind colour utility on those elements
app-wide. Two surfaces were affected and both are fixed here, locally: the
sign-in door (rebuilt on tokens) and the legacy dashboard (one class-scoped
rule). The product routes are unaffected because they style controls with the
token classes rather than with utilities.

The general fix is to move the base rules into `@layer base`, which would make
every such utility apply as written. That is the better answer and it is not
made here: it changes the cascade under every route, and a cutover PR that is
also re-running the finish gate is the wrong place to take that risk. Recorded
as a follow-up.

### 10.10 Verified against `main` after PR #64

`main` moved while this branch was in review:
[PR #64](https://github.com/kudratsingh/MovieLens-RecSys/pull/64) changed which
titles every persona is served and when `learned` may be reported. A gate
re-run that only described the branch's own base would have been describing a
system that no longer exists, so this section was produced twice.

**Run one** — the branch as pushed, on `25807b2`. All commands in
[10.2](#102-what-was-run), plus CI's eight green checks.

**Run two** — a throwaway verification tree combining `origin/main` at
`fb459dc` (which includes #64) with this branch's `web/`, which is what this
branch becomes once the owner rebases it. Built and seeded as its own Compose
stack, then:

| Command | Result |
|---|---|
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e` | 7 tests, pass ×2 (20.8 s, 22.2 s) |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:perf` | 5 tests, pass |
| `MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7d` | 10 captures |

Nothing in the cutover depends on which policy the router chose, and the run
confirms it: the journey asserts copy against the response rather than against
a constant, so a stricter `learned` rule and a different ranked set change what
the assertions read without changing whether they hold. Every verdict in
[10.4](#104-criteria) is unaffected.

**The committed evidence is from run two.** It is labelled that way in
[`evidence/bundle-7d/README.md`](evidence/bundle-7d/README.md), and it matters:
capturing this branch on its own base would have committed pictures of
recommendations that `main` no longer serves. Re-running
`npm run evidence:bundle7d` after the rebase reproduces them; running it on the
branch before the rebase does not.

**The one conflict the owner will hit on the rebase**, checked with
`git merge-tree` rather than guessed at. It is in `CLAUDE.md`, and it is the
only one — `finish-gate-review.md` merges cleanly, because #64's change to it
(the `Resolved in PR #64` line under
[N2](#n2-learned-serving-reports-zero-positive-seeds)) is in a region this
branch does not touch.

Both sides insert a bullet immediately above `Remaining Phase 3 — product
track`, and both sides also edit that bullet. Resolve as:

1. keep #64's `Item-item retrieval seeds from watched history again` bullet;
2. keep this branch's `Frontend cutover — the product is the front door`
   bullet after it;
3. take **this branch's** version of `Remaining Phase 3 — product track` —
   `main`'s still lists the cutover as outstanding work, which this branch is.

### 10.11 Re-running this section

```bash
# Service-backed
make demo-up && make demo-seed
cd web && PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e
PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:perf
MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7d

# Visual and accessibility
cd web && MOVIELENS_UI_PORT=3113 npm run test:e2e:ui
```

In CI: the `frontend` job runs the visual and accessibility gate, and
`browser-auth-e2e` runs the service-backed journey and then the browser timing
against the bypass-disabled Compose stack.
