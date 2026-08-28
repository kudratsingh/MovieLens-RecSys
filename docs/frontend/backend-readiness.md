# Movie-discovery frontend: backend readiness

**Status:** Bundles 0–3 implemented: source audit, Auth.js boundary, durable Library,
and scalable local catalog/detail. The Bundle 6 backend prerequisite —
separate positive-history and excluded-ID serving inputs plus audit evidence —
is implemented (PR #54).

**Last updated:** 2026-08-21

## Outcome

The backend now supports authenticated Discover, a scalable local-metadata
Browse/detail surface, and a durable selected-persona Library. Quick Picks,
end-user `/me` ownership, and broader observability still require contract work.

This document separates current truth from proposed behavior and records which
parts of the route design are implementation claims.

## Current truth

| Concern | What works now | Boundary or gap |
|---|---|---|
| Recommendation serving | Authenticated, tenant-scoped item-item candidates, Feast/Redis features, LightGBM ranking, popularity fallback, prediction audits; histories below five unique movies now remain on fallback. Positive watched history and excluded/dismissed IDs are separate inputs end to end, filtered at fallback, retrieval, hydration, and final validation, and the response carries a `serving_policy` object with the policy name, learned flag, positive-signal count, threshold, score scale, and filter policy. Each ranked item also carries the caller's own `state` for that title, or `null` | Static artifacts and snapshot features still do not update on each rating |
| Feedback state | Forced-RLS `user_movie_state` stores independent watched, rating, watchlist, and dismissal state with revisions; append-only events record actor/action/canonical outcome; mutations commit before success | Star magnitude is still not an online model input; watchlist remains organizational; dismissal is durable exclusion rather than a training negative |
| Tenant isolation | RLS and least-privilege application role protect user-scoped rows across tenants | RLS does not establish same-tenant user ownership; arbitrary numeric persona IDs remain addressable |
| Library | Cursor-paginated Rated, Watchlist, and History resources expose canonical state, counts, title filtering, stable sorts, a truthful `live-ratings-v1` summary, and each row's `release_year` and `poster_url` from the shared metadata snapshot | The resources remain selected-persona mode until `/me` ownership mapping lands |
| Catalog | Filter-bound keyset cursor, search, genre/year filters, three stable sorts, 48-item cap, local detail, and complete watched/rating/watchlist/dismissal state overlay | Selected-persona ownership remains the boundary; full-catalog query profiling is still required before widening the reviewed fixture |
| Demo catalog | Reviewed 120-title fixture; 24 complete poster/overview records; 480 background interactions make all 120 titles artifact-eligible after regeneration | Visible, poster-backed, and policy-specific eligibility remain separate measures |
| Metadata | Shared persisted read model supplies Browse, detail, and recommendation hydration with complete/partial/unavailable status | Snapshot enrichment is offline; partial titles intentionally render deterministic fallbacks |
| Browser auth | Keycloak gives both callers the API audience; FastAPI pins issuer, audience, calling client, tenant registry, and demo role. Auth.js owns PKCE, encrypted HttpOnly sessions, server-side token refresh/logout, BFF authorization, CSRF/origin, and internal/public issuer routing; bypass-disabled Playwright passes | `/me` subject-to-profile ownership remains for a non-persona product mode |
| BFF loading | The current dashboard proxy fetches recommendations, history, and catalog concurrently; TypeScript types are now generated from committed OpenAPI | One failed request still fails the entire dashboard and BFF bodies are not yet runtime validated |
| Auditing | Recommendation requests persist detailed tenant-scoped prediction audits and successful responses now wait for the fail-closed audit transaction to commit. Each audit records the input-state revision, positive-history and exclusion digests, positive/excluded counts, feature event time, filter policy, per-source candidate contributions, and a structured reason, all exposed on `GET /users/{user_id}/audits` | Other reads and mutations have no generic request audit or retention policy |
| Performance | Authenticated recommendation k6 gate enforces p99 below 100 ms for its pinned workload | It does not measure page-shaped BFF fan-out, real poster enrichment, catalog paging, or mutation-plus-refresh |
| Observability | Load results and health checks exist. Every response echoes `X-Request-ID`, adopting a well-formed caller-supplied value so a BFF request id survives the hop, and recommendation audits store it as `correlation_id` | There is no working FastAPI metrics surface, page-route instrumentation, DB-pool visibility, or separate readiness contract |

### Source anchors

- [Authentication and transaction lifecycle](../../src/auth/middleware.py)
- [FastAPI routes and response contracts](../../src/serving/app.py)
- [Catalog, history, and rating persistence](../../src/serving/recommendations.py)
- [Online routing and learned/fallback policy](../../src/serving/orchestration.py)
- [TMDB client and process-local cache](../../src/serving/tmdb.py)
- [Feast materialization](../../src/features/materialize.py) and
  [model-server feature cache](../../src/serving/model_server.py)
- [Demo persona seed](../../synthetic/personas/seed.py) and
  [artifact build](../../src/training/demo_artifacts.py)
- [Keycloak browser-client realm](../../infra/keycloak/realms/demo-realm.json)
  and [demo Compose](../../docker-compose.demo.yml)
- [Current aggregate BFF route](../../web/app/api/users/[userId]/route.ts) and
  [OpenAPI-derived TypeScript aliases](../../web/lib/api.ts)
- [RLS policy migration](../../alembic/versions/0004_enable_rls_on_scoped_tables.py)
  and [tenant-isolation canaries](../../tests/tenant_isolation/test_no_cross_tenant_leak.py)
- [k6 thresholds](../../synthetic/load/thresholds.js) and
  [recommendation workload](../../synthetic/load/recommendations.js)

## Frontend-safe claims

The first frontend implementation may say:

- `This movie was marked watched.` after a committed canonical response.
- `Your recommendations were refreshed.` when a new request completed.
- `This title will no longer appear in unseen recommendations.` after the
  committed watched or dismissal state is visible.
- `Similar to movies in this persona's watched history.` only when structured
  candidate-source evidence supports it.
- `Popular while we learn.` for histories below the accepted threshold.

It must not say:

- `Your 5-star rating immediately trained the model.`
- `We learned you dislike this genre` from a low star value.
- `92% match` for an uncalibrated LightGBM score.
- `Your private library` while the route is a selected shared demo persona.
- `Personalized after one rating` while the accepted threshold is five.
- `All 62,423 movies can be recommended` merely because they can be browsed.

## Required product contracts

### Identity and browser session

The implemented browser flow is:

```text
browser
  → Keycloak authorization code + PKCE
  → HttpOnly BFF session
  → server-side refreshed access token
  → FastAPI validates issuer, signature, expiry, audience, and calling client
  → actor is authorized for /me or explicit demo-persona impersonation
```

Normal product resources should use `/me/...` after a tenant-scoped mapping
from OIDC subject to internal user exists. The portfolio persona selector may
continue under role-gated target-user routes. Every request must also reject a
realm that lacks a registered tenant.

The checked-in browser E2E exercises the seeded demo actor and the real PKCE
callback with API bypass disabled. Unit tests separately force access-token
refresh success and rejection, and mutation tests pin Origin plus Auth.js
double-submit CSRF validation.

### Catalog and movie detail

Proposed grid contract:

```http
GET /v1/users/{user_id}/catalog
  ?q=&genre=&year_from=&year_to=&rating_state=&sort=&limit=24&cursor=
```

The response contains bounded `items` plus `page.next_cursor` and
`page.has_more`. Every item includes:

- `movie_id`, title, release year, genres;
- TMDB ID, poster URL, and metadata source;
- compact popularity/rank data only when its meaning is pinned; and
- the target persona's watched/rating/watchlist/dismissal state.

Use an opaque, versioned, filter-bound keyset cursor with `movie_id` as the
stable tie-breaker. Default to 24 items and cap at 48 or 50. Do not promise a
total count in the first version. Title, genre, and year filtering can begin
with measured PostgreSQL queries; add trigram or normalized genre storage only
if profiling justifies it.

Proposed detail contract:

```http
GET /v1/users/{user_id}/movies/{movie_id}
```

Detail adds overview, larger poster/backdrop, current movie state, and an
optional structured recommendation explanation. Cast, trailers, streaming
providers, and similar-movie modules remain deferred until their data and
selection policies exist.

Poster, year, and grid fields come from a shared non-RLS metadata read model or
the reviewed demo fixture. A full-catalog option is a shared
`movie_catalog_metadata` table keyed by `movie_id`; user overlays remain in
forced-RLS tables. Browse never makes a live TMDB request per card.

### Feedback and Library

Use a tenant-scoped current projection rather than extending the imported
MovieLens `ratings` row into every product concept:

```text
user_movie_state
  tenant_id, user_id, movie_id
  watched_at
  rating, rating_updated_at
  watchlisted_at
  dismissed_at
  state_version, updated_at
```

The composite identity is `(tenant_id, user_id, movie_id)`. Rating is optional,
constrained to 0.5–5 in half-star steps, and implies watched. Editing a rating
preserves `watched_at`; deleting it leaves watched state intact. Removing from
history is separate. Watchlist has no model effect. Dismissal excludes a title,
is undoable, and is neither a positive candidate seed nor a training negative.

An append-only `user_feedback_events` table supports activity history and
auditability without pretending the current rating projection is immutable
history. Every state table and event table follows ADR 0008: tenant FK, forced
RLS, `USING` and `WITH CHECK` policy, minimal grants, tenant-leading keys and
indexes, and cross-tenant tests.

Implemented persona-mode resources (normal `/me` ownership remains pending):

- `PUT|DELETE /users/{user_id}/movies/{movie_id}/watched`
- `PUT|DELETE /users/{user_id}/movies/{movie_id}/rating`
- `PUT|DELETE /users/{user_id}/movies/{movie_id}/watchlist`
- `PUT|DELETE /users/{user_id}/movies/{movie_id}/dismissal`
- `GET /users/{user_id}/library?tab=rated|watchlist|history&cursor=...`
- `GET /users/{user_id}/taste-profile`

Persona-mode equivalents require the explicit impersonation role. Mutation
success returns the committed canonical movie state, state revision, and
request ID. A response is never sent before its transaction can succeed.

### Recommendation inputs and explanations

**Implemented (PR #54).** Serving accepts positive watched history and excluded/dismissed
IDs as different inputs. Positives come from watched-and-not-dismissed
`user_movie_state`; exclusions are dismissals plus already-seen titles. A
dismissed title is filtered from popularity, candidate retrieval, hydration,
and final validation, is never used to retrieve similar titles, and is never
written back as a rating or training negative. The final check fails closed:
an excluded ID that reaches the outgoing list is dropped and the block is
logged and audited rather than served.

Histories below five use fallback; histories of five or more may use the
learned path. The response reports this through `serving_policy`
(`name`, `learned`, `positive_signal_count`, `threshold`, `reason`,
`score_scale`, `filter_policy`, `excluded_count`); the flat `policy` string is
retained and always equals `serving_policy.name`. Prediction audits record the
input-state revision, positive-history hash, exclusion hash, feature event
time, filtering policy, per-source candidate contributions, and a structured
reason.

The first honest learned reason is `Similar to movies in this persona's watched
history`, backed by source-item similarity contributions. `Because you liked`
is not valid while watched history ignores star magnitude. SHAP and calibrated
match percentages remain later model/explanation work.

### Per-item overlays on the ranked set and the Library

**Implemented.** Three read models were widened so a surface no longer has to
guess at what it is rendering, and none of the three costs a TMDB call:

- `RecommendationItem.state` carries the caller's own movie state for that
  title, or `null` when no row exists — the same required-and-nullable shape
  `CatalogItem.state` already used. A ranked title is never watched or
  dismissed, but it can be watchlisted, and it can hold a revision left behind
  by a write that has since been undone. Without the field a client has to
  assume revision 0, and its first write on such a title is rejected as stale.
  The overlay is a bounded keyed read on the request's RLS-bound connection,
  taken alongside the metadata hydration rather than joined into the compiled
  candidate and popularity statements, whose plans are what the k6 gate measures.
- `LibraryMovieResponse` and `HistoryItem` carry `release_year` and
  `poster_url` from the shared `movie_catalog_metadata` snapshot, joined in the
  query that already reads the rows. Both are `null` for a title the snapshot
  has never covered, and neither is conditioned on `visible` — that column
  governs what Browse lists, not whether a title the viewer has already acted
  on may show its artwork.

The cross-tenant canaries cover all three fields: the artwork on both read
models per tenant (populated on one canary title, absent on another), and the
recommendation state overlay across a watchlist write and its undo.

## Platform requirements before broad route fan-out

- Split recommendation, catalog, movie detail, library counts, history, and
  technical evidence into independent BFF resources with timeouts and local
  error boundaries.
- Keep personalized responses `private, no-store`; propagate request IDs; keep
  access tokens and upstream secrets server-side.
- Maintain the committed OpenAPI artifact and generated TypeScript types with
  stable operation IDs, bearer security, constrained schemas, shared errors,
  and CI drift detection.
- Move blocking database work off the event loop or adopt async DB access, and
  shorten transactions around external calls.
- Add tenant/actor/route-class rate limits with `429` and `Retry-After` at
  FastAPI, not only the BFF.
- Add generic request audits with an explicit durability and retention policy.
- Add bounded-cardinality route, dependency, database-pool, auth, model, Feast,
  and TMDB metrics plus separate `/healthz` and `/readyz` behavior.
- Preserve the existing direct recommendation SLO and add a page-shaped load
  profile for BFF fan-out, catalog paging, Library reads, and mutation/refetch.

## Catalog coverage rule

Browse coverage and prediction coverage are independent release measures.

Expanding the clean demo from 24 to roughly 120 visible movies is migration
free, but the new titles need deliberate background interaction histories and
regenerated item-item/popularity/features/model artifacts before the product
may claim comparable recommendation coverage. CI should assert both:

1. the number of browsable poster-backed titles; and
2. the percentage of those titles eligible for the intended recommendation
   policies.

## Release blockers by route

| Route | Can UI work begin? | Backend gate before complete behavior |
|---|---|---|
| `/discover` | Yes, against the existing response | Structured reasons and independent BFF route; remeasure the SLO with commit latency included |
| `/browse` | Yes: cursor catalog, local metadata, durable state overlay, filters, load more, fallbacks, and scroll restoration | Run seeded browser/visual gates and profile full-catalog queries before expanding beyond the reviewed fixture |
| `/library` | Yes: durable tabs, counts, state controls, filtering, and canonical reconciliation | `/me` ownership mapping and shared poster-card integration remain follow-up work |
| `/movies/[id]` | Yes: local detail, source status, durable state, CSRF-protected rating action, and fallbacks | Add structured explanation and the broader shared state-control component later |
| `/quick-picks` | Yes: the serving prerequisite is implemented (PR #54) | Watched/watchlist/dismissal resources, undo, and the Quick Picks state machine remain frontend work; separate positive/excluded inputs, five-signal routing, and audit evidence are in place |
| Real signed-in product | Role-gated persona mode is signed in through Auth.js | `/me` mapping remains required before claiming a private end-user profile |

## Verification gates

Backend changes are not ready until they pass:

- migration upgrade/downgrade, constraints, grants, and RLS tests;
- cross-tenant and same-tenant cross-user authorization tests, now including
  `/users/{id}/features`, `/users/{id}/catalog`, and `DELETE /users/{id}/ratings`;
- immediate read-after-write and commit-failure tests;
- cursor stability, filter composition, and maximum-page tests;
- OpenAPI generation/drift and generated-TypeScript checks;
- real-Keycloak browser audience, role, refresh, logout, and unknown-tenant tests;
- TMDB timeout/missing-image behavior without grid-path fan-out;
- dismissed-title exclusion and positive-history separation tests;
- history sizes 0, 1, and 3 on fallback and 5 and 10 on learned serving;
- direct recommendation and page-shaped load gates; and
- Playwright login → browse → save → watched/rate → refreshed recommendation
  → edit/remove → logout.

The detailed frontend matrix lives in [testing-strategy.md](testing-strategy.md).
The accepted cross-cutting decisions live in
[ADR 0012](../adr/0012-browser-identity-feedback-and-online-freshness.md).
