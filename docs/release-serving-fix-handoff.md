# Release Serving Fix Handoff

Date: 2026-08-26 (America/Los_Angeles)

This note is the continuation point for closing the two verified MVP release
blockers before deployment. The working branch is
`fix/release-serving-readiness-latency`, based on `main` at `001bbb4`.

## What was fixed

The four model-server Uvicorn processes each allowed LightGBM/OpenMP and BLAS
to create a native thread team sized to the whole host. Under concurrent rank
traffic, process parallelism multiplied by native parallelism, producing a
periodic CPU backlog. That backlog is what the local reproduction measured and
what the clean-start learned-serving timeouts came from. It is **not** what
fails the gate on GitHub's runner — see "What CI showed" below.

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

## What CI showed

With the pins on the branch the `synthetic-load-smoke` job failed twice more
(runs 33031225997 and 33031336242): p99 198.97 ms and 164.14 ms, 6 and 8
dropped iterations, zero errors, zero silent fallbacks, 0% CPU steal, run queue
1–2. The artifacts, read next to PR #68's failure and the two passing runs on
the same commit range, say the runner's tail is not the model server's:

- cold traffic (popularity fallback; never touches model-server or
  feature-server) breaches exactly like warm traffic — cold p99 174.61 ms vs
  warm 146.37 ms on the second run, 429.60 vs 417.62 on PR #68;
- failing runs have a *faster* median (8.4–9.2 ms) than passing runs
  (10.6–11.3 ms, p99 15–19 ms, no second over 100 ms) — different runner
  hardware, same code;
- all of the time is `http_req_waiting`; nothing in connect/blocked.

Every request shares auth → pgBouncer → the RLS request transaction → the audit
insert → a synchronous commit (one `fdatasync`). The working hypothesis is
storage under the Postgres volume on some runner VMs. Do not rerun the gate to
fish for a green, and do not touch thresholds or durability.

The gate now records the evidence to settle it (this branch, ADR 0010's second
2026-08-26 note): `server-side.json` (audit-row handler latency vs k6, per
traffic class and policy; WAL/IO counter deltas; `track_io_timing` and
`track_wal_io_timing` on in `docker-compose.yml`), a per-second `srv_p99`
column, and an `fdatasync` baseline burst on Postgres's volume
(`disk-fsync.jsonl`). Continuous fsync sampling is opt-in
(`LOAD_FSYNC_PROBE=on`) because it perturbs the gate. The decision rule is
unchanged.

## Exact next steps

1. Read the instrumented `synthetic-load-smoke` artifact from PR #69's next
   run: `breakdown.txt`'s "server-side vs k6" block and the `srv_p99` column in
   the slowest seconds. Handler fast while the request is slow, on a runner
   whose handler percentiles match the passing runs, means the runner's
   storage; handler slow means the service.
2. Decide from that evidence, in ADR 0010, between a storage-stall
   measurement-validity rule beside the steal rule and taking the runner's disk
   out of the measurement. Neither changes a threshold or the commit-before-
   response contract.
3. Browser journeys are covered by the `browser-auth-e2e` job on the same
   commit (green on both PR #69 runs); a local `make web-e2e` needs Node 22 and
   `cd web && npm ci` first.
4. Squash-merge only after every required check is green, delete the feature
   branch. PR #68 has already merged; the deployment work continues on
   `feat/production-deployment` from the reviewed plan.
5. After both PRs merge, select the deployment target. Railway remains the
   recommended fastest MVP path; DigitalOcean plus Coolify trades more owner
   operations for a lower fixed cost; AWS remains the scale-ready but heaviest
   option. Production work cannot be finalized until the owner chooses one.

The MVP goal is still active. Do not report it deployed or complete until the
fix is green and merged, the deployment target is selected and implemented,
and production OIDC, tenant-isolation, learned/cold serving, browser journeys,
health, rollback, backup/restore, and production-safe load checks all pass.
