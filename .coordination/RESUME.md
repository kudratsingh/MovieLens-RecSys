# RESUME — read this first when picking the project back up

Written 2026-09-05 23:55 by the orchestrator session at wind-down. Companion files: `STATE.md` (board),
`DECISIONS.md` (owner decisions O-1…O-19), `ORCHESTRATION.md` (protocol), `inbox/*.md` (per-lane queues),
`reports/*.md` (what each lane did, newest first for megatron.md, appended at the bottom for
starscream.md), `HANDOFF-megatron.md`. Everything here is local-only (git info/exclude).

## Where the models stand (all on `main`, all measured through `src/evaluation/`)

| Line | Number of record | Status |
|---|---|---|
| Item-item retrieval | warm recall@500 0.3991 (run `4b342e87…`) | served champion |
| SASRec retrieval (ADR 0016) | warm recall@500 **0.5092** after the O-9 fix (run `528b1451…`); artifact `a11af5ed…` SHA `43320b87…` | retrieval gate `promote`, single-seed regime |
| Per-route bundle (O-7) | SASRec + booster `7e2052c1…` (learned) + incumbent booster `05610e60…` (fallback): overall NDCG@10 **0.2218** vs 0.2008, warm 0.1014 vs 0.0728 | end-to-end gate `promote` both scopes; **not yet served champion** |
| Rung 3 increment 1 (ADR 0018) | +2.78% warm NDCG@10 vs bundle, bar +3% | refused, closed; DIN not built |
| Two-tower v2 corrected (ADR 0006/0015) | 6% pilot 0.3759 vs item-item 0.3587; full run in flight (epoch 1 = 0.4902 on holdout) | pending: gate vs `4b342e87…`, PR from failover F |
| Content cold-item (ADR 0017) | coverage yes, relevance 0.0001 | increment 2 proposal after TMDB coverage |
| Rung 5 mixing (ADR 0019) | PR #174 | **awaiting owner approval** |
| SASRec v2 (ADR 0020) | PR from failover G | awaiting owner approval |

## Serving path state
Manifest v2 (per-route rankers, lineage), generic retriever interface, sidecar loads a SASRec bundle
fail-closed (fast-path fix applied at load), CPU-only torch pins with no-CUDA check, audit provenance
columns (migration 0019), bundle publisher baking fixture + served bundle (production compose selects the
served one), `make promote` / `promote-revert` (proven on the demo stack), demo DB seeded with the full
62,423-movie catalog (#178), k6 gate derives its policy from the bundle (#176), gate records host memory (#170).
Fresh incumbent k6 baseline: **p99 11.06 ms** (control). **W10 (SASRec bundle k6) has not been measured**:
attempt failed on the 120-item catalog (fixed by #178). Champion swap (W11) is owner-authorised but waits
on W10.

## What is in flight at wind-down (2026-09-05 23:55)
- Failover F: two-tower full run → gate → PR (branch `feat/twotower-v2-fulldata`); then stops.
- Failover G: ADR 0020 PR + Q1 stable-sort draft PR (runs pending); then stops.
- Orchestrator: TMDB pull (7 req/s, throttled) → `dvc add` → `tmdb_load` → coverage; #178 shepherd.
- Starscream: final block, stops. Megatron (Codex): silent since ~21:00; Q1/Q2 taken by G.

## How to resume
1. `git fetch --all`; read `STATE.md` work board and this file; check open PRs (`gh pr list`).
2. Owner decisions pending: approve/amend #174 (Rung 5) and the ADR 0020 PR; O-3 GPU (reopened by ADR 0020);
   O-5 seed policy; O-17 (done in draft if G pushed it).
3. Next execution order: merge F's two-tower PR (if gate passes, decide SASRec vs two-tower vs union as the
   retrieval line — that is ADR 0019's question) → Starscream: fresh baseline + W10 on the full-catalog demo
   (promote on the demo stack is measurement setup) → orchestrator executes W11 champion swap with item-item
   rollback (owner authorised) → TMDB coverage numbers feed ADR 0017 increment 2 → Megatron/failover: Q3
   (sequence-valid cold cohort v2), then whatever the owner approves.
4. Restart liveness: heartbeats, staleness monitor, one serial merge shepherd (parallel shepherds thrash).
5. Environment facts: `OMP_NUM_THREADS=1` alone does not fix the torch/faiss OpenMP collision on this Mac;
   `KMP_DUPLICATE_LIB_OK=TRUE` is validated bit-exact (W26); MLflow: incumbent/SASRec runs live in the local
   file store `mlruns/362463800125436511`, not the Docker server; the Docker MLflow server's `/mlartifacts`
   upload is broken from the host; `pgrep -f` matches shell argv (check by executable or artifact);
   TMDB token is in `.env` (gitignored); dev Postgres `ratings` is clean (25,000,095 rows).

## Owner-only items
Hetzner deploy (W16), which now also needs the served payload published (W25) and the promote command in
the runbook; the moderated frontend sessions.

## Flagged by Starscream at wind-down (owner should read first)
- The 13 frontend evidence sets in `docs/frontend/evidence/` describe a `/browse` that no longer exists:
  #178 grew the demo catalog to 62,423 titles and 62,303 of them have no poster, so Browse is sparser.
  Cheap to note now, expensive to rediscover; the evidence README must say which sets are stale.
- `demo-seed` gained ~95 s; whether CI's synthetic-load-smoke and browser-auth-e2e jobs absorb it is
  settled by #178's CI run — if not, that is a W27 cost to decide, not absorb.
- W10's exact command sequence is in Starscream's final block in `reports/starscream.md` (bottom).
- Starscream's queue S2–S6 is untouched; S6 (ONNX memo) is "owner decision required after".

## #178 (full-catalog demo) is RED on CI — first fix on resume (Starscream's lane)
`synthetic-load-smoke` failed in `demo-seed` → `demo-materialize`: Postgres `DiskFull` on
`base/pgsql_tmp` ("No space left on device"). The CI load job runs Postgres's data directory on tmpfs
(`docker-compose.ci-load.yml`, ADR 0010), and the full catalog plus the 499,384-row
`user_item_features` materialization exceeds that tmpfs. Options: raise the tmpfs `size:` in
docker-compose.ci-load.yml (measure the real footprint first), or move `pgsql_tmp` off tmpfs, or keep
the CI job on a reduced catalog while local W10 uses the full one (weaker: CI then measures a
different database than production). This is exactly the "real cost of W27" Starscream said CI would
settle. Do not merge #178 until it is green; W10 depends on it locally, not on CI.

## Findings from failover G at wind-down (ADR 0020 = PR #179, stable-sort draft = PR #180)
- **Megatron was working, invisibly.** Two unpushed local branches existed in its lane
  (`fix/popularity-stable-ties` @ de2d7d0 20:52, `docs/adr-0020-sasrec-v2` @ 8fb4593 21:04, a 188-line
  ADR draft). It committed without pushing or heartbeating, so it looked stopped and its items were
  reassigned. G carried both forward (cherry-pick with attribution into #180; contributions folded into
  #179). **Those two local branches should be dropped, not opened as competing PRs.** Protocol fix:
  a session must push and heartbeat; local commits do not count as liveness.
- **W28 — the SASRec training loop is the real cost, not the model.** `fit` materialises one left-padded
  window per target (19.7M × L tensor) and runs a full encoder forward per target using only the last
  position; canonical SASRec predicts every position of a window in one pass. As written, the ADR 0020
  grid costs 839–1,397 CPU-hours; with all-positions training it is 17–29 hours, and L=200 does not fit
  in memory today. **Rewrite the training loop before any SASRec v2 cell** — this is the single biggest
  modeling-velocity lever and it also changes the GPU calculus (ADR 0020 recommends keeping O-3 skipped
  for reproducibility reasons once the loop is fixed).
- Three of five ADR 0020 capacity cells project at or over the 15 ms encoder budget on amd64 (hidden 256
  clearly over); decide cells with that in view.
- #180 must stay draft until its two reproduction re-runs pass (command in HANDOFF-megatron.md).

## GPU policy (owner decision, 2026-09-06 00:10)
Stay on CPU. Fix the SASRec training loop (W28) first and measure; the ADR 0020 grid is expected to run in
about a day on this Mac, reproducible and free. Trigger for renting a GPU: a predeclared cell that costs more
than one night on the fixed CPU loop (bigger SASRec at L=200, DIN, generative rungs). Then: one on-demand
A10-class instance per experiment, with the non-determinism tolerance written into that rung's ADR before the
first run. Recorded in DECISIONS O-3.

## TMDB pull — done 2026-09-06 00:12 (PR #181)
62,081 requests, 61,234 resolved, 847 not on TMDB, 0 failures, 142 min at 7.1 req/s after 429 throttling.
Snapshot DVC-tracked (md5 da35a30da8b56ff7de01c9f08bb94be1.dir, 436 MB, 34 shards) — **no DVC remote is
configured, so the bytes exist only on this Mac; configure a remote and `dvc push`** (follow-up).
Loaded into the dev Postgres (61,468 movies, 745,593 cast, 580,241 crew, 303,280 keyword links). Coverage:
98.5% of catalog and 98.3% of the 27,962 cold items resolved; 98% of cold items have an overview, 71%
keywords, 95% cast. ADR 0017 increment 2 (content retriever on TMDB text) can now be proposed with real
numbers (Megatron queue Q4). Note: GitHub reports 2 Dependabot alerts on main (1 high, 1 low) — check
`/security/dependabot` on resume.

## Worktree map at wind-down (2026-09-06 00:30) — who owns what
- Orchestrator: `.claude/worktrees/agent-a10b67614115e6def` (F, two-tower run — remove after its PR),
  `agent-a6020dfc870d40b2a` (B, done, locked — remove), `agent-a64b973c3b79133d8` (E, done — remove).
- Starscream (/private/tmp/MovieRecSys-*): manifest2, retriever, sidecar, mem, gatepolicy, guard, promote,
  w23, cmd, w10 (unmerged branch docs/w10-latency-evidence — the runbook port fix, still needed),
  catalog (#178, red on CI), 159bundle and gate (detached; `gate` has 8 dirty files — check before pruning).
  All merged ones are safe to prune; Starscream prunes its own.
- Megatron (Codex, /private/tmp/MovieRecSys-*): sasrec-fastpath and sasrec-verdict (merged), surrogate
  (merged), adr0020 and popularity-ties (superseded by G's #179/#180 — drop), model-planning (old),
  **twotower-artifact** (`feat/twotower-run-artifact`, 16:14 "persist immutable model artifacts" — check
  whether pushed/merged; may be valuable), **synth-cold-v2** (`feat/synth-cold-v2`, 5 uncommitted files —
  Megatron was working on queue item Q3, the sequence-valid cold cohort, at wind-down; it must push and
  heartbeat on resume, otherwise this is invisible work again).
  Push state checked 00:32: none of Megatron's four branches (twotower-run-artifact, synth-cold-v2,
  adr-0020-sasrec-v2, popularity-stable-ties) exist on origin. `feat/twotower-run-artifact` holds one
  unpushed commit (339ff60 "persist immutable model artifacts") — review before F's two-tower PR merges,
  it may be the artifact-export piece W19 needs. `synth-cold-v2` dirty files: src/training/sasrec_ranker.py,
  synthetic/cold_start/generator.py, + new sequence.py, synthetic_cold_v2_recheck.py, tests. First action on
  resume for Megatron: commit, push, heartbeat.

## Two-tower v2 corrected, full data — DONE 2026-09-06 01:09 (failover F; PR pending)
Seed 42, exact FAISS, 16,384 sampled negatives, hard negatives + item features, 3 epochs, under
`OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE`; same split/population as SASRec (1,931 warm / 710 cold).
Warm recall@500 **0.5113** (item-item 0.3991 → +28.1%; SASRec post-O-9 0.5092 → +0.4%), warm NDCG@500
0.1856; cold 0.5263 (identical routing); overall 0.5153. Per-epoch warm: 0.4902 / 0.5058 / 0.5113.
Wall-clock 1h54m39s, MLflow run `2d7f1a49…`, protocol hash `sha256:b4ed5afa…` (same as incumbent and SASRec).
**Retrieval gate: PROMOTE** — warm +28.14% with one-sided 95% lower bound +25.28%; cold +0.00%; overall +18.95%;
both tolerances 0.0, single-seed regime, i.e. judged exactly as SASRec was. Record is **PR #182** (docs only:
results.md, gate JSON, roadmap Rung 1 row, ADR 0006/0015 notes incl. the OpenMP condition), merging on green.
**Implication for the retrieval line:** SASRec and the corrected two-tower are now within 0.4% of each
other at retrieval; the union (ADR 0019 / PR #174, Rung 5) is the natural next question, and the paired
ranker step for the two-tower has NOT been run (not authorised tonight). The old Rung 1 closure was a
measurement of a bug.

## Final state at stop (2026-09-06 ~01:20)
Nothing is running. Open PRs: #174 (ADR 0019 Rung 5, owner approves), #179 (ADR 0020 SASRec v2, owner
approves), #180 (draft, stable sort, re-runs pending), #178 (full-catalog demo, RED on CI tmpfs — fix first),
#182 (two-tower record, merging on green). Docker may be stopped by the owner. Resume order is in
"How to resume" above; W28 (loop rewrite) is the first training item, W10 → W11 (champion swap) the first
serving item, and the retrieval line decision (SASRec vs two-tower vs union) is now the owner's, informed by
#174.

## Cleanup note (01:40)
Five orphaned waiter shells from the increment-1 agent were found and killed at stop; they polled with
`pgrep -f` for a name present in their own argv and could never exit. Any future waiter must poll by
executable name or by an artifact. All monitors, shepherds, agent worktrees and stacks are down; the
dev Docker stack may be off. PR #182 merged; open PRs are #174, #178 (red), #179, #180 (draft).

## Session 2 — 2026-09-06 morning, single-worker mode (Megatron only), stopped ~12:00 when Megatron ran out of usage
Done: M1 (both stranded branches pushed: `feat/synth-cold-v2`, `feat/twotower-run-artifact`; superseded
locals dropped); M2 partial (#178: tmpfs DiskFull fixed, but `demo-load-pages` now aborts — k6 exit 107,
"no usable mutation pool", the page profile's pool comes from a bounded catalog walk that no longer reaches
the personas' titles at 62k; fix direction is in inbox/megatron.md at the top of the QUEUE); M3 done and
**#180 merged** (deterministic popularity ties; O-21 approved: SASRec cold NDCG@500 moved −5.2e-6, new record
run `02438649…`, prior `528b1451…` superseded). Open PRs: #178 (red, M2 follow-up), #174, #179 (owner approval).
M4 (W28 loop rewrite) NOT started. Next on resume for Megatron: finish M2 → M4 → M5 …, as listed in the inbox
QUEUE. Completion-signal protocol: `TASK DONE / BLOCKED / OWNER DECISION` lines; the orchestrator's watcher
must be restarted on resume (`Monitor` on those prefixes, 10-min poll).

## 2026-09-10 resume housekeeping
/private/tmp worktrees were purged by macOS; dead directories removed; all branches verified on origin; `feat/sasrec-all-positions` (Megatron's started M4, commit 'train all causal positions') pushed by the orchestrator. Worktrees now live under `~/worktrees/`. 19 merged local branches deleted; main checkout on `main` @ 0933834.

## Session 3 — 2026-09-11, stopped ~18:15 (orchestrator monitor off)
Merged: #178 (full-catalog demo + load-profile mutation pool fix). Open: #183 (all-positions training
control, opt-in objective; CI red on three mypy attr-defined re-export errors — fix at top of Megatron's
inbox), #174 and #179 (owner approval). Decided: O-22 — keep the copied-prefix objective of record; rewrite
only its data path (M4b, memory-bounded streaming/gather, L=200); all-positions stays a named ablation
(−4.62% warm at full scale, 9.8x faster). Next for Megatron, in order: #183 mypy fix → M5 (W10 latency pair
on the full-catalog demo; procedure in Starscream's final block) → M4b code+equality test → M4b's pilot and
full run → M6 (synth cold v2, branch pushed) → M7 (ADR 0017 inc. 2 proposal) → M8 (DVC remote, owner picks)
→ M9 (Rung 5 after #174). Orchestrator on resume: re-arm the `TASK DONE/BLOCKED/OWNER DECISION` watcher,
merge on green, execute W11 (champion swap) after M5 passes. Worktrees live under `~/worktrees/` now.

## 2026-09-15
Status ledger + CLAUDE.md brought to the 5–11 Sept reading (PR #184). `.coordination/` is now TRACKED in git
(committed on the same PR) so any clone can resume from it; keep it free of secrets (the TMDB token lives
only in the gitignored `.env`). Dependabot: 11 alerts on main (4 critical) — queued as M2b for Megatron.
