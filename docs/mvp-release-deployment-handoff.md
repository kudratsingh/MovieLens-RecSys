# MVP release and deployment handoff

_Last updated: 2026-08-27 (America/Los_Angeles)_

## Start here

The objective is unchanged: get the MovieLens MVP working end to end, close the
remaining release blockers with evidence, and deploy it. Almost everything this
note used to hand off is now settled.

- **The deployment target is one Hetzner CX22**, not Railway. [ADR
  0013](adr/0013-production-deployment-target.md) is rewritten for it: the same
  `docker-compose.prod.yml` behind its own Caddy edge, images published to GHCR by
  CI, a deploy over SSH on every merge to `main`, and an automatic rollback when
  verification fails. Cost was the deciding factor — ≈€4.50/month against a
  measured ≈$27 — and the single-host consequences are stated in the ADR rather
  than implied. `docs/deployment-runbook.md` is the operator's half.
- **The production-mode rehearsal has been run end to end**, and the defects it
  exposed are fixed on this branch (`1be6a90`). That was the first boot of this
  codebase with `ENVIRONMENT != dev`, and it found five classes of real defect —
  a `Settings` failure respawning forever inside uvicorn while the container
  reported "running", an API started by a line no deployment uses, pgBouncer's
  `auth_query` mode failing server-side SCRAM through the forced-user aliases,
  Keycloak provisioning that had never once completed, and four smaller ones in
  the backup, verify and schema-fence paths.
- **The deployment is code, not a checklist.** `infra/host/bootstrap.sh` plus
  three systemd units make the machine; `infra/deploy/deploy.sh` runs a release
  and rolls back on a failed verify; `ci.yml`'s `publish-images` job pushes
  `linux/amd64` images tagged with the commit SHA and `main`;
  `deploy-production.yml` and `production-canary.yml` are the two workflows that
  drive the box.
- **The rate-limiting question is decided.** [ADR
  0014](adr/0014-request-rate-limiting.md) carries the measurement the rehearsal
  produced and the numbers that follow from it.

**Nothing is deployed. No server exists yet.** The work left is in
`docs/deployment-runbook.md` §1–§7 and takes an afternoon, not a sprint.

Do not deploy the development Compose files unchanged. They contain development
credentials, published host ports, Keycloak `start-dev`, localhost issuers, and
no production TLS, secret, backup or rollback configuration. `docker-compose.prod.yml`
is the production stack — the same file the box runs — and it exists for exactly
this reason.

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

The clean-start defect was real: immediately after a seed, the smoke reported
`Action Fan did not use learned two-stage serving: popularity`, and the audit
proved the fallback honest rather than a label bug (`fallback_reason:
model-server-unavailable`, `ReadTimeout`). `/healthz` had been proving that
artifacts loaded, not that a representative rank fit inside the 0.5-second client
timeout.

`src/serving/model_server.py` now warms each worker **inside `lifespan`** — the
same online feature read `rank()` performs, then a real `rank()`, asserted
non-degenerate — and `/healthz` returns **503 until warm**, reporting `warm`,
`warmup_ms`, `workers` and `native_threads`. The rehearsal is the evidence that
was owed: the release sequence ran from empty volumes with no manual priming and
the smoke passed on the first request, with Action Fan on
`item-item-cosine+lightgbm` and Cold Start on `popularity`.

Two consequences worth knowing before reading a failure: a worker that cannot
find materialized item features in Redis refuses to boot rather than serving
zeros, so a crash-looping sidecar whose log says `no Feast event timestamp` means
run `materialize`, not roll back; and the startup cost moved into boot, which is
why the serving tier's readiness timeout is 300 s.

## Closed: the load gate

Pinning the model-server's native parallelism
(`OMP/OPENBLAS/MKL/VECLIB_MAXIMUM_THREADS=1`) took the local gate from p99
903.64 ms to 48.99 ms with no threshold, timeout or durability change. The
invariant is baked into `infra/features/Dockerfile` rather than set per
environment.

The rehearsal re-ran the pinned k6 gate at the production topology — production
images, baked artifacts, `ENVIRONMENT=production` — and it passed at **p50
6.85 ms, p95 9.47 ms, p99 12.93 ms**, zero errors and zero dropped iterations.

`docs/release-serving-fix-handoff.md` carries the one question still open: two
low-steal CI breaches whose tail was in the shared auth → pooler → audit →
`fdatasync` path rather than in the ranker, and ADR 0010's rule that the verdict
comes from the first instrumented runner failure rather than from a laptop.

## Closed: rate limiting

The same rehearsal ran the production canary with ADR 0014's first defaults
(120/minute, burst 30) and **37.9% of one subject's 301 requests were refused**.
The limiter was working; the arithmetic behind the numbers was not. An in-process
token bucket is per worker, and HTTP keep-alive pins a client to one worker, so a
single caller meets one bucket rather than the `workers × limit` aggregate the
numbers assumed.

ADR 0014 now records the measurement and the numbers that follow from it — **600
requests/minute with a burst of 120, per worker** — and names a Redis-backed
shared bucket as the follow-up that would let the numbers describe one client
again rather than one worker. `src/config.py` is the only place the defaults are
declared; no Compose file or env example sets `RATE_LIMIT_*`, because an explicit
value in an env example is one copy-paste away from disabling the limiter on a
public service.

## What remains before the site is live

1. **The machine and the DNS** — `docs/deployment-runbook.md` §1–§3: a CX22 with
   Ubuntu 24.04 (x86, **not** ARM), a Cloud Firewall open on 22/80/443, two A
   records, and `infra/host/bootstrap.sh`.
2. **`.env.prod`** — §4: generated secrets, the two hostnames, `EDGE_TLS=acme`,
   and this machine's sizing (`API_WORKERS=2`, `MODEL_SERVER_WORKERS=2`).
3. **GitHub** — §6: the `production` and `production-canary` environments,
   `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`, and a decision on GHCR package
   visibility.
4. **The first deploy** — §7: `workflow_dispatch` on **Deploy production**, then
   `make prod-verify`.
5. **The owner decisions** — §0, ten rows still open. Deploying with a row open
   is how a decision gets made by accident.

Three things the rehearsal could not prove on a laptop, so the first deploy is
their first exercise: **ACME issuance against real DNS** (the rehearsal used
Caddy's internal CA on `*.localtest.me`), **the GHCR pull path** (the rehearsal
built images locally), and **the SSH deploy itself** — `publish-images` →
`deploy-production.yml` → `deploy.sh` end to end. Expect the first run to find
something in one of those three; none of them can corrupt data.

Unrelated to the deployment and still owed: the **moderated frontend sessions**
the finish gate is holding on (`docs/frontend/finish-gate-review.md` §10.8).

## Current local state

- The demo Compose stack (project `movielens-demo`) is up and healthy, seeded
  from this checkout with the four deterministic personas, the 120-title catalog,
  and materialized features.
- The production-mode stack (project `movielens-prod`) is down with its volumes
  kept, and a gitignored `.env.prod` from the rehearsal sits in the checkout with
  `EDGE_TLS=internal` and the `*.localtest.me` hostnames. `make prod-reset`
  starts it over from empty volumes; that is the state every rehearsal run starts
  from.
- Local demo login is `demo` / `demo` in Keycloak realm `demo`; the development
  Keycloak admin is `admin` / `admin` at `http://localhost:8080/admin`. These are
  local-only fixtures and must never be reused for a deployment.

### The local dev Keycloak has drifted from the committed seeds

Worth knowing before anything is concluded from a local realm. `--import-realm`
**never overwrites a realm that already exists**, so a long-lived development
Keycloak database keeps whatever it was first created with. This host's does, and
it is looser than the repository:

| | Committed seed | This host's running Keycloak |
|---|---|---|
| `default` realm roles | `user`, `demo-impersonator` | `user` only — no `demo-impersonator` |
| `movielens-api` redirect URIs (both realms) | `http://localhost:3001/api/auth/callback/keycloak` | `http://localhost:3001/*` |

The wildcard is the pre-PR #47 value and the missing role is the pre-PR #45 seed.
A fresh import matches the seeds exactly, which is what CI does and what
`make demo-reset` would do — so this is a stale-database artifact, not a
repository defect. The `realm-drift` CI job exists to keep it that way.

**`make keycloak-export-realms` is broken and cannot be used to reconcile this.**
Two independent reasons: it exports into `/opt/keycloak/data/import`, which
`docker-compose.yml` mounts read-only, so it cannot write; and `kc.sh export`
inside a running container starts a second Keycloak runtime whose management
interface collides with the server's port 9000, so it exits non-zero even when the
export succeeds. Both are one-line fixes — a different `--dir`, and
`--http-management-port 9001` — and neither has been made.

## Definition of done

Do not report the MVP deployed or the goal complete until the box exists, a
release has run through `deploy-production.yml` with `DEPLOY-OK` in its log, and
`make prod-verify` passes against it — production health, the https OIDC login,
the cross-tenant isolation canaries, the learned and cold-start serving
assertions, the write round trip, and the audit rollup. A backup that has run and
a restore drill with an exit code are part of it, not follow-up work.
