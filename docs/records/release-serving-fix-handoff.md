# Release Serving Fix Handoff

> **Record.** Accurate as of 2026-08-27. Closed by PR #69; the substance now
> lives in [ADR 0010](../adr/0010-synthetic-load-k6.md)'s 2026-08-26 notes.
> Not maintained.

Date: 2026-08-27 (America/Los_Angeles)

**Status: closed.** The work this note handed off merged as PR #69
(`fix(serving): pin model native parallelism and instrument the load gate's
shared path`). It is kept because one question it opened is still open, and
because the deployment carries the invariant it established.

## What shipped

The four model-server Uvicorn processes each allowed LightGBM/OpenMP and BLAS to
size a native thread team to the whole host, so process parallelism multiplied by
native parallelism into a periodic CPU backlog. `docker-compose.demo.yml` now
sets `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and
`VECLIB_MAXIMUM_THREADS` to `1` on `model-server`.

The unchanged local gate reproduced the defect at p50 81.57 ms / p95 551.37 ms /
p99 903.64 ms with seven silent learned fallbacks, and passed with only those
four values changed at p50 7.24 ms / p95 12.50 ms / p99 48.99 ms, zero errors and
zero fallbacks. The four Uvicorn workers, the 0.5-second model-server timeout,
the commit-before-response contract, the 55-request/second workload and the
p99 < 100 ms threshold are all unchanged. **A production deployment carries the
same native-thread invariant** unless a separately measured topology replaces it,
and it is baked into `infra/features/Dockerfile` rather than set per environment —
so no deployment depends on anyone remembering it.

The production-mode rehearsal (2026-08-27) re-ran the pinned gate against the
production images with baked artifacts and `ENVIRONMENT=production`, and it passed
at p50 6.85 ms, p95 9.47 ms, p99 12.93 ms with zero errors and zero dropped
iterations. That is the number the deployment inherits.

The gate also gained the instrumentation that distinguishes a slow service from a
slow runner — `server-side.json` (audit-row handler latency beside k6's, per
traffic class and policy; WAL and IO counter deltas), a per-second `srv_p99`
column next to `steal%`, and an `fdatasync` baseline burst on Postgres's volume
(`disk-fsync.jsonl`). Continuous fsync sampling stays opt-in
(`LOAD_FSYNC_PROBE=on`) because it measurably perturbs the service it observes.

## What is still open

**The runner-storage question has no verdict yet.** With the pins on the branch
the `synthetic-load-smoke` job still failed twice (p99 198.97 ms and 164.14 ms,
0% CPU steal), and the artifacts across five runs say the tail on those runners
is not the ranker's: cold traffic, which never reaches the model server, breached
as hard as warm traffic, the median was *faster* on the failing runs than on the
passing ones, and all of the time was `http_req_waiting`. What every request
shares is auth → pgBouncer → the RLS request transaction → the audit insert → one
synchronous commit. ADR 0010's 2026-08-26 note records this and states the
decision rule: whether it earns a storage-stall validity rule beside the CPU-steal
rule, or means taking the runner's disk out of the measurement, **is decided from
the first instrumented runner failure, not from a laptop.** Until then the rule is
unchanged — a low-steal breach is not re-measured, thresholds do not move, and the
gate is not re-run to fish for a green.

## Where the work went next

The deployment work continues on `feat/production-deployment`, which now targets
a single Hetzner CX22 (ADR 0013, rewritten). Three things from this note are
carried there rather than repeated:

- the cold-worker readiness defect described in
  `docs/records/mvp-release-deployment-handoff.md` is closed by warming each model-server
  worker inside `lifespan`, with `/healthz` answering 503 until warm;
- the native-thread pins are part of the production image rather than only the
  demo Compose file;
- ADR 0013 and `docs/deployment-runbook.md` record that
  `synthetic/load/thresholds.js` in CI remains the SLO's only authority, and that
  the production canary is a deliberately lower-authority instrument that records
  p99 with no verdict — doubly so now that k6 runs on the same two vCPUs as the
  service it measures.

A local `make web-e2e` still needs Node 22 and `cd web && npm ci` first; the
`browser-auth-e2e` CI job is what covers the browser journeys.
