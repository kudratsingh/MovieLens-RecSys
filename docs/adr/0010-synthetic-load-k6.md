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

The first accepted local Docker Desktop implementation run on 2026-08-15 used
k6 2.1.0, four slim API workers, four model-sidecar workers, and the initial
60-request/second arrival target. The recommendation-only summary (setup
validation excluded) reported:

- p50: 5.83 ms
- p95: 9.97 ms
- p99: 70.08 ms
- achieved throughput: 59.18 requests/second across 3,570 requests
- request error rate: 0; response check rate: 1
- dropped iterations: 31

The arrival target is 55 requests/second, while the contractual throughput
threshold is the achieved rate above 50. Dropped iterations remain visible in
the JSON summary as capacity evidence; they do not replace the achieved-rate,
latency, correctness, or error thresholds.

Implementation tuning later exposed a measurement artifact: k6's default
five-second Prometheus batches stalled enough in Docker Desktop to inflate its
own p99, while an otherwise identical run without the output reported p99
78.64 ms at 54.02 requests/second. The smoke profile therefore uses a two-minute
push interval. That is longer than the scenario, and k6's `PeriodicFlusher`
[performs one final flush on shutdown](https://pkg.go.dev/go.k6.io/k6/output#PeriodicFlusher),
so the full batch still reaches Prometheus after measurement without pausing
in-flight VUs. The higher-volume profile uses 30-second batches to bound memory
without letting exporter work dominate its much larger p99 sample.

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
- **CI job additions.** The `synthetic-load-smoke` job boots the isolated demo stack, seeds/materializes the Feast and model artifacts, and invokes `make demo-load-smoke`. That target recreates the feature, model, and real-auth load-serving processes before traffic so every run has the same process-cache boundary. k6 threshold exit status is the job result; serving logs are uploaded on failure.
- **Larger run.** `make demo-load-nightly` selects the five-minute, 100-VU profile locally. A scheduled staging workflow remains pending on the environment-specific Compose bundle, so this ADR does not claim a scheduler that does not yet exist.
- **k6 container in dev tools.** `make demo-load-smoke` and CI both resolve the image tag from `infra/ci/k6-version`; no host `brew install` is required.
- **Prometheus config.** Prometheus's `remote_write` receiver is enabled in the compose stack; k6's `experimental-prometheus-rw` output points at it. The receiver is *not* enabled in production compose stacks — synthetic-load metrics are dev/CI-only, and mixing them with production metrics would pollute the tsdb.
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
