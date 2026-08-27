# Railway config-as-code

One JSON file per Railway service, twelve in all. Each file carries the build and
deploy settings for exactly one service; nothing here is shared and nothing here is
merged.

Railway reads a service's config file from a path set **per service**, and that path
is **repo-root-relative regardless of the service's Root Directory** — which is the
only reason a single repository can hold twelve of these instead of one
`railway.json` at the root. It is also the reason `web.json` lives here rather than
under `web/` even though `web` is the one service with a Root Directory set.

Which of these files and the dashboard wins when both set the same setting is not
something this repository has confirmed against the platform's current behaviour, so
do not let one silently win: set each value in exactly one place. Everything in the
table below lives here; everything in the section after it lives only in the
dashboard.

## The twelve services

| File | Source | Root Directory | Start command | Healthcheck | Restart | Cron |
|---|---|---|---|---|---|---|
| `api.json` | `infra/api/Dockerfile` | *(unset)* | `entrypoint.sh serve --no-access-log` (`API_WORKERS` workers) | `/readyz` | ON_FAILURE ×10 | – |
| `model-server.json` | `infra/features/Dockerfile` | *(unset)* | uvicorn, 4 workers, `--no-access-log` | `/healthz` | ON_FAILURE ×10 | – |
| `feature-server.json` | `infra/features/Dockerfile` | *(unset)* | *(image `CMD`)* | `/health` | ON_FAILURE ×10 | – |
| `web.json` | `web/Dockerfile` | **`web`** | *(image `CMD`)* | `/` | ON_FAILURE ×10 | – |
| `keycloak.json` | `infra/keycloak/Dockerfile` | *(unset)* | *(image `ENTRYPOINT` + `CMD`)* | `/realms/master/.well-known/openid-configuration` | ALWAYS | – |
| `pgbouncer.json` | `infra/pgbouncer/Dockerfile` | *(unset)* | *(image `ENTRYPOINT` + `CMD`)* | *(none — not HTTP)* | ALWAYS | – |
| `redis.json` | public image `redis:7` | *(n/a)* | `redis-server …` | *(none — not HTTP)* | ALWAYS | – |
| `release.json` | `infra/api/Dockerfile` | *(unset)* | `entrypoint.sh bootstrap all` | *(none)* | NEVER | – |
| `keycloak-provision.json` | `infra/keycloak/Dockerfile` | *(unset)* | `/opt/keycloak/provision.sh` | *(none)* | NEVER | – |
| `verify.json` | `infra/api/Dockerfile` | *(unset)* | `entrypoint.sh verify --all` | *(none)* | NEVER | `17 6 * * *` |
| `backup.json` | `infra/backup/Dockerfile` | *(unset)* | `/usr/local/bin/backup.sh` | *(none)* | NEVER | `0 4 * * *` |
| `loadcheck.json` | `infra/k6/Dockerfile` | *(unset)* | `k6 run /scripts/recommendations.js` | *(none)* | NEVER | – |

`postgres-app` and `postgres-keycloak` come from Railway's Postgres template and have
no file here: their image, start command and healthcheck belong to the template, and
everything this deployment decides about them (volume size, backups, the absence of a
public proxy) is dashboard-only anyway.

`web` is the one service with a **Root Directory**, because `web/Dockerfile:3` is
`COPY package.json package-lock.json ./` against a repository root that has no
`package.json`. Its `dockerfilePath` is therefore `"Dockerfile"` — relative to the
Root Directory — while this config file's own path stays repo-root-relative.

Every other service must have **no Root Directory**: `infra/api/Dockerfile` and
`infra/features/Dockerfile` both `COPY src ./src`, `infra/pgbouncer/Dockerfile` and
`infra/k6/Dockerfile` copy from `infra/` and `synthetic/`, and all of that needs the
whole repository as the build context.

## What is NOT expressible here, and must be set in the dashboard

This is the list to work through when creating the project, and the list to check
when something behaves differently from what these files say.

1. **Which config file a service reads.** The path (`infra/railway/<service>.json`) is
   a per-service dashboard setting. A service pointed at no file, or at the wrong
   file, deploys with dashboard defaults and gives no warning. This is the single
   setting that makes every other setting in this directory take effect.
2. **Root Directory.** `web` only, set to `web`. Everything else unset. See above.
3. **The service source for `redis`** — the public image `redis:7`. A config file can
   describe how an image is *run*, not where it comes from; `redis.json` therefore
   carries a `deploy` block and no `build` block. Same for the two Postgres services,
   which have no file at all.
4. **Environment variables and secrets.** The whole environment contract in the
   deployment runbook — every `${{shared.*}}` and
   `${{Service.RAILWAY_PRIVATE_DOMAIN}}` reference, and all sixteen generated
   credentials. Nothing secret is expressible as code in a public repository, and
   nothing non-secret is worth splitting across two homes. Three variables are
   load-bearing enough to call out here:
   - `PORT` on every service — Railway polls `healthcheckPath` on the port it thinks
     the app listens on, so `PORT` and the `--port` in the start command must agree.
   - `KEYCLOAK_AUTHORIZED_PARTIES` is **JSON**, not CSV. The CSV form raises
     `SettingsError` and crash-loops the container.
   - `REDIS_PASSWORD`, which `redis.json`'s start command interpolates.
5. **Volumes** — mount path and size, one per service, not shareable: `postgres-app`
   5 GB, `postgres-keycloak` 1 GB, `redis` 1 GB at `/data`. Neither sidecar gets one;
   the serving bundle and the Feast registry are baked into the image, which is what
   makes the one-volume-per-service rule a non-issue rather than a workaround.
   `backup` deliberately gets no volume — dumps stage in the ephemeral filesystem and
   are pushed off-provider.
6. **Volume backups.** The Backups tab (daily/weekly/monthly) has no CLI and no config
   form. It is a second line of defence behind `infra/backup/`, not a replacement for
   it: a provider-side snapshot is one account compromise away from the data it
   protects.
7. **Custom domains and TCP proxies.** Only `web` (`app.<domain>`) and `keycloak`
   (`auth.<domain>`) ever get one. `api`, `model-server`, `feature-server`,
   `pgbouncer`, `redis` and both Postgres services get neither, ever — the feature
   server has no authentication at all and the model sidecar's `/healthz` is
   unauthenticated. That is asserted by the deploy workflow's `no_public_sidecar`
   step rather than remembered.
8. **Auto-deploy (the connected branch).** Disabled on every service. The release
   workflow is the only deploy path, because the cross-service ordering
   (`keycloak` → `keycloak-provision` → `release` → `model-server` → `feature-server`
   → `api` → `web` → `verify`) is the whole reason the sequence works. The
   `watchPatterns` below are declared anyway so that re-enabling it later is a
   deliberate act rather than an accident.

   Two consequences of auto-deploy being off, both of which shape how a deploy and a rollback are
   actually performed. A *redeploy* reuses whatever commit a service already carries, which is not
   the commit CI just proved green — so `.github/workflows/deploy-production.yml` ships an explicit
   commit through `serviceInstanceDeployV2` rather than redeploying. And rollback is the GraphQL
   mutation `deploymentRollback(id)` against a deployment id recorded before the release started:
   the pinned CLI's `railway redeploy` takes no id and `railway down` deletes the latest deployment
   rather than reverting to a previous one. The runbook has the operator form of both.
9. **App sleeping.** Must stay off on `keycloak`, `api` and `model-server`. Keycloak's
   JVM cold start trips the web app's OIDC discovery, and a slept model-server pays
   its Feast warm-up again on the first request after every idle period — the exact
   cold-worker defect the warm-up in `lifespan` exists to remove.
10. **Replica count and region.** Single replica everywhere. Keycloak in particular:
    `start --optimized` brings up an Infinispan cache on a JGroups UDP stack, and a
    second replica on a mesh where multicast discovers nothing would split sessions
    silently rather than fail loudly.
11. **The one-time SQL.** Creating the `app_user` / `admin_user` / `migrator` /
    `pgbouncer_auth` roles and installing `infra/postgres/pgbouncer-auth.sql` happens
    once, by hand, against `postgres-app` before the first release. See the runbook.

## Start commands vs. image entrypoints

Every start command here was cross-checked against the image it runs in, because a
start command and an `ENTRYPOINT` interact and the interaction is not visible in
either file alone.

| Service | Image `ENTRYPOINT` | Image `CMD` | This file's `startCommand` |
|---|---|---|---|
| `api` | `/usr/local/bin/entrypoint.sh` (a `serve\|bootstrap\|verify` dispatcher) | `serve` | uvicorn |
| `model-server` | *(none)* | `feast … serve` | uvicorn on 6570 |
| `feature-server` | *(none)* | `feast -c src/features/feast_repo serve --host 0.0.0.0 --port 6566` | *(none — the CMD is already right)* |
| `web` | *(none)* | `node server.js` | *(none)* |
| `keycloak` | `/opt/keycloak/bin/kc.sh` | `start --optimized` | *(none)* |
| `pgbouncer` | `/usr/local/bin/pgbouncer-entrypoint.sh` | `/usr/bin/pgbouncer /etc/pgbouncer/pgbouncer.prod.ini` | *(none)* |
| `keycloak-provision` | `/opt/keycloak/bin/kc.sh` | `start --optimized` | `/opt/keycloak/provision.sh` |
| `backup` | *(cleared: `ENTRYPOINT []`)* | `backup.sh` | `/usr/local/bin/backup.sh` |
| `loadcheck` | *(cleared: `ENTRYPOINT []`)* | `k6 run /scripts/recommendations.js` | `k6 run /scripts/recommendations.js` |

**The open question, and it must be answered on the first deploy:** does Railway's
start command *replace* the container's whole command, or does it replace only `CMD`
and leave `ENTRYPOINT` in front of it? Every start command in this directory is
written for the replace-the-whole-command reading, which is what the deployment plan
assumes. **One service would break under the other reading**, and it has a one-line fix; the three
that run the API image are safe either way, for a reason worth not undoing:

- **`keycloak-provision`** — `/opt/keycloak/provision.sh` would become
  `kc.sh /opt/keycloak/provision.sh` and die on the first line. Fix: give
  `infra/keycloak/Dockerfile` a small `serve|provision` dispatcher as its
  `ENTRYPOINT` instead of `kc.sh`, and set the start command to `provision`. Verify
  this during the Keycloak provisioning rehearsal, before the first real run against
  a server that matters — not during it.
- **`api`, `release` and `verify` are already safe under both readings, and it is worth knowing
  why rather than rediscovering it.** All three name the dispatcher by absolute path
  (`/usr/local/bin/entrypoint.sh serve --no-access-log`, `… bootstrap all`, `… verify --all`), and
  `infra/api/entrypoint.sh`'s default branch execs an unrecognised first argument as given. So under
  the appending reading the command becomes `entrypoint.sh /usr/local/bin/entrypoint.sh bootstrap
  all`, the outer invocation does not recognise a path as a mode, falls through, and execs the inner
  one — which does recognise `bootstrap`. Do not "simplify" these three to bare `serve` /
  `bootstrap all` / `verify --all`: that form is correct only under the replace-everything reading
  and is exactly what breaks if the platform appends.

The two images this repository can settle on its own already are: `infra/backup/`
clears its base image's entrypoint, and `infra/k6/Dockerfile` clears
`grafana/k6`'s `ENTRYPOINT ["k6"]` for exactly this reason — otherwise
`k6 run /scripts/recommendations.js` expands to `k6 k6 run …` under the appending
reading. Both are correct either way.

Four more cross-checks worth carrying forward:

- **`api`'s worker count lives in `API_WORKERS` and nowhere else.** It used to be written twice —
  a `--workers 4` literal in `api.json` alongside the `API_WORKERS` variable the image's `serve`
  dispatcher reads — with only one of the two live depending on which start command the service
  ended up with. The literal is gone and the start command is `entrypoint.sh serve
  --no-access-log`, so there is one value to get right. It matters: the measured p99 baseline is a
  four-worker number, and `synthetic/load/recommendations.js` sizes its warm-up from `API_WORKERS`,
  so a warm-up sized for four workers against a service running one leaves workers cold. Do not
  reintroduce the literal. `model-server` is the opposite case and deliberately so — it runs uvicorn
  directly with `--workers 4`, and `MODEL_SERVER_WORKERS` exists only because the process cannot read
  its own flag back to report it on `/healthz`.
- **`model-server`'s pre-deploy runs from the features image, and that image now carries the
  migration graph for reading.** `bootstrap materialize --wait-for-schema` waits for the database to
  reach *the image's head revision*, and it has to be able to tell "the database is behind" from
  "the database is ahead of this image, because a rollback is in progress" — the second is a no-op,
  the first is a wait. Both are questions about which revisions this image knows, which is why
  `infra/features/Dockerfile` copies `alembic.ini` and `alembic/` and
  `infra/features/requirements.txt` pins `alembic` even though this image runs no migrations. The
  only override is the `--expected-revision` flag on `bootstrap materialize`, for a container that
  cannot read the graph; there is no environment literal to remember to bump. Recorded here because
  it is a property of the pairing of a config file with an image, and nothing else looks at both.
- **Pre-deploy commands mount no volumes and persist no filesystem changes**, and a
  non-zero exit aborts the deploy without retrying. `materialize` writes to Postgres
  and Redis, so it is a legitimate pre-deploy; publishing an artifact to a volume
  would not be.
- **`loadcheck` needs `RESULTS_DIR` set to a directory that already exists.** k6 does
  not create the parent directory for the file `handleSummary` writes; it logs
  `could not save some summary information` and still **exits 0** (measured against
  the pinned image), so a canary that lost its own evidence looks exactly like a
  clean run. `infra/k6/Dockerfile` creates `/tmp/results`, which is the value the
  `loadcheck` service sets — the two have to stay in agreement, and the image is the
  half that can be checked here.

## Health checks

Railway's health checks are **deploy-time only**. Railway polls the path until it
answers 200, swaps the new deployment in, and then stops checking — there is no
continuous liveness probe, which is why the scheduled `verify` job exists at all.

- `api` → `/readyz`, unauthenticated by deliberate exception. It probes Postgres
  through pgBouncer and the cached JWKS, and reports sidecar reachability without
  gating on it. Consequence: the API cannot become ready until Keycloak's serving
  realm exists **and** migrations have run, which is what pins the first-deploy
  ordering.
- `model-server` → `/healthz`, which answers **503 until the worker is warm**. Each
  worker constructs its Feast client, does one wide online read and one booster
  predict before it accepts traffic, so "healthy" here means "warm", and a green
  health check is a real statement about the first request. 300 s is generous
  against a measured cold cost in the tens of seconds; it is not padding for a
  misconfiguration, and a model-server that will not go healthy with
  `no Feast event timestamp` in its log needs a materialize, not a rollback.
- `feature-server` → `/health`, **not** `/healthz`. Feast's own server names it
  differently and the difference has no fallback.
- `keycloak` → `/realms/master/.well-known/openid-configuration`, deliberately the
  master realm. On the first deploy the serving realm does not exist yet — the
  provisioning job creates it — so probing it would deadlock the first deploy
  forever. Keycloak's own `/health/ready` is on management port 9000, which Railway's
  probe cannot reach. The serving realm's discovery document is asserted by `verify`
  instead.
- `pgbouncer` and `redis` have no health check: neither speaks HTTP, and Railway has
  no other kind.

Railway sends health checks with `Host: healthcheck.railway.app`. Nothing in this
deployment restricts `Host` today; if Keycloak's hostname strictness ever starts
rejecting it, that is where to look first.

## Watch patterns

Declared on every Dockerfile-built service and currently inert, since auto-deploy is
off. They describe what actually goes into each image, so they are worth keeping
honest: `api`/`release`/`verify` share one image and therefore one pattern set
(`src/`, `alembic/`, `alembic.ini`, `synthetic/`, `infra/api/`), `model-server` and
`feature-server` share another (`src/`, `alembic/`, `alembic.ini`, `infra/features/`,
`infra/model-bundle/` — the baked serving bundle is a build input, so a new bundle is a
new image, and the migration graph is copied in for the pre-deploy fence to read), and
`loadcheck` watches `synthetic/load/` because that is what its image copies.

Whether Railway resolves watch patterns relative to the repository root or to a
service's Root Directory is not something this repository has established, and it
only matters for `web`. It is safe to leave as it stands while auto-deploy is off,
and worth confirming before turning it on.

## Verifying this directory

```sh
for f in infra/railway/*.json; do python -m json.tool "$f" > /dev/null || echo "BAD $f"; done
docker build -f infra/k6/Dockerfile -t recsys-k6 .
docker run --rm recsys-k6 k6 version
pytest tests/unit/test_railway_config.py -q
```

The test asserts the structural properties this directory has to hold — that every
file parses, that every service named in the topology has exactly one, that only keys
Railway actually supports appear, that every `dockerfilePath` resolves to a file that
exists, that jobs restart `NEVER` and only the two scheduled ones carry a
`cronSchedule`, that the health paths are the ones the images really serve, and that
`infra/k6/Dockerfile`'s pinned k6 version still matches `infra/ci/k6-version`. That
last one is the drift a reviewer would never catch by eye: a Dockerfile cannot read a
file to build its own `FROM` line, so the pin is duplicated, and a canary running a
different k6 than the CI gate would quietly undo what pinning the version was for.

What it deliberately does not assert is that a `startCommand` names an executable
that exists. `entrypoint.sh` is created inside the API image rather than checked in at
the path the command names, so the only honest check of those three commands is a
deploy — which is the same reason the entrypoint question above is written down as
something to answer rather than something already settled.
