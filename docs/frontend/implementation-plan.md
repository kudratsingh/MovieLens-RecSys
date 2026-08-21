# Movie-discovery frontend: implementation plan

**Status:** Bundles 0–2 merged; Bundles 3–4 implementation complete and pending merge; Bundles 5–7 handed off, not implemented

**Last updated:** 2026-08-21

## Delivery principle

The redesign proceeds as a set of reviewable vertical slices. Frontend shell
and visual-state work can run alongside backend contracts, but a route does not
claim completion until its persistence, authorization, loading, accessibility,
and test contracts are real.

The current dashboard remains available behind a temporary legacy route or
feature flag until the authenticated Discover → Library → feedback → refreshed
prediction loop passes browser and load gates.

## Dependency map

```text
Bundle 0: discovery, route contracts, backend audit, testing contract
  ├─> Bundle 1: identity + feedback ADR and API contract infrastructure
  │     ├─> Bundle 2: state schema, RLS, committed mutations, Library API
  │     └─> Bundle 3: catalog query, local metadata, movie detail
  └─> Bundle 4: frontend shell, tokens, route split, independent BFF resources

Bundles 2 + 3 + 4
  └─> Bundle 5: Discover, Browse, Library, and movie-detail vertical slices
          └─> Bundle 6: Quick Picks and serving feedback integration
                  └─> Bundle 7: finish gate, page-shaped load, legacy removal
```

Bundle numbers describe delivery order, not one mandatory PR each. Each PR
remains coherent and includes the tests and documentation for the behavior it
changes.

Implementation stops after Bundle 4 in the current delivery. The exact starting
state, PR cuts, risks, commands, and acceptance criteria for the next owner are
recorded in [the Bundles 5–7 handoff](bundles-5-7-handoff.md).

## Bundle 0 — Discovery and contracts

**Goal:** Freeze the product problem, interaction architecture, truthfulness
boundary, backend dependencies, and verification plan before visual code.

**Artifacts:**

- product discovery and reference-pattern study;
- source-backed baseline plus screenshot matrix;
- route-level design contracts;
- frontend testing/finish-gate contract;
- backend-readiness audit;
- frontend experience ADR 0002; and
- accepted cross-cutting ADR 0012.

**Exit:** The two audiences, first-read object, route map, feedback semantics,
identity distinction, safe product claims, backend blockers, PR order, and
PASS/HOLD criteria are explicit. Source audit is complete. Rendered baseline
captures remain a required pre-visual-change artifact and are not silently
treated as complete without the seeded browser harness.

## Bundle 1 — Identity, durability, and contract infrastructure

**Progress as of 2026-08-21:**

- [x] Accept ADR 0012.
- [x] Commit authenticated request transactions before returning success.
- [x] Return failure when commit fails instead of exposing a false 2xx.
- [x] Align online routing to five unique watched movie IDs.
- [x] Support API and browser calling clients with an explicit API audience.
- [x] Require the demo-impersonator role for browser persona selection.
- [x] Reject verified Keycloak realms missing from the tenant registry.
- [x] Prove real browser login, protected API access, and local-session logout
  with bypass disabled; unit-test token rotation and rejected refresh.
- [x] Commit generated OpenAPI and TypeScript contracts with CI drift gates.

**Backend:**

- preserve ADR 0012 while implementing its remaining browser-session and
  feedback-state decisions;
- define the OIDC audience/calling-client contract for `movielens-web` → API;
- add server-side BFF session, refresh/logout, CSRF/origin, tenant-registry, and
  actor authorization contracts;
- choose `/me` ownership, role-gated persona mode, or both explicitly;
- maintain the versioned OpenAPI artifact with operation IDs, auth/error
  schemas, constrained inputs, generated TypeScript types, and CI drift
  detection;
- move successful mutation and fail-closed prediction commits before response;
  and
- align online routing to the accepted five-interaction threshold.

**Frontend:**

- implement the server-side session boundary and auth-required/error states;
- distinguish signed-in actor from selected demo persona in navigation and
  content labels; and
- introduce independent BFF clients with request IDs, timeouts, runtime
  validation, and private/no-store behavior.

**Exit:** A real browser token reaches `/whoami`; unknown tenant and forbidden
persona access fail; a successful mutation is immediately readable; histories
0/1/3 use fallback and 5/10 use learned serving; generated client types are in
sync.

**Completed evidence:** the browser-auth Playwright flow signs in through the
real Keycloak PKCE page with `DEV_AUTH_BYPASS=false`, reaches the role-gated API
and `/whoami`, proves tokens are absent from the public session response,
rejects a mutation without CSRF proof, and propagates logout. Refresh success
and failure are pinned by server-token tests. The authenticated baseline matrix
is stored under [`evidence/baseline/`](evidence/baseline/).

## Bundle 2 — Durable feedback and Library foundation

**Progress as of 2026-08-21:**

- [x] Add forced-RLS `user_movie_state` and append-only `user_feedback_events`.
- [x] Backfill latest legacy ratings without rewriting imported MovieLens rows.
- [x] Enforce composite identity, rating/state constraints, tenant-leading
  indexes, least-privilege grants, and state revisions.
- [x] Implement idempotent watched/rating/watchlist/dismissal mutations with
  canonical replay and optimistic-revision conflicts.
- [x] Separate rating deletion from destructive watched-history removal.
- [x] Add cursor-paginated Rated, Watchlist, and History Library resources.
- [x] Add `live-ratings-v1` taste summaries with non-model attribution copy.
- [x] Build the authenticated selected-persona `/library` tabs, counts, URL
  sort/filter, optimistic reconciliation, rollback, empty/error states, and
  focus recovery.
- [x] Move live history, seen filtering, and demo rating overlays to the new
  projection while retaining raw ratings for source/training provenance.

**Backend:**

- add tenant-scoped `user_movie_state` and append-only feedback events;
- backfill deliberately from legacy ratings without mutating raw MovieLens
  inputs;
- enforce composite identity, rating constraints, RLS, grants, actor ownership,
  indexes, and state revisioning;
- implement idempotent watched, rating, watchlist, and dismissal mutations;
- separate rating deletion from removal of watched history;
- implement cursor-based Rated, Watchlist, and History resources; and
- add a live-ratings taste summary whose copy does not claim model attribution.

**Frontend:**

- build `/library` tabs, counts, URL-preserved sort/filter state, empty/error
  states, optimistic feedback with canonical reconciliation, and focus recovery;
- build the shared watched/rating/watchlist/dismissal controls; and
- label the selected persona throughout persona mode.

**Exit:** State transitions are idempotent and immediately readable; rating
edits preserve watched time; watchlist has no model effect; dismissal excludes
without seeding or training; tenant and same-tenant non-owner tests pass.

## Bundle 3 — Scalable catalog and movie detail

**Progress as of 2026-08-21:**

- [x] Add filter-bound cursor pagination with deterministic movie-ID tie-breakers.
- [x] Add searchable title, genre, year, and sort composition with a 48-item cap.
- [x] Add a persisted shared metadata read model and source-status fields.
- [x] Move Browse, detail, and recommendation metadata off live TMDB fan-out.
- [x] Expand the fixture to 120 visible titles and 480 background interactions.
- [x] Assert visible, poster-backed, and recommendable fixture coverage separately.
- [x] Move catalog/detail SQL off the async event loop.
- [x] Add local movie detail and the complete durable user-state overlay.
- [x] Build Browse grid/search/filter/load-more/fallback/scroll-restoration behavior.
- [x] Build movie detail with source-aware fallbacks and rating interaction.
- [x] Regenerate OpenAPI and TypeScript contracts.

**Backend:**

- add a cursor-paginated, searchable, filterable catalog contract with stable
  tie-breakers and user-state overlay;
- pre-enrich the reviewed demo catalog and/or add a shared non-RLS metadata read
  model for poster/year/grid fields;
- add movie detail with overview and metadata-source status;
- keep live per-card TMDB calls off Browse and recommendation critical paths;
- expand the clean fixture toward roughly 120 visible movies;
- add explicit background interactions and regenerate artifacts so prediction
  coverage is measured separately from browse coverage; and
- move blocking catalog/detail database access off the async event loop.

**Frontend:**

- build poster-card primitives and all metadata fallbacks;
- build `/browse` grid, search, filters, active-filter state, load more/cursor,
  keyboard traversal, and scroll restoration; and
- build `/movies/[movieId]` with state controls and progressive evidence.

**Exit:** Search/filter/cursor combinations are deterministic; page size is
bounded; poster failures do not collapse layout; Browse generates no per-card
TMDB fan-out; visible and recommendable coverage are separately asserted.

**Exit status:** Integrated atop Bundles 1 and 2. Catalog and detail reads use
the server Auth.js token, rating writes use the established Origin/CSRF boundary,
and the complete watched/rating/watchlist/dismissal state is overlaid.

## Bundle 4 — Frontend system and route split

This bundle can begin after Bundle 0 and run beside Bundles 1–3 using recorded
contract fixtures. It must not hard-code unresolved backend semantics.

**Work:**

- formalize semantic surface/text/accent/focus/status/poster tokens;
- implement responsive top and bottom navigation;
- split the monolithic dashboard into route-owned server/client boundaries;
- build poster, rail, collection, state-control, drawer/sheet, error-boundary,
  skeleton, and empty-state primitives;
- make recommendation, catalog, Library, and technical evidence load
  independently; and
- add Vitest, React Testing Library, user-event, axe, and Playwright scaffolding.

**Exit:** Every route shell renders against typed fixtures at 390, 768, and
1440 widths; core actions are keyboard reachable; one upstream failure does not
erase unrelated content; lint, typecheck, build, component, and axe checks pass.

## Bundle 5 — Core movie-discovery vertical slices

Ship small end-to-end slices rather than one all-routes visual rewrite:

1. `/discover`: primary recommendation, honest fallback label, ranked rail,
   poster fallbacks, watched/watchlist controls, `Why this?` disclosure.
2. `/browse`: real catalog query state, filters, cursor continuation, movie
   detail navigation and scroll restoration.
3. `/library`: Rated, Watchlist, and History backed by canonical state.
4. `/movies/[movieId]`: overview, state management, source-aware metadata, and
   structured explanation when present.

Each slice ships loading, empty, partial-error, auth-expired, reduced-motion,
mobile, keyboard, tenant, browser, and visual evidence with the happy path.

**Exit:** A user can discover, browse, save, mark watched, rate, find/edit that
state, and refresh recommendations without an all-or-nothing dashboard fetch.
Technical evidence is reachable in two deliberate actions and does not block
the first movie.

## Bundle 6 — Quick Picks and serving feedback

**Backend:**

- pass `positive_history_movie_ids` and `excluded_movie_ids` separately;
- enforce dismissal in fallback, retrieval, hydration, and final validation;
- record state revision/input hashes, feature freshness, filters, and
  source-item similarity contributions in prediction audits; and
- expose structured reasons without presenting the rank score as probability.

**Frontend:**

- add the optional one-movie Quick Picks queue;
- provide equal button, keyboard, and gesture behavior;
- implement Watchlist, Watched → optional rating, Not for me, undo, failure
  rollback, progress to five signals, queue exhaustion, and exit to Browse; and
- respect reduced motion.

**Exit:** Dismissed items never seed or appear; watchlist changes no model
inputs; histories below five remain fallback; source explanations are
traceable; gesture and non-gesture paths have identical outcomes.

## Bundle 7 — Finish gate and cutover

- complete the required desktop/tablet/mobile state matrix;
- run moderated movie-viewer and technical-reviewer tasks;
- pass component, contract, backend, RLS/ownership, browser-auth, accessibility,
  visual, and performance suites;
- retain the direct recommendation p99 gate and add page-shaped BFF/catalog/
  Library/mutation load budgets;
- verify metrics, request IDs, readiness, rate limits, and degraded TMDB modes;
- run the written UI Finish-Gate review and record PASS or HOLD; and
- remove the legacy dashboard only after the new end-to-end loop passes.

## Deferred beyond this redesign

- Rating-aware ranker labels and online features.
- SHAP-based user-facing explanations.
- Calibrated match percentages.
- Cast, trailers, streaming availability, and social features.
- Full 62,423-title eager TMDB enrichment.
- Phase 4 retraining/promotion automation, Phase 5 drift surfaces, and Phase 6
  champion/challenger controls.

If star magnitude becomes model-relevant, it receives a separate ADR covering
graded labels, negative/neutral evidence, point-in-time features, online
freshness, cache invalidation, evaluation metrics, retraining, and promotion.
