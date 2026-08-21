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
- Ports 3001, 5432, 6379, 6432, 8000, and 8080 available on localhost, plus
  9090 if you run the nightly load profile (it starts Prometheus as the k6
  remote-write receiver; the 60-second smoke does not). The demo does not start
  MLflow or Grafana.

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
catalog, inserts only missing demo catalog rows, refreshes the 120-title local
metadata snapshot, and replaces the controlled demo persona/background
interactions with the same deterministic fixture. It
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
make demo-load-quiesce
make demo-load-smoke
```

`make demo-load-quiesce` stops the services the gate does not measure — the
browser demo's `api` and `web` plus the one-shot setup containers — so they
stop competing for CPU with the ones it does. It stops rather than removes
them, so `make demo-logs` still explains a failure afterwards, and
`make demo-up` brings them back. Skipping it is fine on a roomy laptop and is
the difference between measuring the service and measuring the host on a
shared CI runner.

`make demo-load-smoke` starts an internal `api-load` process with development
impersonation disabled and recreates the feature/model/load processes so each
run begins at the same process-cache boundary. It obtains a real Keycloak
token, then primes every uvicorn worker for every persona with real
authenticated requests — the sidecar's feature cache is keyed by user *and*
candidate set, so this is the only warm-up that pays for itself — and refuses
to start measuring if the stack is not serving the seeded personas. It then
targets 55 recommendation arrivals/second for 60 seconds with 10 preallocated
VUs and a ceiling of 40. The measured traffic follows a deterministic 7/2/3
warm/cold/mixed ratio. The command fails unless all recommendation checks pass,
request errors are zero, p99 is below 100 ms, and achieved throughput is above
50 requests/second. Warm personas must additionally report
`serving_policy.learned`: a request that quietly degrades to the popularity
fallback answers HTTP 200, and a latency gate that accepts it is timing the
wrong answer.

The final JSON object is the compact evidence summary. It includes p50, p95,
p99, achieved throughput, request count, total test-run duration, error/check
rates, warm-up cost, dropped iterations, and silent learned fallbacks. Dropped
iterations are kept visible as a capacity signal; the gate is based on achieved
throughput together with latency, correctness, and error thresholds. Note that
k6 divides the request count by the *whole* test-run duration, warm-up
included, so the reported warm-up cost is spent out of the achieved-rate
margin. The accepted 2026-08-20 implementation baseline reported p50 6.31 ms,
p95 14.27 ms, p99 41.30 ms, 54.08 measured requests/second, zero request
errors, and zero dropped iterations across 3,301 measured requests.

Everything the run produced lands under `artifacts/load-smoke/`, which CI
uploads on pass and fail alike:

```
artifacts/load-smoke/
├── docker-stats-{before,after}.txt   # CPU/memory and the effective CFS weights
├── cpu-stat-{before,after}.txt       # cgroup throttling counters per service
└── window-1/
    ├── summary.json                  # the JSON printed above
    ├── per-second.txt / .json        # p50/p95/p99/max per second, with steal
    ├── host-cpu.jsonl                # /proc/stat deltas, one line per second
    ├── raw-metrics.json.gz           # every k6 sample, for re-deriving anything
    ├── decision.json                 # the measurement-validity verdict
    └── k6-stdout.txt, k6-exit, breakdown.txt
```

The per-second table is the thing to read first when the gate fails. A slow
opening second is a cold cache; a tail smeared across the middle is contention;
a tail that follows one traffic class is a serving regression. Each second also
carries the host's CPU **steal** — time the hypervisor spent elsewhere while
this kernel had work to run — and its run-queue depth. The ten slowest seconds
are printed into the log so a failure is readable without downloading anything.

**The re-measure rule.** A breached window is re-measured exactly once, and only
when at least three of its ten slowest seconds recorded 10% or more CPU steal:
that combination means the machine was not scheduled, which is a measurement to
redo rather than a result to report. The repeat reuses the warm stack, its
verdict is final however its own steal looks, and the decision is labelled in
the log ("re-measured after hypervisor steal: N%"). A breach with low steal is
never re-measured — it is the service's and fails immediately. This is a
validity rule about when a number counts, not a threshold: no threshold,
arrival rate, run length, or traffic mix changes.

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

### Page-shaped budgets and browser timing

The gate above measures one endpoint. Two further commands measure what a
*page* costs, and they are kept separate on purpose — the frontend testing
strategy forbids conflating browser timing with the serving-only k6 number, so
they run in different suites and produce different reports.

```bash
make demo-load-pages          # page-shaped API workloads, per step
make demo-reliability-check   # request ids, readiness, degraded metadata, ...
```

`make demo-load-pages` runs `synthetic/load/pages.js` through the same wrapper
as the smoke gate — same warm-up, same host-CPU probe, same re-measure rule —
and models five routes as tagged step sequences read off the web client's own
loaders:

| Scenario | What it drives |
|---|---|
| `discover` | recommendations + history + personas concurrently, then the audits/features disclosure |
| `browse` | catalog first page → next cursor → next cursor → open a movie, across the search/genre/decade/sort variants |
| `library` | the active tab + taste profile + personas, then the two tab switches |
| `mutation` | read state → mutate → replay the idempotency key → read state → counts refresh → list read → revert → read state |
| `quickpicks` | recommend → dismiss → recommend → undo → watched → recommend → revert |

The two writing scenarios mutate demo personas and put them back inside the
iteration; `teardown()` sweeps anything left and fails the run if it had to.
Cold Start `900000104` is never mutated. Run the browser suite and this target
one at a time locally — in CI they use different Compose projects and cannot
collide.

Correctness always fails the run: every check, zero request errors, zero
unreverted mutations. The per-step latency budgets in
`synthetic/load/page_thresholds.js` are **advisory** by default and reported
rather than enforced (`PAGE_LATENCY_ENFORCED=true` enforces them, and
`make demo-load-pages-nightly` does over a three-minute window). ADR 0010's
2026-08-21 page-shaped note carries the budgets, the baselines they came from,
and what it takes to promote them.

The evidence lands under `artifacts/load-pages/` in the same shape as the smoke
gate's, plus `window-1/steps.txt` — the per-step table with each step's
percentiles next to its budget, which is the first thing to read when a budget
slips. `reliability.json` sits alongside it.

`make demo-reliability-check` reports ten pass/fail facts a percentile cannot
express: `/healthz` reachable without a token while nine other routes answer
401; a caller-supplied `X-Request-ID` echoed on the response *and* persisted to
the audit row's `correlation_id`; auth, model and database provenance readable
from `/whoami` and the audit row; bounded page sizes; a cursor reused under a
different filter refused with 400; and a poster-less movie rendering as a record
rather than a failure. It also records that **rate limiting is not implemented**
— sixty rapid authenticated requests all answer 200, with no `429` and no
`X-RateLimit-*` header. That check is advisory and describes the behaviour, so
it will start describing a limiter the day one lands.

Browser timing is a Playwright suite, not a load test:

```bash
cd web && npm run test:perf     # needs the demo stack up and seeded
```

It signs in through real Keycloak, warms each route, then measures LCP, CLS, and
time-to-visible-acknowledgement on the agreed mobile profile — 390x844, device
scale factor 3, touch, 4x CPU throttle, no network throttling — and asserts the
structural promises: reserved poster boxes, below-fold lazy loading, a bounded
catalog page, zero per-card TMDB requests, and technical evidence that loads on
disclosure rather than blocking the first movie. Targets are LCP ≤ 2.5 s,
CLS ≤ 0.1, and acknowledgement ≤ 100 ms; CLS, LCP and the structural claims are
enforced, the acknowledgement budget is advisory for now. A route that answers
404 is skipped and listed as skipped — `/quick-picks` does not exist yet, and
the report says so rather than quietly measuring four routes and calling it
five. The report is written to `artifacts/browser-timing/browser-timing.json`
with a compact table in the log.

## Routine operations

```bash
make demo-logs   # tail the services that explain startup/runtime failures
make demo-down   # stop containers and preserve demo volumes
make demo-up     # restart while preserving the database (also undoes a quiesce)
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
- **The load gate fails:** read `artifacts/load-smoke/window-1/per-second.txt`
  first, then use the emitted JSON to separate response errors,
  bad policy/check results, throughput saturation, and a p99 regression. A
  non-zero `silent_learned_fallbacks` means warm personas were served by the
  popularity fallback — look for `model-server-unavailable` in `model-server`
  and `api-load`. A non-zero `dropped_iterations` means the load generator
  could not start every arrival, so the percentiles understate the tail; treat
  the run as capacity-limited rather than as evidence either way. A flat p50
  with a moved p99 is contention, not a regression: check what else is running
  on the host and confirm `make demo-load-quiesce` ran. Then inspect
  `api-load`, `model-server`, `feature-server`, `pgbouncer`, and `postgres`
  with `make demo-logs`.
- **Posters are missing:** this is expected without a TMDB token. If a token is
  configured, restart with `make demo-down && make demo-up` so FastAPI reads the
  new environment.
- **A clean rebuild is required:** run `make demo-reset`. This is the recovery
  path for stale or incompatible demo volumes.
