# Bundles 5–7 implementation handoff

> **Record.** Accurate as of 2026-08-21. Bundles 5–7 have since been delivered
> (PRs #53–#65) and the cutover is done. Superseded by
> [`implementation-plan.md`](../implementation-plan.md) and
> [`finish-gate-review.md`](../finish-gate-review.md). Not maintained.
>
> Kept because the finish gate is still run against the ten-step journey and the
> acceptance checklist defined here, and the review cites both by section.

**Status:** Ready for implementation after Bundle 4 merges

**Prepared:** 2026-08-21

**Scope boundary:** This document hands off Bundles 5–7. It does not implement
Bundle 5, 6, or 7.

## Outcome of Bundles 0–4

The project now has the contracts, authenticated data boundary, durable movie
state, scalable catalog, and reusable frontend system needed to finish the
movie-discovery product as vertical slices.

| Bundle | Finished capability |
|---|---|
| 0 | Product discovery, route and design contracts, baseline evidence, backend audit, testing strategy, and PASS/HOLD finish criteria. |
| 1 | Real Keycloak authorization-code + PKCE login through Auth.js, encrypted HttpOnly session, server-side refresh/logout, API audience and caller validation, registered-tenant and demo-role authorization, Origin/CSRF protection, commit-before-success, generated OpenAPI/TypeScript drift gates, and bypass-disabled browser coverage. |
| 2 | Forced-RLS `user_movie_state`, append-only feedback events, idempotent and revisioned watched/rating/watchlist/dismissal transitions, cursor-paginated Library resources, truthful live-ratings taste summary, and an authenticated selected-persona Library. |
| 3 | Persisted local catalog metadata, deterministic filter-bound cursor pagination, title/genre/year search and sort, local movie detail, complete movie-state overlay, a 120-title fixture with independent prediction coverage, authenticated catalog/detail BFF routes, and Browse/detail foundations without per-card TMDB requests. |
| 4 | Semantic design tokens, responsive authenticated navigation, route-owned shells, reusable poster/rail/grid/state/rating/drawer/loading/empty/error primitives, typed recorded fixtures, isolated partial-failure states, legacy-route preservation, component/axe tests, responsive Playwright coverage, and screenshot evidence. |

### Implemented trust and data path

```text
browser
  -> Keycloak PKCE
  -> Auth.js HttpOnly session
  -> Next.js BFF uses the server-held access token
  -> FastAPI validates issuer, audience, caller, tenant, and demo role
  -> PostgreSQL transaction sets tenant context and forced RLS applies
  -> committed canonical movie state / catalog / recommendation response
```

Browser code must never accept or forward an `Authorization` header. Mutations
continue to use the established same-origin, Auth.js CSRF, idempotency-key, and
state-revision boundary. Personalized BFF responses remain private and
`no-store`.

### Current product truth

- The selected portfolio persona can receive tenant-scoped popularity fallback
  or item-item candidates ranked by LightGBM.
- Fewer than five unique watched titles remains fallback. Five or more may use
  learned serving when the deployed artifacts are available.
- A committed watched/rating action immediately changes live history, unseen
  filtering, and the IDs available to the deployed item-item lookup.
- Star magnitude is display feedback only in the deployed recommender. A
  1-star and a 5-star rating are both observed positive interactions under ADR
  0002.
- Watchlist is organizational only. Dismissal is an undoable exclusion, not a
  learned negative.
- Feast features, item-item similarities, popularity artifacts, and LightGBM
  are snapshots refreshed by materialization/retraining, not by every rating.
- Browse contains 120 reviewed titles. Twenty-four have complete reviewed
  poster/overview metadata; partial and unavailable metadata intentionally use
  deterministic UI fallbacks.
- Browse visibility, poster completeness, and eligibility for a prediction
  policy are separate coverage measures.
- Shared movie metadata may be global. User state is tenant-scoped and forced
  through RLS.
- Current routes are role-gated selected-persona mode. They must not be called
  a private end-user library until `(tenant_id, OIDC subject)` maps to an owned
  `/me` profile.

## Bundle 5 — Core movie-discovery vertical slices

### Goal

Replace fixture-backed route content with independently loaded authenticated
resources while preserving the Bundle 4 interaction and visual system. Ship
one complete route slice per PR; do not reintroduce an all-or-nothing dashboard
request.

### PR 5A: shared live-resource boundary

- Add one server-owned client for recommendation, catalog, Library, detail, and
  technical-evidence resources.
- Preserve Auth.js access-token ownership and reject browser bearer forwarding.
- Add timeouts, private/no-store cache behavior, request-ID propagation,
  generated TypeScript types, and small runtime validators at untrusted JSON
  boundaries.
- Define resource-local `loading`, `empty`, `forbidden`, `auth-expired`,
  `not-found`, `upstream-error`, and `retry` states.
- Keep typed fixtures available only to component tests, screenshot harnesses,
  and explicit failure injection. Production route data must never silently
  fall back to a fixture.

**Exit:** Failing recommendations does not erase catalog or Library content;
failing technical evidence does not block the first movie; expired auth leads
to a clear reauthentication path.

### PR 5B: Discover

- Load recommendations independently from history and technical evidence.
- Make the primary movie the first visual read, followed by a scan-friendly
  ranked rail and an obvious Browse path.
- Render the serving policy exactly: `Popular while we learn` for fallback and
  learned copy only when the response reports learned serving.
- Reuse canonical watched, rating, watchlist, and dismissal controls.
- A successful action reconciles the returned state, then requests fresh
  recommendations. Copy says the recommendations were refreshed only after
  that request completes.
- `Why this?` displays only structured evidence that exists. It never renders a
  raw rank score as a match percentage or says `Because you liked` when only
  watched-history evidence exists.
- Technical audit/features stay behind progressive disclosure and are reachable
  in no more than two deliberate actions.

**Exit:** Learned, fallback, loading, empty, partial-error, poster-failure, and
auth-expired states work at 390, 768, and 1440 widths.

### PR 5C: Browse and movie detail

- Wire Bundle 4 Browse controls to the real Bundle 3 query contract.
- Serialize query, genre, year, sort, and cursor state without inventing a total
  count.
- Append cursor pages without duplicates, preserve deterministic ordering, and
  restore Browse position after a detail visit.
- Keep the endpoint page cap; use reserved poster dimensions and lazy loading.
- Render `complete`, `partial`, and `unavailable` metadata states without a live
  TMDB request per card.
- Wire movie detail to its real resource and the canonical shared state
  controls. Reconcile mutations from the committed response.

**Exit:** Search/filter/cursor combinations remain stable; poster errors do not
move the grid; returning from detail restores query and scroll state.

### PR 5D: Library

- Move the Bundle 4 Library presentation onto Bundle 2 Rated, Watchlist, and
  History resources.
- Preserve URL-owned tab, sort, query, and cursor state.
- Use canonical optimistic reconciliation with rollback and focus recovery.
- Keep `delete rating` distinct from `remove from history`; the latter requires
  confirmation because it also removes the positive watched interaction.
- Label the selected demo persona in the route and mutation feedback.
- Use the `live-ratings-v1` taste summary only as a summary of current ratings;
  do not attribute it to the deployed ranker.

**Exit:** A rating created from Discover or detail is immediately findable and
editable in Rated and History; watchlist actions have no recommendation claim.

### Bundle 5 verification

- Unit-test runtime validation, timeout mapping, auth expiry, and request-ID
  propagation.
- Component-test all route states with semantic queries and zero critical or
  serious axe violations.
- Prove one failed resource leaves unrelated route regions usable.
- Run bypass-disabled browser login -> Browse -> detail -> watchlist -> watched
  and rating -> Library -> refreshed Discover -> edit/remove -> logout.
- Add tenant and same-tenant authorization canaries for every new or reshaped
  user-scoped route.
- Capture the required 390x844, 768x1024, and 1440x1000 evidence matrix.

## Bundle 6 — Quick Picks and serving-feedback integration

### Backend prerequisite

The Quick Picks UI must not ship as a production claim until serving accepts
and enforces positive history and excluded IDs as separate inputs.

- Pass `positive_history_movie_ids` and `excluded_movie_ids` separately.
- Filter dismissal from popularity fallback, candidate retrieval, metadata
  hydration, and final output validation.
- Never use a dismissed item as an item-item seed or silently convert it into a
  training negative.
- Record input-state revision/hash, exclusion hash, feature event time,
  filtering policy, candidate-source contribution, and structured reason in
  prediction audits.
- Return a policy field that lets the UI prove fallback below five and learned
  serving at or above five when artifacts exist.

### Quick Picks interaction

- Present one movie at a time with poster, title, year/genres, short overview,
  and enough context to decide.
- Provide equal visible buttons, keyboard controls, and optional pointer/touch
  gestures. Swipe is enhancement, never the only route to an action.
- Actions are `Watchlist`, `Watched` with optional rating, `Not for me`, `Undo`,
  and `Exit to Browse`.
- Watchlist does not advance personalization progress.
- Watched advances the positive signal count only after canonical success.
- Not for me dismisses and excludes, does not advance positive progress, and
  offers undo.
- Show progress toward five positive watched signals without promising a
  learned transition until the returned policy confirms it.
- Queue exhaustion offers Browse and restart paths; a failed mutation keeps the
  current card and restores controls.
- Respect reduced motion and announce mutation/progress changes without a
  chattering live region.

### Bundle 6 verification

- State-machine tests for buttons, keyboard, gestures, undo, retries, queue
  exhaustion, and reduced motion.
- Contract tests proving action-to-state and action-to-model semantics.
- Serving tests at history sizes 0, 1, and 3 for fallback and 5 and 10 for the
  learned path when artifacts exist.
- Tests proving dismissal is absent from every serving stage and never becomes
  a positive seed.
- Browser parity tests demonstrating that button, keyboard, and gesture paths
  produce identical canonical outcomes.
- Mobile and desktop Quick Picks screenshots, including failure and reduced-
  motion states.

## Bundle 7 — Finish gate and cutover

### Service-backed browser gate

Run against the seeded Compose stack with `DEV_AUTH_BYPASS=false`, real
Keycloak, RLS, local catalog metadata, features, model server, and the web BFF.
Fixture-only Playwright does not satisfy this gate.

Required journey:

1. Sign in through Keycloak and select a named demo persona.
2. Distinguish learned and cold-start policy labels.
3. Open a recommendation and inspect a supported explanation.
4. Search/filter Browse, continue the cursor, open detail, and return with
   state restored.
5. Watchlist, mark watched, rate, and observe canonical committed state.
6. Find and edit/remove the state in Library.
7. Refresh Discover and verify only the documented immediate effects.
8. Dismiss and undo through Quick Picks once Bundle 6 backend enforcement is
   present.
9. Expire auth, fail one upstream resource, fail poster metadata, and recover.
10. Log out and prove the protected product is no longer available.

### Visual and accessibility gate

- Complete the named screenshot matrix at 390x844, 768x1024, and 1440x1000.
- Review learned, fallback, loading, empty, upstream-error, poster-error,
  populated/empty Library, detail, Quick Picks, and auth-required states.
- Require zero critical or serious axe violations, logical headings and
  landmarks, visible focus, keyboard completeness, 44x44 mobile targets,
  semantic state text, poster alternative-text policy, focus restoration,
  reduced-motion support, forced-colors usability, and no horizontal page
  overflow at 320px.

### Performance and reliability gate

- Keep the existing authenticated recommendation p99 threshold below 100 ms
  for its pinned direct-API workload.
- Add page-shaped workloads for recommendation/history/catalog BFF fan-out,
  cursor continuation, Library reads, mutation plus immediate read, and Quick
  Picks actions.
- Measure browser and direct API timing separately. Target LCP <= 2.5 seconds,
  CLS <= 0.1, and visible action acknowledgement within 100 ms on the agreed
  mobile profile.
- Confirm reserved poster dimensions, below-fold lazy loading, bounded catalog
  pages, no per-card TMDB fan-out, and progressive technical-data loading.
- Verify request IDs, auth/model/database dependency visibility, rate-limit
  behavior, readiness, and degraded metadata operation.
- Record whether a load failure is repeatable before changing thresholds. Do
  not weaken a gate because of one noisy runner.

### Product finish review

- Run the five-second and moderated viewer/technical-reviewer tasks from the
  testing strategy.
- Apply the written UI Finish-Gate in product-legibility, hierarchy, pattern,
  state, responsive, implementation-fidelity, and truthfulness order.
- Record an explicit PASS or HOLD with linked screenshots and automated results.
- Keep `/legacy` until the new journey passes. Remove it in a dedicated final
  PR only after PASS, with a documented rollback to the preceding release.

## Required implementation order

```text
5A live resource boundary
  -> 5B Discover
  -> 5C Browse/detail
  -> 5D Library
  -> 6 backend positive/excluded separation and audit evidence
  -> 6 Quick Picks
  -> 7 service-backed finish gate
  -> 7 legacy removal/cutover
```

5B, 5C, and 5D may use separate worktrees after 5A merges, but each PR must be
rebased onto the latest dependency and must carry its own loading, error,
accessibility, responsive, and browser evidence. Do not open one long-lived
frontend mega-branch.

## Known risks and non-goals

| Risk or boundary | Required handling |
|---|---|
| Rating magnitude is not learned online | Keep rating copy explicit and narrow; a graded/rating-aware model needs a separate ADR, features, evaluation, cache, retraining, and promotion work. |
| Feast and learned artifacts are snapshots | Show freshness honestly; do not promise online retraining after feedback. |
| Model-server caches depend on deployed inputs/artifacts | Invalidate or version only as part of an explicit serving change; never imply the UI bypasses cache semantics. |
| Actor and persona are different identities | Keep persona labels and role checks; add `/me` mapping before private-user claims. |
| Shared metadata is incomplete | Use deterministic source-aware fallbacks and offline enrichment; never add per-card live TMDB calls. |
| Fixture leakage | Fixtures are test inputs only unless a route explicitly advertises a demo/failure mode. Production fetch failures remain visible failures. |
| Migration order | Preserve one Alembic head: Bundle 2 `0010` followed by Bundle 3 `0011_catalog_metadata`. New migrations descend from the current head. |
| Native FAISS/OpenMP instability on macOS | Run local Python/FAISS validation with native thread limits and never run multiple Python suites concurrently. |
| JavaScript dependency audit | Address framework/package advisories in a dedicated dependency PR with full auth/build/browser regression; do not force-upgrade inside a feature slice. |
| Load-run variance | Keep the accepted threshold, inspect the failed endpoint distribution, and rerun once to establish repeatability before code or threshold changes. |

## Validation commands

Run the smallest relevant checks while developing, then the full PR gates. On
macOS, keep the native-library limits on every Python test invocation:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 pytest tests/unit/ -q
ruff check src/ synthetic/ tests/ notebooks/
black --check src/ synthetic/ tests/ notebooks/
mypy src/ synthetic/ notebooks/
python -m scripts.generate_openapi --check
alembic heads
```

```bash
cd web
npm ci
npm run api:types:check
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e:ui
```

For service-backed checks, follow the CI and demo runbook rather than inventing
a bypassed local stack:

```bash
make demo-up
make demo-seed
make demo-smoke
make demo-load-smoke
```

Run the browser-auth and responsive Playwright projects/scripts introduced by
Bundles 1 and 4 against their documented environments. A PR is not green based
on local fixture tests alone; required GitHub checks include frontend, lint,
unit, tenant isolation, feature parity/learned serving, browser auth, Compose
build, and synthetic load.

## Handoff acceptance checklist

- [ ] Bundle 4 is squash-merged and this document is rebased onto that merge.
- [ ] The first Bundle 5 PR links the governing route and truthfulness contracts.
- [ ] No production route silently falls back to a recorded fixture.
- [ ] No browser request forwards a caller-supplied bearer token.
- [ ] Every mutation reconciles a committed canonical state and revision.
- [ ] Every user-scoped endpoint has tenant and actor authorization evidence.
- [ ] Fallback/learned labels follow the returned serving policy and five-signal threshold.
- [ ] Watchlist, watched/rating, and dismissal keep their distinct model meanings.
- [ ] Browse, poster, and prediction coverage are reported separately.
- [ ] Bundle 7 records a written PASS or HOLD before `/legacy` is removed.

## Stop point

Bundles 0–4 plus this handoff are the end of the current delivery. The next
owner starts with PR 5A. No Bundle 5–7 implementation is included here.
