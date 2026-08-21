# ADR 0010 — Synthetic Load Testing Tool: k6

**Status:** Accepted
**Date:** 2026-07-03

## Context

CLAUDE.md's non-negotiable #4 pins **p99 < 100ms, measured under synthetic load, not assumed**. Non-negotiable #11 elevates the measurement to a gate: **every PR touching `src/serving/` runs a synthetic-load smoke test in CI and fails if p99 exceeds the SLO on a defined baseline workload.** The synthetic-load harness is not observability decoration — it is the piece of infrastructure that turns the SLO from a claim into a fact.

The forcing constraints:

- **Runs in CI on every serving PR.** The smoke variant has to boot fast, produce enough load to make p99 measurable, and pass/fail deterministically within a CI job's time budget.
- **Runs against the authenticated multi-tenant API.** [ADR 0007](0007-auth-provider-keycloak.md) pinned Keycloak; every synthetic virtual user needs a real Bearer token minted via the direct password grant, scoped to a specific tenant. The load-testing tool must handle stateful auth flows, not just fire canned HTTP requests.
- **Metrics land in the project's Prometheus + Grafana.** The stack table has both from Phase 1; the load tester should push its measurements to the same Prometheus so latency histograms are inspectable in the same dashboards operators use for production observability.
- **Cold-start coverage** ([ADR 0011](0011-cold-start-coverage.md)) requires the load test to hit programmatically-generated new-user profiles alongside warm ones — the tool has to script arbitrary request shapes, not just uniform random traffic.
- **Drift simulation** (Phase 5) and **A/B fixtures** (Phase 6) reuse the load harness. Whatever we pick here we live with for the whole load-testing surface.

The two families a design review will actually raise:

1. **k6** (Grafana Labs, Go binary, JavaScript scripting) — purpose-built for CI/CD load testing, native Prometheus integration, declarative thresholds, single-binary distribution.
2. **Locust** (Python, coroutine-based, web UI) — the older and broader alternative, familiar in Python shops, dashboard-first workflow.

Plus a few we'll dismiss quickly: **wrk / wrk2** (no scripting, no auth flow support), **Artillery** (Node-based, smaller community, no Grafana-native integration), **custom asyncio harness** (reinventing k6 poorly).

## Decision

The synthetic-load harness is **k6** (`grafana/k6` OSS distribution), with the following shape:

- **Scripts live in `synthetic/load/`.** `recommendations.js` owns the first SLO surface, with focused helpers for Keycloak auth and shared threshold declarations. Additional endpoint workloads get separate entry points only when they have a distinct traffic contract.
- **Auth via Keycloak direct password grant.** `setup()` mints a real Bearer token for the reviewed demo tenant and passes it to VUs. The same tenant is intentional: its stable warm and cold personas let the harness verify serving policy as well as HTTP status. `synthetic/load/lib/auth.js` refreshes the token before expiry during longer runs.
- **Declarative thresholds define pass/fail.** The recommendation-tagged contract is `p(99)<100`, request failure rate `==0`, response check rate `==1`, and achieved request rate `>50` for the smoke variant. Threshold violations are the CI job's failure signal — no separate assertion layer.
- **Prometheus remote-write.** k6 pushes metrics to the project's Prometheus via the `k6 run --out experimental-prometheus-rw` output. Labels kept low-cardinality: `endpoint`, `method`, `status`, `tenant`. Latency histograms show up in the same Prometheus tsdb as production metrics.
- **CI smoke variant.** GitHub Actions runs a 60-second constant-arrival workload targeting 55 requests/second with 10 preallocated VUs. The target leaves measurable headroom above the contractual achieved-rate threshold without turning ordinary runner jitter into a different capacity test. It currently runs on every PR, which is stricter than the minimum serving-path trigger, and fails on any threshold breach.
- **Larger profile.** `make demo-load-nightly` exposes a five-minute, 600-request/second, 100-VU capacity profile against the same stack. Scheduling it against staging is deferred until the staging Compose environment exists.
- **k6 version pinned in Docker.** `infra/ci/k6-version` pins the exact `grafana/k6` image tag used by both Make and CI, avoiding a separate host binary installation and eliminating local/CI version drift.

## Implemented baseline

The accepted local Docker Desktop implementation run on 2026-08-20 used k6
2.1.0, four slim API workers, four model-sidecar workers, and the defined
55-request/second arrival target. The recommendation-only summary (setup
validation excluded) reported:

- p50: 6.31 ms
- p95: 14.27 ms
- p99: 41.30 ms
- achieved throughput: 54.08 requests/second across 3,301 requests
- request error rate: 0; response check rate: 1
- dropped iterations: 0

The arrival target is 55 requests/second, while the contractual throughput
threshold is the achieved rate above 50. Dropped iterations remain visible in
the JSON summary as capacity evidence; they do not replace the achieved-rate,
latency, correctness, or error thresholds.

Implementation tuning exposed two independent tail-latency problems. First,
k6's default five-second Prometheus batches stalled enough in Docker Desktop
to inflate its own p99. The smoke profile therefore uses a two-minute push
interval. That is longer than the scenario, and k6's `PeriodicFlusher`
[performs one final flush on shutdown](https://pkg.go.dev/go.k6.io/k6/output#PeriodicFlusher),
so the full batch still reaches Prometheus after measurement without pausing
in-flight VUs. The higher-volume profile uses 30-second batches to bound memory
without letting exporter work dominate its much larger p99 sample.

Second, synchronous JWT verification and PostgreSQL reads were running on the
API workers' event loops. Durable stage audits showed learned-request p99 at
87.84 ms while the model sidecar was only 18.68 ms, isolating the orchestration
overhead. Moving those blocking operations to the request thread pool reduced
end-to-end p99 from 117.40 ms to the accepted 41.30 ms without changing the
arrival target, traffic mix, audit durability, or thresholds.

### 2026-08-21 — measuring the service rather than the runner

The job then began failing about half its runs on unchanged code: p99 between
219 ms and 299 ms with 70–111 dropped iterations, while p50 stayed at 9–11 ms
in passing and failing runs alike. A flat p50 with a moving p99 is contention,
not a regression, and the tail had two distinct populations — a cold burst in
the first two seconds with `feature_latency_ms` up to 452 ms, and a mid-run
cluster with `feature_latency_ms` at zero. The measurement, not the service,
was what varied. Four changes, none of them to a threshold:

- **CPU headroom.** `make demo-load-quiesce` stops the services the gate does
  not measure — the browser demo's `api` and `web` plus the one-shot setup
  containers — between `demo-seed` and `demo-load-smoke`. They are stopped, not
  removed, so `make demo-logs` still explains a failure. The runner's four
  vCPUs are shared by every container in the stack, and CPU taken by processes
  outside the measured path arrives as tail latency inside it.
- **Warm-up in `setup()`.** The load scripts now prime every uvicorn worker for
  every persona through real authenticated requests before the measured window
  opens, in rotated rounds of at least two requests per worker per persona.
  Priming through the endpoint is the only thing that works: the sidecar's
  feature cache is keyed by `(tenant, user, candidate set)`, so warming a
  process without warming that key pays nothing. The rounds are budgeted,
  because k6 divides request counts by the whole test-run duration and `setup()`
  is part of it — warm-up time is spent out of the achieved-rate threshold's
  margin, and the summary now reports both so the cost stays visible.
- **Arrival-rate fidelity.** The smoke profile keeps 10 preallocated VUs but
  raises the ceiling to 40 (nightly: 100 and 400). At the old ceiling of 10 the
  executor dropped arrivals it could not start, which removed the slowest
  requests from the percentiles and depressed the achieved rate — the gate was
  quietly measuring less as the service got slower. Headroom makes the
  measurement stricter. Dropped iterations remain reported and deliberately
  advisory rather than a threshold: they also occur when the load generator
  itself is descheduled, which is a property of the host, not of the service.
- **Learned serving is asserted, not assumed.** A warm persona whose sidecar
  call exceeds `model_server_timeout_seconds` degrades to the popularity
  fallback and answers HTTP 200. That is fast and wrong, and a latency gate that
  accepts it is measuring the wrong thing. Warm traffic now fails `checks` when
  `serving_policy.learned` is not true, and a `silent_learned_fallbacks` counter
  names the failure in the summary and in Prometheus.

Local runs against a 1-CPU-capped `api-load` and `model-server` — the
reproduction the investigation used for the runner — confirm the cold-burst
population is gone: the largest `feature_latency_ms` inside the measured window
falls from 410 ms to under 1 ms. They also show that a `--cpus` cap is a harsher
environment than a shared runner rather than a faithful model of one: the cgroup
counters record dozens of CFS throttle events per run, each stalling every
thread in the container until the next 100 ms period. Capped numbers are a
stress bound, not a runner prediction.

`model_server_timeout_seconds` stays at 0.5. Nothing in the warmed steady state
tripped it — the sidecar's own p99 stayed near 12 ms even under the cap — and
raising it would only widen the window in which the API waits on a sidecar that
has already blown the end-to-end budget.

No threshold changed: p99 < 100 ms, zero request errors, check rate 1, achieved
rate above 50.

### 2026-08-21 — telling preemption apart from a regression

Two CI runs of the hardened gate came back p99 127 ms with 1 dropped iteration
and p99 472 ms with 23, p50 flat at 8.8–10.2 ms in both. The cold-burst
population and the dropped-iteration cascade were gone, which was the point of
the previous change; what was left is a runner whose four vCPUs are shared by
about ten containers. Raising the VU ceiling means the gate now *measures* the
stalls it used to drop — honest, and also a gate that fails whenever the runner
is preempted, no matter what the service did. So the second round of work is
about giving the measurement less to compete with, and about being able to prove
which of the two happened.

**Less to compete with.** The demo overlay assigns CFS weights rather than caps,
and it only ever *promotes*: `api-load` and `model-server` to 4096,
`feature-server` and `k6` to 2048, everything else left at the default 1024.
Weights only decide who wins when the host is oversubscribed, so an uncontended
measurement is bit-for-bit what it was — this cannot flatter a result, only stop
an unrelated process from spoiling one.

Promotion-only is a correction, and the reason is worth keeping. The first
attempt also demoted the neighbours — Keycloak, its Postgres, `api`, `web` — and
pinned Keycloak to a 512 MB serial-GC heap with its JIT stopped at C1. That was
wrong twice over. It buys nothing, because shares arbitrate between *runnable*
tasks and a mostly-idle Keycloak consumes nothing whatever its weight; only the
ratio between the busy processes matters, and promotion sets that ratio by
itself. And it is not free, because `browser-auth-e2e` drives `api`, `web`, and
an interactive Keycloak login through these same Compose files, on the same
4-vCPU runner, with 10-second assertion timeouts. Starving the services under
test in one job to speed up another is not a trade this repository should make.
The demotions and the JVM flags are gone; `api`, `web`, `keycloak`,
`keycloak-postgres`, `postgres`, `pgbouncer` and `redis` resolve identically to
what they were before this ADR note. The one demotion left is `prometheus` at
256, which no other job starts.

Prometheus does not start for the smoke profile at all: remote-write is a trend
feature for the nightly run, and the 60-second smoke holds its batch until after
the scenario anyway, so all it contributed was another process. Its evidence
goes to the run artifact instead. `demo-load-nightly` keeps it.

**Proving which happened.** `synthetic/load/probe_host_cpu.py` samples
`/proc/stat` once a second for the whole window, and `synthetic/load/
summarize.py` reads back k6's own sample stream and joins per-second latency
buckets against per-second CPU steal and run-queue depth. Steal is the
discriminator: it is time the hypervisor gave to someone else while this kernel
had runnable work, so it cannot be caused by our own service being slow. Run
queue is reported next to it but is deliberately *not* part of any rule — a deep
run queue is exactly what a slow service produces, so it cannot separate the two
cases. The table prints the ten slowest seconds into the job log, and the whole
directory — k6 summary, raw samples, per-second table, `docker stats` and cgroup
throttling counters from either side of the window — uploads as a CI artifact on
success and failure alike, because a passing run is the baseline the next
failure gets read against.

**The re-measure rule.** A window whose p99 breached is re-measured exactly once,
and only when at least three of its ten slowest seconds recorded 10% or more CPU
steal. The second window reuses the warm stack, so it repeats the same
measurement rather than running a different one, and its verdict is final
whatever its own steal looks like — the rule cannot loop and cannot turn a
failing service green. A breach with low steal is *not* re-measured: it is the
service's and fails immediately, which is the half of the rule that keeps it
from being a retry button. Both windows and the decision, including the label
"re-measured after hypervisor steal: N%", are recorded in the artifact and the
log. This is a measurement-validity rule, not a threshold: the thresholds, the
arrival rate, the run length, and the traffic mix are all unchanged. The 10% and
three-second constants are the honest weak point — they are reasoned rather than
derived, because the Docker Desktop hypervisor does not report steal to its
guest at all and no local run could produce a non-zero sample. The artifact
carries every per-second value so they can be re-derived from real runner data
rather than guessed at twice.

**Worker count.** `api-load`'s uvicorn worker count is now a single variable
(`API_LOAD_WORKERS`, default 4) that the k6 warm-up also sizes itself from, so
the two can no longer drift. Four workers plus a four-worker sidecar on four
vCPUs is oversubscribed on paper, so it was measured: with both processes capped
to one CPU, four workers gave p99 24.94 ms and 46.26 ms across two runs and two
workers gave 25.29 ms and 36.28 ms. The spread within each setting is as wide as
the gap between them, so the default stays at 4 — but it is now one variable to
change if runner data says otherwise.

### 2026-08-21 — page-shaped workloads and browser timing

The recommendation gate measures one endpoint. It is the SLO and it does not
move, but it cannot see what a *page* costs, and every remaining Bundle 7
performance question is about pages: a fan-out is only as fast as its slowest
leg, a cursor continuation is three round trips deep, and a mutation is not
finished when the PUT returns — it is finished when the next read shows the
committed state. None of that appears in a single-endpoint percentile.

So two new measurement surfaces land, deliberately separate from each other and
from the pinned gate:

- **`synthetic/load/pages.js`** — page-shaped API workloads in k6, run by
  `make demo-load-pages` through the same `run_gate.sh`, with the same warm-up,
  quiesce, host-CPU probe, and re-measure rule. Budgets live in
  `synthetic/load/page_thresholds.js`; `thresholds.js` is untouched.
- **`web/tests/perf/browser-timing.spec.ts`** — LCP, CLS and time-to-visible
  acknowledgement in a real browser, run by `npm run test:perf` in the
  `browser-auth-e2e` job after the journeys.

The frontend testing strategy already says browser and API timing must not be
conflated. That is enforced structurally here: different suites, different
reports, different artifacts. Neither number may ever be quoted for the other.

#### What the page workloads model

Each scenario is read off the route's loader in `web/lib/**`, not invented.
Every request carries `page` and `step` tags so a percentile is attributable to
a route and to a position inside it, and every step asserts correctness.

| Scenario | Modelled sequence | Source |
|---|---|---|
| `discover` | `recommendations?limit=10` ‖ `history?limit=8` ‖ `/personas`, all three blocking the server render; then `audits?limit=5` ‖ `features` on disclosure | `web/lib/discover/resources.ts`, `web/app/discover/page.tsx` |
| `browse` | `catalog?limit=24` → next cursor → next cursor → open a movie, rotating search / genre / decade / `popular` / `newest` | `web/components/browse/browse-explorer.tsx`, `web/lib/browse/query.ts` |
| `library` | active tab `limit=12` ‖ `taste-profile` ‖ `/personas`, then the two tab switches | `web/app/library/page.tsx`, `web/lib/library/url-state.ts` |
| `mutation` | read state → mutate → replay the same idempotency key → read state → the `limit=1` counts refresh (‖ `taste-profile` for a rating) → the list read → revert → read state | `web/lib/movie-state/mutate.ts`, `web/components/library/library-experience.tsx` |
| `quickpicks` | recommend → dismiss → recommend (id absent) → undo → watched → recommend (policy still coherent) → revert | the API contract; see the gap noted below |

One in five Discover views is the cold-start persona, which is the share the
fallback deserves in a page mix: rare, but never zero, because it is a different
code path with a different cost. The whole mix runs at ~72 requests/second,
close to the pinned gate's arrival rate, so the two are read in the same
register.

The two writing scenarios run a single virtual user each. They are journeys, not
throughput tests, and serialising them means an `expected_revision` conflict can
only ever be the service disagreeing with itself rather than two copies of the
harness racing. They mutate `900000103` (rating edits, watchlist) and
`900000102` (dismiss/undo/watched), revert inside the iteration, and are swept
again in `teardown()` — which repairs any divergence and then *fails* the run,
because a run that needed repairing did not measure what it claimed to. Cold
Start `900000104` is never mutated: pushing it past five positive signals would
flip the fallback path this harness exists to keep honest.

#### Correctness and latency are separate verdicts

k6's exit status cannot say "the budgets slipped but the API is correct", and
that is exactly the distinction a new budget needs. `pages.js` therefore
classifies each breached threshold and writes a verdict into `summary.json`;
`summarize.py` turns it into a `GATE=pass|fail` line and `run_gate.sh` uses that
as its exit code.

- **Correctness** — every check passes, zero request errors, zero unreverted
  mutations. Deterministic: preemption cannot make an API return a wrong body.
  **Always enforced**, on PR CI and nightly alike, and never re-measured.
- **Latency** — the per-step and per-page budgets below. These move with the
  host, so `LOAD_LATENCY_ENFORCED=false` reports them instead of failing.

The re-measure rule is unchanged and now applies only to latency breaches.

#### Baselines and the budgets they produced

Local Docker Desktop, k6 2.1.0, four `api-load` workers, four model-sidecar
workers, 45-second window, two runs per configuration. "1-CPU" is `api-load` and
`model-server` each capped with `cpus: 1.0`; per the 2026-08-21 note above that
is a harsher environment than a shared runner rather than a model of one, so
those numbers are a stress bound. Every column is the worst of the two runs.

Every budget is **1.5x the worst 1-CPU observation, rounded up to the next
10 ms**, and steps that hit the same endpoint inside the same page share the
worst of their budgets — encoding the gap between two 23-sample percentiles as
two different contracts would be recording noise as a promise.

| Step | uncapped p95/p99 | 1-CPU p95/p99 | budget p95/p99 | margin over 1-CPU |
|---|---:|---:|---:|---:|
| `browse:catalog_first` | 22 / 30 | 28 / 41 | 50 / 70 | 1.8x / 1.7x |
| `browse:catalog_next_1` | 12 / 20 | 22 / 52 | 70 / 120 | 3.2x / 2.3x |
| `browse:catalog_next_2` | 9 / 17 | 46 / 74 | 70 / 120 | 1.5x / 1.6x |
| `browse:movie_detail` | 5 / 10 | 22 / 44 | 40 / 70 | 1.8x / 1.6x |
| `discover:audits` | 8 / 15 | 21 / 66 | 40 / 100 | 1.9x / 1.5x |
| `discover:features` | 11 / 17 | 25 / 66 | 40 / 100 | 1.6x / 1.5x |
| `discover:history` | 16 / 23 | 18 / 36 | 30 / 60 | 1.7x / 1.7x |
| `discover:personas` | 15 / 20 | 16 / 34 | 40 / 60 | 2.5x / 1.8x |
| `discover:recommendations` | 31 / 39 | 40 / 74 | 60 / 120 | 1.5x / 1.6x |
| `library:library_history` | 9 / 13 | 47 / 65 | 80 / 110 | 1.7x / 1.7x |
| `library:library_rated` | 22 / 26 | 31 / 64 | 80 / 110 | 2.6x / 1.7x |
| `library:library_watchlist` | 12 / 18 | 26 / 68 | 80 / 110 | 3.0x / 1.6x |
| `library:personas` | 15 / 22 | 20 / 38 | 40 / 60 | 2.0x / 1.6x |
| `library:taste_profile` | 19 / 25 | 23 / 45 | 40 / 70 | 1.8x / 1.6x |
| `mutation:library_counts` | 6 / 10 | 35 / 46 | 90 / 100 | 2.5x / 2.2x |
| `mutation:library_read_after` | 7 / 8 | 56 / 61 | 90 / 100 | 1.6x / 1.6x |
| `mutation:mutate` | 16 / 16 | 74 / 89 | 120 / 140 | 1.6x / 1.6x |
| `mutation:mutate_replay` | 9 / 12 | 43 / 71 | 120 / 140 | 2.8x / 2.0x |
| `mutation:revert` | 7 / 13 | 46 / 81 | 120 / 140 | 2.6x / 1.7x |
| `mutation:state_read` | 21 / 25 | 33 / 50 | 70 / 100 | 2.1x / 2.0x |
| `mutation:state_read_after` | 5 / 7 | 42 / 61 | 70 / 100 | 1.7x / 1.6x |
| `mutation:state_read_final` | 4 / 5 | 23 / 56 | 70 / 100 | 3.0x / 1.8x |
| `mutation:taste_refresh` | 6 / 8 | 55 / 68 | 90 / 110 | 1.6x / 1.6x |
| `quickpicks:dismiss` | 18 / 21 | 67 / 73 | 110 / 120 | 1.6x / 1.6x |
| `quickpicks:recommendations_after_dismiss` | 14 / 16 | 59 / 66 | 90 / 140 | 1.5x / 2.1x |
| `quickpicks:recommendations_after_watched` | 11 / 14 | 31 / 57 | 90 / 140 | 2.9x / 2.5x |
| `quickpicks:recommendations_before` | 40 / 48 | 44 / 87 | 90 / 140 | 2.1x / 1.6x |
| `quickpicks:revert_watched` | 6 / 9 | 27 / 35 | 110 / 120 | 4.1x / 3.5x |
| `quickpicks:undo` | 6 / 6 | 35 / 44 | 110 / 120 | 3.1x / 2.7x |
| `quickpicks:watched` | 5 / 6 | 24 / 30 | 110 / 120 | 4.6x / 4.0x |

Per page, on the two custom trends the script records — `blocking` is what
the route must finish before it can render its first-read object,
`journey` is the whole modelled sequence:

| Page | Trend | uncapped p95/p99 | 1-CPU p95/p99 | budget p95/p99 |
|---|---|---:|---:|---:|
| `discover` | blocking | 31 / 44 | 41 / 74 | 70 / 120 |
| `discover` | journey | 41 / 52 | 81 / 109 | 130 / 170 |
| `browse` | blocking | 23 / 30 | 28 / 42 | 50 / 70 |
| `browse` | journey | 49 / 65 | 104 / 180 | 160 / 280 |
| `library` | blocking | 23 / 33 | 33 / 65 | 50 / 100 |
| `library` | journey | 46 / 56 | 97 / 162 | 150 / 250 |
| `mutation` | blocking | 36 / 39 | 91 / 140 | 140 / 210 |
| `mutation` | journey | 65 / 80 | 153 / 407 | 230 / 620 |
| `quickpicks` | blocking | 41 / 49 | 44 / 87 | 70 / 140 |
| `quickpicks` | journey | 92 / 101 | 197 / 209 | 300 / 400 |

These are deliberately generous. A brand-new budget's first job is to not
flake, and the margins above are measured against an environment nothing in CI
actually runs in. What they catch today is an order-of-magnitude regression —
`mutation:mutate` going from 16 ms to 130 ms, a fan-out that stopped being
parallel, a keyset scan that became a table scan. What they do not catch is
drift, and tightening them is the follow-up below.

Two verification runs followed the derivation, on the budgets above rather than
on placeholders: one uncapped with `PAGE_LATENCY_ENFORCED=true` and one capped,
both `GATE=pass` with every check passing, zero request errors, zero dropped
iterations, and zero unreverted mutations. The capped run met every budget
outright, so the advisory path was not even exercised by it — it was exercised
during derivation, when ten steps breached the placeholder budgets and the run
still exited 0 with the breaches named in the log and the artifact.

Two honest weak points in the table. `mutation` and `quickpicks` produce 45 and
23 samples per smoke window, so their p99 is barely more than a maximum — which
is why `mutation:journey` p99 is budgeted at 620 ms off a single 407 ms
iteration. And `browse:catalog_next_2` was *faster* than `catalog_next_1`
uncapped and slower capped, which is noise, not a property of the second cursor;
the two share a budget for that reason.

#### Browser timing, measured separately

`web/playwright.perf.config.ts` pins what "the agreed mobile profile" means,
which was previously only a phrase in the testing strategy: viewport 390x844
(the mobile column of the evidence matrix), device scale factor 3, `isMobile`
and `hasTouch`, and a 4x CPU throttle applied through CDP. Network throttling is
deliberately *not* applied — the stack under test is on loopback, so any
emulated RTT would be a number the harness invented rather than one it measured,
and it would dominate every result.

Everything is measured inside the page rather than around it: LCP from a
buffered `PerformanceObserver`, CLS by the web-vitals session-window definition
rather than a naive sum, and acknowledgement as the milliseconds between a
capture-phase input event and the first mutation that changes the visible text
of the control's status region. Timing a Playwright call would measure
Playwright's round trip as much as the application.

Three runs on the seeded local stack, worst reading per route:

| Route | LCP (ms) | CLS | acknowledgement (ms) | interaction measured |
|---|---:|---:|---:|---|
| `/discover` | 392 | 0.0000 | 15.9 | watchlist the featured movie |
| `/browse` | 108 | 0.0000 | 14.6 | load the next cursor page |
| `/movies/{id}` | 104 | 0.0000 | 9.9 | watchlist from the detail controls |
| `/library` | 128 | 0.0000 | 16.9 | edit a rating |
| `/quick-picks` | — | — | — | **skipped: HTTP 404** |

Alongside the timings, the structural promises the testing strategy makes are
asserted rather than assumed, because a good number on one run is not the same
as a design that cannot shift:

- **Reserved poster dimensions** — every `img` sits in a container with a fixed
  aspect ratio (`.poster-frame`, 2/3). Checked structurally as well as through
  CLS: a run that happened to load every poster from cache would show CLS 0
  while the markup was still capable of shifting.
- **Below-fold lazy loading** — nothing below the initial viewport loads
  eagerly. On Browse that is 7/7 images; the first cells are allowed to be
  eager because they are the LCP candidate.
- **Bounded catalog pages** — 24 cards on the first page, 48 after one
  continuation, against the API's own `limit <= 48`.
- **No per-card TMDB fan-out** — zero `api.themoviedb.org` requests and zero
  direct `image.tmdb.org` requests; artwork reaches the browser only through
  this origin's `/_next/image`. Classified by host, never by substring: the
  optimizer's own URL carries the TMDB origin percent-encoded in its `url`
  parameter, and a substring match reported every correctly-proxied poster as a
  fan-out until that was fixed.
- **Progressive technical data** — zero `audits` and zero `features` requests
  before the "Why this?" disclosure is opened, and one of each after.

#### Reliability facts a percentile cannot express

`synthetic/load/reliability.py` (`make demo-reliability-check`) runs once
against the warm load stack and reports ten pass/fail facts. All ten hold today:

- `/healthz` answers 200 without a token, and nine protected routes answer 401
  without one.
- A caller-supplied `X-Request-ID` is echoed on the response **and** reaches the
  durable audit row as `correlation_id` — a request is traceable end to end, not
  just within one process. A caller that supplies none gets a minted UUID.
- Auth, model and database provenance are each proven by something the service
  returns: `/whoami` resolves the verified token to a tenant, realm and role
  set; the audit row names every artifact version and the per-stage latencies;
  and that row exists only because the request's transaction committed.
- Catalog and library reject an over-large `limit` with 422 and never return
  more than asked. A cursor reused under a different filter is a 400, as is a
  malformed one.
- Degraded metadata renders: 41 of the 48 first-page catalog items in the
  reviewed fixture have no poster, and a detail read for one answers 200 with
  `source_status: "partial"` and a title. A missing poster is a render decision,
  not a failure — which it has to be, because most of Browse is in that state.

And one measured absence: **rate limiting is not implemented.** Sixty rapid
authenticated requests all answered 200, with no `429` and no `X-RateLimit-*`
or `Retry-After` header. There is no limiter in `src/serving/`; the per-tenant
quota column exists but nothing reads it, and `src/serving/tenancy/router.py`
says so. This check is advisory and records the behaviour rather than asserting
a contract, so the day a limiter lands it starts describing it without being
rewritten. Inventing a limiter to have something to assert would have been worse
than the gap.

#### What is enforced, and how an advisory budget gets promoted

| Measure | PR CI | Nightly | Why |
|---|---|---|---|
| Page correctness (checks, request errors, unreverted mutations) | enforced | enforced | Deterministic. A wrong body is never the runner's fault. |
| Page latency budgets (per step and per page) | **advisory** | enforced | Derived from two local runs and zero runs on the 4-vCPU runner. |
| Reliability checks (nine required) | enforced | enforced | Pass/fail facts, not distributions. |
| Rate-limit behaviour | recorded | recorded | Not implemented; reported, not asserted. |
| Browser CLS | enforced | — | A property of the markup. Contention cannot make a reserved box shift. |
| Browser structural claims | enforced | — | Same reason. |
| Browser LCP | enforced | — | Worst local reading 392 ms against 2 500 ms under a 4x throttle, and LCP here is dominated by the server render and one poster rather than a long JavaScript task, so it degrades gradually. |
| Browser acknowledgement | **advisory** | — | The thinnest margin of the three (worst 16.9 ms against 100 ms) and the most CPU-sensitive: a React state update racing a 100 ms budget on a shared runner. |

Promotion is a one-line change with a recorded reason, never an edit to an
assertion:

- **Page latency budgets** → set `PAGE_LATENCY_ENFORCED=true` in the
  `synthetic-load-smoke` job. Do it once ten consecutive runs of
  `make demo-load-pages` on the GitHub runner have recorded every step inside
  its budget, and re-derive the budgets from those runs at the same time —
  runner data replaces the 1-CPU stress bound rather than sitting next to it.
- **Browser acknowledgement** → set `PERF_ENFORCE_ACK=true`. Do it once three
  consecutive `browser-auth-e2e` runs record every route under 50 ms.
- Tightening in either direction requires re-recording a baseline. The rule from
  the previous note still governs the other direction: record whether a load
  failure is repeatable before changing a threshold, and never loosen one
  because of a single noisy runner.

#### Gaps this note is recording rather than closing

- **There is no `/quick-picks` route on this branch.** It exists only as a
  zero-network fixture preview under `/ui-preview/quick-picks`. The browser
  script navigates to `/quick-picks`, sees the 404, skips, and lists it in the
  report as skipped rather than silently measuring four routes and calling it
  five. The k6 `quickpicks` scenario still runs, because the API contract it
  exercises — durable suppression, undo, and a coherent policy after a fresh
  signal — is real and worth holding under load whether or not a page drives it
  yet.
- **There is still no scheduled nightly workflow.** `make demo-load-pages-nightly`
  enforces the budgets over a three-minute window; nothing invokes it on a
  schedule, exactly as `demo-load-nightly` has stood since this ADR was written.
  The nightly page profile keeps the smoke profile's arrival rates and only
  lengthens the window: a budget is enforceable against the workload it was
  measured on, and raising the rate would mean enforcing measured numbers
  against an unmeasured mix. Capacity probing stays `demo-load-nightly`'s job.
- **The writing scenarios share personas with the browser suite.** In CI the two
  run in different Compose projects against different databases, so they cannot
  collide; locally, run one at a time. Every mutation self-reverts and teardown
  sweeps whatever did not.

## Rationale

1. **Purpose-built for CI/CD load testing is the argument.** k6 was designed by Grafana Labs specifically to fit into the shape non-negotiable #11 is asking for: a scriptable load test with declarative thresholds that a CI job can wait on and fail against. Threshold declarations *are* the pass/fail signal — you write `p(99)<100` in the script and CI stops on breach without a separate assertion harness. Locust's dashboard-first workflow was designed for an operator watching a graph, not for a CI job asserting an inequality; you can bolt CI-shape usage onto Locust with `--headless --check` and post-run parsing, but that's adaptation, not fit.

2. **Grafana ecosystem integration is first-class and this project already has Grafana.** The stack table has Prometheus + Grafana from Phase 1; k6's `experimental-prometheus-rw` output pushes latency histograms straight into the same Prometheus tsdb. During a CI run or a manual investigation, latency percentiles from synthetic load land next to production metrics in the same Grafana dashboards — no extra glue. Locust supports Prometheus via `prometheus_exporter` or third-party plugins but the integration is bolt-on and the metric shape is exporter-specific. When CLAUDE.md says "hand this to an enterprise SRE," the SRE reading a k6-shaped `endpoint`-labeled `http_req_duration` histogram is looking at the same schema every k6 deployment produces; a Locust exporter's shape is bespoke to whichever exporter was picked.

3. **Go runtime handles the concurrency shape better on a single CI runner.** A GitHub Actions runner (2 vCPU, 7 GB RAM) can produce meaningfully more concurrent HTTP load with k6 than with Locust because k6's VUs are goroutines and Locust's are Python coroutines under the GIL — coroutines are cheap, but the per-request work (auth header injection, HTTP client, response parsing) is still Python. For the smoke variant this doesn't matter — 10 VUs is trivial for either — but the nightly variant does matter, and picking the tool that scales inside the CI budget avoids a "we need distributed load generation" conversation later.

4. **Auth flow support is real, not a fig leaf.** The Keycloak direct password grant is a two-step: `POST /realms/<realm>/protocol/openid-connect/token` → parse token → attach as Bearer. k6's `http` module + `setup()` return the token as `data` that every VU receives, so token minting happens once per script and every request uses the shared token. Token refresh mid-run is a helper that lives in `synthetic/load/lib/auth.js`. Locust models the same flow via `on_start` per-user, which is functionally equivalent — this isn't a rationale for choosing k6, but it defends against the "k6 can't do stateful flows" concern.

5. **The escape hatch is bounded.** If k6 becomes a problem — Grafana Labs pivots the licensing, JS-scripting becomes a reader tax we can't defend, distributed load generation becomes necessary and k6-native distributed isn't good enough — the migration to Locust is a rewrite of the scripts (10–20 short files) but not of the thresholds or the traffic-shape decisions. What we're picking is a scripting-and-runtime layer; the workload definitions transfer.

## Alternatives considered

- **Locust.** The honest alternative. Python is the language the rest of the codebase is written in, which lowers the reader tax for anyone touching the load scripts — this is a real advantage that this ADR does not dismiss. Locust's Python API is more expressive for complex user-state modeling (Phase 5 drift users with evolving preferences might be *easier* in Locust than in k6's JS). Rejected on rationale #1 (CI-fit is worse — dashboard-first workflow adapts to CI rather than fitting it) and rationale #2 (Grafana integration is bolt-on rather than native). Also weaker on rationale #3 (Python concurrency ceiling under a single CI runner). If we discover the drift simulation in Phase 5 wants Python-native user modeling more than we want k6's CI fit, the escape hatch (rationale #5) is available — the workload definitions transfer.

- **wrk / wrk2.** The "just measure latency" tool. Lua-scripted, fast, single-purpose. Rejected because it doesn't handle the auth flow (token minting per VU with mid-run refresh), doesn't produce the tenant-scoped request patterns cold-start coverage will need, and doesn't have declarative thresholds — it prints numbers, you compare them yourself. Right tool for "how fast can this endpoint go under raw HTTP," wrong tool for "does the authenticated multi-tenant API meet its SLO on realistic traffic."

- **Artillery.** Node-based, YAML-scenario configuration, plugin ecosystem. Comparable to k6 on scripting expressiveness (both are JS-adjacent). Rejected because the community is smaller, the Prometheus integration is via a third-party plugin rather than a first-class output, and the CI story is less standardized. There's no dimension where Artillery is strictly better than k6 for what this project needs.

- **Custom Python asyncio harness.** The "we don't need a framework" alternative. Rejected because it dispenses with the pieces we're paying a load tester for: threshold declarations, VU orchestration, latency histograms, Prometheus integration, and the tenant-isolation-adjacent semantics of "each VU is a stateful user session." Building any one of those poorly is a project on its own; building all of them poorly is what this ADR is written to prevent.

- **No load testing tool — measure latency in production only.** Not seriously considered. Non-negotiable #4 pins measurement under synthetic load, and non-negotiable #11 pins CI enforcement. This option exists only so the ADR names it explicitly for the design review.

## Consequences

- **New source tree.** `synthetic/load/` contains `recommendations.js`, `lib/auth.js`, and `thresholds.js`. The workload mixes warm, cold, and mixed traffic in a deterministic 7/2/3 ratio per 12 arrivals and validates policy, non-empty results, and an auditable request ID.
- **Page-shaped workloads alongside it** (2026-08-21). `pages.js`, `page_thresholds.js`, `lib/stack.js`, and `reliability.py` join the same directory, driven by `make demo-load-pages` / `demo-load-pages-nightly` / `demo-reliability-check`. `run_gate.sh` gained a script selector and a workload mode; `summarize.py` gained a per-(page, step) table and a `GATE=` verdict that separates a correctness breach from a latency one. The `k6` Compose service's entry point is now `${LOAD_SCRIPT}`, defaulting to `recommendations.js` so nothing that invokes it without an override changes behaviour.
- **A browser-timing suite that is not a load test.** `web/playwright.perf.config.ts` and `web/tests/perf/` measure LCP, CLS, acknowledgement latency, and the structural layout promises on the pinned mobile profile, run by `npm run test:perf` in `browser-auth-e2e`. It produces its own artifact (`artifacts/browser-timing/`) and its numbers are never mixed with k6's.
- **CI job additions.** The `synthetic-load-smoke` job boots the isolated demo stack, seeds/materializes the Feast and model artifacts, quiesces the unmeasured services with `make demo-load-quiesce`, and invokes `make demo-load-smoke`, then `make demo-load-pages` and `make demo-reliability-check` against the same warm stack (about 90 s on top). That target runs `synthetic/load/run_gate.sh`, which recreates the feature, model, and real-auth load-serving processes before traffic so every run has the same process-cache boundary, samples host CPU accounting for the whole window, and applies the re-measure rule. k6 threshold exit status is the job result; the evidence directory uploads on every run and serving logs are uploaded on failure.
- **Larger run.** `make demo-load-nightly` selects the five-minute, 100-VU profile locally. A scheduled staging workflow remains pending on the environment-specific Compose bundle, so this ADR does not claim a scheduler that does not yet exist.
- **k6 container in dev tools.** `make demo-load-smoke` and CI both resolve the image tag from `infra/ci/k6-version`; no host `brew install` is required.
- **Prometheus config.** Prometheus's `remote_write` receiver is enabled in the compose stack; the nightly profile's `experimental-prometheus-rw` output points at it. The 60-second smoke does not start Prometheus at all (see the 2026-08-21 note) and writes its evidence to the run artifact. The receiver is *not* enabled in production compose stacks — synthetic-load metrics are dev/CI-only, and mixing them with production metrics would pollute the tsdb.
- **Auth fixture.** The workload authenticates a dedicated Keycloak client user in the `demo` realm, then reads the stable demo persona IDs. Recommendation traffic is read-only; the resulting prediction audits are intentionally visible in the demo walkthrough.
- **Deferred to Phase 5 / Phase 6.** Drift simulation scripts (`synthetic/drift/`) reuse this harness for programmatically shifted user preferences. A/B fixtures (`synthetic/ab_fixtures/`) reuse it for deterministic tenant+user combos in integration tests.

## Risks

- **Single CI runner can't produce enough load for meaningful measurement.** The 2-vCPU/7GB GitHub Actions runner puts a ceiling on VU concurrency. At 10 VUs for 60 seconds this is a non-issue; if we ever want the smoke test to exercise higher concurrency, the CI job would need a larger runner (paid) or the smoke variant stays deliberately small and the nightly does the heavy lifting. Mitigation: keep smoke small on purpose; the smoke's job is "did we regress the p99 SLO," not "what's the max throughput."
- **k6 version drift between local and CI.** A floating container tag could change behavior without a code diff. Mitigation: `infra/ci/k6-version` pins the exact image tag and both local Make targets and CI consume that file. Version bumps must demonstrate the same thresholds before merge.
- **JS scripts as a reader-tax for a Python codebase.** Anyone touching the load scripts has to context-switch from Python to JavaScript. Mitigation: keep each entry point direct, use the k6 stdlib rather than clever patterns, and isolate reusable auth and threshold code. If user-state logic becomes the majority of a workload, that's rationale #5's escape-hatch signal.
- **Threshold tuning as an ongoing skill.** Initial thresholds might be wrong — too tight (flakiness) or too loose (miss regressions). Mitigation: baseline thresholds on the actual measured latencies from the first serving PR, then tighten if we observe consistently better performance. Failed CI runs on threshold breach get the same investigation cycle as any other CI failure.
- **k6 Prometheus remote-write cardinality blowup.** Dynamic user, request, or model identifiers in metric tags would pollute the Prometheus tsdb. Mitigation: the workload tags only the bounded endpoint and traffic class, and exercises exactly four stable recommendation URLs. Request IDs and model versions remain in Postgres audits rather than metric labels.
- **k6 Cloud vs OSS divergence.** k6 Cloud has features (test result comparison, hosted runs, notifications) that don't exist in OSS. Mitigation: this ADR pins OSS-only, and no script relies on Cloud-specific features. If we ever want Cloud, it's an additive change on the same scripts.

## How we'd know we're wrong

- **CI smoke job flakes frequently on unrelated causes.** Would suggest thresholds are too tight for the CI environment's variability, or the smoke's surface is too broad (hitting endpoints whose latency is dominated by cold-cache warmup, not real regression). Fix by loosening thresholds or narrowing scope — not by changing tools.
- **JavaScript-scripted user modeling becomes complex enough that a Python-native harness would have been easier.** Phase 5's drift simulation is where this risk lives — if a "user whose preferences shift over 30 days" is 200 lines of JS state management, that's the escape-hatch signal. Fix by evaluating Locust for `synthetic/drift/` specifically; the load smoke stays k6.
- **Prometheus tsdb grows faster than expected during CI runs.** Would mean cardinality controls in the remote-write config aren't strong enough. Fix by tightening `drop` rules, not by changing tools.
- **The smoke test consistently passes on real serving regressions.** Would mean the smoke's traffic shape doesn't hit the paths where regressions manifest. Fix by expanding scenario coverage — cold-start users, feature-store cache misses, tenants on different champion models.
- **k6 threshold declarations don't cover a class of failure we care about.** E.g. per-endpoint p99 hides a specific endpoint regressing while others compensate. Fix by splitting thresholds per endpoint via `tag`-scoped threshold declarations — a k6-native feature, not a tool change.
