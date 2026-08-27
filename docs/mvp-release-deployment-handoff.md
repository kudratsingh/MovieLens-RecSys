# MVP release and deployment handoff

_Last updated: 2026-08-26 (America/Los_Angeles)_

## Start here

The active objective is to get the MovieLens MVP working end to end, close the
remaining release blockers with evidence, and deploy it to a production
environment selected by the owner.

The repository was clean and synchronized with `origin/main` at
`001bbb4` (`test(e2e): leave Cold Start with zero signals after every journey
(#67)`) before this documentation branch was created. Frontend Bundles 0-7 are
merged. The product routes are live in source; the old dashboard is intentionally
kept only at `/legacy` as the documented rollback.

Do not deploy the current development Compose files unchanged. They contain
development credentials, public host ports, Keycloak `start-dev`, localhost
issuers, and no production TLS, secret, backup, or rollback configuration.

## What the product is supposed to show

- `/` is a signed-out door and sends an authenticated user to `/discover`.
- `/discover` is poster-first: a featured movie, ranked rail, independent
  recommendation/history regions, movie-state controls, and progressive
  `Why this?` / prediction-audit disclosure.
- `/browse` is the searchable, filterable, cursor-paginated movie catalog.
- `/movies/{movie_id}` is movie detail plus watchlist, watched, rating, and
  reversible dismissal controls.
- `/library` separates Rated, Watchlist, and History.
- `/quick-picks` is the one-card-at-a-time decision flow with button, keyboard,
  and swipe input.
- `/legacy` is the pre-redesign rating wall and is not the intended front door.

The product plan is in `docs/frontend/implementation-plan.md`; the design system
is in `docs/frontend/frontend-system.md`; the final review is in
`docs/frontend/finish-gate-review.md`. Representative screenshots are under
`docs/frontend/evidence/bundle-4`, `bundle-5b`, `bundle-6`, and `bundle-7d`.

## Why localhost appeared unchanged

On 2026-08-26, `http://localhost:3001` was still served by a container created
from `/private/tmp/movielens-bundle1-auth`, not this checkout. Docker labels on
`movielens-demo-web-1` proved that its Compose working directory and config files
came from the old Bundle 1 worktree. Bundle 1 predates the redesign, so localhost
correctly looked like the original dashboard even though current `main` contained
Bundles 4-7.

The stale API and web containers were replaced with images built from the real
checkout. Alembic setup was applied, the four deterministic personas were reset,
the 120-title catalog and 480 background interactions were restored, Feast was
materialized, and current item-item/LightGBM artifacts were published. Container
labels then reported this repository as the Compose working directory. The web
entry document contained the new `MovieLens recommendation lab` sign-in door and
both API and web health checks passed.

Local demo login:

- application account: `demo` / `demo`, Keycloak realm `demo`;
- development Keycloak admin: `admin` / `admin` at
  `http://localhost:8080/admin`.

These credentials are local-only and must never be reused for deployment.

## Open release blocker: cold model workers

The refreshed stack exposed a real clean-start defect. Immediately after
`make demo-seed`, `make demo-smoke` reported:

```text
DemoSmokeError: Action Fan did not use learned two-stage serving: popularity
```

The tenant-scoped prediction audit proved the fallback was honest rather than a
policy-label bug:

```text
fallback_reason: model-server-unavailable
reason: model-server-unavailable: ReadTimeout
positive_signal_count: 8
```

The relevant mechanics are:

- `ModelServerClient` has a 0.5-second timeout (`src/serving/models.py` and
  `src/config.py`).
- the model server runs four Uvicorn workers in `docker-compose.demo.yml`;
- each worker owns a separate in-process Feast feature cache;
- model-server startup warms LightGBM native initialization, but not a real
  Feast candidate lookup;
- `/healthz` proves artifacts loaded, not that a representative rank request is
  within the client timeout.

A direct cold internal rank call returned HTTP 200 only after 10.597 seconds;
the response attributed about 6.567 seconds to Feast feature lookup and about
7.068 seconds to model work. After the exact most-recent-first Action Fan input
had reached every worker, repeated direct calls were approximately 14-19 ms.
After that warmup, `make demo-smoke` passed with:

```json
{
  "action_history_count": 8,
  "action_recommendation_count": 8,
  "cold_history_count": 0,
  "cold_recommendation_count": 8,
  "persona_count": 4
}
```

This pass proves the currently running warm demo, not clean-start readiness. A
release fix must make `make demo-seed && make demo-smoke` pass on the first try
without an undocumented manual request. Do not solve this by silently relaxing
the production p99 SLO or globally inflating the 0.5-second client timeout.

The most promising scoped direction is an explicit, testable demo warmup/readiness
step after the feature/model sidecars start. It must exercise the same ordered
positive/exclusion inputs as the API and cover every configured model worker.
The load gate already has worker-aware priming; reuse that contract where
possible instead of creating a second incompatible warmup definition. Preserve
the existing rule that warm users must report learned serving and fail if they
quietly fall back to popularity.

Required regression evidence:

1. Start from sidecars recreated with cold in-process caches.
2. Run the documented materialize/start sequence.
3. Run `make demo-smoke` once, with no manual priming.
4. Prove Action Fan uses `item-item-cosine+lightgbm` and Cold Start uses
   `popularity`.
5. Run the service-backed browser journeys and load gate after the fix.

## Deployment decision awaiting owner

Three options were presented; the owner has not selected one yet.

1. **Railway (recommended):** public Next.js and Keycloak; private FastAPI,
   model server, and Feast; separate application and Keycloak Postgres services;
   Redis; persistent model/feature storage; GitHub deployment; managed domain and
   TLS. This is the fastest path with the least server administration.
2. **DigitalOcean plus Coolify:** one 8 GB / 4 vCPU Droplet initially (16 GB if
   measured pressure requires it), Docker Compose, automatic TLS, off-host
   encrypted backups, and GitHub deployment. Lower fixed cost, but the owner is
   responsible for host patching, backup restoration, and the single-node
   failure domain.
3. **AWS:** ECS/Fargate, RDS, ElastiCache, S3, ALB, Route 53/ACM, Secrets
   Manager, and CloudWatch. Scale-ready but excessive for the first MVP release.

Do not split only the frontend onto Vercel for the first release: Keycloak,
FastAPI, Feast, and model serving would still need another provider, complicating
issuer URLs, private BFF networking, egress, and rollback. Do not introduce
Kubernetes for the MVP.

## Exact next sequence

1. Confirm the deployment selection (`Railway`, `DigitalOcean + Coolify`, or
   `AWS`). Default to Railway when the owner chooses the recommended path.
2. Implement and test cold-worker startup warmup/readiness on its own feature
   branch and substantial PR.
3. From a clean demo lifecycle, run migrations, seed/materialize, the first-try
   `make demo-smoke`, and the authenticated Playwright journey (`make web-e2e`).
4. Run backend/unit, auth/RLS/ownership, migration, contract-drift, frontend
   lint/typecheck/unit/build, page-timing, and synthetic-load gates. Keep host
   Python/FAISS tests serialized and use one thread for OMP/BLAS/MKL/VECLIB on
   macOS.
5. Add the selected platform's production manifest and environment contract:
   production Keycloak issuer/hostname, generated secrets, private service
   addresses, migration/release jobs, persistent artifacts, database backups
   with a restore test, health/readiness probes, TLS/domain configuration,
   monitoring, and a previous-image rollback.
6. Deploy, then run production health, OIDC login, tenant-isolation canaries,
   Discover/Browse/detail/Library/Quick Picks journeys, learned/cold serving
   assertions, and a production-safe smoke/load check.

## Current local state at handoff

- Git source was clean before this documentation-only branch.
- `main` and `origin/main` were synchronized at `001bbb4`.
- Current API, web, Feast, model server, Keycloak, Postgres, pgBouncer, and Redis
  containers were healthy.
- The four demo personas were reset to deterministic seed state.
- The current warm stack passed `make demo-smoke`.
- A full service-backed Playwright run and full load gate were not rerun during
  this short handoff session.
- The in-app browser had no connected browser instance, so runtime verification
  used container provenance, HTTP health/entry checks, durable audits, direct
  internal model measurements, and the repository's committed screenshot
  evidence.

Do not report the MVP deployed or the goal complete until the deployment target
is chosen, the cold-start readiness regression is fixed, all release gates pass,
and the public deployment itself passes production smoke and browser journeys.
