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
- Ports 3001, 5432, 6432, 8000, and 8080 available on localhost.

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
6. Verifies FastAPI, Next.js, and the Keycloak demo realm from inside the demo
   network.

`make demo-seed` can be run repeatedly. It preserves an existing full-ingest
catalog, inserts only missing demo catalog rows, and replaces the controlled
demo persona/background interactions with the same deterministic fixture.

`make demo-smoke` fails unless all of these contracts hold:

- FastAPI, Next.js, and the Keycloak demo realm are reachable.
- Action Fan, Drama Fan, Eclectic Viewer, and Cold Start are discoverable.
- Action Fan has history and recommendations with no seen-item overlap.
- Cold Start has no history and receives popularity fallback recommendations.

## Walkthrough

Open <http://localhost:3001>.

1. Select **Action Fan**. Show the recent action/thriller history, top-eight
   unseen recommendations, popularity policy, and model version.
2. Select **Drama Fan**. Point out the contrasting drama/romance history and
   changed unseen set.
3. Select **Eclectic Viewer**. Show the broader multi-genre taste signal.
4. Select **Cold Start**. Confirm the history panel explicitly identifies the
   zero-history state while recommendations still load.
5. Give several movies 1–5 stars. Show the history update, the
   `genre-affinity` policy, and the refreshed unseen recommendations.
6. Use **Reset this profile** to return the selected persona to cold start.
7. If a TMDB token is configured, point out the real posters and release years.
   Otherwise show that the fallback artwork keeps the same flow usable.

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
- **Posters are missing:** this is expected without a TMDB token. If a token is
  configured, restart with `make demo-down && make demo-up` so FastAPI reads the
  new environment.
- **A clean rebuild is required:** run `make demo-reset`. This is the recovery
  path for stale or incompatible demo volumes.
