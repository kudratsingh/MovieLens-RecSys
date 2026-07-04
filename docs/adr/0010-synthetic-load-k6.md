# ADR 0010 — Synthetic Load Testing Tool: k6

**Status:** Accepted
**Date:** 2026-07-03

## Context

CLAUDE.md's non-negotiable #4 pins **p99 < 100ms, measured under synthetic load, not assumed**. Non-negotiable #11 elevates the measurement to a gate: **every PR touching `src/serving/` runs a synthetic-load smoke test in CI and fails if p99 exceeds the SLO on a defined baseline workload.** The synthetic-load harness is not observability decoration — it is the piece of infrastructure that turns the SLO from a claim into a fact.

The forcing constraints:

- **Runs in CI on every serving PR.** The smoke variant has to boot fast, produce enough load to make p99 measurable, and pass/fail deterministically within a CI job's time budget.
- **Runs against the authenticated multi-tenant API.** [ADR 0007](0007-auth-provider-keycloak.md) pinned Keycloak; every synthetic virtual user needs a real Bearer token minted via the direct password grant, scoped to a specific tenant. The load-testing tool must handle stateful auth flows, not just fire canned HTTP requests.
- **Metrics land in the project's Prometheus + Grafana.** The stack table has both from Phase 1; the load tester should push its measurements to the same Prometheus so latency histograms are inspectable in the same dashboards operators use for production observability.
- **Cold-start coverage** ([ADR-pending 0011](.)) requires the load test to hit programmatically-generated new-user profiles alongside warm ones — the tool has to script arbitrary request shapes, not just uniform random traffic.
- **Drift simulation** (Phase 5) and **A/B fixtures** (Phase 6) reuse the load harness. Whatever we pick here we live with for the whole load-testing surface.

The two families a design review will actually raise:

1. **k6** (Grafana Labs, Go binary, JavaScript scripting) — purpose-built for CI/CD load testing, native Prometheus integration, declarative thresholds, single-binary distribution.
2. **Locust** (Python, coroutine-based, web UI) — the older and broader alternative, familiar in Python shops, dashboard-first workflow.

Plus a few we'll dismiss quickly: **wrk / wrk2** (no scripting, no auth flow support), **Artillery** (Node-based, smaller community, no Grafana-native integration), **custom asyncio harness** (reinventing k6 poorly).

## Decision

The synthetic-load harness is **k6** (`grafana/k6` OSS distribution), with the following shape:

- **Scripts live in `synthetic/load/`.** One JavaScript file per surface being exercised — `recommendations.js`, `features.js`, `healthz_baseline.js`. Scripts are small (~50–100 lines each) and use the k6 stdlib rather than custom abstractions.
- **Auth via Keycloak direct password grant.** Each script's `setup()` mints a Bearer token for a synthetic user in the `demo` tenant (or `synth_load` tenant if we want it dedicated), and the token is passed to VUs via the `data` return. Token refresh mid-run is handled by a helper in `synthetic/load/lib/auth.js`.
- **Declarative thresholds define pass/fail.** `thresholds` object per script: `http_req_duration{expected_response:true}: p(99)<100`, `http_req_failed: rate<0.01`, `http_reqs: rate>50` for the smoke variant. Threshold violations are the CI job's failure signal — no separate assertion layer.
- **Prometheus remote-write.** k6 pushes metrics to the project's Prometheus via the `k6 run --out experimental-prometheus-rw` output. Labels kept low-cardinality: `endpoint`, `method`, `status`, `tenant`. Latency histograms show up in the same Prometheus tsdb as production metrics.
- **CI smoke variant.** GitHub Actions job runs a 60-second, 10-VU script on every PR touching `src/serving/`, `src/auth/`, `src/features/`, or `synthetic/load/`. Fails the job on any threshold breach.
- **Nightly larger variant.** A separate scheduled workflow runs a longer, higher-concurrency scenario against a staging compose stack — surfaces trends the 60-second smoke can't see.
- **k6 binary version pinned.** `infra/ci/k6-version` file pins the exact release (starting at whatever the current stable is when the code PR ships); the CI runner installs from that pin, not `latest`.

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

- **New source tree.** `synthetic/load/` — JavaScript scripts (`recommendations.js`, `features.js`, `healthz_baseline.js`), a `lib/` subdirectory for shared helpers (auth token minting, tenant fixture selection), and a `thresholds.js` module that exports the per-endpoint SLO declarations so a threshold change is one file edit.
- **CI job additions.** A new GitHub Actions job `synthetic-load-smoke` triggers on paths matching `src/serving/**`, `src/auth/**`, `src/features/**`, `synthetic/load/**`. Boots the docker-compose stack (Postgres + Redis + Keycloak + MLflow + FastAPI), waits for readiness, runs `k6 run synthetic/load/recommendations.js --out experimental-prometheus-rw`, and fails on threshold breach.
- **Nightly job.** A separate `synthetic-load-nightly` scheduled workflow runs a longer scenario (5 minutes, 100 VUs) against staging. Results push to Prometheus with a `run_type=nightly` label so they can be sliced apart from smoke runs in Grafana.
- **k6 binary in dev-tools.** Locally, `make load-smoke` runs the same script the CI job runs. `brew install k6` (macOS) or the Docker image (`grafana/k6`) — Makefile calls out both paths. Binary version pinned in `infra/ci/k6-version`.
- **Prometheus config.** Prometheus's `remote_write` receiver is enabled in the compose stack; k6's `experimental-prometheus-rw` output points at it. The receiver is *not* enabled in production compose stacks — synthetic-load metrics are dev/CI-only, and mixing them with production metrics would pollute the tsdb.
- **Auth fixture.** A `synth_load` tenant + user pair is provisioned in the seeded Keycloak realm JSON (`infra/keycloak/realms/dev-realm.json`), used exclusively by load scripts. Keeping load fixtures out of the `demo` tenant means demo walkthroughs aren't polluted by synthetic traffic.
- **Deferred to Phase 5 / Phase 6.** Drift simulation scripts (`synthetic/drift/`) reuse this harness for programmatically shifted user preferences. A/B fixtures (`synthetic/ab_fixtures/`) reuse it for deterministic tenant+user combos in integration tests.

## Risks

- **Single CI runner can't produce enough load for meaningful measurement.** The 2-vCPU/7GB GitHub Actions runner puts a ceiling on VU concurrency. At 10 VUs for 60 seconds this is a non-issue; if we ever want the smoke test to exercise higher concurrency, the CI job would need a larger runner (paid) or the smoke variant stays deliberately small and the nightly does the heavy lifting. Mitigation: keep smoke small on purpose; the smoke's job is "did we regress the p99 SLO," not "what's the max throughput."
- **k6 binary version drift between local and CI.** A developer running an older k6 locally might get different behavior than CI. Mitigation: `infra/ci/k6-version` file pins the exact release; the Makefile's `load-smoke` target checks the installed version against the pin and fails loud if it drifts. Automated version bumps land as their own PR with the CI run demonstrating no threshold regression.
- **JS scripts as a reader-tax for a Python codebase.** Anyone touching the load scripts has to context-switch from Python to JavaScript. Mitigation: keep scripts small (~50–100 lines), one entry point per surface, use the k6 stdlib rather than clever patterns. If script complexity outgrows this constraint, that's rationale #5's escape-hatch signal.
- **Threshold tuning as an ongoing skill.** Initial thresholds might be wrong — too tight (flakiness) or too loose (miss regressions). Mitigation: baseline thresholds on the actual measured latencies from the first serving PR, then tighten if we observe consistently better performance. Failed CI runs on threshold breach get the same investigation cycle as any other CI failure.
- **k6 Prometheus remote-write cardinality blowup.** k6's default label set includes `expected_response`, `name`, `status`, `method`, `url`, `scenario`, `group`, and per-VU labels. Left unchecked, cardinality explodes and pollutes the Prometheus tsdb. Mitigation: k6 script explicitly names each request (`http.get(url, { tags: { endpoint: '/recommendations' } })`) so `name` collapses to a small set, and the remote-write output config drops the high-cardinality labels (`url`, `scenario`, `group`) via `keep`/`drop` rules.
- **k6 Cloud vs OSS divergence.** k6 Cloud has features (test result comparison, hosted runs, notifications) that don't exist in OSS. Mitigation: this ADR pins OSS-only, and no script relies on Cloud-specific features. If we ever want Cloud, it's an additive change on the same scripts.

## How we'd know we're wrong

- **CI smoke job flakes frequently on unrelated causes.** Would suggest thresholds are too tight for the CI environment's variability, or the smoke's surface is too broad (hitting endpoints whose latency is dominated by cold-cache warmup, not real regression). Fix by loosening thresholds or narrowing scope — not by changing tools.
- **JavaScript-scripted user modeling becomes complex enough that a Python-native harness would have been easier.** Phase 5's drift simulation is where this risk lives — if a "user whose preferences shift over 30 days" is 200 lines of JS state management, that's the escape-hatch signal. Fix by evaluating Locust for `synthetic/drift/` specifically; the load smoke stays k6.
- **Prometheus tsdb grows faster than expected during CI runs.** Would mean cardinality controls in the remote-write config aren't strong enough. Fix by tightening `drop` rules, not by changing tools.
- **The smoke test consistently passes on real serving regressions.** Would mean the smoke's traffic shape doesn't hit the paths where regressions manifest. Fix by expanding scenario coverage — cold-start users, feature-store cache misses, tenants on different champion models.
- **k6 threshold declarations don't cover a class of failure we care about.** E.g. per-endpoint p99 hides a specific endpoint regressing while others compensate. Fix by splitting thresholds per endpoint via `tag`-scoped threshold declarations — a k6-native feature, not a tool change.
