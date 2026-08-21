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
harness racing.

Which persona each scenario touches follows the ownership table the browser
suite keeps in `web/tests/e2e/browser-auth.spec.ts`, so one rule governs both
suites: rating and watched history belong to Action Fan `900000101`, watchlist
to Eclectic Viewer `900000103`, Discover's writes to Drama Fan `900000102`, and
Cold Start `900000104` is read-only for everyone. In CI the two suites cannot
collide anyway — the load gate runs under the `movielens-demo` Compose project
and the browser job under `movielens-browser-e2e`, against separate databases —
but locally they share one, which is why the same table governs both.

Which persona each *reader* uses is a measurement decision layered on top.
Discover and Browse read Eclectic Viewer, whose only writes here are watchlist
changes, and a watchlist change is not a recommendation input — so the budgeted
recommendation step is coupled to the cheapest possible writer rather than to
one whose edits invalidate the sidecar's feature cache. Library reads Action
Fan, whose rating edits churn values it reports but not the counts it asserts.

Every write is undone inside its iteration, and `teardown()` sweeps again: it
repairs any divergence and then *fails* the run, because a run that needed
repairing did not measure what it claimed to. One residue it cannot remove is
worth naming: adding and then removing a watchlist entry leaves a
`user_movie_state` row whose every field is null. The API has no endpoint that
deletes the row, and every consumer — library counts, catalog card state,
recommendation exclusions, the positive-signal count — treats it exactly as it
treats no row at all. That is the precision "restore exactly" has here.

Cold Start gets belt and braces. The script refuses to start if any writer is
pointed at it, and `teardown()` reads its policy back and fails unless it is
still `popularity` with zero positive signals. That protects two separate
contracts at once: this workload's own cold-traffic assertion, and the browser
suite's Quick Picks journey, which owns that persona precisely for the counter
a stray write would move.

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
workers, 45-second windows. "1-CPU" caps `api-load` and `model-server` with
`cpus: 1.0` each; per the 2026-08-21 note above that is a harsher environment
than a shared runner rather than a model of one, so those numbers are a stress
bound. Six windows in all: two uncapped and four capped, the latter spanning
both sides of the persona realignment described earlier, since the workload
shape did not change with it.

The derivation, stated once so it can be reapplied rather than re-invented:

> **budget = 1.5x the worst value at least two runs corroborate, rounded up to
> the next 10 ms.**

"Corroborated" means the second-highest of the four capped windows, not the
highest, and that is the load-bearing part. A capped container is stalled by
CFS throttling dozens of times per run, and these journeys produce 23 to 46
samples per window — so a p99 there is barely more than a maximum, and a single
stall can carry it. Sizing off the single worst reading produced, on the first
attempt, a 230 ms p99 budget for a recommendation read whose median is 26 ms:
arithmetically correct and useless as a gate. Requiring two runs to agree stops
a hypervisor stall from buying permanent headroom, and it makes every budget
*tighter* than the naive rule rather than looser. The cost is explicit and
accepted: one of the four capped windows is expected to breach, because it is
the outlier the rule deliberately excluded.

Steps that hit the same endpoint inside the same page share the worst of their
budgets, and a page's p99 budget is floored at 1.3x its p95 budget — on the
low-sample journeys the two statistics land on top of each other, and a p99
contract that is really a p95 contract is a trap for whoever reads it next.

| Step | uncapped p95/p99 | 1-CPU worst | 1-CPU corroborated | budget p95/p99 |
|---|---:|---:|---:|---:|
| `browse:catalog_first` | 28 / 31 | 28 / 62 | 26 / 41 | 40 / 70 |
| `browse:catalog_next_1` | 14 / 17 | 22 / 55 | 13 / 52 | 40 / 100 |
| `browse:catalog_next_2` | 13 / 25 | 46 / 74 | 21 / 67 | 40 / 100 |
| `browse:movie_detail` | 5 / 12 | 22 / 54 | 21 / 44 | 40 / 70 |
| `discover:audits` | 10 / 23 | 21 / 66 | 19 / 48 | 30 / 80 |
| `discover:features` | 13 / 33 | 29 / 66 | 25 / 64 | 40 / 100 |
| `discover:history` | 21 / 26 | 18 / 40 | 18 / 36 | 30 / 60 |
| `discover:personas` | 18 / 24 | 17 / 34 | 17 / 26 | 30 / 60 |
| `discover:recommendations` | 34 / 44 | 40 / 77 | 35 / 74 | 60 / 120 |
| `library:library_history` | 11 / 30 | 47 / 68 | 27 / 65 | 50 / 100 |
| `library:library_rated` | 26 / 31 | 31 / 64 | 25 / 52 | 50 / 100 |
| `library:library_watchlist` | 11 / 14 | 26 / 68 | 15 / 36 | 50 / 100 |
| `library:personas` | 18 / 20 | 20 / 41 | 20 / 38 | 30 / 60 |
| `library:taste_profile` | 21 / 31 | 23 / 45 | 23 / 29 | 40 / 50 |
| `mutation:library_counts` | 7 / 15 | 35 / 46 | 26 / 45 | 50 / 80 |
| `mutation:library_read_after` | 5 / 7 | 56 / 61 | 32 / 47 | 50 / 80 |
| `mutation:mutate` | 13 / 13 | 74 / 89 | 42 / 84 | 70 / 130 |
| `mutation:mutate_replay` | 7 / 8 | 61 / 74 | 43 / 71 | 70 / 130 |
| `mutation:revert` | 6 / 7 | 46 / 81 | 42 / 52 | 70 / 130 |
| `mutation:state_read` | 23 / 27 | 33 / 63 | 29 / 50 | 60 / 90 |
| `mutation:state_read_after` | 5 / 6 | 42 / 61 | 38 / 55 | 60 / 90 |
| `mutation:state_read_final` | 3 / 4 | 23 / 56 | 18 / 41 | 60 / 90 |
| `mutation:taste_refresh` | 7 / 18 | 55 / 68 | 48 / 57 | 80 / 90 |
| `quickpicks:dismiss` | 15 / 16 | 67 / 73 | 61 / 71 | 100 / 110 |
| `quickpicks:recommendations_after_dismiss` | 11 / 14 | 61 / 72 | 59 / 66 | 90 / 140 |
| `quickpicks:recommendations_after_watched` | 9 / 9 | 47 / 57 | 31 / 54 | 90 / 140 |
| `quickpicks:recommendations_before` | 38 / 39 | 44 / 150 | 41 / 87 | 90 / 140 |
| `quickpicks:revert_watched` | 5 / 11 | 30 / 38 | 27 / 35 | 100 / 110 |
| `quickpicks:undo` | 5 / 7 | 39 / 45 | 35 / 44 | 100 / 110 |
| `quickpicks:watched` | 4 / 5 | 24 / 30 | 24 / 25 | 100 / 110 |

Where a budget looks larger than 1.5x its own corroborated value, the row
shares a family budget with a slower sibling — the three Library tabs, the
two cursor continuations, the three mutation writes and so on.

Per page, on the two custom trends the script records:

| Page | Trend | uncapped p95/p99 | 1-CPU worst | 1-CPU corroborated | budget p95/p99 |
|---|---|---:|---:|---:|---:|
| `discover` | blocking | 34 / 45 | 41 / 77 | 36 / 74 | 60 / 120 |
| `discover` | journey | 49 / 77 | 81 / 129 | 76 / 109 | 120 / 170 |
| `browse` | blocking | 28 / 31 | 28 / 64 | 26 / 42 | 40 / 70 |
| `browse` | journey | 56 / 78 | 104 / 180 | 94 / 155 | 150 / 240 |
| `library` | blocking | 28 / 32 | 33 / 65 | 26 / 53 | 40 / 80 |
| `library` | journey | 51 / 83 | 97 / 162 | 75 / 114 | 120 / 180 |
| `mutation` | blocking | 34 / 37 | 91 / 147 | 72 / 140 | 110 / 210 |
| `mutation` | journey | 58 / 73 | 153 / 407 | 114 / 202 | 180 / 310 |
| `quickpicks` | blocking | 38 / 39 | 44 / 150 | 42 / 87 | 70 / 140 |
| `quickpicks` | journey | 80 / 82 | 197 / 254 | 171 / 209 | 260 / 340 |

Two verification windows followed the derivation, on the budgets above rather
than on placeholders: one uncapped with `PAGE_LATENCY_ENFORCED=true` and one
capped and advisory. Both reported `GATE=pass` with every check passing, zero
request errors, zero dropped iterations and zero unreverted mutations, and both
met every budget outright — the capped one included, which is a stronger result
than the rule required.

These are still generous against the uncapped readings, and deliberately so: a
brand-new budget's first job is to not flake, and it is measured against an
environment nothing in CI actually runs in. What they catch today is an
order-of-magnitude regression — `mutation:mutate` going from 16 ms to 130 ms, a
fan-out that stopped being parallel, a keyset scan that became a table scan.
What they do not catch is drift, and tightening them is the follow-up below.

The honest weak point is sample count. `mutation` yields 46 iterations per smoke
window and `quickpicks` 23, so a p99 there is barely more than a maximum; the
corroboration rule is a mitigation, not a cure. The nightly profile is where
these become percentiles — it keeps the smoke arrival rates and lengthens the
window to three minutes, giving 180 and 90 samples. A second, smaller one:
`browse:catalog_next_2` reads *faster* than `catalog_next_1` uncapped and slower
capped, which is noise rather than a property of the second cursor, and the two
share a budget for that reason.

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

Each route is measured against the persona whose journey already owns that kind
of write, per the same ownership table, and every write is undone. Cold Start is
read and never written: Quick Picks owns it for the five-signal counter, and
timing a decision queue is not a reason to spend the signal it is counting.

Three runs on the seeded local stack, worst reading per route:

| Route | Persona | LCP (ms) | CLS | ack (ms) | interaction measured |
|---|---|---:|---:|---:|---|
| `/discover` | 900000102 | 400 | 0.0000 | 14.4 | watchlist the featured movie |
| `/browse` | 900000103 | 104 | 0.0000 | 14.7 | load the next cursor page |
| `/movies/{id}` | 900000103 | 108 | 0.0000 | 7.7 | watchlist from the detail controls |
| `/library` | 900000101 | 120 | 0.0000 | 16.5 | edit a rating |
| `/quick-picks` | 900000104 | — | — | — | **skipped: HTTP 404 on this branch** |

Quick Picks lands on `main` with its own pull request; the route is measured as
soon as this branch is rebased onto it, and until then the report says skipped
rather than implying five routes were covered.

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

#### First observation from the runner

The gap this note keeps naming — no data from a shared 4-vCPU runner — got its
first entry when this work's own CI run went green. One run is not a trend and
it does not promote anything, but it is the first evidence that the budgets are
not fantasy, and it belongs here rather than in a pull-request comment that will
be closed. Every step and every page landed inside budget, with zero request
errors, zero dropped iterations and zero unreverted mutations at 71.6
requests/second.

| Step (tightest margin first) | runner p95/p99 | budget p95/p99 | margin |
|---|---:|---:|---:|
| `library:personas` | 19 / 22 | 30 / 60 | 1.6x / 2.8x |
| `discover:recommendations` | 34 / 41 | 60 / 120 | 1.7x / 2.9x |
| `library:taste_profile` | 23 / 28 | 40 / 50 | 1.7x / 1.8x |
| `quickpicks:recommendations_before` | 50 / 52 | 90 / 140 | 1.8x / 2.7x |
| `browse:catalog_first` | 21 / 26 | 40 / 70 | 1.9x / 2.7x |
| `discover:history` | 16 / 22 | 30 / 60 | 1.9x / 2.7x |

| Page | runner blocking p95/p99 | budget | runner journey p95/p99 | budget |
|---|---:|---:|---:|---:|
| `discover` | 35 / 41 | 60 / 120 | 58 / 64 | 120 / 170 |
| `browse` | 22 / 27 | 40 / 70 | 65 / 75 | 150 / 240 |
| `library` | 25 / 32 | 40 / 80 | 61 / 70 | 120 / 180 |
| `mutation` | 40 / 44 | 110 / 210 | 88 / 94 | 180 / 310 |
| `quickpicks` | 51 / 52 | 70 / 140 | 124 / 136 | 260 / 340 |

Two things worth reading off that. The runner's numbers sit close to the local
*uncapped* readings and well under the 1-CPU ones, which is the clearest
confirmation yet of what the previous note asserted: a `--cpus` cap is a harsher
environment than a shared runner, so budgets derived from it are conservative
rather than optimistic. And the tightest margin in the whole table is
`quickpicks` blocking at 1.4x — a recommendation read taken immediately after a
state change, which is a deliberate cache miss and therefore the step most
worth watching as the catalog grows.

The browser suite's first runner readings, from the same run: LCP 112–448 ms
against a 2 500 ms budget, CLS 0.0000 everywhere, and acknowledgement 12.1–43.0
ms against 100 ms. The acknowledgement margin narrowed from 5.9x locally to 2.3x
on the runner, which is exactly the sensitivity that kept it advisory.

Both observations predate the persona realignment described above, and the
budgets were tightened after them by the corroboration rule. The run is quoted
against the tightened numbers, not the ones in force when it happened: it clears
every one of them, which is the useful fact. The workload shape did not change
with the realignment, and the budgets stand on the re-measured local windows.

#### What is enforced, and how an advisory budget gets promoted

| Measure | PR CI | Nightly | Why |
|---|---|---|---|
| Page correctness (checks, request errors, unreverted mutations) | enforced | enforced | Deterministic. A wrong body is never the runner's fault. |
| Page latency budgets (per step and per page) | **advisory** | enforced | Derived from six local windows and corroborated by exactly one run on a 4-vCPU runner. The promotion rule below asks for ten. |
| Reliability checks (nine required) | enforced | enforced | Pass/fail facts, not distributions. |
| Rate-limit behaviour | recorded | recorded | Not implemented; reported, not asserted. |
| Browser CLS | enforced | — | A property of the markup. Contention cannot make a reserved box shift. |
| Browser structural claims | enforced | — | Same reason. |
| Browser LCP | enforced | — | Worst reading 400 ms locally and 448 ms on the runner, against 2 500 ms under a 4x throttle. LCP here is dominated by the server render and one poster rather than a long JavaScript task, so it degrades gradually rather than off a cliff. |
| Browser acknowledgement | **advisory** | — | The thinnest margin of the three and the most CPU-sensitive: a React state update racing a 100 ms budget. The worst reading went from 16.5 ms locally to 43.0 ms on the runner — a 2.3x margin, and only one observation of it. |

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

- **A missing route can answer 200.** The first CI run of the browser script
  reported `/quick-picks` with an LCP of 120 ms and a clean CLS — for a route
  that did not exist on that branch. It had redirected, the script checked only
  for a 404, and it therefore measured whatever it landed on and filed the
  numbers under a route that was not there. Locally the same navigation had
  answered a real 404 and skipped correctly, which is exactly why it survived to
  CI. The check now compares the landed path against the requested one, and the
  skip reason says which of the two failures it saw. This is the failure mode
  the skip logic existed to prevent, and it took a runner to expose it.
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
