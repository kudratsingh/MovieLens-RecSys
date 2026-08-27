# MVP release and deployment handoff

_Last updated: 2026-08-27 (America/Los_Angeles)_

## Start here

The objective is unchanged: get the MovieLens MVP working end to end, close the
remaining release blockers with evidence, and deploy it. Three of the four things
this note originally handed off are now settled.

- **PR #68 (the documentation handoff) and PR #69 (the serving fix) are merged.**
  `main` and `origin/main` are at `c2db933`.
- **The deployment target is chosen: Railway.** ADR 0013 records the decision, the
  topology, the six alternatives it was weighed against, and the sub-decisions
  that came with it. `docs/deployment-runbook.md` is the operator's half.
- **The cold-worker blocker is closed in code, pending a rehearsal that proves
  it.** See below.
- **Still open:** the runner-storage question the load gate was instrumented to
  settle (`docs/release-serving-fix-handoff.md`), the local production-mode
  rehearsal, and the twelve owner decisions in `docs/deployment-runbook.md` §0.

The deployment work lives on `feat/production-deployment`. Nothing has been
deployed anywhere, and no Railway project exists.

Do not deploy the development Compose files unchanged. They contain development
credentials, published host ports, Keycloak `start-dev`, localhost issuers, and no
production TLS, secret, backup or rollback configuration. `docker-compose.prod.yml`
is the production-shaped rehearsal stack that exists for exactly this reason.

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

## Closed: cold model workers

The clean-start defect was real. Immediately after `make demo-seed`,
`make demo-smoke` reported `Action Fan did not use learned two-stage serving:
popularity`, and the tenant-scoped audit proved the fallback honest rather than a
label bug (`fallback_reason: model-server-unavailable`, `ReadTimeout`,
`positive_signal_count: 8`). A direct cold internal rank returned 200 only after
10.597 s, about 6.567 s of it Feast; after every worker had seen the input,
repeated calls were 14–19 ms. `/healthz` proved artifacts had loaded, not that a
representative rank was inside the 0.5-second client timeout.

The fix, on `feat/production-deployment`: `src/serving/model_server.py` now warms
each worker **inside `lifespan`** — it performs the same online feature read
`rank()` performs and then a real `rank()`, and asserts the feature matrix is
non-degenerate — and `/healthz` returns **503 until warm**, reporting `warm`,
`warmup_ms`, `workers` and `native_threads`. Two consequences worth knowing
before reading a failure: a worker that cannot find materialized item features in
Redis refuses to boot rather than serving zeros, so a crash-looping sidecar whose
log says `no Feast event timestamp` means run `materialize`, not roll back; and
the startup cost moved into boot, which is why the platform healthcheck timeout is
300 s.

**The required regression evidence has not been produced yet.** It is a rehearsal
step, not a code question: recreate the sidecars with cold in-process caches, run
the documented materialize/start sequence, run the smoke **once** with no manual
priming, prove Action Fan on `item-item-cosine+lightgbm` and Cold Start on
`popularity`, then run the browser journeys and the load gate. Do not close this
item on the unit tests alone.

## Closed: the load gate

PR #69 pinned the model-server's native parallelism
(`OMP/OPENBLAS/MKL/VECLIB_MAXIMUM_THREADS=1`), which took the local gate from
p99 903.64 ms to 48.99 ms with no threshold, timeout or durability change, and
instrumented the gate's shared path so a slow runner can be told from a slow
service. The invariant travels into production: it is baked into
`infra/features/Dockerfile` and set again on the Railway `model-server` service.

`docs/release-serving-fix-handoff.md` carries the part that is still open — two
low-steal CI breaches whose tail was in the shared auth → pooler → audit →
`fdatasync` path rather than in the ranker, and ADR 0010's rule that the verdict
comes from the first instrumented runner failure rather than from a laptop.

## What remains before a deploy

**Fourteen rehearsal steps against `docker-compose.prod.yml`**, none of which this
codebase has ever done. In the order the runbook and CLAUDE.md's current-step
paragraph put them: the first boot with `ENVIRONMENT != dev` and no
`DEV_AUTH_BYPASS` variable at all; `KEYCLOAK_AUTHORIZED_PARTIES` in CSV form
crash-looping before the JSON form works; `feast apply` at image-build time with
only dummy connection `ARG`s; the whole release sequence from empty volumes; a
full https OIDC round trip through the Caddy edge on a real hostname; Keycloak
provisioning run twice with the second run changing nothing; pgBouncer doing
server-side SCRAM through the forced-user aliases in **both** `auth_query` and
`userlist` modes; Alembic and the seeder as `migrator` rather than the superuser;
four deliberate breaks each refusing to boot; a clean-start smoke passing on the
**first** request; killing the sidecar and separately emptying Redis, with
`verify` failing in both cases; a rollback across a migration proving the
database-ahead no-op; a restore drill with the seed step deliberately skipped;
and the pinned k6 gate plus the browser journeys at the production worker
topology. **Nothing is created on Railway until `make prod-reset && make
prod-seed && make prod-verify` runs clean twice from a cold start.**

**Twelve owner decisions**, in `docs/deployment-runbook.md` §0 as a table with a
`Recorded` column. D6 (how a rollback is actually performed) is answered from the
Railway API documentation; the other eleven — the domain, the plan tier, the
exposure of Keycloak's admin console, persona-impersonation blast radius, Postgres
TLS, feature-store retention, whether browser journeys run against production,
one serving tenant, the web boot-time env schema, and secret custody — are open,
and deploying with a row still open is how a decision gets made by accident.

Then the Railway work itself: create the project with the service names the deploy
workflow asserts, attach the two domains, create the four volumes, enable the
Backups tab, create the `production` and `production-canary` GitHub environments,
set the variables, run `infra/deploy/provision-roles.sql` once, deploy `keycloak`
and run `keycloak-provision`, and run the release workflow.

## Current local state

- The demo Compose stack (project `movielens-demo`) is up and healthy, seeded from
  this checkout with the four deterministic personas, the 120-title catalog, and
  materialized features.
- Local demo login is `demo` / `demo` in Keycloak realm `demo`; the development
  Keycloak admin is `admin` / `admin` at `http://localhost:8080/admin`. These are
  local-only fixtures and must never be reused for a deployment.
- Earlier confusion about localhost looking unchanged was container provenance,
  not code: `http://localhost:3001` was being served by a container created from
  an old Bundle 1 worktree. Rebuilt from this checkout, it serves the redesigned
  product.

### The local dev Keycloak has drifted from the committed seeds

Worth knowing before anything is concluded from a local realm. `--import-realm`
**never overwrites a realm that already exists**, so a long-lived development
Keycloak database keeps whatever it was first created with. This host's does, and
it is looser than the repository:

| | Committed seed | This host's running Keycloak |
|---|---|---|
| `default` realm roles | `user`, `demo-impersonator` | `user` only — no `demo-impersonator` |
| `movielens-api` redirect URIs (both realms) | `http://localhost:3001/api/auth/callback/keycloak` | `http://localhost:3001/*` |

The wildcard is the pre-PR #47 value and the missing role is the pre-PR #45 seed;
both realms have been running looser than `infra/keycloak/realms/*.json` claims
since those merged. A fresh import matches the seeds exactly, which is what CI
does and what `make demo-reset` would do — so this is a stale-database artifact,
not a repository defect. The `realm-drift` CI job exists to keep it that way.

**`make keycloak-export-realms` is broken and cannot be used to reconcile this.**
Two independent reasons: it exports into `/opt/keycloak/data/import`, which
`docker-compose.yml` mounts read-only, so it cannot write; and `kc.sh export`
inside a running container starts a second Keycloak runtime whose management
interface collides with the server's port 9000, so it exits non-zero even when the
export succeeds. Both are one-line fixes — a different `--dir`, and
`--http-management-port 9001` — and neither has been made.

## Definition of done

Do not report the MVP deployed or the goal complete until the rehearsal passes,
all release gates are green, the deployment exists, and it passes production
health, OIDC login, tenant-isolation canaries, the learned/cold serving
assertions, the browser journeys, and a production-safe smoke and canary.
