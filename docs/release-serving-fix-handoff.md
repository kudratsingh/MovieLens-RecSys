# Release Serving Fix Handoff

Date: 2026-08-26 (America/Los_Angeles)

This note is the continuation point for closing the two verified MVP release
blockers before deployment. The working branch is
`fix/release-serving-readiness-latency`, based on `main` at `001bbb4`.

## What was fixed

The four model-server Uvicorn processes each allowed LightGBM/OpenMP and BLAS
to create a native thread team sized to the whole host. Under concurrent rank
traffic, process parallelism multiplied by native parallelism, producing a
periodic CPU backlog. That backlog caused both the clean-start learned-serving
timeouts and the low-steal p99 failure from PR #68.

`docker-compose.demo.yml` now sets these values on `model-server`:

```text
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1
```

The four Uvicorn workers, 0.5-second model-server timeout, durable transaction
commit, 55-request/second workload, and p99 < 100 ms gate are unchanged. Do not
replace this fix by raising the timeout, weakening the SLO, or returning success
before the audit/mutation transaction commits. A production deployment must
carry the same native-thread invariant unless a separately measured topology
replaces it.

The cause, evidence, and troubleshooting contract are also recorded in ADR
0010 and `docs/demo-runbook.md` on this branch.

## Verification already completed

The unchanged local gate first reproduced the defect:

- p50 81.57 ms, p95 551.37 ms, p99 903.64 ms;
- 3,249 requests at 50.83 requests/second;
- 52 dropped iterations and seven silent learned fallbacks;
- warm-up: 2,344 ms, 15 first-round fallbacks, 376 ms slowest request;
- 0% CPU steal and no cgroup throttling, so no remeasurement was permitted.

With only the native-thread limits applied, the same gate passed:

- p50 7.24 ms, p95 12.50 ms, p99 48.99 ms;
- 3,300 requests at 54.18 requests/second;
- zero errors, 100% checks, zero dropped iterations, and zero silent fallbacks;
- warm-up: 666 ms, correct learned/fallback policies throughout, 42.2 ms
  slowest request;
- `REMEASURE=no`, `GATE=pass`.

Local ignored evidence is under `artifacts/load-smoke/` for the failing run and
`artifacts/load-smoke-threads1/` for the passing run.

The clean lifecycle was then verified separately with no manual priming:

```text
make demo-up
make demo-seed
make demo-smoke
```

`demo-seed` recreated the feature and model sidecars. The first `demo-smoke`
passed with Action Fan on `item-item-cosine+lightgbm`, Cold Start on
`popularity`, eight Action Fan history rows, eight recommendations for each
persona, and all four personas present. Compose rendering and `git diff --check`
also passed.

## Deliberately unfinished verification

`make web-e2e` was attempted after the clean smoke but did not start Playwright
because this host checkout had no `web/node_modules` (`playwright: command not
found`). A lockfile install was started, reported that local Node 20.10 is below
one dependency's preferred engine range, and was stopped when the owner asked
to conserve usage. No browser result should be claimed from this session.

## Exact next steps

1. Use Node 22 (the frontend container and CI runtime), then run `cd web && npm
   ci`. Install the pinned Chromium binary if Playwright requests it.
2. With the freshly seeded demo stack still running, run `make web-e2e`.
3. Run the proportional static gates: Compose config, frontend lint/typecheck,
   and the focused repository tests. Python/FAISS tests on macOS must be
   serialized with one OMP/BLAS/MKL/VECLIB thread.
4. Commit and push this branch if it was not already preserved, open a
   substantial PR, and let the real `synthetic-load-smoke` CI job validate the
   fix on GitHub's runner.
5. Squash-merge only after every required check is green, delete the feature
   branch, then update PR #68 onto the new `main`. PR #68 is the separate MVP
   release/deployment handoff and was the only open PR at the start of this
   work.
6. After both PRs merge, select the deployment target. Railway remains the
   recommended fastest MVP path; DigitalOcean plus Coolify trades more owner
   operations for a lower fixed cost; AWS remains the scale-ready but heaviest
   option. Production work cannot be finalized until the owner chooses one.

The MVP goal is still active. Do not report it deployed or complete until the
fix is green and merged, the deployment target is selected and implemented,
and production OIDC, tenant-isolation, learned/cold serving, browser journeys,
health, rollback, backup/restore, and production-safe load checks all pass.
