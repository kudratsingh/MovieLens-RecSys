# Local Demo Runbook

This runbook starts the first repeatable MovieLens portfolio demo from a clean
checkout. It uses a small reviewed MovieLens catalog snapshot; downloading or
ingesting the 25M dataset is not required for the walkthrough. The full dataset
remains the source for training and offline evaluation.

## Prerequisites

- Docker Desktop or another Docker Engine with Compose v2.
- Enough free disk space for the Python, Next.js, Postgres, Keycloak, and
  pgBouncer images. The first start downloads base images and is substantially
  slower than later cached starts.
- Ports 3000, 3001, 5000, 5432, 6379, 6432, 8000, 8080, and 9090
  available on localhost.

Python and Node.js are not required on the host for the containerized
walkthrough. They are only required for direct backend or frontend development.

## First start

From the repository root:

```bash
cp .env.example .env
make demo-up
make demo-seed
make demo-smoke
```

`TMDB_READ_ACCESS_TOKEN` in `.env` is optional. Leave it empty to use the
generated poster artwork, or set a TMDB API Read Access Token before
`make demo-up` to enable real posters.

`make demo-up` does the following:

1. Builds the FastAPI and standalone Next.js images.
2. Starts isolated Postgres and Keycloak databases, Keycloak, and pgBouncer.
3. Runs a one-shot schema job that creates the ingest-owned base tables and
   applies every Alembic migration.
4. Starts FastAPI only after schema setup succeeds and pgBouncer is healthy.
5. Starts Next.js only after FastAPI is healthy.
6. Restarts the feature/model sidecars when a previously seeded artifact
   volume exists; a first clean start leaves them for `make demo-seed`.
7. Verifies FastAPI, Next.js, and the Keycloak demo realm from inside the demo
   network.

`make demo-seed` can be run repeatedly. It preserves an existing full-ingest
catalog, inserts only missing demo catalog rows, and replaces the controlled
demo persona/background interactions with the same deterministic fixture. It
then materializes the tenant's Feast features, trains the deterministic
item-item and LightGBM demo artifacts, and starts the private feature/model
sidecars after their registry and artifact volumes are ready. Re-running the
command recreates both sidecars so their in-process registry, model bundle, and
version-scoped feature cache cannot retain the previous snapshot.

`make demo-smoke` fails unless all of these contracts hold:

- FastAPI, Next.js, and the Keycloak demo realm are reachable.
- Action Fan, Drama Fan, Eclectic Viewer, and Cold Start are discoverable.
- Action Fan has history and recommendations with no seen-item overlap.
- Action Fan reports `item-item-cosine+lightgbm` with versioned artifacts.
- Cold Start has no history and reports the `popularity` fallback.

## Walkthrough

Open <http://localhost:3001>.

1. Select **Action Fan**. Show the recent action/thriller history, top-eight
   unseen recommendations, `item-item-cosine+lightgbm` policy, and the
   `demo-itemitem-v1/demo-lgbm-v1` model version.
2. Select **Drama Fan**. Point out the contrasting drama/romance history and
   changed unseen set.
3. Select **Eclectic Viewer**. Show the broader multi-genre taste signal.
4. Select **Cold Start**. Confirm the history panel explicitly identifies the
   zero-history state while recommendations still load.
5. Give several movies 1–5 stars. Show the immediate history update, learned
   policy, and refreshed unseen recommendations. Candidate generation consumes
   the live history, so the newly seen movie is excluded without retraining.
6. Use **Reset this profile** to return the selected persona to cold start.
7. If a TMDB token is configured, point out the real posters and release years.
   Otherwise show that the fallback artwork keeps the same flow usable.

## Audit and latency proof

After generating at least one recommendation, print its three newest durable
audits:

```bash
make demo-audits
```

The response is read through the same authenticated, RLS-bound application
connection used by the rest of the API. For a warm result, point out the
request ID, exact ranked movie IDs and scores, eight online feature values per
prediction, candidate/ranker/feature versions, and the separate candidate,
feature, ranker, model, and total latency fields. Cold Start records
`popularity`, `fallback_reason: cold-start`, and no fabricated ranker features.

Run the authenticated smoke gate:

```bash
make demo-load-smoke
```

This starts an internal `api-load` process with development impersonation
disabled and recreates the feature/model/load processes so each run begins at
the same process-cache boundary. It obtains a real Keycloak token, concurrently
warms and validates all four personas across the worker pool, and
then targets 55 recommendation arrivals/second for 60 seconds with 10 VUs. The
measured traffic follows a deterministic 7/2/3 warm/cold/mixed ratio. The
command fails unless all recommendation checks pass, request errors are zero,
p99 is below 100 ms, and achieved throughput is above 50 requests/second.

The final JSON object is the compact evidence artifact. It includes p50, p95,
p99, achieved throughput, request count, error/check rates, and dropped
iterations. Dropped iterations are kept visible as a capacity signal; the gate
is based on achieved throughput together with latency, correctness, and error
thresholds. The accepted 2026-08-15 implementation baseline used the initial
60-request/second target and reported p50 5.83 ms, p95 9.97 ms, p99 70.08 ms,
59.18 requests/second, and zero request errors across 3,570 measured requests.

Both local and CI runs use the exact k6 image version pinned in
`infra/ci/k6-version` and remote-write their measurements to the local
Prometheus receiver. The 60-second smoke holds its remote-write batch until the
scenario has ended, then k6's final flush publishes the samples. This prevents
the load generator's own five-second exporter batches from contaminating the
p99 it is measuring. The larger profile is available separately:

```bash
make demo-load-nightly
```

That profile targets 600 requests/second for five minutes with 100 VUs. Treat
it as a capacity probe: a laptop may fail to generate or serve that target. A
scheduled staging run remains deferred until the environment-specific Compose
stack is implemented.

The API container deliberately enables the guarded development impersonation
mode for tenant `demo`; the browser therefore needs no manual token during this
portfolio walkthrough. `Settings` refuses to start with that bypass in any
non-development environment.

## Routine operations

```bash
make demo-logs   # tail the services that explain startup/runtime failures
make demo-down   # stop containers and preserve demo volumes
make demo-up     # restart while preserving the database
make demo-reset  # delete only movielens-demo volumes, rebuild, migrate, and reseed
```

The Compose project name is pinned to `movielens-demo`. `make demo-reset` cannot
remove volumes belonging to the normal development Compose project, but it does
permanently delete the isolated demo Postgres and Keycloak data.

## Troubleshooting

- **A port is already allocated:** stop the normal development stack with
  `make infra-down`, then rerun `make demo-up`.
- **Schema setup failed:** run `make demo-logs` and inspect `demo-setup` and
  `postgres`. The API will not start after a failed setup job.
- **FastAPI is unhealthy:** inspect `api` and `pgbouncer` logs. Startup checks
  reject a BYPASSRLS application role or non-transaction pooling.
- **Personas are missing:** run `make demo-seed`, then `make demo-smoke`.
- **Warm personas show `popularity`:** inspect `model-server`, `feature-server`,
  and `api` with `make demo-logs`, then rerun `make demo-seed`. The API falls
  back deliberately when artifacts, online features, or the sidecar are invalid.
- **`make demo-audits` returns no rows:** generate a recommendation for Action
  Fan first. If the request succeeded but no row appears, inspect `api`; audit
  persistence is part of the request transaction and should fail the request
  rather than silently dropping a row.
- **The load gate fails:** use the emitted JSON to separate response errors,
  bad policy/check results, throughput saturation, and a p99 regression. Then
  inspect `api-load`, `model-server`, `feature-server`, `pgbouncer`, and
  `postgres` with `make demo-logs`.
- **Posters are missing:** this is expected without a TMDB token. If a token is
  configured, restart with `make demo-down && make demo-up` so FastAPI reads the
  new environment.
- **A clean rebuild is required:** run `make demo-reset`. This is the recovery
  path for stale or incompatible demo volumes.
