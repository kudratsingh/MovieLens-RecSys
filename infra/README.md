# Infrastructure

Images, platform configuration, and the operational scripts around them. Every
production image except the web app is built from a directory here, always with
the **repository root as the build context** — which is why the Dockerfiles'
`COPY` paths are repo-root-relative. `infra/mlflow/` is the one exception; its
context is itself.

Nothing here is invoked directly. The Makefile's `demo-*`, `staging-*` and
`prod-*` targets own the Compose invocation, the project name and the env-file
path, and the two deploy workflows drive the production ones over SSH.

Three environments read these directories, and two of them read the same files:
**dev** is `docker-compose.yml` + `docker-compose.demo.yml` (`make up-dev`),
**staging** is `docker-compose.prod.yml` + `docker-compose.staging.yml`
(`make up-staging`), and **production** is `docker-compose.prod.yml` alone. So
wherever a row below says "prod", read it as "staging too": staging changes the
Compose project and `ENVIRONMENT`, and nothing about which image, config file or
script a service uses.

## Directories

| Directory | What it is | Consumed by |
|---|---|---|
| [`api/`](#api) | The slim FastAPI runtime image — `src/`, `alembic/` and `synthetic/`, but no Feast, pandas or LightGBM | demo `demo-setup`, `api`, `api-load`; prod `api`, `release`, `verify`, `canary`; CI builds it as `movielens-api-ci` |
| [`features/`](#features) | The Feast feature-server and LightGBM model-sidecar image | demo `feature-setup`, `feature-server`, `model-server`; prod `model-server`, `feature-server`, `materialize`; `make serving-artifacts` |
| [`model-bundle/`](#model-bundle) | The committed serving bundle: item-item index, LightGBM booster, SHA-256 manifest | Build input to `features/` only. Written by `make serving-artifacts`, checked by `serving-artifacts-check` |
| [`pgbouncer/`](#pgbouncer) | Transaction-pool-mode pooler — bind-mounted dev config, env-rendered production image | Dev stack mounts `pgbouncer.ini`; prod builds the image |
| `postgres/` | The `pgbouncer_auth.user_lookup` SECURITY DEFINER function that pgBouncer's `auth_query` mode calls, so the pooler never holds a superuser credential. Deliberately **not** in `postgres-init/`, where it would run on a fresh dev volume before its role exists | Prod `postgres-provision` only, during `make prod-stores` |
| `postgres-init/` | Dev-only `CREATE DATABASE mlflow` — the image's `POSTGRES_DB` creates only `movielens`, and MLflow would crash on startup without it | Dev stack's `postgres`, once, on a fresh volume |
| [`keycloak/`](#keycloak) | Realm-per-tenant configuration: dev import seeds, production image and templates, provisioning job | Dev stack imports `realms/`; prod builds `keycloak` and `keycloak-provision`; CI's `realm-drift` job |
| [`backup/`](#backup) | `pg_dump` + `age` + `rclone` image, nightly backup and the restore drill | Prod `backup` service (`jobs` profile); `make prod-backup`; the `movielens-backup` systemd timer |
| `k6/` | The `loadcheck` image — `grafana/k6` with `synthetic/load/` baked in, because the production host never builds and never mounts a source tree. The dev stack does not use it; there, k6 is the stock image with the scripts bind-mounted | Prod `loadcheck`; `make prod-load` |
| `edge/` | The Caddy edge terminating https for the deployment's two public hostnames, in both TLS modes: Let's Encrypt on the box (`EDGE_TLS=acme`) and Caddy's internal CA on a laptop. `admin off` — no authenticated admin API, even on the private network | Prod `edge`; `make prod-stores`, `prod-edge-ca` |
| [`host/`](#host) | Host bootstrap and the five systemd units | **Nothing automated.** Run by hand on the box per the deployment runbook |
| [`deploy/`](#deploy) | The release script, the production **and staging** environment contracts, the role SQL, the rollback rehearsal | `make prod-deploy` / `prod-rollback` / `prod-rollback-rehearsal`; prod and staging `postgres-provision`; `make up-staging` reads `staging.env.example`; both deploy workflows |
| `mlflow/` | The official MLflow image plus the `psycopg2` driver it lacks, so a Postgres `--backend-store-uri` does not crash on import. The one build context here that is not the repository root | Dev stack's `mlflow` |
| [`ci/`](#ci) | One file: the pinned k6 version | Makefile and CI both read it |
| `prometheus.yml` | Dev-stack scrape config, pointed at an API on the host | Dev stack's `prometheus`. Not used in production |

Which stack uses what, at a glance: **dev only** — `postgres-init/`, `mlflow/`,
`prometheus.yml`. **Production only** — `postgres/`, `edge/`, `backup/`, `k6/`,
`deploy/`, `host/`. **Shared** — `api/`, `features/`, `pgbouncer/`, `keycloak/`,
`model-bundle/`, `ci/`.

## Notes worth having before you read the files

### `api/`

`entrypoint.sh` dispatches three named modes plus a passthrough:

- `serve` (the default) — constructs `Settings` in a throwaway process *before*
  uvicorn forks, so a misconfiguration fails fast instead of respawning forever
  behind a healthy-looking container. Then `uvicorn` with `${API_WORKERS:-4}`.
- `bootstrap` → `src.release.bootstrap` (preflight, schema, seed, materialize).
- `verify` → `src.release.verify` (the post-deploy matrix).
- Anything else is `exec`'d as given, which several Compose services rely on.

`requirements.txt` is exact-pinned rather than floored, so rebuilding a rollback
installs what it replaced.

### `features/`

Two things are baked in at build time rather than resolved at runtime, and both
are deliberate. The Feast registry is applied during the build
(`feast apply` + a semantic registry check), with the store connection supplied
as `ARG`s defaulting to `build` — so a runtime that forgets them refuses rather
than silently connecting somewhere. And `infra/model-bundle/` is copied in,
which is what makes **rolling back the model the same act as rolling back the
image**; the SHA-256s are re-verified on boot.

It also bakes `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and
`VECLIB_MAXIMUM_THREADS` to `1`. That is not tuning: four uvicorn workers each
sizing a native thread team to the whole host was the difference between a p99
of 904 ms and 49 ms on the same hardware (ADR 0010). It lives in the image so no
deployment depends on anyone remembering it.

### `model-bundle/`

`candidate-index.json` (item-item cosine neighbours), `ranker.txt` (the LightGBM
booster in its text format), and `manifest.json` — which binds them by SHA-256 to
a tenant, a feature version, and the ordered 8-column feature contract.
`ARTIFACT_AS_OF` is a literal in the Makefile so the manifest stays byte-stable,
which is what non-negotiable #5's reproducibility check compares against.

### `pgbouncer/`

Dev uses `auth_type = trust` with a committed `userlist.txt` of five dev-only
pairs — labelled as such, and the reason nothing secret is in this repository.
Production renders its config and a 0600 auth file from the environment at
container start.

The default production auth mode is **`userlist`**, and that is a measured
result rather than a preference. With forced-user aliases pgBouncer opens the
server connection itself and, in `auth_query` mode, has nothing to present —
the rehearsal recorded `password authentication failed for user "app_user"`.
`auth_query` is kept because it is one variable away and needs no rebuild.

### `keycloak/`

Two template families that are easy to confuse. `realms/*.json` are **dev import
seeds** — full realm documents including dev-only users with plaintext
credentials. `realms/prod/` is inert: realm settings, one file per client, and
one audience mapper, with no users and no secrets. The split follows the admin
API's own shape, since a realm `PUT` ignores nested clients and a client `PUT`
ignores nested mappers.

The production image deliberately does **not** use `--import-realm`, because
that never overwrites an existing realm. `provision.sh` is a one-shot job that
reads before it writes, so a second run changes nothing and a template edit is
picked up on the next one. It asserts its invariants rather than assuming them —
registration disabled, brute-force protection on, and the audience mapper
present on all three clients, without which tokens carry no `aud=movielens-api`
and the API rejects every request. It prints `PROVISION-OK` on success.

### `backup/`

`backup.sh` asserts the dumping role holds `BYPASSRLS` **before writing a
byte** — a dump taken without it exits 0 and contains no rows, which is the
worst possible backup. It keeps privileges while dropping ownership, because the
`app_user` / `admin_user` grants *are* ADR 0008's isolation. Redis is
deliberately not backed up; the repair path is `bootstrap materialize`.

`restore-drill.sh` deliberately **skips the seed step**, so what it proves is
that the dump restores rather than that the seeder can rebuild the same state.

### `host/`

The only written record of how the production host is configured, and the only
directory here that nothing automated touches — it is run by hand once, per
`docs/deployment-runbook.md` §3. `bootstrap.sh` is idempotent: deploy user, sshd
hardened to keys only, `ufw` on 22/80/443, unattended security upgrades, swap
sized for the deploy spike, Docker CE from Docker's own repository, 20 MB × 5 log
rotation, and the five systemd units.

Two details it records that are easy to get wrong: the sshd drop-in is numbered
`01-`, not `99-`, because Ubuntu's `Include` sits at the top of the file and sshd
takes the *first* value; and `ufw` does not filter Docker-published ports, which
is why the production Compose file publishes nothing but 80 and 443.

The units are `movielens.service` (brings the stack up at boot; `ExecStop` is
`stop`, never `down`), the nightly backup service and timer (04:00 UTC), and the
weekly prune service and timer — **images only, never volumes**.

### `deploy/`

Read [`deploy/README.md`](deploy/README.md) rather than duplicating it here: it
documents `deploy.sh` and its sentinels, the `production.env.example` variable
contract and its `staging.env.example` sibling, `provision-roles.sql`, the
rollback rehearsal, the two rehearsal-only environment switches, **and**
`infra/host/` in detail.

The one thing worth repeating: `deploy.sh` rolls back to `.release/previous` and
re-verifies **on its own** when verification fails. `DEPLOY-OK` and `ROLLBACK-OK`
are its sentinels, and the deploy workflow greps for them.

### `ci/`

`k6-version` pins the k6 image tag so local, CI and canary measurements are
comparable (ADR 0010). The Makefile and CI both read the file;
`infra/k6/Dockerfile` duplicates the value as an `ARG`, because a Dockerfile
cannot read a file for its `FROM`, and a unit test asserts the two agree.

## Images published to GHCR

CI's `publish-images` job runs only after every other job is green, and pushes
`linux/amd64` images tagged with the commit SHA and `main`: `api`, `features`,
`web` (from `web/`), `pgbouncer`, `keycloak`, `backup`, `k6`. Every service in
`docker-compose.prod.yml` carrying a `build:` block is in that list, and nothing
else is. GHCR is the image source of truth for the deployment, so no retention
policy may expire SHA tags — a rollback resolves one.
