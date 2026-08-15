# Working Demo Execution Plan

## Purpose

This document turns Phase 3's broad architecture into the shortest defensible
path to a working portfolio demo. `CLAUDE.md` remains authoritative for project
scope, architecture, non-negotiables, and phase sequencing. This file owns the
demo milestone: what must work, the order in which it lands, and how to prove it
is ready to show.

The demo is not a separate toy implementation. It is a thin vertical slice of
the target system. Every demo request remains authenticated, tenant-aware, and
served through the same FastAPI and Postgres boundaries later phases extend.

## Demo story

A viewer opens the Next.js application, chooses a named demo persona, and sees:

1. The persona's recent movie history.
2. A ranked top-eight recommendation grid with real movie metadata and posters.
3. The serving policy and model version responsible for the results.
4. A useful cold-start result for a persona with no history.
5. A visibly different result for at least two taste-oriented personas.

The walkthrough should also show that the same user identifier in a different
tenant cannot access the demo tenant's interactions or results.

## Current state

The first demo milestone, interactive feedback extension, and feature-store path are merged
or in delivery:

- Authenticated, tenant-scoped FastAPI recommendation and history endpoints.
- An online popularity policy with seen-item filtering and explicit cold-start behavior.
- A Next.js user selector, recommendation grid, history panel, loading/error
  states, and server-side FastAPI proxy.
- Keycloak JWT validation, tenant routing, Postgres row-level security, and
  cross-tenant CI canaries.
- Frontend and backend linting, type checking, unit tests, production builds,
  and tenant-isolation checks in GitHub Actions.
- A Docker-only clean-checkout environment with schema bootstrap, seed, and
  behavioral smoke commands.
- An interactive rating loop that immediately refreshes a tenant-safe,
  rating-weighted genre-affinity baseline.
- A pinned Feast repository with Postgres historical snapshots, Redis online
  materialization, a dedicated feature-server container, and parity/isolation CI.

The walkthrough is now repeatable and the feature-store seam is live. The
remaining gap to the Phase 3-complete demo is learned two-stage serving, audit
logging, and the enforced k6 latency gate; recommendations still use the
interactive genre-affinity bridge until Bundle D5 loads model artifacts.

## Definition of done

The first working demo is complete when all of the following are true:

- `make demo-up` starts every required service from a clean checkout.
- `make demo-seed` idempotently loads the demo tenant and named personas.
- The frontend loads at `http://localhost:3001` without manual database edits.
- A viewer can select Action Fan, Drama Fan, Eclectic Viewer, and Cold Start.
- Recommendation and history cards show real titles, genres, and poster images.
- Warm personas do not receive movies already present in their history.
- Cold Start receives an explicit popularity fallback result.
- The response displays tenant, policy, and model version.
- Every endpoint except `/healthz` requires auth outside guarded dev mode.
- Tenant-isolation CI exercises recommendations and history with real canaries.
- A smoke-load check records p50, p95, and p99 against the pinned workload.
- The complete walkthrough can be reset and repeated from documented commands.

The first demo may serve the popularity baseline. Learned two-tower and
LightGBM serving improve the demo but do not block the first repeatable
walkthrough. They become required for the Phase 3-complete demo.

## Delivery bundles

Each bundle is one focused PR with its tests, documentation update, and CI
changes included. A bundle does not merge with failing checks.

### Bundle D1 — Durable demo personas and seed path

Goal: make the current API and UI produce meaningful, repeatable data.

- Add `synthetic/personas/` with four named persona definitions.
- Add a migration for any required synthetic/persona metadata.
- Seed tenant-scoped interactions without modifying the raw MovieLens dataset.
- Choose histories that create visibly different genre profiles.
- Make the seed command idempotent and safe to rerun.
- Replace numeric-only UI labels with named persona definitions.
- Add tests for fixture determinism and tenant isolation.
- Add `make demo-seed`.

Acceptance proof:

- Re-running the seed command produces the same rows and counts.
- Action Fan and Drama Fan have different histories.
- Cold Start has zero interactions and still receives recommendations.
- No default-tenant request returns a demo persona row.

### Bundle D2 — TMDB metadata and poster proxy

Goal: turn the recommendation grid into a recognizable movie experience.

- Add server-only TMDB configuration.
- Resolve MovieLens `tmdbId` values through FastAPI; never expose the API key.
- Cache poster metadata and degrade cleanly when TMDB is unavailable.
- Add a backend poster/metadata endpoint or enrich recommendation responses.
- Render optimized poster images with accessible fallbacks in Next.js.
- Document key setup and the no-key fallback.
- Test missing IDs, missing posters, upstream timeouts, and cache behavior.

Acceptance proof:

- Demo cards show real posters when a TMDB read credential is configured.
- The demo remains usable without a key or during an upstream failure.
- No TMDB secret appears in browser responses or the client bundle.

### Bundle D3 — One-command demo environment

Goal: remove manual startup and configuration steps.

- Split or layer development/demo Compose configuration as required by
  `CLAUDE.md`.
- Add FastAPI and Next.js services to the runnable demo stack.
- Add health/readiness dependencies and deterministic startup ordering.
- Add `make demo-up`, `make demo-down`, `make demo-reset`, and `make demo-smoke`.
- Provide checked-in non-secret example environment configuration.
- Run migrations and persona seeding through explicit, repeatable commands.

Acceptance proof:

- A clean machine with Docker Compose v2 can reach the UI using only the
  documented setup commands.
- Restarting the stack preserves expected data; resetting recreates it.
- Startup failures identify the unhealthy dependency.

### Bundle D3.5 — Interactive rating feedback

Goal: turn the portfolio surface from a read-only list into a usable recommender demo.

- Expose a compact rateable catalog for registered demo personas.
- Save 1–5 star feedback through the request-scoped RLS connection.
- Re-rank unseen candidates using rating-weighted genre affinity.
- Refresh history and recommendations immediately after each rating.
- Allow a selected demo profile to be reset to cold start.
- Reject writes for arbitrary non-persona user IDs.

Acceptance proof:

- A Cold Start profile can rate a movie and immediately gain history.
- Warm feedback reports the genre-affinity policy; reset returns to popularity.
- Rating and reset writes cannot cross tenant boundaries.
- A real production container build and browser-facing API flow pass.

### Bundle D4 — Feast and Redis feature parity

Goal: replace provisional/direct feature reads with the target online path.

- Implement ADR 0009's Feast repository and entity definitions.
- Materialize tenant-scoped online features into Redis.
- Route online reads through tenant-prefixed keys.
- Add strict offline-versus-online feature parity fixtures.
- Fail CI on any parity mismatch.
- Record feature freshness in the serving response or audit record.

Acceptance proof:

- The same user/item/as-of fixture produces equal offline and online values.
- Tenant A cannot read Tenant B's feature keys.
- The recommendation endpoint keeps its existing response contract.

### Bundle D5 — Learned two-stage serving

Goal: demonstrate the architecture rather than only its fallback policy.

- Define versioned candidate and ranker artifact manifests.
- Load a candidate index and LightGBM ranker at application startup.
- Retrieve candidates, fetch online features, rank, and return top-K.
- Keep popularity as the explicit cold-start and failure fallback.
- Log candidate policy, ranker version, feature version, and latency.
- Add deterministic fixtures for routing and ranking behavior.

Acceptance proof:

- Warm personas report a learned candidate/ranker policy.
- Cold Start reports the popularity fallback.
- The handler does not refit a model or rebuild an index per request.
- Results exclude already-seen movies.

### Bundle D6 — Audit logging and synthetic load gate

Goal: prove the online path is observable and meets its latency contract.

- Persist request audit rows with tenant, user, endpoint, model versions,
  outcome, and latency.
- Implement ADR 0010's k6 smoke workload.
- Include warm, cold, and mixed persona traffic.
- Export p50, p95, p99, throughput, and error rate.
- Run the small workload in CI for serving changes.
- Document the larger local/nightly workload separately.

Acceptance proof:

- Every recommendation request emits one auditable record.
- Cross-tenant audit reads remain impossible for the application role.
- The pinned smoke workload passes p99 under 100 ms with zero unexpected
  errors, or produces an explicit performance blocker with stage timing.

## Local walkthrough target

The finished command sequence should be no longer than:

```bash
make install
make web-install
make demo-up
make demo-seed
make demo-smoke
```

The walkthrough itself:

1. Open `http://localhost:3001`.
2. Select Action Fan and point out history, recommendations, policy, and model version.
3. Select Drama Fan and show the change in taste and results.
4. Select Cold Start and show the named fallback policy.
5. Show the request/audit entry and per-stage latency.
6. Optionally show tenant isolation with the same user under the default tenant.

## Required demo data

Persona definitions must be stable, reviewed fixtures rather than random rows:

| Persona | History target | What it demonstrates |
|---|---|---|
| Action Fan | 8–12 action/thriller titles | Strong single-genre preference |
| Drama Fan | 8–12 drama/romance titles | Contrasting preference profile |
| Eclectic Viewer | 10–15 titles across at least five genres | Mixed-history ranking |
| Cold Start | 0 interactions | Explicit fallback and coverage |

Movie IDs should come from a checked-in demo catalog manifest and resolve to
valid MovieLens links. Persona IDs must remain stable so UI fixtures, load
tests, and later A/B bucketing tests can reuse them.

## Scope boundaries

Required for the first repeatable demo:

- Durable personas and data.
- Real movie metadata/posters with graceful fallback.
- One-command startup and reset.
- Auth and tenant isolation already in place.
- Popularity-based recommendations and history.

Required for Phase 3 completion, but not the first walkthrough:

- Feast/Redis feature reads.
- Learned two-tower plus LightGBM serving.
- Audit persistence.
- Enforced k6 latency gate.

Explicitly deferred to later phases:

- Prefect retraining and automated promotion gate (Phase 4).
- SHAP explanation panel (Phase 4).
- Drift simulation and dashboards (Phase 5).
- Champion/challenger UI and statistical testing (Phase 6).
- Production hosting, secrets, backup, and rollback runbooks (Phase 7).

## Risks to manage

- Direct aggregation over the full ratings table is a demo bridge, not the
  latency-compliant final path. Measure it before claiming the SLO.
- Demo data must never mutate the raw DVC-tracked MovieLens files.
- Dev auth bypass must remain impossible in staging/production.
- Poster availability is an external dependency; visual fallbacks are required.
- Named personas should demonstrate behavior honestly. Do not hand-curate the
  returned recommendations themselves.
- Frontend polish must not outrun tenant isolation, feature parity, or serving correctness.

## Progress tracking

- [x] Authenticated recommendation/history API baseline.
- [x] Next.js recommendation/history demo surface.
- [x] Frontend CI and production build.
- [x] Durable demo personas and idempotent seeding.
- [x] TMDB metadata/poster proxy.
- [x] One-command demo environment and smoke test.
- [x] Feast/Redis feature path and parity test.
- [ ] Learned two-stage model serving.
- [ ] Audit logging and k6 latency gate.
- [ ] Recorded, repeatable portfolio walkthrough.

## Immediate next step

Implement Bundle D5. Load versioned candidate and ranker artifacts once at
startup, use the Feast online features during ranking, and retain popularity as
the explicit cold-start/failure fallback.
