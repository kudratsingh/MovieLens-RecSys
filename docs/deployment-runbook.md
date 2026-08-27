# Production Deployment Runbook

This is the operational companion to [ADR 0013](adr/0013-production-deployment-target.md), which
pins the target (Railway, one `production` environment, two public hostnames) and the reasoning.
Start here with the one thing that surprises everybody: **there is no single `DATABASE_URL` for this
system.** Four Postgres identities are in play, they are not interchangeable, and using the wrong one
fails in a different way each time — silently, loudly, or at boot.

| Identity | Privilege | Created by | Used for |
|---|---|---|---|
| `postgres` (Railway's superuser) | SUPERUSER | the Postgres template | The one-time provisioning SQL below, and nothing else. It never appears in a service variable |
| `migrator` | LOGIN, **BYPASSRLS** | pre-created by you; migration `0001` leaves it alone | The `release` job only: `create_tables()` then `alembic upgrade head`, plus the persona seed. Owns every base table |
| `admin_user` | LOGIN, **BYPASSRLS** | pre-created by you; migration `0001` leaves it alone | `model-server`, `feature-server` and materialization — cross-tenant reads and writes, direct to Postgres, bypassing the pooler by design |
| `app_user` | LOGIN, plain — RLS applies | pre-created by you; migration `0001` leaves it alone | **The only role that serves a request.** Reaches Postgres through pgBouncer's `movielens_app` forced-user alias. The API refuses to boot if this role has BYPASSRLS or SUPERUSER |

A fifth name, `pgbouncer_admin`, is a **pgBouncer-internal identity with no Postgres role behind it**
— the provisioning SQL deliberately does not create it. It exists only in the pooler's own userlist,
in both auth modes, because the API's boot check opens the pooler's admin console with it. A sixth,
`pgbouncer_auth`, *is* a real Postgres role and is the one the pooler uses to look up SCRAM
verifiers.

## What is deployed

Nine long-lived services — `postgres-app`, `postgres-keycloak`, `redis`, `pgbouncer`, `keycloak`,
`api`, `model-server`, `feature-server`, `web` — and five run-to-completion jobs: `release`,
`keycloak-provision`, `verify` (cron `17 6 * * *`), `backup` (cron `0 4 * * *`) and `loadcheck`
(manual). Four volumes: `postgres-app` 5 GB, `postgres-keycloak` 1 GB, `redis` 1 GB, none on either
sidecar.

**Two hostnames, and only two:** `app.<domain>` → `web`, `auth.<domain>` → `keycloak`.

> **Hard rule.** `api`, `model-server`, `feature-server`, `pgbouncer`, `redis`, `postgres-app` and
> `postgres-keycloak` never get a custom domain and never get a TCP proxy enabled outside a
> time-boxed maintenance window you are actively watching. `feature-server` has no authentication of
> any kind and the model sidecar's `/healthz` is unauthenticated; both are safe only because nothing
> outside the private mesh can reach them. The deploy workflow asserts this before it deploys
> anything (`no_public_sidecar`), so the way you find out you broke the rule is a failed deploy, not
> an incident.

Config lives in git: one `infra/railway/<service>.json` per service. Railway reads that path relative
to the repository root regardless of a service's Root Directory. Set a Root Directory **only** on
`web` (value `web`, with `dockerfilePath: "Dockerfile"`); every other service builds from the repo
root. Auto-deploy is off on every service — the release workflow is the only deploy path.

## 0. Decisions to record before the first deploy

Each row has the plan's recommendation as its default. Replace `☐` with `☑` and a date when you
accept it, or write the override in place. Deploying with a row still open is how a decision gets
made by accident.

| # | Decision | Default | Recorded |
|---|---|---|---|
| D1 | Do the native-parallelism pins (`OMP/OPENBLAS/MKL/VECLIB_MAXIMUM_THREADS=1`) land on `main` with a green load gate first? | **Yes — hard prerequisite.** They are the difference between p99 ≈49 ms and p99 ≈904 ms. Deploying a build whose own SLO gate is red is not a deployment | ☐ |
| D2 | The domain | Own domain: `app.<domain>` + `auth.<domain>`, Railway-managed certificates. A `*.up.railway.app` issuer makes any later move an identity migration | ☐ |
| D3 | Hobby or Pro | **Hobby.** Both cost about the same at this topology (~$28–34/mo usage). The 5 GB volume cap is the binding limit and the fixture is 120 titles | ☐ |
| D4 | Keycloak's admin console is internet-reachable at `https://auth.<domain>/admin`, with no path-level ACL on a Railway domain | Accept, with: a 48-character bootstrap password used once, a named human admin, brute-force protection, and the bootstrap variables deleted afterwards. Otherwise front both hostnames with a CDN path rule from day one | ☐ |
| D5 | Persona impersonation: any signed-in `demo`-realm account can read **and mutate** all four personas across every product route | `registrationAllowed: false` (already seeded) plus exactly three deliberately-created accounts, and a line on the sign-in door saying the published account drives shared named personas | ☐ |
| D6 | Does the pinned Railway CLI redeploy a *specific previous* deployment, or does rollback need the GraphQL `deploymentRollback` mutation with `RAILWAY_API_TOKEN`? | **Answered: the CLI cannot.** `railway redeploy` takes no deployment id and redeploys the *latest*; `railway down` **deletes** the latest deployment rather than reverting — never reach for it during an incident. Rollback is the GraphQL mutation `deploymentRollback(id:)` with `RAILWAY_API_TOKEN`, against the ids the deploy workflow publishes as its `rollback-target` artifact. Hold both tokens: `RAILWAY_API_TOKEN` (account/workspace, `Authorization: Bearer`) is what every step here prefers, `RAILWAY_TOKEN` (project, `Project-Access-Token`) is the fallback | ☑ 2026-08-27 — from the Railway API documentation, not yet from a run |
| D7 | Postgres TLS | Keep all Postgres traffic on Railway's private mesh; `server_tls_sslmode = prefer` in pgBouncer gives the `app_user` leg TLS for free. Recorded in ADR 0013 — requiring TLS everywhere is a DSN-construction change plus a Feast config change | ☐ |
| D8 | Retention for `feature_store.*`, which gains one generation per release and is never pruned | Delete-then-insert per `as_of`, keeping the last three generations — bounded by construction rather than by attention | ☐ |
| D9 | Do the browser journeys run against production? | No. One **read-only** canary spec (sign in, load `/` and `/discover`, assert the learned policy label, sign out). The mutating set writes real rows and stays on the seeded Compose stack in CI | ☐ |
| D10 | Is one serving tenant enough for the MVP? | Yes: `demo` serves the product, `default` exists so the isolation canary has a subject that must be denied. A second serving tenant needs a second sidecar process today | ☐ |
| D11 | Does a boot-time schema for the `web` environment variables land before or after the first deploy? | After. Every web variable has a silent `?? "http://localhost:…"` fallback, and a missing `AUTH_SECRET` fails **only on mutations** while reads keep working. Not a boot blocker; land it as a small web PR in the first post-deploy week | ☐ |
| D12 | Who holds the ~16 generated secrets, who owns rotation, and which GitHub environment gates the deploy? | A password manager as the authority; Railway shared/service variables and a `production` GitHub environment with a required reviewer as the two derived copies | ☐ |

## 1. Generate the secrets

**Generate every password, token and secret with this exact command:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

This is not a style preference. Every DSN in `src/config.py` is built by f-string concatenation with
no URL-encoding, so a password containing `@` silently mis-parses the host and one containing `%` is
percent-decoded into something else. The URL-safe alphabet (`A–Z a–z 0–9 - _`) removes the whole
class of failure. Do not use `openssl rand -base64`.

Every credential currently in the tree is a dev fixture in a public repository. All of them need a
generated replacement:

- [ ] `APP_USER_DB_PASSWORD` — replaces `app_user/app_user`
- [ ] `ADMIN_USER_DB_PASSWORD` — replaces `admin_user/admin_user`; **the same value is `FEAST_POSTGRES_PASSWORD`**
- [ ] `MIGRATOR_DB_PASSWORD` — replaces `migrator/migrator`
- [ ] `PGBOUNCER_ADMIN_PASSWORD` — replaces `pgbouncer_admin/pgbouncer_admin`, which is also published in this repository's pooler config
- [ ] `PGBOUNCER_AUTH_PASSWORD` — new; the `pgbouncer_auth` lookup role
- [ ] `MODEL_SERVER_AUTH_TOKEN` — replaces `dev-model-server-token`
- [ ] `REDIS_PASSWORD` — new; Redis has no password in the dev stack
- [ ] `KC_DB_PASSWORD` — replaces Keycloak DB `keycloak/keycloak`
- [ ] `KEYCLOAK_ADMIN_PASSWORD` — replaces `admin/admin`; used once, then deleted
- [ ] `KC_HUMAN_ADMIN_PASSWORD` — the named human admin created in the `master` realm
- [ ] `KC_API_CLIENT_SECRET` — replaces `movielens-api-secret-dev-only`
- [ ] `KC_VERIFY_CLIENT_SECRET` — new; the confidential `movielens-verify` client, in both realms
- [ ] `WALKTHROUGH_PASSWORD` — the portfolio account published on the sign-in door
- [ ] `VERIFY_PASSWORD` — the `verify` account, known only to Railway and GitHub Actions
- [ ] `ISOLATION_PASSWORD` — the `default`-realm `isolation` account the tenant canary authenticates as
- [ ] `AUTH_SECRET` (web) — replaces `movielens-demo-auth-secret-change-outside-local-dev`. Encrypts the session JWE holding the API tokens **and** keys the BFF CSRF hash
- [ ] `RCLONE_CONFIG_REMOTE_*` — object-store credentials for the off-provider backup copy
- [ ] `BACKUP_AGE_RECIPIENT` — an `age` **public** key; not a secret, but the private key is, and it lives only in the password manager

Custody (D12): the password manager is the authority. Railway shared variables hold anything two
services need; Railway service variables hold single-service secrets; the GitHub `production`
environment (required reviewer) holds `RAILWAY_TOKEN`, `RAILWAY_API_TOKEN`, `VERIFY_CLIENT_SECRET`
and `VERIFY_PASSWORD`, and a second environment `production-canary` holds the same Railway token
values with no protection rules (§2 step 8 says why). Nothing else in GitHub. CI holds zero secrets
today, so this is new plumbing either way — do not extend an existing workflow's permissions to reach
it.

## 2. Create the project

1. Create the Railway project with one environment named `production`. Do not create a second
   environment yet; a persistent staging environment is a Pro-tier decision (D3).
2. Add the two Postgres template services (`postgres-app`, `postgres-keycloak`) and the `redis`
   service. Redis is **not** the template: it is image `redis:7` with an explicit start command,
   because the online feature store must not evict:

   ```
   redis-server --requirepass "$REDIS_PASSWORD" --maxmemory-policy noeviction \
                --appendonly yes --appendfsync everysec --dir /data
   ```

   Feature-view TTLs are 3650 days and a missing feature reads as `0.0` rather than an error, so an
   eviction would silently degrade every ranking score with nothing failing anywhere.
3. Add the remaining services from this repository, each pointed at its `infra/railway/<service>.json`
   config file. Set a Root Directory only on `web`. Pointing a service at no config file, or at the
   wrong one, deploys it with dashboard defaults and warns about nothing — this is the setting that
   makes every other setting in `infra/railway/` take effect. `infra/railway/README.md` lists what
   stays dashboard-only.

   **The service names are load-bearing — type them exactly.** The deploy workflow resolves services
   by name before it deploys anything, and asserts each of these exists:

   ```
   keycloak  release  model-server  feature-server  api  web  verify
   pgbouncer  redis  postgres-app  postgres-keycloak
   ```

   The last four are named so that the `no_public_sidecar` assertion is actually asserting about
   something: a Postgres service called `Postgres` instead of `postgres-app` makes the check pass
   over a service it never found. The three remaining jobs — `keycloak-provision`, `backup` and
   `loadcheck` — are not resolved by the workflow, but name them after their config files too. A
   wrong name fails the workflow loudly on its first run rather than silently, which is the good
   version of this mistake, but it is cheaper to avoid.

   **One thing must be answered on the first deploy:** does Railway's start command replace the
   container's whole command, or only its `CMD`, leaving the image's `ENTRYPOINT` in front? Every
   start command in `infra/railway/` is written for the replace-everything reading. Exactly one
   service breaks under the other reading: `keycloak-provision` becomes
   `kc.sh /opt/keycloak/provision.sh` and dies on its first line. The fix is one line and is written
   down in `infra/railway/README.md`. Answer it during the Keycloak rehearsal, not during a real
   provisioning run. (`api`, `release` and `verify` name their dispatcher by absolute path, and its
   default branch execs an unrecognised first argument as given, so they survive either reading —
   which is why those three start commands keep the path rather than the bare mode word.)
4. **Domains.** Attach `app.<domain>` to `web` and `auth.<domain>` to `keycloak`. Attach nothing
   else, ever.
5. **Volumes.** `postgres-app` 5 GB, `postgres-keycloak` 1 GB, `redis` 1 GB mounted at `/data`. No
   volume on `model-server` or `feature-server` — the bundle and the registry are baked into the
   image. No volume on `backup`: dumps stage in the ephemeral filesystem and are pushed
   off-provider, because a backup on the same provider as the data is one account away from total
   loss.
6. **Backups tab.** For `postgres-app`, `postgres-keycloak` and `redis`, enable scheduled backups:
   Daily (kept 6 days) and Weekly (kept 1 month). **This has no CLI and no config-file form** — it is
   a dashboard step, so take a screenshot of each Backups tab and keep it with the deployment record.
   A restore mounts a *new* volume for the whole service, which is exactly why the application and
   identity databases are separate services.
7. **Do not enable app sleeping** on `keycloak`, `api` or `model-server`. Keycloak's 8–15 s JVM cold
   start trips the web app's OIDC discovery, and a slept model-server reintroduces the cold-worker
   defect on the first request after every idle period.
8. **Create two GitHub environments, not one.** Both hold the same values; they differ only in their
   protection rules.

   | Environment | Protection | Secrets | Variables |
   |---|---|---|---|
   | `production` | required reviewer | `RAILWAY_TOKEN`, `RAILWAY_API_TOKEN`, `VERIFY_CLIENT_SECRET`, `VERIFY_PASSWORD` | `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` |
   | `production-canary` | **none — deliberately no protection rules** | the same Railway token values | the same two ids |

   The second one is not redundant. `.github/workflows/production-canary.yml` runs on a schedule, and a required
   reviewer on a job that fires every thirty minutes means every canary queues for approval — that
   is, no canary at all. The project and environment ids are GitHub **variables** rather than
   secrets, because ids are configuration; with an account or workspace token they must be set, and
   the workflow refuses with a named error if they are missing. (A project token resolves them
   itself, but its reach across the API operations these workflows use is unproven — see D6.)

## 3. Set the variables

`${{Svc.VAR}}` is Railway's reference syntax and `${{shared.X}}` is an environment-level shared
variable. **S** marks a generated secret from §1. Three things below matter more than the lists
themselves, so read them first.

**`KEYCLOAK_AUTHORIZED_PARTIES` is JSON, not CSV.** The setting is typed `tuple[str, ...]`, which
pydantic-settings parses as JSON. The comma-separated form a variable panel invites raises
`SettingsError` and crash-loops the container with no partial mode. Type exactly:

```
["movielens-api","movielens-web","movielens-verify"]
```

**These three must agree exactly, and a mismatch is a total auth outage with no partial failure
mode** — every request 401s, on every route, immediately:

| Who | Setting | Production value |
|---|---|---|
| `keycloak` | `KC_HOSTNAME` | `https://auth.<domain>` |
| `api` | `KEYCLOAK_PUBLIC_BASE_URL` | `https://auth.<domain>` |
| `web` | `KEYCLOAK_PUBLIC_ISSUER` | `https://auth.<domain>/realms/demo` |

The auth middleware reconstructs `expected_issuer = f"{public_base_url}/realms/{realm}"` and rejects
any token whose `iss` differs by a single character — including a trailing slash, `http` instead of
`https`, or the Railway-generated hostname instead of the custom one. The release preflight asserts
issuer equality against the live discovery document before anything else runs, which is the check
most likely to save the first deploy.

**`FEAST_POSTGRES_*` and `ADMIN_USER_DB_*` must carry identical values on `model-server` and on the
release path.** `configure_feast_environment` in `src/features/online.py` `setdefault`s Feast's
variables from the `admin_user_*` settings, while materialization builds its own engine from
`settings.admin_user_database_url`. If the two spellings disagree, half the system reads one database
and half reads another, and nothing errors — it is a silent split-brain.

Three more that are easy to miss:

- `MODEL_SERVER_WORKERS` must be set explicitly on `model-server` even though the worker count is
  also on that service's start command (`infra/railway/model-server.json` runs uvicorn directly).
  The process cannot read its own `--workers` flag, so without the variable `/healthz` reports
  `"workers": null` and you lose the one place that tells you how many workers actually warmed. Keep
  the two in step. The API does not have this problem — its worker count lives in `API_WORKERS`
  alone.
- `MODEL_TENANT_ID=demo` must be set explicitly on `api` as well. `/readyz` fetches the JWKS for that
  realm, so the readiness probe and the serving tenant are coupled — make it deliberate rather than a
  default.
- `KC_PROXY_HEADERS=xforwarded` on `keycloak` is absent everywhere in the dev tree. Without it
  Keycloak builds `http://` absolute URLs behind the TLS-terminating edge and the OIDC redirect loop
  breaks.

Dev-only variables that **must be absent** from every production service: `DEV_AUTH_BYPASS`,
`DEV_BYPASS_TENANT`, `DEV_BYPASS_USER`, `MOVIELENS_UI_FIXTURE_MODE`. `Settings` refuses to construct
if the bypass is set outside `dev`, so this is enforced — but the enforcement is a crash-loop, and
knowing why is better than debugging it.

### Shared (defined once at the environment level)

`PUBLIC_APP_ORIGIN` = `https://app.<domain>` · `PUBLIC_AUTH_ORIGIN` = `https://auth.<domain>` ·
`SERVING_REALM` = `demo` · `APP_USER_DB_PASSWORD` **S** · `ADMIN_USER_DB_PASSWORD` **S** ·
`MIGRATOR_DB_PASSWORD` **S** · `PGBOUNCER_ADMIN_PASSWORD` **S** · `PGBOUNCER_AUTH_PASSWORD` **S** ·
`MODEL_SERVER_AUTH_TOKEN` **S** · `REDIS_PASSWORD` **S** · `REDIS_CONNECTION_STRING` **S** =
`${{redis.RAILWAY_PRIVATE_DOMAIN}}:6379,password=${{shared.REDIS_PASSWORD}}` — Feast's own
comma-separated `k=v` form, **not** a `redis://` URL.

### `api`

| Variable | Value |
|---|---|
| `ENVIRONMENT` / `PORT` / `API_WORKERS` | `production` / `8000` / `4` — `API_WORKERS` is the **only** home for the API's worker count. `infra/railway/api.json` carries no `--workers` literal, the image's `serve` mode reads this variable, and `synthetic/load/recommendations.js` sizes its warm-up from it. The measured p99 baseline is a four-worker number |
| `APP_USER_DB_HOST` / `_PORT` | `${{pgbouncer.RAILWAY_PRIVATE_DOMAIN}}` / `6432` — **the pooler, not Postgres**; the boot check opens the pooler's admin console at exactly this host and port |
| `APP_USER_DB_NAME` | `movielens_app` — **a pgBouncer alias, not a database name** |
| `APP_USER_DB_USER` / `_PASSWORD` | `app_user` / `${{shared.APP_USER_DB_PASSWORD}}` **S** — this role must have neither BYPASSRLS nor SUPERUSER or the boot fails |
| `PGBOUNCER_ADMIN_USER` / `_PASSWORD` | `pgbouncer_admin` / `${{shared.PGBOUNCER_ADMIN_PASSWORD}}` **S** |
| `KEYCLOAK_BASE_URL` | `http://${{keycloak.RAILWAY_PRIVATE_DOMAIN}}:8080` — discovery and JWKS only |
| `KEYCLOAK_PUBLIC_BASE_URL` | `${{shared.PUBLIC_AUTH_ORIGIN}}` — **the trusted issuer origin** |
| `KEYCLOAK_AUDIENCE` | `movielens-api` |
| `KEYCLOAK_AUTHORIZED_PARTIES` | `["movielens-api","movielens-web","movielens-verify"]` — **JSON** |
| `KEYCLOAK_SERVICE_CLIENT_ID` | `movielens-api` — deliberately **not** `movielens-verify`, so a verify token gets no `azp` bypass on persona access |
| `JWKS_CACHE_TTL_SECONDS` | `300` |
| `MODEL_SERVER_URL` / `_TIMEOUT_SECONDS` / `_AUTH_TOKEN` | `http://${{model-server.RAILWAY_PRIVATE_DOMAIN}}:6570` / `0.5` — **do not raise it to paper over cold workers** / `${{shared.MODEL_SERVER_AUTH_TOKEN}}` **S** |
| `FEAST_FEATURE_SERVER_URL` | `http://${{feature-server.RAILWAY_PRIVATE_DOMAIN}}:6566` |
| `MODEL_TENANT_ID` | `demo` — `/readyz` fetches this realm's JWKS |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` / `_BURST` | `120` / `30` — the ADR 0014 token bucket's refill rate and its capacity. **Leave `RATE_LIMIT_ENABLED` unset**: it is tri-state, and unset means on everywhere except `environment == "dev"`. The image bakes `ENVIRONMENT=production`, so the limiter is on without anyone remembering a variable |
| `ADMIN_USER_DB_*`, `POSTGRES_*`, `TMDB_READ_ACCESS_TOKEN` | **absent.** The API holds no BYPASSRLS credential, no migrator DSN, and needs no outbound internet except JWKS |

Two things about the limiter that will not be obvious from the two numbers. **The bucket lives in
the worker process**, so a four-worker API admits up to `4 × 120` per minute for one subject — the
configured value is per worker, not per service, and ADR 0014 records the Redis-backed shared bucket
as the named upgrade path rather than pretending otherwise. And **the key is the verified
`(tenant, sub)`, never a client address**: behind Railway's edge every request arrives from a proxy,
so an address-keyed limiter would throttle the whole deployment as one caller.

### `model-server`

| Variable | Value |
|---|---|
| `ENVIRONMENT` / `PORT` / `MODEL_SERVER_WORKERS` | `production` / `6570` / `4` |
| `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS` | all `"1"` — also baked into the image. ADR 0010 calls these a serving invariant, not a local test convenience: without them p99 was 903.64 ms at 0% steal; with only these four changed, 48.99 ms |
| `MODEL_SERVER_AUTH_TOKEN` | `${{shared.MODEL_SERVER_AUTH_TOKEN}}` **S** |
| `PGBOUNCER_ADMIN_PASSWORD` | `${{shared.PGBOUNCER_ADMIN_PASSWORD}}` **S** — and it is not a mistake that a sidecar which never opens the pooler needs it. `src/serving/model_server.py` builds `Settings()` at import time and both production guards in `src/config.py` fire at construction, whether or not the value is ever read. Omit it and the sidecar exits before it serves anything, with `the default pgBouncer admin password is only permitted in development`. **The same variable is required on the pre-deploy command**, which runs the same image |
| `MODEL_ARTIFACT_DIR` / `MODEL_MANIFEST_NAME` | `/app/models/serving` (the baked path) / `manifest.json` |
| `MODEL_FEATURE_CACHE_MAX_ENTRIES` / `MODEL_TENANT_ID` | `256` / `demo` |
| `FEAST_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `${{postgres-app.RAILWAY_PRIVATE_DOMAIN}}` / `5432` / `movielens` / `admin_user` / `${{shared.ADMIN_USER_DB_PASSWORD}}` **S** — direct to Postgres, bypassing the pooler by design |
| `ADMIN_USER_DB_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` | **identical values to `FEAST_POSTGRES_*`** — see the split-brain note above |
| `FEAST_REPO_PATH` / `REDIS_CONNECTION_STRING` | `src/features/feast_repo` / `${{shared.REDIS_CONNECTION_STRING}}` **S** |

### The rest

- **`feature-server`** — `ENVIRONMENT=production` · `PORT=6566` · the same `FEAST_POSTGRES_*` and
  `REDIS_CONNECTION_STRING` as `model-server` · `FEAST_REPO_PATH=src/features/feast_repo`. No start
  command; the image's `CMD` is already right. Health path is `/health`, **not** `/healthz`.
- **`web`** — `PORT=3001` · `HOSTNAME=0.0.0.0` · `AUTH_SECRET` **S** ·
  `AUTH_URL=${{shared.PUBLIC_APP_ORIGIN}}` · `AUTH_TRUST_HOST=true` ·
  `APP_ORIGIN=${{shared.PUBLIC_APP_ORIGIN}}` (the expected `Origin` on every BFF mutation) ·
  `RECOMMENDATION_API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000` ·
  `KEYCLOAK_PUBLIC_ISSUER=${{shared.PUBLIC_AUTH_ORIGIN}}/realms/demo` ·
  `KEYCLOAK_INTERNAL_ISSUER=http://${{keycloak.RAILWAY_PRIVATE_DOMAIN}}:8080/realms/demo` ·
  `KEYCLOAK_CLIENT_ID=movielens-web`. Deploy from the committed Dockerfile, never a buildpack —
  `NODE_ENV=production` from the image is the production half of the UI fixture lockout.
- **`keycloak`** — `KC_DB=postgres` · `KC_DB_URL_HOST=${{postgres-keycloak.RAILWAY_PRIVATE_DOMAIN}}` ·
  `KC_DB_URL_DATABASE=keycloak` · `KC_DB_USERNAME=keycloak` · `KC_DB_PASSWORD` **S** ·
  `KC_HOSTNAME=${{shared.PUBLIC_AUTH_ORIGIN}}` · `KC_PROXY_HEADERS=xforwarded` ·
  `KC_HTTP_ENABLED=true` (only because TLS terminates at the edge) · `KC_HTTP_PORT=8080` ·
  `PORT=8080` · `KC_HOSTNAME_STRICT=true` · `KC_HEALTH_ENABLED=true` ·
  `JAVA_OPTS_APPEND=-XX:MaxRAMPercentage=60` · `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` **S**,
  both **deleted after provisioning**.
- **`pgbouncer`** — `PGB_UPSTREAM_HOST=${{postgres-app.RAILWAY_PRIVATE_DOMAIN}}` ·
  `PGB_UPSTREAM_PORT=5432` · `PGBOUNCER_LISTEN_PORT=6432` · `PGBOUNCER_ADMIN_USER=pgbouncer_admin` ·
  `PGBOUNCER_ADMIN_PASSWORD` **S** · `PGBOUNCER_AUTH_USER=pgbouncer_auth` · `PGBOUNCER_AUTH_PASSWORD`
  **S** · `PGBOUNCER_AUTH_MODE=userlist`, which additionally needs `APP_USER_DB_PASSWORD` and
  `ADMIN_USER_DB_PASSWORD` **S** so the entrypoint can render the file. `auth_query` does not work
  against the forced-user aliases — §4 has the measurement.
- **`release`** (J1) — `ENVIRONMENT=production` ·
  `POSTGRES_HOST=${{postgres-app.RAILWAY_PRIVATE_DOMAIN}}` · `POSTGRES_PORT=5432` ·
  `POSTGRES_DB=movielens` · **`POSTGRES_USER=migrator`** · `POSTGRES_PASSWORD` **S** ·
  `APP_USER_DB_*` and `PGBOUNCER_ADMIN_*` (the preflight pool-mode assertion) · `ADMIN_USER_DB_*`
  (the registry and online-read assertions) · `REDIS_CONNECTION_STRING` **S** · the five
  `KEYCLOAK_*` values (the issuer preflight, and for `Settings()` to construct) ·
  `MODEL_SERVER_AUTH_TOKEN` **S** · `SERVING_TENANT_ID=demo` and `MODEL_TENANT_ID=demo` — the
  release job names the tenant it seeds and materializes, and the second spelling is the one
  `Settings` itself reads. Alembic runs as `migrator` purely
  because of `POSTGRES_USER` — no code change — which is what makes `migrator` own every base table
  and what makes the forced-RLS statements and the 0010 backfill work.
- **`verify`** (J3) — `ENVIRONMENT=production` · `API_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000` ·
  `WEB_URL=http://${{web.RAILWAY_PRIVATE_DOMAIN}}:3001` ·
  `KEYCLOAK_URL=http://${{keycloak.RAILWAY_PRIVATE_DOMAIN}}:8080` ·
  `KEYCLOAK_PUBLIC_BASE_URL=${{shared.PUBLIC_AUTH_ORIGIN}}` · `VERIFY_REALM=demo` ·
  `VERIFY_CLIENT_ID=movielens-verify` · `VERIFY_CLIENT_SECRET` **S** · `VERIFY_USERNAME=verify` ·
  `VERIFY_PASSWORD` **S** · `ISOLATION_REALM=default` · `ISOLATION_USERNAME=isolation` ·
  `ISOLATION_PASSWORD` **S** · `APP_USER_DB_*` and `PGBOUNCER_ADMIN_*` (the audit rollup runs as
  `app_user` through the pooler, inside a tenant-scoped transaction) · `MODEL_SERVER_AUTH_TOKEN`
  **S**. **No `POSTGRES_*`.**

  Three more that `src/release/verify.py` reads and that a `verify` job is easy to create without,
  because each is a *row* that fails rather than a boot that fails:

  | Variable | Value | Which row needs it |
  |---|---|---|
  | `MODEL_SERVER_URL` | `http://${{model-server.RAILWAY_PRIVATE_DOMAIN}}:6570` | The artifact-provenance row reads the sidecar's `/healthz` through `settings.model_server_url`, whose default is `localhost` |
  | `APP_ORIGIN` (or `PUBLIC_APP_ORIGIN`) | `${{shared.PUBLIC_APP_ORIGIN}}` | The realm-invariants row cannot know which redirect URIs are the expected ones without the public app origin |
  | `KEYCLOAK_ADMIN_CLIENT_ID` + `KEYCLOAK_ADMIN_CLIENT_SECRET` **S**, or `KEYCLOAK_ADMIN_USERNAME` + `KEYCLOAK_ADMIN_PASSWORD` **S** with `KEYCLOAK_ADMIN_REALM` (default `master`) | a service-account client holding only `view-realm` and `view-clients`, or the named human admin from D4 | The realm-invariants row again — it reads the Keycloak admin API |

  The last one is the awkward one and it is awkward on purpose: `movielens-verify` is
  `serviceAccountsEnabled: false` and holds no realm-management role, so a leaked verify credential
  is not an admin credential. That is exactly why the realm-invariants row is the one row that
  structurally cannot use the verify identity, and why the least-privilege answer is a small
  service-account client rather than reusing the human admin.
- **`backup`** (J4) — `PGHOST_APP` / `PGUSER_APP=migrator` / `PGPASSWORD_APP` **S** · `PGHOST_KC` /
  `PGUSER_KC=keycloak` / `PGPASSWORD_KC` **S** · `BACKUP_AGE_RECIPIENT` (an `age` public key) ·
  `RCLONE_CONFIG_REMOTE_*` **S** · `BACKUP_RETENTION_DAILY=7` · `BACKUP_RETENTION_WEEKLY=4` ·
  `BACKUP_RETENTION_MONTHLY=6`.
- **`loadcheck`** (J5) — `BASE_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000` ·
  `KEYCLOAK_URL=http://${{keycloak.RAILWAY_PRIVATE_DOMAIN}}:8080` · `LOAD_PROFILE=prod-canary` ·
  `KEYCLOAK_REALM=demo` · `KEYCLOAK_CLIENT_ID=movielens-verify` · `KEYCLOAK_CLIENT_SECRET` **S** ·
  `KEYCLOAK_USERNAME=verify` · `KEYCLOAK_PASSWORD` **S** · `API_WORKERS=4` ·
  `RESULTS_DIR=/tmp/results`.
- **`keycloak-provision`** (J2) — see §5; its variable list is there because it is run by hand.

## 4. One-time provisioning SQL

**There is exactly one copy of this SQL: `infra/deploy/provision-roles.sql`.** Do not retype it into
a dashboard query box from memory or from an older version of this document — a second copy is how
the `ALTER DEFAULT PRIVILEGES` line below goes missing, and its absence fails *every* deploy rather
than the first one. Run the file once, as Railway's `postgres` superuser, connected to the
application database, **before the release job's first migration**. Railway's `postgres` role **is** a
real superuser, which is what makes the BYPASSRLS grants possible and is the specific bar
DigitalOcean Managed Postgres does not clear.

```sql
CREATE DATABASE movielens;   -- migration 0001 hardcodes GRANT CONNECT ON DATABASE movielens
```

```bash
# Connected to the application database, as the superuser. The file reads the
# database name from psql's own :"DBNAME", which is why it must be run from
# inside the target rather than from `postgres` — and why the same file works
# unchanged against the restore-drill database.
psql "$SUPERUSER_DSN_TO_MOVIELENS" -v ON_ERROR_STOP=1 \
  -v app_password="$APP_USER_DB_PASSWORD" \
  -v admin_password="$ADMIN_USER_DB_PASSWORD" \
  -v migrator_password="$MIGRATOR_DB_PASSWORD" \
  -v pgbouncer_auth_password="$PGBOUNCER_AUTH_PASSWORD" \
  -f infra/deploy/provision-roles.sql

psql "$SUPERUSER_DSN_TO_MOVIELENS" -v ON_ERROR_STOP=1 \
  -f infra/postgres/pgbouncer-auth.sql
```

The file creates `app_user` (plain LOGIN, deliberately **not** BYPASSRLS), `admin_user` and
`migrator` (both BYPASSRLS), and `pgbouncer_auth`, each with the generated password passed in as a
psql variable so no secret is ever written into a file or into the server log. Migration `0001`'s
`IF NOT EXISTS` guards then leave those three alone, which is why pre-creating them here is what keeps
the tree-literal passwords `app_user` / `admin_user` / `migrator` — published in a public repository —
from ever taking effect. It is idempotent (`ALTER` where the role already exists), so re-running it
after a password rotation or a restore is how a rotation is applied.

**Never wrap these statements in a `DO $$ … $$` block.** psql does not interpolate `:'var'` inside a
dollar-quoted string: the roles would be created with the literal text `:'app_password'` as their
password, the script would report success, and nobody would be able to log in. The file builds every
role statement with `format(… %L …)` and runs it through `\gexec` for exactly that reason.

The consequence of `format(… %L …)` is that the password *is* in the SQL text the server executes.
Postgres defaults to `log_statement = none`, but set `PGOPTIONS="-c log_statement=none"` on the
session anyway — the rehearsal job does — so a server someone configured otherwise does not end up
with four generated credentials in its log.

The one statement worth reading before you run it:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO admin_user;
```

The model-server's pre-deploy fence reads `public.alembic_version` to learn whether the release job's
schema has arrived, and it runs as `admin_user` — that is the identity the features image carries.
`alembic_version` is created by whoever ran the migration and carries no `GRANT` of its own, so
without this line the fence fails with `insufficient_privilege` on every deploy, and
`src/release/bootstrap.py` raises a `ReleaseError` naming this exact statement. Default privileges
apply only to objects created *after* they are set, so it has to be in place before the first
migration. It grants nothing meaningful — `admin_user` is already BYPASSRLS.

What the file deliberately does **not** do, in case an older copy of it does: it does not transfer
ownership of the database or of schema `public` to `migrator`, and it does not create
`pgbouncer_admin`. `migrator` gets `CONNECT … WITH GRANT OPTION`, `CREATE` on the database, and
`USAGE, CREATE … WITH GRANT OPTION` on `public` — exactly the privileges migration `0001`'s onward
grants and migration `0007`'s `CREATE SCHEMA feature_store` exercise, and nothing more. Ownership
stays with the superuser.

Order matters: `infra/postgres/pgbouncer-auth.sql` runs second because it installs a
`SECURITY DEFINER` lookup function executable only by `pgbouncer_auth` and **raises a named exception
rather than half-installing** if that role does not exist yet. It grants CONNECT on
`current_database()` rather than a hardcoded name, so it too works unchanged against the
restore-drill database.

The same two files are what the rehearsal stack runs: `docker-compose.prod.yml`'s `postgres-provision`
job mounts them read-only and applies them before anything else starts. So this step is rehearsed
rather than performed for the first time against production.

On `postgres-keycloak`, create the `keycloak` database and role with the generated `KC_DB_PASSWORD`.
Nothing else is needed there — Keycloak manages its own schema.

**`PGBOUNCER_AUTH_MODE=userlist`, and this is measured rather than preferred.** `auth_query` was the
mode this deployment wanted — only the lookup role's own password reaches disk, and rotating an
application password is an `ALTER ROLE` plus a variable change — and the rehearsal (R-7, 2026-08-27,
pgbouncer 1.24 against Postgres 16) established that it cannot be used here. The lookup returns a
stored SCRAM **verifier**. That is enough to check a connecting client's proof, and it is not a
password; both `[databases]` entries pin a forced user (`user=app_user`, `user=admin_user`), so
pgBouncer opens the server connection under its own identity rather than passing the client's
exchange through, and in `auth_query` mode it has nothing to present. What that looks like in the
pooler's log is worth recognising, because the client leg succeeds and only the second leg fails:

```
LOG  C-…: movielens_app/app_user@… login attempt: db=movielens_app user=app_user
LOG  S-…: movielens_app/app_user@…:5432 new connection to server
WARNING server login failed: FATAL password authentication failed for user "app_user"
WARNING C-…: pooler error: password authentication failed for user "app_user"
```

Every connection through both aliases is refused, so the release job dies on its first query and the
API never boots. `userlist` renders `/etc/pgbouncer/userlist.txt` at 0600 at container start from
`APP_USER_DB_PASSWORD`, `ADMIN_USER_DB_PASSWORD` and `PGBOUNCER_ADMIN_PASSWORD` — never into an image
layer, never into the repository — and authenticates in both directions. `pgbouncer_auth` and its
lookup function stay provisioned regardless: switching back is a variable change with no rebuild, and
`auth_query` becomes correct again the moment the forced users go.

Note that `pgbouncer`'s healthcheck (`pg_isready`) does **not** authenticate, so a pooler in a mode
that refuses every login still reports healthy. The failure surfaces one layer later, at the first
service that actually connects, and it is loud when it does.

`pgbouncer_admin` is rendered into the userlist in **both** modes on purpose — it has no Postgres
role behind it, so an `auth_query` lookup would find nothing and the API's boot check, and therefore
every deploy, would fail.

## 5. Keycloak and provisioning

**The first deploy has one order and it is not a preference: `keycloak` → `keycloak-provision` →
`release` → `api`.** `/readyz` — the path Railway's deploy probe polls on the API — checks two
things, `SELECT 1 FROM public.tenants` through the pooler and the cached JWKS for the realm named by
`MODEL_TENANT_ID`. So the API cannot answer 200 until the serving realm exists *and* the migrations
have run, and a deploy that starts the API before either is a deploy that never promotes and times
out at its healthcheck with nothing in its own logs to explain why. The sidecars slot in between
`release` and `api` (§6 has the full per-release order); on a first deploy the four steps above are
the ones that fail closed against each other.

Deploy `keycloak` first. Its healthcheck path is
`/realms/master/.well-known/openid-configuration`, deliberately not the `demo` realm: on a first
deploy the `demo` realm does not exist yet, and probing a realm that a later job creates would
deadlock the first deploy forever. Keycloak 25's own `/health/ready` lives on management port 9000,
which Railway's probe cannot reach.

Then run `keycloak-provision` (start command `/opt/keycloak/provision.sh`). It is idempotent and
takes **1.5–3 minutes**, not seconds — every `kcadm` call starts a JVM and a first run makes 75–85 of
them. Do not give it a short timeout. Its variables: `APP_ORIGIN`, `KEYCLOAK_URL` (the private
Keycloak URL), `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`, `KC_API_CLIENT_SECRET`,
`KC_VERIFY_CLIENT_SECRET`, `WALKTHROUGH_PASSWORD`, `VERIFY_PASSWORD`, `ISOLATION_PASSWORD`,
`KC_HUMAN_ADMIN_USERNAME`, `KC_HUMAN_ADMIN_PASSWORD`. It prints `PROVISION-OK` on success.

What it creates: realms `demo` and `default` (matching the two rows migration `0002` seeds into
`public.tenants` — **provisioning a tenant means creating both a realm and a DB row, and either alone
fails closed**); clients `movielens-api`, `movielens-web` and `movielens-verify` with the
`oidc-audience-mapper` on each; and exactly three accounts — `walkthrough` (realm `demo`,
`demo-impersonator`), `verify` (realm `demo`, `demo-impersonator`) and `isolation` (realm `default`,
**deliberately without** the role, so the cross-tenant canary has a subject that must be denied
everywhere).

Run it a second time and confirm it prints no `created` / `granted` / `password set` lines. An
account that already exists keeps its password unless `KC_RESET_PASSWORDS=true`.

Then, per D4: create the named human admin, delete `KEYCLOAK_ADMIN` and `KEYCLOAK_ADMIN_PASSWORD`
from the service variables, and disable the bootstrap account.

**Keycloak 25.0.6 prints `WARN … The following used options or option values are DEPRECATED …
- hostname - hostname-strict` on every boot.** It is informational — those options change behaviour
in Keycloak 26 — and it is not a misconfiguration. Do not "fix" it during an incident.

Realm changes made in the console or by `kcadm` do not travel back to git on their own. Export them
into `infra/keycloak/realms/prod/` or the templates drift permanently; a realm export carries
generated ids and timestamps, so the diff has to be read semantically (realm flags, client ids and
their grant flags, redirect URIs, mapper presence and its audience) rather than textually.

## 6. Release

Deploys run from the `.github/workflows/deploy-production.yml` workflow — `workflow_dispatch`, or a
push to `main`. There is no tag trigger. The gate job runs without an environment; the job that
actually touches Railway declares `environment: production`, so the required reviewer is asked to
approve a commit that has already passed CI rather than one that has not been checked yet. In order:

1. Refuse to proceed unless **every job `ci.yml` declares** is `success` on this exact commit:
   `browser-auth-e2e`, `changed-paths`, `demo-compose`, `feature-parity`, `frontend`, `lint`,
   `synthetic-load-smoke`, `tenant-isolation` and `test`. `serving-artifacts` and `realm-drift` are
   the two conditional jobs — `ci.yml` gates them on a path filter, so `success` **or** `skipped`
   passes and a skip is reported as a warning. (A push to `main` has no base commit to diff against
   and deliberately turns every gate on, so on the runs this workflow normally reads they are
   `success` anyway.) `api-contract-check` and `web-api-types-check` are **steps, not jobs** —
   `python -m scripts.generate_openapi --check` inside `lint` and `npm run api:types:check` inside
   `frontend` — so requiring those two jobs is what requiring those two checks means. There is no
   override.
2. **Record the current deployment id of every service** and publish it as a workflow artifact and in
   the job summary. This is the rollback target, captured before anything changes rather than
   reconstructed during an incident.
3. Assert `no_public_sidecar` — no domain and no TCP proxy on `api`, `model-server`,
   `feature-server`, `pgbouncer`, `redis` or either Postgres.
4. `release` → grep the deployment log for `RELEASE-BOOTSTRAP-OK <revision>`.
5. `model-server` → its pre-deploy runs `python -m src.release.bootstrap materialize
   --wait-for-schema 300`, then every worker warms inside `lifespan` before `/healthz` returns 200.
   That fence reads the expected head out of the image: `infra/features/Dockerfile` copies
   `alembic.ini` and `alembic/`, and `infra/features/requirements.txt` pins `alembic`, so the
   features image knows its own revision set and can tell "the database is behind" from "the
   database is *ahead* of this image, because a rollback is in progress". The only override is the
   `--expected-revision` flag on `bootstrap materialize`, for a container that cannot read the
   graph; there is no environment literal anybody has to remember to bump.
6. `feature-server`, then `api`, then `web`, waiting for each.
7. `verify` → the full post-deploy matrix → grep for `VERIFY-OK`.

**Additive migrations only.** Alembic downgrades are untested here and migration `0010`'s backfill
and `0012`'s audit columns are not safely reversible against live data. The paired rule that makes
this safe: pre-deploy commands run on **every** deploy including a rollback, so the schema step
compares the database's revision against the revisions its own image knows about and **exits 0 when
the database is ahead**. Without that, rolling back to an older image raises "Can't locate revision"
and turns one incident into two. A release that must ship a destructive migration has a database
restore as its rollback path, not a redeploy, and needs a rehearsed restore before it merges.

**Personas reset to seed state on every release.** The seeder is delete-then-insert over nine known
user ids in tenant `demo`. That is what makes the smoke assertions deterministic, and it also wipes
any movie state a visitor created on the four personas. It is user-visible; say so on the sign-in
door rather than only here.

## 7. Verify

`verify` runs `python -m src.release.verify --all` from inside the private network and prints
`VERIFY-OK`. What it covers:

- Issuer equality on the live `demo` discovery document.
- Cold-start and learned serving: four persona slugs present; Action Fan has history **and**
  recommendations with policy exactly `item-item-cosine+lightgbm`; Cold Start has no history but does
  get recommendations with policy exactly `popularity`; zero overlap between seen and recommended.
- `serving_policy.learned === true` for a warm persona — the check that catches a silent popularity
  fallback returning HTTP 200.
- The auth boundary: 401 unauthenticated across nine routes, plus request-id echo and persistence,
  dependency visibility, degraded metadata, bounded pages and cursor rejection
  (`python -m synthetic.load.reliability`, invoked whole — it has no `--checks` argument and every
  check is read-only), plus rate limiting: the `X-RateLimit-*` headers on an admitted request and a
  429 carrying `Retry-After` once the bucket is drained, with no third behaviour. Because the bucket
  is per worker, the probe has to spend roughly `workers × burst` requests to drain one, which is
  why it sends more than the burst suggests.
- Tenant isolation: the `default`-realm `isolation` account must be 403 on every persona-guarded
  route, and a `demo` token must be refused against a `default` user id. **An unreachable target is a
  hard failure, never a skip.**
- One write round trip: an idempotent `PUT` with `expected_revision`, an authenticated read asserting
  the committed revision, then a revert — **on Eclectic Viewer (900000103) only. Never Cold Start
  (900000104)**, whose zero-signal state the browser suite depends on.
- Realm invariants: `registrationAllowed=false`, `bruteForceProtected=true`, the audience mapper on
  both clients, exactly the expected redirect URIs.
- The audit SLI: one JSON line from the last 24 h of `recommendation_audits` — p50/p95/p99, per-stage
  split, learned-vs-fallback ratio, fallback reasons with counts, error count, row count.
- Artifact provenance: the sidecar's `/healthz` reporting `candidate_version` / `ranker_version` /
  `feature_version` from the SHA-256-pinned manifest it verified on load.

The latency canary is separate and manual: `loadcheck` at `LOAD_PROFILE=prod-canary`, 5 arrivals/s
for 60 s. Set `RESULTS_DIR=/tmp/results` and leave it there — k6 does not create the parent directory
for the summary file, and it logs the failure while still exiting 0, so a canary that lost its own
evidence looks exactly like a clean run. **Correctness thresholds and the warm-traffic learned assertion are enforced; p99 is
recorded with no verdict.** `synthetic/load/thresholds.js` in CI remains the SLO's only authority —
it is four lines, it has never moved, and it is never edited. A canary regression opens an
investigation and a CI re-run; it never re-baselines anything.

**The canary and the rate limiter share one subject, so watch that interaction on the first run.**
`loadcheck` authenticates as the single `verify` account and the ADR 0014 bucket is keyed on
`(tenant, sub)`, so the whole 60-second run spends one subject's allowance: 5 arrivals/s is 300
requests a minute against an effective `4 × 120 = 480`. That fits with 1.6× headroom, and it is the
thinnest margin in ADR 0014's table — the `setup()` warm-up is burstier than the steady phase. If the
canary ever reports 429s, **raise `RATE_LIMIT_REQUESTS_PER_MINUTE` on `api`. Do not weaken the canary
and do not exempt its client**; the headroom was simply not real. The CI gate is unaffected either
way, because `api-load` runs with `ENVIRONMENT: dev`, where the limiter is off by default so it
cannot measure itself.

One maintenance note that will surprise whoever adds the next endpoint: a new persona-guarded route
must be added to `PERSONA_ROUTES` in `synthetic/tenant_isolation/remote_canary.py`, or a unit test
fails by design. A guarded route absent from the canary is a route no deployment proves is isolated.
The canary lives under `synthetic/` rather than `tests/` because it has to ship inside the deployed
image: `infra/api/Dockerfile` copies `src/`, `alembic/` and `synthetic/`, and never `tests/`, so a
harness the `verify` job runs cannot live in the test tree. The CI leakage suite that runs against
the Compose stack stays in `tests/tenant_isolation/`.

## 8. Incident quick reference

| Symptom | First response |
|---|---|
| **Recommendations look uniformly wrong** — same titles for everyone, or nothing personal | `python -m src.release.bootstrap materialize`. This is the first response, before reading any code. An empty or stale online store is by far the likeliest cause |
| `model-server` crash-looping with a missing Feast event timestamp or an all-zero feature frame | **Materialize, do not roll back.** The sidecar refuses to boot against an unmaterialized Redis on purpose — that is the check working. Run materialize, then redeploy the sidecar |
| Every request 401s, on every route, immediately | The three issuer settings in §3 disagree. Compare `KC_HOSTNAME`, `KEYCLOAK_PUBLIC_BASE_URL` and `KEYCLOAK_PUBLIC_ISSUER` character by character, including scheme and trailing slash |
| A container crash-loops on `SettingsError` at startup | `KEYCLOAK_AUTHORIZED_PARTIES` was entered as CSV. It is JSON |
| A container refuses to start with "the default model-server token is only permitted in development" | `MODEL_SERVER_AUTH_TOKEN` is missing on a service that constructs `Settings()` — that includes `model-server`, `release` and `verify`, none of which use the token to talk to anything |
| The API boots but recommendations report `learned: false` with `model-server-unavailable` | The two `MODEL_SERVER_AUTH_TOKEN` values disagree, or the sidecar is not warm. A token mismatch degrades every recommendation to popularity **at HTTP 200** rather than erroring — rotate that value in a window and let `verify` confirm recovery |
| The API refuses to boot on a pgBouncer check | Either the pooler is down (the API cannot boot without it, by design), or `pool_mode` is not `transaction`, or `PGBOUNCER_ADMIN_PASSWORD` disagrees between the API and the pooler |
| A deploy never promotes and the healthcheck times out | `/readyz` is returning non-200. It fails on database or JWKS only, never on a sidecar — so this means the pooler path or Keycloak, not the model server |
| `verify` fails but the site looks fine | Read the failing row before anything else. A silent popularity fallback, a broken isolation guard and a stale audit table all look fine from a browser |
| Requests start coming back `429` with `Retry-After` | The ADR 0014 token bucket, working. It is keyed on `(tenant, sub)` from the verified token, so it is one *account* that is over, not the deployment. If the workload is legitimate, raise `RATE_LIMIT_REQUESTS_PER_MINUTE` on `api`; never reach for `RATE_LIMIT_ENABLED=false` on a public service, and never exempt a client |

**Rollback.** The deploy workflow rolls back automatically on any failure at or after its release
step, in reverse dependency order — `web` → `api` → `feature-server` → `model-server` — and then
re-runs `verify`. `keycloak` and `release` are deliberately excluded: reverting the IdP is an
identity migration, and re-running an older bootstrap against a newer database is how one incident
becomes two.

To roll back by hand, **there is no CLI command for it and no `make` target** (D6). The pinned
Railway CLI cannot reach a specific previous deployment: `railway redeploy` accepts no deployment id
and redeploys the latest, and `railway down` *deletes* the latest deployment rather than reverting to
a previous one — do not reach for it during an incident. Rollback is one GraphQL mutation per
service, and the ids it needs are in the `rollback-target` artifact the deploy workflow published
before it changed anything (also printed into the job summary):

```bash
curl -sS https://backboard.railway.com/graphql/v2 \
  -H "Authorization: Bearer $RAILWAY_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"mutation deploymentRollback($id: String!) { deploymentRollback(id: $id) { id status } }",
       "variables":{"id":"<deployment-id-from-the-rollback-target-artifact>"}}'
```

The mutation is documented as working only on a deployment whose `canRollback` is true. Roll the four
services back in the order above, then re-run `verify`. Rolling back the *model* is rolling back the
sidecar image: the bundle is baked, so there is no separate artifact to revert.

## 9. Backups and the restore drill

`backup` runs nightly at `0 4 * * *`: `pg_dump -Fc --no-owner` of both databases, `age`-encrypted
client-side, copied off-provider with `rclone` to
`<remote>:/recsys/<db>/{daily,weekly,monthly}/<ts>.dump.age`. Retention is 7 daily / 4 weekly /
6 monthly; the weekly and monthly copies are server-side copies of the daily object, so a longer
window costs one API call rather than a second upload. The final line is `BACKUP-OK`.

Two details worth knowing before you need them:

- The dump is `--no-owner` but **not** `--no-privileges`. Stripping privileges would strip the
  `app_user` / `admin_user` grants that migrations `0002`, `0003` and `0006` install, and the
  restored database would be one the API cannot read at all — permission denied before RLS is even
  reached.
- The dump role must hold BYPASSRLS or SUPERUSER. Tenant-scoped tables are `FORCE ROW LEVEL
  SECURITY` and `pg_dump` sets no `app.tenant_id`, so a dump taken by an ordinary role exits 0 and
  contains no rows. The script asserts this before dumping.

**Redis is deliberately not backed up.** It holds only derived state, the repair path is
`bootstrap materialize`, and an empty Redis now fails the model-server boot rather than silently
serving zeros.

`infra/backup/restore-drill.sh` is a script with an exit code, not a document. It pulls the latest
dump, decrypts, restores into a scratch database, asserts the alembic revision matches the image's
head, optionally boots the API image against it and runs the smoke, and writes a JSON record (dump
object, bytes, TOC entries, alembic head, per-stage and total seconds, outcome) to stdout and to
`--report PATH` — **including on failure**, which is the run worth having evidence of. `--dry-run`
rehearses it safely.

Five things the drill will tell you the hard way if you skip them:

- **Restore as `migrator`, not as the superuser.** The dump is `--no-owner`, so every restored
  object is owned by whatever role ran `pg_restore`. On a normal deployment `migrator` owns every
  base table — `create_tables()` runs as `migrator` before Alembic does, which is what makes
  `ALTER TABLE … FORCE ROW LEVEL SECURITY` and the `0010` backfill work — and a restore performed as
  the superuser silently moves all of it. The restored database serves fine: the `app_user` and
  `admin_user` grants travel in the dump, so `prod-verify` is green and nothing looks wrong. The
  *next deploy* is what breaks, because the release job's `alembic upgrade head` runs as `migrator`
  and `migrator` can no longer read `public.alembic_version`. `migrator` holds BYPASSRLS, so it also
  satisfies the row-count requirement below. Restoring as the superuser and repairing afterwards
  with `REASSIGN OWNED BY <restoring role> TO migrator;` works too; doing neither leaves a database
  that passes every check you would think to run.
- The restore target needs the §4 provisioning roles **before** `pg_restore`, because the dump
  carries their grants.
- The drill refuses a non-empty target outright, with no override flag.
- The drill's own connection must hold BYPASSRLS or SUPERUSER, or its row counts read zero and an
  empty restore looks successful.
- Booting the API against a restored database needs a pgBouncer alias pointing at it. Without one,
  run with `--skip-api-smoke`, which prints `RESTORE-DRILL-PARTIAL` instead of `RESTORE-DRILL-OK` —
  record the partial as a partial.

**The seed step is deliberately skipped.** A restore that needs the seeder to look right has proven
nothing. Record the wall-clock time and everything done by hand; anything done by hand becomes a
ticket.

If Railway's Postgres template ever moves to a new major version, `infra/backup/Dockerfile`'s
`FROM postgres:16` must move with it or every dump fails `pg_dump`'s own version check.

## 10. What this deployment does not do

Stated plainly so nobody reads an aspiration as a description of what is running:

- **Audit coverage is recommendations-only.** `src/serving/audit.py` matches
  `^/users/(\d+)/recommendations/?$`, so every mutation passes through unaudited. CLAUDE.md's "every
  authenticated request emits a row" describes the intent, not the deployed system; generic request
  audits are an open platform-track item.
- **Feature parity has no production form.** `tests/feature_parity/` stays a CI gate; pointing it at
  production would mean giving a runner production database and Redis credentials and letting it
  write demo data. The deployed proxy — comparing `GET /users/{id}/features` against the
  `feature_values` in the audit row for the same request id — is worth building and is not built.
- **The pinned latency gate has no production form.** Its wrapper needs Compose recreation,
  `docker stats`, cgroup reads and a `/proc/stat` probe. CI keeps the verdict; the canary is a weaker
  instrument and says so.
- **There is no continuous platform healthcheck.** Railway polls until 200, swaps the deployment in,
  and then stops checking. The nightly `verify` cron and the scheduled canary workflow are the only
  recurring health signals this deployment has.
- **There is no `/me` ownership mapping.** Any signed-in `demo`-realm account can read and mutate all
  four personas. That is why registration is disabled and only three accounts exist.
- **`feature_store.*` is never pruned.** One generation per release, and `user_item_features` is a
  users × movies cross join. Small at 120 titles, unbounded in principle — D8 is the decision that
  fixes it.
