# Movie-discovery frontend: backend readiness

**Status:** Bundle 0 source audit; Bundle 1 correctness update applied

**Last updated:** 2026-08-21

## Outcome

The proposed frontend is feasible, but the current backend supports a narrower
product than the route contracts describe. Discover can be redesigned on the
existing recommendation path. A scalable Browse, durable Library, honest Quick
Picks loop, and real browser login require backend contract work.

This document separates current truth from proposed behavior. It is not an
implementation claim.

## Current truth

| Concern | What works now | Boundary or gap |
|---|---|---|
| Recommendation serving | Authenticated, tenant-scoped item-item candidates, Feast/Redis features, LightGBM ranking, popularity fallback, prediction audits; histories below five unique movies now remain on fallback | Static artifacts and snapshot features still do not update on each rating |
| Rating feedback | Stores 0.5–5 stars, lists current rows as history, excludes rated movies, and passes unique rated movie IDs as positive candidate seeds after commit; transaction success is now acknowledged only after commit | Star magnitude is not an online model input; edit is delete-plus-insert; there is no per-title delete or independent watched state |
| Tenant isolation | RLS and least-privilege application role protect user-scoped rows across tenants | RLS does not establish same-tenant user ownership; arbitrary numeric persona IDs remain addressable |
| Catalog | The shared MovieLens catalog contains 62,423 titles; user rating state can be overlaid | Endpoint returns at most 100 ID-ordered rows with no poster/year/search/filter/sort/cursor/detail contract |
| Demo catalog | Reviewed 24-title fixture gives deterministic demos | Merely adding titles does not add them to prediction artifacts or popularity coverage |
| Metadata | TMDB is bounded, cached, and failure-tolerant for small recommendation sets | Cache is per process and live fan-out can block; it is unsuitable for a large poster grid |
| Browser auth | Keycloak now gives both `movielens-api` and `movielens-web` tokens the API audience; FastAPI validates audience plus calling client, rejects unregistered realms, and role-gates browser persona selection | BFF session, refresh/logout, CSRF, Compose issuer routing, and a bypass-disabled browser E2E remain |
| BFF loading | The current dashboard proxy fetches recommendations, history, and catalog concurrently; TypeScript types are now generated from committed OpenAPI | One failed request still fails the entire dashboard and BFF bodies are not yet runtime validated |
| Auditing | Recommendation requests persist detailed tenant-scoped prediction audits and successful responses now wait for the fail-closed audit transaction to commit | Other reads and mutations have no generic request audit or retention policy |
| Performance | Authenticated recommendation k6 gate enforces p99 below 100 ms for its pinned workload | It does not measure page-shaped BFF fan-out, real poster enrichment, catalog paging, or mutation-plus-refresh |
| Observability | Load results and health checks exist | There is no working FastAPI metrics surface, page-route instrumentation, DB-pool visibility, or separate readiness contract |

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

The target browser flow is:

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

Proposed idempotent resources:

- `PUT|DELETE /me/movies/{movie_id}/watched`
- `PUT|DELETE /me/movies/{movie_id}/rating`
- `PUT|DELETE /me/movies/{movie_id}/watchlist`
- `PUT|DELETE /me/movies/{movie_id}/dismissal`
- `GET /me/library?tab=rated|watchlist|history&cursor=...`
- `GET /me/taste-profile`

Persona-mode equivalents require the explicit impersonation role. Mutation
success returns the committed canonical movie state, state revision, and
request ID. A response is never sent before its transaction can succeed.

### Recommendation inputs and explanations

Serving must accept positive watched history and excluded/dismissed IDs as
different inputs. A dismissed title is filtered from popularity, candidate,
hydration, and final results and is never used to retrieve similar titles.

Histories below five use fallback; histories of five or more may use the
learned path. Prediction audits record the feedback-state revision or input
hash, excluded-state hash, feature timestamp, filtering policy, candidate
source contributions, and structured reason.

The first honest learned reason is `Similar to movies in this persona's watched
history`, backed by source-item similarity contributions. `Because you liked`
is not valid while watched history ignores star magnitude. SHAP and calibrated
match percentages remain later model/explanation work.

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
| `/browse` | Shell/grid states yes | Cursor catalog contract, local metadata read model, measured search/filter queries, coverage fixture |
| `/library` | Static route/states yes | User movie state, feedback events, RLS/ownership, cursor APIs, canonical mutations |
| `/movies/[id]` | Layout/states yes | Detail endpoint, local metadata, canonical user state, optional explanation |
| `/quick-picks` | Prototype only | Watched/watchlist/dismissal resources, undo, five-signal routing, separate positive/excluded inputs |
| Real signed-in product | No ownership claim yet | Browser-token audience, BFF session, `/me` mapping or role-gated impersonation, CSRF/refresh/logout E2E |

## Verification gates

Backend changes are not ready until they pass:

- migration upgrade/downgrade, constraints, grants, and RLS tests;
- cross-tenant and same-tenant cross-user authorization tests;
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
