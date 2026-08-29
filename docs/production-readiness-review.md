# Production readiness review

> **Record.** A review as of 2026-08-27 at `4c74f0c`. The rehearsal it records
> passed and every defect it found was fixed, but **the machine has not been
> provisioned**, so nothing described here is running anywhere. It is kept as
> written because the gap analysis is the reasoning, not a status page.
> For what to do next, see
> [`deployment-runbook.md`](deployment-runbook.md) §1–§7.

**Date:** 2026-08-27 · **Reviewed at:** `4c74f0c` (`main`, after PR #68) ·
**Resolved on:** `feat/production-deployment`

Phase 3 was code-complete and had never been deployed. Everything the project
claimed about itself — the auth boundary, the tenant-isolation canaries, the
pinned p99 gate, the browser journeys, the frontend finish gate — was proved on
one laptop against `docker-compose.yml` + `docker-compose.demo.yml`, and no job
in the repository had ever started the application with `ENVIRONMENT != dev`.
The distance between that and a deployment was not a hosting form to fill in: it
was a set of platform constraints meeting code that had been written for a
machine where everything shares a filesystem, a network namespace and a set of
development credentials. This document is the record of that distance — what was
found, what was fixed, what was deliberately left, and where the evidence is.

The gap list came from a six-pass review of the tree (configuration and auth,
container topology, the release sequence, the serving path, CI and operational
docs, identity and the browser client) and deduplicated to **20 blockers and 22
majors**, each with file evidence. Twenty-eight are fixed on this branch, seven
are partly closed with the remainder named, five are deliberately deferred
against an ADR or a recorded owner decision, one stopped applying once the
target was chosen, and one is simply still open. Everything then ran once, from
empty volumes, in production mode: a fourteen-step local rehearsal that produced
the first boot this codebase has ever had with `ENVIRONMENT=production`, exposed
defects in the service entrypoint, the pooler's authentication mode, identity
provisioning, the backup job and the verification matrix, and closed with the
pinned k6 gate passing at the production worker topology.

## Starting point

The demo stack was a good development environment and an impossible deployment.
It ran two Compose files on one host with:

- **Development credentials everywhere.** `app_user/app_user`,
  `admin_user/admin_user`, `migrator/migrator`, `pgbouncer_admin/pgbouncer_admin`
  (hardcoded in application source), Keycloak `admin/admin`, a client secret and
  two fixed user passwords committed in the realm seeds, and a default
  model-server token. The repository is public, so every one of them needed a
  replacement, a home and a rotation story.
- **`trust` authentication on Postgres, `start-dev` Keycloak, localhost
  issuers.** `KEYCLOAK_PUBLIC_BASE_URL` was `http://localhost:8080` in every
  value in the tree, and both realm seeds pinned `http://localhost:3001` as the
  only redirect URI. Behind TLS the browser's tokens would carry an `https`
  issuer and the API would reject every request.
- **Published host ports** for Postgres, Redis, pgBouncer and Keycloak, which in
  a cloud deployment become internet-reachable — including a feature server with
  no authentication of any kind.
- **`ENVIRONMENT` never set to anything but `dev`.** The two `Settings`
  guardrails that refuse a dev auth bypass and a default sidecar token outside
  development had therefore never once fired against a real boot, and
  `environment` was an unvalidated `str`, so `"prod"`, `"prd"` and `"Production"`
  all silently satisfied every `!= "dev"` check.
- **A serving bundle and a Feast registry that travelled between three services
  on two shared read-write volumes**, with no artifact provenance, no
  reproducibility check, and a sidecar that crash-looped against empty storage
  while the API answered 200 with `learned: false`.
- **No production manifest, no backups, no restore or rollback rehearsal, no
  rate limiting, and no remote-executable form** of the smoke, isolation or
  latency checks.

## What was found

Ids are the review's. **Fixed** means fixed on this branch; **Partly** names what
remains; **Deferred** carries the ADR or owner decision that records it; **Moot**
stopped applying once the target was decided; **Open** is neither fixed nor
recorded elsewhere.

| Id | Gap | Resolution | Where |
|---|---|---|---|
| B1 | pgBouncer is a non-substitutable boot dependency and its admin credentials were hardcoded in application source | Fixed — credentials are settings that refuse the dev default outside dev; the pool-mode assertion reads `SHOW CONFIG` unconditionally | `src/config.py`, `src/serving/startup_checks.py`, `infra/pgbouncer/` |
| B2 | The model bundle and Feast registry moved between three services over shared read-write volumes | Fixed — both are build inputs: the 247 KB bundle is committed and baked, the registry is written by `feast apply` at build time and compared semantically | `infra/model-bundle/`, `infra/features/Dockerfile`, ADR 0013 |
| B3 | `ENVIRONMENT` was never non-dev, so both production guardrails were inert | Fixed — a `Literal` field, both service images defaulting to `production`, the demo stack saying `dev` explicitly | `src/config.py`, `infra/api/Dockerfile`, `infra/features/Dockerfile` |
| B4 | Keycloak ran `start-dev` on a localhost hostname with `admin/admin` and no proxy-header config | Fixed — an optimized production image provisioned by an idempotent `kcadm` script | `infra/keycloak/Dockerfile`, `infra/keycloak/provision.sh` |
| B5 | The realm seeds pinned localhost redirect URIs and carried a client secret and two fixed passwords | Fixed — production realm templates carry no users and no secrets; clients, the audience mapper and three named accounts are provisioned | `infra/keycloak/realms/prod/`, `infra/keycloak/provision.sh` |
| B6 | `KEYCLOAK_PUBLIC_BASE_URL` was `http://localhost:8080` everywhere; an https issuer 401s every request | Fixed — the release preflight asserts issuer equality against the live discovery document before anything boots on it | `src/release/bootstrap.py`, `infra/deploy/production.env.example` |
| B7 | Migration `0001` hardcodes three role passwords and a database name and needs a true superuser | Fixed by pre-creation — provisioning SQL creates the roles with real passwords, `0001`'s `IF NOT EXISTS` guards leave them alone, and the file reads the database name from `psql` | `infra/deploy/provision-roles.sql`, the runbook |
| B8 | No connection can require TLS to Postgres, and Feast pins `sslmode: disable` | Deferred — all Postgres traffic stays on the host's private network; the DSN and Feast changes are named as prerequisites of ever leaving it | ADR 0013, runbook D7 |
| B9 | A first deploy to empty storage crash-looped the sidecar, then served popularity at 200 with no runtime signal | Fixed — `/readyz`, per-worker warm-up that refuses a degenerate feature frame, and a verify row that fails unless warm traffic is genuinely learned | `src/serving/app.py`, `src/serving/model_server.py`, `src/release/verify.py` |
| B10 | `AUTH_SECRET` fell back to a tree literal and failed only on mutations | Partly — the production model refuses to render without it and CI validates that model against the committed example; the boot-time web env schema is deferred | `docker-compose.prod.yml`, `.github/workflows/ci.yml`, runbook D11 |
| B11 | No production deployment manifest existed at all | Fixed — a production-mode stack behind a TLS edge, every service in its production shape, no development flag anywhere | `docker-compose.prod.yml`, `infra/edge/Caddyfile` |
| B12 | Data-store ports were published to the host | Fixed — only the edge binds ports; every other service is reachable only on the private network | `docker-compose.prod.yml`, ADR 0013 |
| B13 | Redis had no persistence yet holds every ranker feature; an eviction silently scores zeros | Fixed — durable volume, `noeviction`, a preflight assertion, and a sidecar that refuses to become healthy against an empty store | `docker-compose.prod.yml`, `src/release/bootstrap.py`, `src/serving/model_server.py` |
| B14 | Rate limiting was measured absent rather than implemented | Fixed — a per-(tenant, subject) token bucket on every authenticated route, with the per-worker semantics written down | ADR 0014, `src/serving/ratelimit.py` |
| B15 | The tenant-isolation canary was in-process only and skipped silently against an unreachable target | Fixed — a remote canary in the package the API image ships, run as a verify row; an unreachable target is a hard failure and CI can no longer skip | `synthetic/tenant_isolation/`, `src/release/verify.py`, `.github/workflows/ci.yml` |
| B16 | No production-safe latency gate; the CI wrapper needs Compose recreation, cgroups and `/proc/stat` | Fixed as a deliberately weaker instrument — a canary profile with its own thresholds module and a scheduled workflow; the pinned gate is untouched | `synthetic/load/canary_thresholds.js`, `infra/k6/`, `.github/workflows/production-canary.yml` |
| B17 | No backup, restore or rollback verification anywhere | Fixed — encrypted off-provider dumps, a restore drill that deliberately skips the seeder, and a rollback rehearsal across a migration; both were run | `infra/backup/`, `infra/deploy/rollback-rehearsal.sh`, runbook §9 (rollback) and §10 (backups and the restore drill) |
| B18 | No job had ever booted the app with `ENVIRONMENT != dev` | Partly — the local rehearsal is the first integration proof (R-9); CI validates the production model and asserts it declares no auth bypass, but does not boot it | `.github/workflows/ci.yml`, rehearsal R-9 |
| B19 | `KEYCLOAK_AUTHORIZED_PARTIES` must be JSON; the CSV form crash-loops the container | Fixed — the preflight asserts the parsed value before the API boots on it, and the runbook prints the JSON form | `src/release/bootstrap.py`, the runbook |
| B20 | Two init jobs were sequenced by `service_completed_successfully`, which a PaaS has no equivalent for | Fixed — one image with `bootstrap` and `verify` modes, ordered by the release script, with entrypoint fences rather than sleeps | `infra/api/entrypoint.sh`, `src/release/bootstrap.py` |
| M1 | One uvicorn worker, no proxy-header handling | Fixed — `API_WORKERS` drives the count (four deployed, one in the demo stack); the proxy flags are recorded as unnecessary because the API terminates no TLS | `infra/api/entrypoint.sh`, `docker-compose.prod.yml` |
| M2 | The API held a BYPASSRLS credential in-process to read two columns | Fixed — the admin engine is gone from the API; the tenant router reads `public.tenants` on the app engine | `src/serving/app.py`, `src/serving/tenancy/router.py` |
| M3 | `/healthz` is liveness-only and there was no readiness endpoint | Fixed — `/readyz` proves the pooled database path and the JWKS fetch, reports the sidecars without gating on them | `src/serving/app.py`, `src/auth/middleware.py`, ADR 0013 |
| M4 | The native-thread serving invariant lived in one compose file and had no test | Fixed — the pins are `ENV` in the image, so the invariant travels with it | `infra/features/Dockerfile`, ADR 0010 |
| M5 | Six host bind mounts carried configuration no image contained | Moot — the target runs the Compose file from a checkout on the host; the images that need their config carry it | ADR 0013 |
| M6 | No resource limits and no restart policies | Partly — restart policies are set on every service; explicit memory caps are not, and the footprint is sized (≈2.0 GB steady) rather than enforced | `docker-compose.prod.yml`, ADR 0013 |
| M7 | Postgres connection budget: two sidecars connect directly as `admin_user`, bypassing the pooler | Partly — the four identities and the direct-by-design path are documented and the pool sizes are parameterised; no automatic budget check exists | `infra/pgbouncer/pgbouncer.prod.ini.tmpl`, the runbook |
| M8 | Feature snapshots grow one generation per release and nothing prunes them | Deferred — the retention rule is an owner decision, recorded rather than enforced | ADR 0013, runbook D8 |
| M9 | Python dependencies were largely unpinned in both service images | Fixed — exact pins in both, with the reasoning that a rollback must install what the image it replaces installed | `infra/api/requirements.txt`, `infra/features/requirements.txt` |
| M10 | Training was not bit-reproducible and `manifest.json` changed every run | Fixed — single-threaded deterministic LightGBM, an explicit `--as-of`, and a CI check that rebuilds the bundle and compares the whole manifest | `src/models/ranker/lgbm.py`, `src/training/demo_artifacts.py`, `.github/workflows/ci.yml` |
| M11 | The web app has no env schema and `trustHost` is hardcoded | Partly — the production model requires each variable; the boot-time schema is a post-deploy web PR | runbook D11 |
| M12 | Release ordering existed only in `make` recipes tied to Compose | Fixed — `bootstrap` (preflight, schema, seed, materialize) and `verify` are entrypoints any release job can call | `src/release/bootstrap.py`, `src/release/verify.py` |
| M13 | No scheduled trigger, so ADR 0010's nightly enforcement tier was unrealised | Fixed — a scheduled workflow re-runs the verify matrix every thirty minutes, with an advisory load check on demand | `.github/workflows/production-canary.yml`, ADR 0013 |
| M14 | `TmdbMetadataClient` is dead code at runtime and the runbook was stale about it | Partly — the deployment records that the API needs no TMDB token and no outbound internet except JWKS; the unused client is still there | `src/serving/tmdb.py`, the runbook |
| M15 | Keycloak had no healthcheck and realm drift was undetected | Fixed — a healthcheck that deliberately probes the `master` realm, plus a realm-drift job in CI | `docker-compose.prod.yml`, `.github/workflows/ci.yml` |
| M16 | No `/me` ownership mapping; `demo-impersonator` grants full persona access | Deferred — mitigated by disabled registration and exactly three provisioned accounts; the real fix stays a product-track item | ADR 0012, ADR 0013 risks |
| M17 | Audit coverage is recommendations-only | Deferred — stated plainly rather than left to read as "every authenticated request emits a row" | runbook §14 |
| M18 | The smoke check could not authenticate against a non-demo Keycloak | Fixed — realm, client, grant and user are flags with unchanged defaults | `synthetic/smoke/demo.py` |
| M19 | No post-deploy check exercised the write path | Fixed — verify runs an idempotent write with `expected_revision`, reads it back and reverts it | `src/release/verify.py` |
| M20 | `/users/{id}/movies/{movieId}` and `/users/{id}/taste-profile` have no tenant-isolation coverage | Open — the suite's own docstring still names both as uncovered | `tests/tenant_isolation/test_no_cross_tenant_leak.py` |
| M21 | Browser journeys are remote-targetable but not production-safe (hardcoded realm and credentials, real writes) | Deferred — the mutating set stays on the seeded CI stack; production gets a read-only canary spec | runbook D9 |
| M22 | `next-auth` is a beta in the auth path and `web/package.json` declares no `engines` | Partly — the deployment builds from the committed Dockerfile, so the Node version is pinned by the image; the `engines` field is still absent | `web/Dockerfile`, ADR 0013 |

## Decisions made along the way

- **The serving bundle and the Feast registry are baked into the image.** They
  are 247 KB of deterministic build output from a committed fixture at a pinned
  `as-of`, re-hashed against the manifest on every boot, which makes rolling
  back the model the same action as rolling back the image. The counterpart
  obligation is a CI gate that rebuilds the bundle and fails on any difference —
  non-negotiable #5 in mechanical form. (ADR 0013)
- **`/readyz` is a second unauthenticated path.** Non-negotiable #10 names
  `/healthz` specifically, so widening the set is recorded rather than quietly
  done. A readiness probe carries no `Authorization` header, so an authenticated
  readiness path is a deploy that can never confirm itself. The endpoint tells an
  anonymous caller only whether this process reaches its own database through the
  pooler and can fetch the serving realm's JWKS. The set is one
  `UNAUTHENTICATED_PATHS` frozenset read by the middleware, the rate limiter and
  the OpenAPI generator. (ADR 0013)
- **pgBouncer runs in `userlist` mode, not `auth_query` — measured, not
  preferred.** The rehearsal ran both: server-side SCRAM through the forced-user
  aliases that ADR 0008 depends on fails in `auth_query` mode, and works in
  `userlist` mode. That is now the default, with the finding written into the
  pooler's own entrypoint so nobody re-derives it. (`infra/pgbouncer/`, runbook)
- **Postgres traffic stays on the private network and TLS to Postgres is not
  required in code.** Every DSN is f-string-concatenated with no query-parameter
  hook and Feast hardcodes `sslmode: disable`, so requiring TLS would mean editing
  the credential path in the same release as the first production boot. It stops
  being true the moment anything moves off the box, and the ADR says so. (ADR 0013)
- **Rate limiting is a per-(tenant, subject) in-process token bucket**, keyed on
  the verified token rather than on a client address, with `X-RateLimit-*` headers
  and a `429` carrying `Retry-After`. The caveat is stated rather than discovered:
  each uvicorn worker holds its own buckets, so the effective limit is
  `limit × workers` and `X-RateLimit-Remaining` is not monotonic across requests.
  The shared Redis bucket is the named upgrade path and is tracked as
  **issue #70**. (ADR 0014)
- **The target is one small VPS running `docker-compose.prod.yml` unchanged**,
  chosen over a PaaS on cost (≈€4.50/month against a measured ≈$27) once owning
  the machine dissolved three of the four code facts that were doing the
  deciding — the superuser migration `0001` needs, the pooler admin console the
  boot check needs, and the shared filesystem the sidecars assumed. (ADR 0013)
- **Images are built by CI and published to GHCR; the host only ever pulls.**
  The commit SHA is the release identity, which makes a deploy and a rollback the
  same mechanism and makes "what is running?" answerable from a file on disk.
  (ADR 0013)
- **Deploys happen on a push to `main`, through one workflow that refuses to run
  unless that commit's CI is green**, records the rollback target before it
  changes anything, and rolls back automatically when post-deploy verification
  fails. (`.github/workflows/deploy-production.yml`)

## Rehearsal evidence

Fourteen steps, run locally against the production-mode stack from empty
volumes — the first boot this codebase has ever had with
`ENVIRONMENT=production`. What it exposed is in the rows below, and not one of
those defects would have been found by reading the code.

| Step | What it exercised | Verdict | Headline |
|---|---|---|---|
| R-1 | First boot with generated secrets and no auth-bypass variable at all | PASS | A `Settings` failure inside a uvicorn worker had been respawned forever while the container stayed "running", so restart policies never fired; the `serve` entrypoint now preflights `Settings` and exits |
| R-2 | `KEYCLOAK_AUTHORIZED_PARTIES` in CSV, then JSON | PASS | CSV fails `Settings` construction with no partial mode; the preflight now asserts the parsed list before the API boots on it |
| R-3 | `feast apply` at image build with no live store | PASS | The registry is a build product; it is checked semantically, never by byte hash, because two applies of identical definitions write different bytes |
| R-4 | The whole release sequence from empty volumes, no manual step | PASS after fixes | The stack had been starting the API by a bare `uvicorn` line no deployment uses; materialize and the model server lacked the pgBouncer admin credential `Settings` refuses to default outside dev |
| R-5 | The full https OIDC round trip through the edge | PASS | The discovery document's issuer is https and equals what the API validates; the browser flow runs against the edge with the registered redirect URI |
| R-6 | Provisioning run twice | PASS after fixes | Provisioning had never completed: two client descriptions exceeded a 255-character column, a `kcadm` field filter aborted every run after its writes, and accounts without an email fail Keycloak 25's user profile — which presents as "Account is not fully set up" on every password grant |
| R-7 | pgBouncer `scram-sha-256` in both auth modes | PARTIAL — decisive | `auth_query` fails server-side SCRAM through the forced-user aliases; `userlist` works and is now the default |
| R-8 | Alembic and the seeder as `migrator` on an empty database with pre-created roles | PASS | `migrator` owns every base table; the next deploy's schema step applies cleanly against the result |
| R-9 | Four deliberate breaks | PASS | Each refuses with a readable message: the dev auth bypass, the default sidecar token, a BYPASSRLS application role, and a session-mode pooler |
| R-10 | Clean-start seed → verify passing on the first request, no priming | PASS | reset 36 s, seed 103 s, verify 27 s, exit 0 — the regression evidence for the cold-worker defect at production shape |
| R-11 | Sidecar kill, then an emptied Redis | PASS | The API stays up, `/readyz` reports the sidecar unavailable, recommendations answer 200 with `learned: false`, and verify fails; an emptied Redis refuses the sidecar's boot rather than scoring zeros |
| R-12 | Rollback across a migration | PASS | The pre-deploy schema step exits 0 against a database ahead of the image — the additive-only policy holding at the moment it matters |
| R-13 | Restore drill: dump → destroy → restore → deploy without the seed step → verify | PASS | 56 s wall clock, then 38 s on the corrected restore-as-`migrator` path. Found that the backup job could not create its directory on a fresh volume and that the schema fence prescribed the wrong remedy after a superuser restore |
| R-14 | The pinned k6 gate at the production topology (4 API / 4 model workers, baked artifacts) | PASS | p50 6.85 ms, p95 9.47 ms, p99 12.93 ms, zero errors, zero dropped iterations |

Two further findings from the same session: the verify job asked the catalog for
a page larger than the contract allows, and it ran the tenant canary twice
against one subject's rate-limit bucket. Both are fixed.

**The one thing the rehearsal did not settle.** The production canary, run at
the rate-limit defaults shipped *at the time* (120/minute, burst 30), failed:
37.9% of one subject's 5 requests per second were refused, because keep-alive
pins connections to workers and the per-worker buckets fill unevenly. Nothing
was changed during the rehearsal to make it pass — that was an owner decision,
and the fix was always a variable (`RATE_LIMIT_REQUESTS_PER_MINUTE`) rather
than an exemption or a weakened canary (ADR 0014).

That decision has since been taken: the defaults are now **600/minute with a
burst of 120**, which puts the canary's 5 requests per second under a
10-per-second refill and leaves it nothing to refuse. The finding above is kept
as measured because the *shape* of it survives the new numbers — the bucket is
still per worker, so a single client's effective ceiling is still one worker's
bucket rather than workers × limit. The durable form of that is issue #70, and
a Redis-backed shared bucket is the named follow-up.

## What is deliberately not done

- **Feature parity has no production form.** `tests/feature_parity/` stays a CI
  gate; pointing it at production would mean handing a runner production database
  and Redis credentials and letting it write demo data. The deployed proxy —
  comparing `GET /users/{id}/features` against the audit row's `feature_values`
  for the same request id — is worth building and is not built.
- **The pinned latency gate's re-measure rule has no production form.** ADR
  0010's measurement-validity rule depends on a CPU-steal record and cgroup
  reads that a deployed host does not offer in the same shape. CI keeps the
  verdict; the canary records p99 without one, and says so.
- **Audit coverage is recommendations-only.** `src/serving/audit.py` matches one
  path; every mutation passes through unaudited. Non-negotiable #8 (predictions)
  holds; CLAUDE.md's broader goal describes intent, not the deployed system.
- **`docker-compose.dev.yml` and `docker-compose.staging.yml` do not exist.**
  The development stack is `docker-compose.yml` + `docker-compose.demo.yml` and
  stays that way, because a development stack that needs generated secrets to
  boot is one nobody uses. Production shape is rehearsed by
  `docker-compose.prod.yml` instead of by a third environment nobody runs.
- **There is no `/me` subject-to-profile ownership.** Any signed-in `demo`-realm
  account can read and mutate all four personas, which is why registration is
  disabled and only three accounts exist.
- **The rate limiter is per-worker, not shared.** The Redis-backed bucket is the
  right answer the moment the API runs more than one replica; until then the
  approximation is documented in the ADR, in the OpenAPI description and in the
  `429` body (issue #70).

## How to read the rest

- **[ADR 0013](adr/0013-production-deployment-target.md)** — the deployment
  target, the alternatives it was weighed against, the release order, and the
  sub-decisions that widen or pin a non-negotiable's literal wording.
- **[ADR 0014](adr/0014-request-rate-limiting.md)** — the limiter: what it keys
  on, why it is per-worker, and the upgrade path.
- **[Deployment runbook](deployment-runbook.md)** — the operator's half: the four
  Postgres identities, the secret inventory, the first-deploy sequence, the
  owner-decision table, the incident quick reference, backups and the restore
  drill (§10), and §14's plain list of what the deployment does not do.
- **`infra/deploy/`** — the environment contract (`production.env.example`, whose
  every credential is a required variable, so CI validating the production model
  against it is also a completeness check), the role provisioning SQL, and the
  rollback rehearsal.
- **[ADR 0010](adr/0010-synthetic-load-k6.md)** — the two-tier latency framing:
  the pinned CI gate keeps the verdict, the production canary is a deliberately
  lower-authority instrument.
- **[CLAUDE.md](../CLAUDE.md)** — the Phase 3 status section, which carries the
  remaining platform-track and product-track items this work did not close.

## Appendix — rehearsal record, 2026-08-27

The verbatim per-step record of the local production-mode rehearsal (plan items R-1 to R-14, run against `docker-compose.prod.yml` with `EDGE_TLS=internal` on the owner's machine, at commit `2d18f7f` before the fixes in `1be6a90`). This is the source for the table above.

| # | Step | Verdict | Evidence |
|---|---|---|---|
| R-1 | First boot with `ENVIRONMENT=production`, generated secrets, no `DEV_AUTH_BYPASS` | PASS | `.env.prod` from the template with 18 distinct `token_urlsafe(48)` values and a real `age` key; `git check-ignore` → `.gitignore` `.env.*`; `Settings()` → `SETTINGS-OK env=production`; all 10 services healthy |
| R-2 | CSV form of `KEYCLOAK_AUTHORIZED_PARTIES` crash-loops, JSON boots | PASS after fix | CSV → `SettingsError: error parsing value for field "keycloak_authorized_parties"`; originally not a crash-loop (workers respawned under uvicorn forever) — fixed by the settings preflight in `infra/api/entrypoint.sh`; after the fix `RestartCount=5 ExitCode=1` |
| R-3 | `feast apply` needs no live store | PASS | in-build step `DONE 8.7s`; re-proved on the built image with `docker run --network none` |
| R-4 | Whole release sequence from empty volumes, no manual step | PASS after two fixes | `RELEASE-BOOTSTRAP-OK 0012_audit_serving_inputs`; preflight issuer `https://auth.localtest.me/realms/demo`, pooler in transaction mode with RLS applied, Redis `noeviction`; materialize 120 / 8 / 960 rows |
| R-5 | Full https OIDC round trip through the edge | PASS | `curl --cacert`: every discovery endpoint https; real browser (Playwright in Docker) 11/11 — PKCE `S256`, `redirect_uri=https://app.localtest.me/api/auth/callback/keycloak`, `__Secure-authjs.session-token.0` `httpOnly` `secure`, Discover renders the learned-model label, sign-out returns the door |
| R-6 | `provision.sh` twice; audience mapper on both clients | PASS after two fixes | run 1 `PROVISION-OK` 44 s; run 2 `PROVISION-OK` 29 s with no `created`/`granted`/`password set`/`email set` lines; mapper present on all three clients in both realms |
| R-7 | pgBouncer scram-sha-256 in both modes | ANSWERED — `auth_query` does not work | `auth_query`: `server login failed: FATAL password authentication failed for user "app_user"` on both aliases; `userlist`: `app_user` and `admin_user` log in with the expected privileges; default changed to `userlist` |
| R-8 | Alembic + seeder as `migrator` on an empty database | PASS | 12 migrations `0001 → 0012_audit_serving_inputs`; seed 4 personas / 27 persona ratings / 480 background / 120 movies / 24 posters |
| R-9 | Four deliberate breaks must refuse | PASS 4/4 | `DEV_AUTH_BYPASS=true` → `dev_auth_bypass=True is only permitted when environment='dev'`; default model-server token → `only permitted in development`; the API on a BYPASSRLS role → `connected DB role 'admin_user' has BYPASSRLS=True ... app_user must have neither`; pgBouncer `pool_mode = session` → `only 'transaction' is safe under ADR 0008` |
| R-10 | Clean-start `prod-seed → prod-verify`, first smoke request | PASS | reset 36 s → seed 103 s → verify 27 s, all exit 0; `VERIFY-OK`, 9/9 verification rows, 10/10 reliability checks; sidecar on first boot `warm: true, warmup_ms: 33.21, workers: 4` |
| R-11 | Sidecar kill; Redis `FLUSHALL` | PASS 4/4 | API stays up, `/readyz` 200 with `model_server: unavailable`, recommendations 200 with `learned: false`; `verify` fails exactly on V-3, V-5, V-12; after `FLUSHALL` the sidecar refuses to become healthy (`DegenerateWarmupError` — no Feast event timestamp); `materialize` repairs it |
| R-12 | Rollback across a migration | PASS | `applied: false`, reason "is ahead of this release, so nothing was applied (rollback path)", `ROLLBACK-REHEARSAL-OK` |
| R-13 | Restore drill, no seed step | PASS after two fixes | 56 s as superuser, 38 s corrected as `migrator`; row counts identical to pre-backup; `prod-verify` exit 0 with `VERIFY-OK` |
| R-14 | Pinned k6 gate at 4 API / 4 model workers; browser | PARTIAL | gate PASS: p50 6.85 ms, p95 9.47 ms, p99 12.93 ms, 3 301 requests at 54.34/s, zero errors, zero dropped iterations, zero silent fallbacks; the production canary refused 37.9 % of one subject's 5 requests/second at the rate-limit defaults shipped at the time (120/minute, burst 30) -- the per-worker bucket finding, ADR 0014, issue #70; the defaults are now 600/minute burst 120, which the same profile fits inside; page-shaped advisory budgets all breached at 71.8 requests/second with correctness intact; the full browser journey suite was not run against the production shape (its fixtures assume the dev realm) — the R-5 journey stands in |

Defects the rehearsal exposed and fixed in the same branch (commit `1be6a90`): a `Settings` failure inside uvicorn workers that never crash-looped the container; the rehearsal stack starting the API by a command no deployment uses; missing pgBouncer admin credential on two services; `auth_query` mode; two Keycloak client descriptions over the 255-character column; a kcadm field filter that aborted provisioning after its writes; accounts without an email failing Keycloak 25's user profile and therefore every password grant; backup volume ownership; the verify job requesting a catalog page above the contract's maximum; the schema fence prescribing the wrong remedy after a superuser restore; `prod-verify` running the tenant canary twice against one subject's bucket; two harness messages that misreported a throttle as a fixture or serving fault.
