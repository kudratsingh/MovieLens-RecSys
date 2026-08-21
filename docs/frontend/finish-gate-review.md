# Movie-discovery frontend: UI finish-gate review

**Verdict: HOLD.**

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
| Legacy removal and cutover | PR 7d | Blocked on this verdict; see §1 |
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
| `MOVIELENS_UI_PORT=3113 npm run test:e2e:ui` | 187 tests across `mobile-390`, `tablet-768`, `desktop-1440`, pass, 43.5 s |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e` | 7 tests, pass, 13.9–15.2 s (three consecutive runs) |
| `npx playwright test tests/e2e/finish-gate-journey.spec.ts` | pass ×3 standalone (7.4 s, 6.5 s, 6.6 s) |
| `PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:perf` | 5 tests, pass — 7b's gate re-run on this branch, [numbers below](#performance-gate--pass-carried-from-7b) |

Every row above was re-run after rebasing onto `876fd36` (PR #62, 7b).

### 3.2 CI

The same gates on a clean runner, against a Compose stack built from scratch:
[PR #63](https://github.com/kudratsingh/MovieLens-RecSys/pull/63), CI run
[32523881379](https://github.com/kudratsingh/MovieLens-RecSys/actions/runs/32523881379).
The `frontend` job carries the visual and accessibility gate; `browser-auth-e2e`
carries the service-backed journey and then 7b's browser timing;
`synthetic-load-smoke` carries the unchanged direct-API p99 gate.

Seeded state at capture time, read from the API in the browser's own session:

```json
{"name":"item-item-cosine+lightgbm","learned":true,"positive_signal_count":8,
 "threshold":5,"reason":"learned-two-stage: item-item-cosine retrieval over 0
 positive seeds, ranked by demo-lgbm-v1","score_scale":"lightgbm-rank-score",
 "filter_policy":"watched-and-dismissed-excluded-v1","excluded_count":8}
```

So the learned path was genuinely exercised, not assumed: the warm persona was
above the five-signal threshold and the response reported learned serving.

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
| 2. Mark three movies watched and rate them | Yes | Detail and Discover both; rating is one press on detail, and `Watched` reveals the stars on Discover | None. The star note is explicit that magnitude is display feedback |
| 3. Find and change one of those ratings | Yes | `/library?tab=rated`, filter by title, edit in the row | None |
| 4. Save a movie and retrieve it from the watchlist | Yes | Detail `Watchlist` → Library `Watchlist` tab | None. Marking watched later consumes the entry, which the contract intends but the UI does not say out loud |
| 5. Explain why one recommendation appeared | Yes | `Why this?` → reason, policy, versions, request ID | None |
| 6. Start from Cold Start and build a useful state | Yes | `/quick-picks?user=900000104` — one card, three equal actions, keyboard shortcuts, progress toward five | Reachable only from `/discover`, which is itself unreachable by navigation ([B2](#b2-discover-has-no-inbound-link)) |
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

One fidelity defect was found and fixed in this PR rather than filed:
`--text-muted` failed WCAG AA against every surface darker than the page
canvas — 4.40 on `--surface-base`, 4.13 behind a Library tab count, 3.55 on a
poster-fallback thumbnail — which put six serious axe violations across request
IDs, tab counts, and freshness notes. The token now clears 4.5:1 against every
surface token, and `web/e2e/finish-gate.spec.ts` fails if that regresses.

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
| No horizontal overflow at 320px | Every state in the matrix, swept at 320×640 |

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
| No production route silently falls back to a recorded fixture | ✅ | `web/lib/resources/fixture-gate.ts` throws outside fixture mode; `resources-fixture-lockout.test.ts` asserts the server client imports no fixture; `web/e2e/discover.spec.ts` proves a live read with no API fails visibly instead of showing recorded data |
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
