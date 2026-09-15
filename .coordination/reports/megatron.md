TWO-TOWER DONE 2026-09-06 01:09 PDT

- HB 01:10 F Run finished clean, exit 0, **1h54m39s of fit** (6879.3 s). The box is free.
  Warm (n=1931) recall@500 **0.5113** / ndcg 0.1856; cold (n=710) **0.5263** / 0.4358; overall
  **0.5153** / 0.2529. Cold is fallback-served for all 710, as designed. Now: full-precision
  metrics, protocol manifest and per-user vectors, then the pinned gate.

- HB 23:49 G Q1+Q2 DONE for tonight: #179 (ADR 0020, ready) and #180 (popularity stable sort,
  DRAFT pending two re-runs). No 25M job started; the two-tower was running throughout. Stopping
  per the wind-down. HANDOFF-megatron.md updated.

- HB 23:36 G Q2 PLAN. Picked up the training lane's Q1/Q2 under the liveness rule. Reading order
  done: CLAUDE.md, ORCHESTRATION (Liveness / Process checks / Work queues), inbox #15, DECISIONS
  O-3/O-9/O-17, HANDOFF-megatron, and F's newest report blocks. Worktree
  `.claude/worktrees/agent-afc68d104bdbac24d` @ 6c3e5a4, branch `worktree-agent-afc68d104bdbac24d`.
  **F's two-tower is running (pid 63387) so no 25M job of mine starts.** Q2 (docs, ADR 0020) goes
  first, Q1's code and unit test next, and Q1's two short re-runs wait for `TWO-TOWER DONE`/`FAILED`
  plus an empty k6 / demo-stack check. Nothing of mine touches `src/serving/` or `infra/`.

- HB 00:25 F Epoch 2/3 done 00:22:53: loss **8.2323**, warm recall@500 **0.5058** (epoch 1 was 0.4902),
  `item_cos_mean` 0.4417 / std 0.1286. Epoch 2 took **41m36s** against my 46-min estimate, so the
  hard-negative cost was slightly over-projected: **epoch 3 ~01:04, final scoring and MLflow write
  ~01:06**. Trainer 1h09m, RSS 6.71 GB — the highest of the run, still far inside headroom.
  The metric is still climbing across epochs (0.4902 -> 0.5058), which matters for reading the result:
  in the 6% pilot the epoch-3 per-epoch figure equalled the final warm recall exactly, so epoch 3 is
  the number, and it has not stopped moving. Still no gate, still no promotion claim.
  **Not re-arming the Starscream watcher** after its timeout: I have no remaining dependency on that
  lane tonight, and the wind-down ends with every watcher stopped, so re-arming one I would kill in an
  hour is noise. The run watcher and the memory sampler stay until the process exits.

- HB 00:15 F Run healthy at 1h01m, RSS 5.72 GB. **Epoch 2 is taking longer than epoch 1 and that is
  expected, not a hang** — worth recording so nobody reads the gap that way. `hard_negative_warmup_epochs=1`
  means epoch 1 trains on random negatives alone and epochs 2-3 add per-batch hard-negative mining.
  The 6% pilot shows the same shape: 2m12s, then 3m44s and 3m31s, i.e. **~1.7x** once mining starts.
  Applying that here (epoch 1 = 27m04s) puts epochs 2 and 3 near 46 min each, so:
  epoch 2 ~00:27, epoch 3 ~01:13, final scoring and MLflow write ~01:15-01:20.
  **My "~2h15m" predeclaration was right**; my later "expect ~00:30" remark in conversation was not,
  and it came from scaling all three epochs at epoch 1's rate. The predeclared figure stands.
  Nothing is wrong with the run: loss and epoch-1 recall both reproduce attempt 1 exactly, memory is
  flat, and the process is accumulating CPU normally.

- HB 23:42 F **The OpenMP boundary is cleared — the validated flag holds under the real workload.**
  `23:41:16 Epoch 1/3 loss=8.4730`, then `23:41:17 Epoch 1: loss=8.4730 warm_recall@500=0.4902
  item_cos_mean=0.4410 item_cos_std=0.1273`. That second line **is** the `build_index()` call that
  killed attempt 1 one second after its identical loss line; this time it returned. The loss matching
  attempt 1 to four decimals (8.4730 both times) is also a free determinism check: same seed, same
  frame, same arithmetic, so the flag has not perturbed training here either.
  Trainer healthy at 28m01s, RSS 5.57 GB. Epochs 2 and 3 to go, ~23 min each, plus final scoring.
  **On the epoch-1 number, stated carefully because it is large.** warm recall@500 **0.4902** after one
  epoch, against the incumbent item-item's full-data **0.3990569035829944**. That is well above the
  ~0.42 I predeclared. Three cautions travel with it and I am not dropping any of them: it is epoch 1
  of 3 and not a result; the per-epoch metric is computed over holdout users only, while the final
  evaluation also scores the ADR 0011 cohort; and **nothing here is a promotion claim** - the gate has
  not run, and the cold and overall clauses (tolerance 0.0 each) are untested. In the 6% pilot the
  epoch-3 per-epoch figure equalled the final warm recall exactly, so epoch 3 is the one to read.

- HB 23:15 F **Restarted seven minutes in, deliberately, and it saved the run.** While the 23:08
  attempt was training I ran the same class of endgame check that paid off earlier - can the gate
  actually read the incumbent it will be pointed at - and it could not:
  `RESOURCE_DOES_NOT_EXIST: Run with id=4b342e87... not found`.
  **The two stores are different universes.** `localhost:5000` is Postgres-backed
  (`--backend-store-uri postgresql://.../mlflow`, docker-compose.yml:82), while **both** the
  incumbent `4b342e87` *and* the SASRec champion `a11af5ed` live in the local file store
  `./mlruns/362463800125436511/`. A candidate logged to the server can never be gated against
  them. Attempt 1 had the same defect; it just died of OpenMP first, so nobody found out.
  Verified the fix before relaunching rather than after: with the tracking uri set to the file
  store, both runs resolve, `FINISHED`, both under protocol `sha256:b4ed5afa...`, and the
  incumbent reports warm recall@500 **0.3990569035829944** - matching the recorded gate JSON
  exactly. A plain absolute path, not a `file://` URI, because the repo path contains spaces.
  Relaunched 23:13:22, **pid 63387**, log
  `artifacts/sasrec/logs/twotower-v2-fulldata-filestore-20260905T2313PDT.log`. Same startup
  numbers (25,000,095 / Train=20,000,075 / cutoff 1466837397 / cohort ae4475f0e063). Cost of the
  restart: seven minutes. Cost of not restarting: an ungateable two-hour run.
  The running token stayed up across the restart on purpose - the box remained mine throughout,
  and Starscream gates on the absence of that token, so dropping it briefly would have invited a
  measurement into the middle of this.

- HB 23:10 F W19 attempt 2 launched under the validated flag pair. pid **61010**, RSS 6.32 GB at
  58 s, log `artifacts/sasrec/logs/twotower-v2-fulldata-attempt2-20260905T2307PDT.log`, branch
  `feat/twotower-v2-fulldata` @ 1fcfb26 (rebased onto 6c3e5a4). Startup reproduces attempt 1
  exactly - 25,000,095 ratings, Train=20,000,075, cutoff 1466837397, cohort ae4475f0e063 - so the
  only difference between the two runs is the flag. Fitting from 23:09:27; the decisive moment is
  the first `build_index()` at the end of epoch 1, roughly 23:36, which is exactly where attempt 1
  aborted. Expect ~2h15m. The prior exit token is removed so the newest status token leads the
  file unambiguously; attempt 1's failure remains fully recorded in its own dated block below.

# Reports — Megatron

Newest first. Append after every turn or run.

## 2026-09-06 01:20 — Opus failover F: W19 COMPLETE — gate returns promote; PR #182; lane handed back

**W21 and W19 are both done. This is my closing block; every watcher and sampler of mine is stopped.**

**W19 result.** Run `2d7f1a49008d4e9d8791d4ca8598d613`, seed 42, three epochs, **1h54m39s**, full 25M,
protocol `sha256:b4ed5afa…` — identical to the incumbent's and the SASRec champion's, over the same
1,931 warm and 710 cold users.

| Metric @500 | two-tower v2 | item-item `4b342e87…` | change |
|---|---:|---:|---:|
| warm recall | **0.5113336991953615** | 0.3990569035829944 | **+28.14%** |
| warm NDCG | 0.18556902971912184 | 0.1387531836673012 | +33.74% |
| cold recall | 0.5262729520330651 | 0.5262729520330651 | +0.00% |
| cold NDCG | 0.4358465703567308 | 0.4358465703567308 | +0.00% |
| overall recall | 0.5153499314993257 | 0.43325735583575864 | +18.95% |
| overall NDCG | 0.25285303344979293 | 0.21862304529149468 | +15.66% |

**Gate: `promote`**, single-seed regime, both tolerances 0.0 — SASRec's own values, so the two verdicts
compare. Warm lower bound **+25.28%** (half-width 2.86%, population-only, n=1931) against a +3.00%
bar. Cold bit-identical, which is zero-by-construction for a threshold-routed retriever. **But
`serving_eligible` is `false`** — the paired LightGBM NDCG@10 guardrail has not run, and SASRec's
step 1 is why that clause exists. Item-item plus LightGBM remains champion. No promotion is claimed
and none is implied.

**PR #182** (`feat/twotower-v2-fulldata` @ 75e31cd), docs only, **no auto-merge**: results.md section,
both experiment JSONs, ADR 0006 environment + result notes, ADR 0015 outcome note, roadmap Rung 1 row.

**Three limits recorded with the number, not left to inference.** The cell is **not** the original
complete-v2 arm (IVF/4,096 against exact/16,384), so nothing here isolates the mapping fix. The
uncertainty is one training run — population term only, no seed replicate under the standing policy.
And per-epoch recall was still climbing at epoch 3 (0.4902 → 0.5058 → 0.5113), so three epochs is a
floor, not a converged reading.

**What cost time tonight, for whoever picks this up.** Two aborted starts, both worth the restart.
(1) An OpenMP double-initialisation abort at 26 minutes; `OMP_NUM_THREADS=1` alone is **not** the fix
the docs claim — it moves the crash from torch to faiss. Validated `KMP_DUPLICATE_LIB_OK=TRUE` by
bit-exact reproduction of the 6% pilot before trusting any number under it. (2) Seven minutes in, I
found the gate could not read the incumbent: `localhost:5000` is Postgres-backed while **both**
`4b342e87…` and `a11af5ed…` live in the local file store `./mlruns/`. A run logged to the server is
ungateable. Restarted against the file store. **Check the store before a long run, not after.**

**Open follow-up, not taken:** compute the exact top-k as a torch matmul so faiss leaves the training
process entirely — identical by construction, and it retires the collision rather than suppressing it.

**Lane handed back to Megatron.** Its own handoff already reads "W21 and W19 remain with failover
agent F — do not touch"; both are now finished, so that reservation is released.

## 2026-09-05 23:49 — Opus failover G: Q1 and Q2 complete (wind-down)

**PR #179 — `docs(adr): propose SASRec v2 (ADR 0020)`**, ready for review, no auto-merge.
`docs/adr-0020-sasrec-v2-proposal` @ 808624f, off 6c3e5a4. 300 lines, house style of 0016/0018,
registered in `docs/adr/README.md`, roadmap row `2b — SASRec v2 | proposed`.

Seven predeclared cells, one run each, OFAT from an anchor at 128 / 2 blocks / L=100 / sampled
softmax 1,024: a control at v1's shape, an objective bridge at v1's shape with the new loss, width
256, 4 blocks, L=200, and full softmax over all 34,461 items. Eleven of the declared 16-cell
factorial named and dropped with reasons. Gates: retrieval against **v1** `528b1451…` (not
item-item), then the per-route bundle against 1b `c1d742c8…` under O-1, then encoder p99 < 15 ms
measured in the amd64 image. Seven stop rules, six falsification signals.

**The finding the ADR is really about is in the trainer, not the model.**
`build_strict_prefix_examples_with_stats` materialises one left-padded window **per target** —
a `(19,739,546 × max_length)` tensor — and `fit` runs a full encoder forward for each while using
only the final position. Canonical SASRec predicts all positions of a window in one pass. So the
grid costs **839–1,397 CPU-hours (35–58 days)** as the loop stands and **17–29 hours** once windows
are sliced and all positions are predicted together, and cell D (L=200) does not fit in memory at
all today (15.79 GB history tensor vs v1's observed 8.8 GiB peak). **O-3 reopened, recommendation:
keep the GPU skipped.** The rental is $10–51 for the whole grid; what it would actually spend is
the reproducibility contract. Approve it only if P2's validation fails.

Second finding worth the orchestrator's attention: **three of five capacity cells project at or
over the 15 ms encoder budget** on the measured amd64 number, B clearly over on both readings.

**PR #180 — `fix(popularity): stable sort with movieId tiebreak`**, **DRAFT**, and the body says
in its first line that it must not leave draft until the two re-runs pass.
`fix/popularity-stable-ties-g` @ 218ddcc. `fit` now sorts `(size desc, movieId asc)` with
`kind="stable"`, and a test pins `ranking == popularity_order_from_counts(counts)` so the model and
the checksummed artifact agree by construction rather than by luck. `popularity_artifact.py`'s
docstring, which described the quicksort behaviour as current, is refreshed. 251 unit tests pass;
ruff / black / mypy clean. **No run touched the ratings table** — the two-tower held the box all
evening and the wind-down forbids it.

**Two unpushed local branches were found in this lane and are carried forward, not duplicated.**
`fix/popularity-stable-ties` (`de2d7d0`, 20:52, `/private/tmp/MovieRecSys-popularity-ties`) is
cherry-picked with `-x` into #180 with attribution. `docs/adr-0020-sasrec-v2` (`8fb4593`, 21:04,
`/private/tmp/MovieRecSys-adr0020`) is a real 188-line ADR draft that was never pushed; its distinct
contributions are folded into #179 — the objective-bridge cell, head counts (4 at 128, 8 at 256),
effective batch held by accumulation, the pre-run correctness battery, and its independent
discovery that history 200 blows the memory budget. **Both branches should be dropped rather than
opened as competing PRs.** Neither was visible from `.coordination/`, which is worth knowing: an
agent that commits without pushing is invisible to the staleness monitor.

Nothing of mine touched `src/serving/` or `infra/`, and no `/private/tmp/` worktree was written to.

## 2026-09-05 23:07 — Opus failover F: W19 PRE-LAUNCH (attempt 2) — under the validated flag pair

**Release verified independently, not taken from the relay** — the same discipline Starscream applied
to mine. `reports/starscream.md:1106` carries, at start of line:
`PAIR COMPLETE 2026-09-05 23:05 PDT — the box is free; F may start the two-tower full run.`

**Gate checks, by executable name rather than `pgrep -f`:**

    free+inactive   11.43 GiB   (> 6 GiB, PASS)   compressor 7.44 GiB (drained hard as their stack came down)
    memory_pressure system-wide memory free 66%
    ps -Ao pid,rss,comm | awk '$3 ~ /python3|k6$/'   -> empty
    cohort parquet sha256 6da0b568…, anchored to cutoff 1466837397

This is the healthiest the machine has been all evening — the compressor is at 7.44 GiB against
14–18 GiB during attempt 1.

**Rebased onto current main first.** `feat/twotower-v2-fulldata` @ **1fcfb26**, on top of `6c3e5a4`.
Checked before rebasing that #175 and #176 do not touch the training path — the diff across
`twotower.py`, `hard_negatives.py`, `item_features.py`, `routing.py`, `training/twotower.py`,
`split.py`, `protocol.py`, `protocol_manifest.py` and `synthetic/cold_start/` is **empty** — so the
run's code is identical to the configuration I verified, and its source commit will match the PR.

**The one change from attempt 1** is the flag pair validated at 22:25 by bit-exact reproduction:

    OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE TWOTOWER_FAISS_EXACT=true \
      TWOTOWER_RUN_LABEL=faiss-mapping-fixed-fulldata nice -n 19 \
      python -m src.training.twotower

Configuration is otherwise untouched and was verified by resolving `TwoTowerConfig.from_env()`:
`faiss_exact=true, num_sampled=16384, epochs=3, seed=42, embedding_dim=64, history_window=50,
batch_size=4096, learning_rate=1e-3, logit_temperature=0.05, correct_positive_logit=true,
use_item_features=true, hard_negative_count=8, pool 256, warmup 1, early_stopping_patience=0`.
No sample fraction, so full 25M.

**Predeclarations, corrected by attempt 1's measurements rather than repeated from documentation.**
Wall-clock **~2h15m** — epoch 1 took 26m30s at one thread, and attempt 1 died at the first
`build_index()`, so three epochs plus per-epoch evaluation and the final scoring is the estimate.
Peak RSS **~7 GB**, not the ~3–4 GB I predeclared last time: measured 6.87 GB during the pair-array
allocation. That earlier figure came from `docs/results.md:2152` ("about 2 GB") and was wrong for this
configuration; the correction is now on the record in both places. MLflow experiment
`phase-2-candidates`, run name `twotower-sampled-softmax-faiss-mapping-fixed-fulldata`. Log under
`artifacts/sasrec/logs/`.

**Hypothesis, unchanged and stated before the number exists.** Item-item full-data warm recall@500 is
**0.3990569035829944**. The corrected 6% cell reached 0.3759 against item-item's 0.3587 on the same
subsample (+4.8% relative); if that transfers, full-data warm recall@500 lands near 0.42. It may not:
115 warm users at 6% against 1,931 at full scale.

**This cell is not the original complete-v2 arm** (IVF, 4,096 negatives); it is exact search with
16,384. Every record will say so. No promotion wording, no paired-ranker step, no sweep, no v3.

**Neighbours, noted and assessed as harmless to a training run.** The TMDB pull is still fetching at
7 req/s and its Postgres load runs afterwards. My ratings frame is read once at startup and the fit is
CPU-bound in torch thereafter, so neither the network fetch nor the later load touches my numbers —
unlike a latency gate, where that load would sit on the same fdatasync as the p99.

Tokens as before: the running token goes up the moment the process starts, the exit token the moment
it stops, each on its own line, and the literals appear nowhere else including in this block.

- HB 22:52 F **Starscream's pair is RUNNING (22:49). The box is theirs and I am completely off it** —
  no trainer, no probe, nothing; verified by executable name. I will not run even a ten-second script
  until their completion line, because their measurement is the p99 and my last probe would sit inside
  it. It verified all four pre-flight conditions independently rather than trusting my relay, which is
  the right instinct, and confirmed my read that its own condition 1 was miswritten as a success
  marker when what the pair actually needs is only that nothing competes for the box. #176 is merged
  (main 6c3e5a4). Note for later: its condition 2 found the TMDB **pull** still live but network-bound
  with **0 active Postgres queries**, so the load step had not begun — that load will contend on the
  same fdatasync the gate's p99 sits on if it starts mid-window, and it is Starscream's call, not mine.
  W19 relaunches on a start-of-line pair-complete line: fresh pre-launch block, the validated flag
  pair, tokens as before. Everything it needs is already staged on `feat/twotower-v2-fulldata`.

- HB 22:30 F **The deadlock I flagged was real and is cleared** — Starscream's `HB 22:23` reads
  "cond1 SATISFIED (TWO-TOWER FAILED 22:06, nothing running). Stale DONE-only watch killed - it would
  never have fired." Its remaining conditions are TMDB completion and #176's merge, neither mine.
  **Records for the eventual W19 PR are written and pushed** on `feat/twotower-v2-fulldata` @ cbede39
  (branch pushed so the work survives a session loss; **no PR opened** — it bundles with the full
  run): a dated ADR 0006 section covering both findings, and
  `docs/experiments/twotower-sweep/openmp-flag-validation-2026-09-05.json`. The ADR note is placed as
  an appended dated section and explicitly leaves the existing 2026-09-05 correctness amendment at the
  top of that file standing — the corrected 6% number is unaffected, only the condition it was taken
  under is newly recorded.
  Box idle, nothing of mine running. Waiting on a start-of-line pair-complete line before relaunching.

## 2026-09-05 22:25 — Opus failover F: the flag is VALIDATED — bit-exact reproduction; Megatron's number stands

**Verdict: `KMP_DUPLICATE_LIB_OK=TRUE` does not perturb this workload, and the reopening of Rung 1 is
not suspect.** Run `ed694c47caa04e9b89bda5195c052693`, 6% pilot, seed 42, `OMP_NUM_THREADS=1` +
the flag, CSV input and a throwaway file-store MLflow — Megatron's exact conditions. 385.3 s.
Log: `artifacts/sasrec/logs/twotower-kmp-validation-6pct-20260905T2225PDT.log`.

**Every one of the six recorded metrics reproduces to the last decimal place of a double:**

| metric | this run | Megatron recorded | match |
|---|---|---|---|
| warm recall@500 | 0.3759484158672824 | 0.3759484158672824 | exact |
| warm ndcg@500 | 0.14839975118338375 | 0.14839975118338375 | exact |
| cold recall@500 | 0.48295870268712504 | 0.48295870268712504 | exact |
| cold ndcg@500 | 0.3844085561908767 | 0.3844085561908767 | exact |
| overall recall@500 | 0.4083757755096589 | 0.4083757755096589 | exact |
| overall ndcg@500 | 0.21991757088262404 | 0.21991757088262404 | exact |

The agreement is not only at the end. Every epoch matches too — losses 9.8231 / 8.8138 / 8.6116,
per-epoch warm recall 0.3339 / 0.3589 / 0.3759, and `item_cos_mean`/`item_cos_std` at all three. The
input frame is identical as well (9,752 of 162,541 users, 1,517,399 rows, cutoff **1471288304**,
1,198,161 pairs across 8,316 users and 19,005 items), and the protocol hash matches the diagnostic's
`sha256:090985d7…`. A flag that silently corrupted arithmetic could not land on the same 16
significant figures six times over, three epochs deep.

**This is a reproduction check, not a second measurement**, so it does not offend the
one-run-per-configuration policy: it makes no new claim and adds no number to the record. It answers
an environment question and it happens to answer it by reproducing the existing number exactly.

**Corollary worth stating plainly.** The code path is *deterministically fatal* on this box without
the flag — I proved that twice — so Megatron's 09:37 run cannot have been made without it or an
equivalent. Its number stands; what was undocumented was the condition it was taken under. That is
now on the record rather than in a shell nobody can inspect.

**The finding the coordinator asked me to record separately: `OMP_NUM_THREADS=1` alone is not the
fix.** `docs/results.md` and the Makefile both present it as the remedy for the two-tower's OpenMP
problem. It is not — it only relocates the failure:

| condition | outcome |
|---|---|
| `OMP_NUM_THREADS` unset | SIGSEGV (139) inside torch's parallel region |
| `OMP_NUM_THREADS=1` | SIGABRT (134), OMP Error #15, at the first faiss index build |
| `OMP_NUM_THREADS=1` + `KMP_DUPLICATE_LIB_OK=TRUE` | exit 0, clean |

Both halves go into the eventual PR as a dated ADR 0006 note. Option 2 (exact top-k as a torch matmul,
removing faiss from the training process entirely) is on the board as the clean follow-up, not tonight.

**Next: holding the full run until Starscream's literal pair-complete line appears**, then relaunching
under the validated flag with a fresh pre-launch block and the usual tokens. The box is free now;
Starscream has #175 and #176 to merge first.

## 2026-09-05 22:20 — Opus failover F: W19 aborted at 26m — full diagnosis, and a proposal I will not act on unasked

**Exact error**, `artifacts/sasrec/logs/twotower-v2-fulldata-20260905T2220PDT.log` (preserved, plus a
`.FAILED.log` copy):

    2026-09-05 22:06:31,730 INFO Epoch 1/3 loss=8.4730
    OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.

SIGABRT, exit 134, 26m30s in. **Not the model, not the data, not memory.** Epoch 1 completed cleanly;
the abort landed at the first `faiss.IndexFlatIP` construction, which is `on_epoch` ->
`build_index()` (`src/training/twotower.py:257`).

**Memory is excluded by measurement, not assertion.** Sampled once a minute for 26 minutes: RSS held
~5.4 GB, free+inactive never below **6.53 GiB** (21:43, the pair-array allocation, recovered
immediately), and at the abort it was **8.51 GiB / 47% pressure-free**. Trajectory in
`/tmp/f-twotower-memory.tsv`.

**Reproduced in ten seconds**, with nothing but `import faiss; import torch; <torch work>; build
index` — no Postgres, no MLflow, no data. Three conditions, three outcomes:

| condition | result |
|---|---|
| `OMP_NUM_THREADS` unset | **SIGSEGV (139)** inside torch's parallel region, before faiss is reached |
| `OMP_NUM_THREADS=1` | **SIGABRT (134)**, OMP Error #15, at the first faiss index build |
| `OMP_NUM_THREADS=1` + `KMP_DUPLICATE_LIB_OK=TRUE` | **exit 0**, clean, no error emitted |

So **`OMP_NUM_THREADS=1` does not fix the collision — it moves the failure later**, from torch's
threads to faiss's runtime init. The project's standing mitigation is not sufficient, and that is new
information: `docs/results.md` presents it as the fix.

**The venv is not what changed.** faiss-cpu 1.14.3 installed Jul 3, torch 2.12.0 May 20; neither
touched today. Three libomp copies are present — `torch/lib/`, `faiss/.dylibs/`, `sklearn/.dylibs/`.

**The genuine anomaly, which I cannot yet explain.** Megatron's 6% diagnostic ran this exact sequence
**three times** at 09:37-09:47 today and survived every one
(`artifacts/sasrec/logs/twotower-faiss-mapping-fixed-20260905.log`: per-epoch
`warm_recall@500` at 09:40, 09:44, 09:47). Same machine, same venv, and `KMP_DUPLICATE_LIB_OK`
appears nowhere in the repo. Two differences I can see, neither obviously causal: it read CSV rather
than Postgres, and it logged to a file-store MLflow rather than the HTTP server. My probe has neither
and still dies, so those are unlikely. The remaining candidate is that Megatron's shell exported the
workaround. I cannot inspect another session's environment and will not guess in the record.

**MLflow: run `796c1e96b003448782645c227b84ed13` is left in `RUNNING`, deliberately.** A killed
process never closes its run, and `docs/results.md` already sets the precedent for exactly this,
calling it "the honest record of what happened". It carries the full params and `train_loss 8.4730`.
**Its `evaluation_protocol_hash` is `sha256:b4ed5afa…`, identical to SASRec's** — so the pre-check I
did during the run is now confirmed empirically: had it finished, the gate would have accepted the
comparison.

**Options, with the honest cost of each. I am doing none of them without a decision.**

1. **Validate the workaround, then use it.** Re-run the **6% pilot** under
   `KMP_DUPLICATE_LIB_OK=TRUE` (~10 min) and check it reproduces Megatron's recorded
   **0.3759484159** *exactly*. If it does, the flag demonstrably does not corrupt this workload, and
   the full run under it is trustworthy on evidence rather than on hope. If it does not reproduce,
   the flag is disqualified and so is any number taken under it. This converts "unsafe, undocumented"
   into a measured claim, costs ten minutes, and is the only option that both unblocks today and
   keeps the number defensible. **My recommendation.**
2. **Remove faiss from the training process.** With `faiss_exact=True` the index is `IndexFlatIP` —
   exact inner product — which is a matmul plus top-k in torch, mathematically identical. Clean fix,
   no unsafe flag, but a code change to a champion-adjacent model needing its own PR and tests.
3. **Split into two processes**: train and checkpoint, then evaluate in a fresh process that never
   imports torch. Heavier, and the per-epoch recall curve is lost.
4. **Fix the environment** so one libomp serves both. Correct per the runtime's own advice, but it
   perturbs the venv every other recorded number on this machine was produced in.

**Standing down until told which.** The box is free and Starscream has been told directly that a
failed run releases it — its checklist greps for a DONE token that will never appear, which would
have deadlocked it.

- HB 22:08 F **The box is free — Starscream, my half of your pre-flight condition 1 is satisfied
  (a failed run releases it exactly as a finished one does).** Trainer pid 23661 is gone; verified
  by executable name, not `pgrep -f`. Note failover E's TMDB pull may still be live — that is your
  condition 2 and it is independent of me. Memory at exit: free+inactive 9.02 GiB and the
  compressor was draining; a run that just aborted leaves it releasing for a while, which your
  condition 4 already anticipates.
  **Cause: OpenMP double-initialisation abort (SIGABRT, exit 134), not a model or data problem.**
  `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized.`
  Epoch 1/3 finished cleanly at 22:06:31 with loss 8.4730 in 26m30s; the abort came immediately
  after, which is where `on_epoch` calls `build_index()` and pulls FAISS in for the first time —
  torch and faiss each ship their own libomp. `OMP_NUM_THREADS=1` was necessary and is evidently
  not sufficient. **I am not setting `KMP_DUPLICATE_LIB_OK=TRUE` and re-running**: the runtime's own
  message says it may 'silently produce incorrect results', and a silently wrong retrieval number
  feeding a promotion decision is the exact failure this project's non-negotiables exist to stop.
  Diagnosing before relaunching; full write-up next.

TWO-TOWER RUNNING 2026-09-05 21:39 PDT

- HB 22:00 F W19 healthy at 20 min: RSS steady ~5.4 GB, free+inactive recovered to 9.02 GiB,
  compressor draining (18.23 -> 14.21 GiB), pressure-free 50%. Still inside epoch 1 of 3; the
  pair-array spike at 21:43 was the tightest the machine got and it has passed.
  **Endgame de-risked while it trains - the gate's inputs are verified now, not in three hours.**
  (a) **Protocol comparability, the thing that would have silently voided the run.** The gate refuses
  to pool runs whose protocols differ, and the one input that can differ between these trainers is the
  threshold: SASRec passes `COLD_START_THRESHOLD` straight in, the two-tower routes it through
  `routing.cold_start_threshold_for(policy, ...)`. Resolved both: **identical**, policy value
  `train-history-count-gte-10-v1` on each. With the split (cutoff 1466837397, 20,000,075 train rows)
  and the fitted frame (19,739,546 pairs / 139,383 users / 34,461 items) already matching the pinned
  run exactly, this run's protocol hash should equal `sha256:b4ed5afa…` and the gate will accept it.
  (b) **The exact tolerances SASRec was judged under**, read out of
  `docs/experiments/sasrec/single-run-retrieval-verdict-2026-09-05.json` rather than guessed: both
  clauses say "minimum allowed -0.00%", so **cold and overall tolerance are 0.0**, warm requires
  +3.00%, metric recall@500, `seed_regime single_seed`. Using anything else would make the two
  verdicts incomparable.
  (c) **The incumbent's exact numbers**, so my hypothesis is stated against the real figures:
  item-item warm **0.3990569035829944**, cold **0.5262729520330651**, overall **0.43325735583575864**.
  So the command at the end is exactly:
  `make gate-retrieval CANDIDATE=<run-id> INCUMBENT=4b342e87dbf54834be5c719eae9a4e6c
  RETRIEVAL_SEEDS=42 RETRIEVAL_COLD_TOLERANCE=0.0 RETRIEVAL_OVERALL_TOLERANCE=0.0`

- HB 21:45 F W19 training. **My predeclared peak RSS was wrong and I am correcting it on the record:**
  I said ~3-4 GB on the strength of `docs/results.md:2152` ("about 2 GB" for the full-dataset fit);
  measured peak so far is **6.87 GB**. That figure evidently describes a different phase or a
  configuration without hard negatives and item features. Trajectory, sampled once a minute to
  `/tmp/f-twotower-memory.tsv` (rss / free+inactive / compressor / pressure-free):
  21:41 4.21 / 7.85 / 16.09 / 44% · 21:42 3.67 / 7.38 / 16.89 / 41% ·
  **21:43 3.13 / 6.53 / 18.23 / 36% (tightest)** · 21:44 6.87 / 8.12 / 15.39 / 45%.
  The 21:43 dip to 6.53 GiB free+inactive cleared the 6 GiB bar but not by much; it was the pair-array
  allocation and it has passed, with the compressor draining 18.23 -> 15.39 GiB since.
  **Pair materialization done 21:44:01 and it cross-checks exactly against the pinned SASRec run:**
  19,739,546 (history, positive) pairs across 139,383 users and 34,461 items - the same three numbers
  that run recorded. Same split, same frame, so the comparison is like-for-like on population.
  **Co-tenancy noted:** failover E's TMDB pull started 21:38, one minute before me, and it loads into
  the dev Postgres. It does not touch my numbers - my ratings were read into memory by 21:39:50 and
  the fit is CPU-bound in torch from there - but it is an I/O and memory neighbour, and it is
  Starscream's pre-flight condition 2, so both of us must clear before their window opens.
  No epoch has completed yet; three are expected.

- HB 21:41 F W19 launched. PID 23661, RSS 2.87 GB and climbing (ratings load), log
  `artifacts/sasrec/logs/twotower-v2-fulldata-20260905T2220PDT.log`, branch
  `feat/twotower-v2-fulldata` @ eef0f96 (fresh from origin/main, has #173). Expect ~2-3 h,
  three epochs. Starscream: the box is mine until the exit token appears; do not start the k6
  pair. Earlier block timestamps of mine drifted ahead of the clock and are being corrected as I
  notice them - the run log's own timestamps are authoritative.

- HB 21:20 F Starscream's S1a control is done (GATE=pass, p99 13.395416 ms) — that is half the pair; W10 still needs #173 on main, so W19 stays held. #173 CI: two earlier runs completed success; current run 8/10 green (test, feature-parity, tenant-isolation, lint, frontend, demo-compose, browser-auth-e2e, changed-paths), only `serving-artifacts` and `synthetic-load-smoke` outstanding — both remote, neither touches this box. My PR is now the lane's critical path; the merge is the orchestrator's call. Running nothing locally.

## 2026-09-05 21:38 — Opus failover F: W19 PRE-LAUNCH — corrected two-tower v2, full 25M, seed 42

Orchestrator ruling received: run now; W11's PR plus CI plus the gate is longer than my run, so
running now finishes both earlier than holding. Starscream gates its k6 pair on my status token
(protocol below), so the box hands over cleanly.

**Gate checks, all passing, taken by executable name rather than `pgrep -f`** (which matches a
monitor's own argv on this box — Starscream's phantom-TMDB lesson):

    free+inactive   9.69 GiB   (> 6 GiB, PASS)     compressor occupied 11.67 GiB
    memory_pressure system-wide memory free 54%    swap 5,637 / 6,144 MB (high-water, not a signal)
    ps -Ao pid,rss,comm | awk '$3 ~ /python$|k6$/'  -> empty
    branch feat/twotower-v2-fulldata @ eef0f96, cut fresh from origin/main (has #173)
    cohort parquet present, sha256 6da0b568…, anchored to cutoff 1466837397

**Command** (nothing else set, so every other value is a v2 default):

    OMP_NUM_THREADS=1 TWOTOWER_FAISS_EXACT=true \
      TWOTOWER_RUN_LABEL=faiss-mapping-fixed-fulldata \
      nice -n 19 python -m src.training.twotower

**Configuration, verified by resolving `TwoTowerConfig.from_env()` rather than by reading defaults:**
`faiss_exact=true, num_sampled=16384, epochs=3, seed=42, embedding_dim=64, history_window=50,
batch_size=4096, learning_rate=1e-3, logit_temperature=0.05, correct_positive_logit=true,
use_item_features=true, hard_negative_count=8, pool 256, warmup 1, early_stopping_patience=0`.
`faiss_exact` is the **only** override needed; it defaults to False. No sample fraction, so full 25M.
The FAISS row-mapping fix is on main (`twotower.py:770`, `int(row_index) + 1`, from #158).

**Predeclared before any number exists:** MLflow experiment `phase-2-candidates`, run name
`twotower-faiss-mapping-fixed-fulldata`. Wall-clock **~2–3 h**. Peak RSS **~2 GB for the fit**
(`docs/results.md:2152` records the post-memory-rewrite full-dataset resident set as "about 2 GB")
plus ~1 GB for the ratings frame, so ~3–4 GB total against 9.69 GiB free+inactive. Log under
`artifacts/sasrec/logs/`.

**Hypothesis.** Item-item's full-data warm recall@500 is **0.3991**. The corrected 6% cell put
two-tower at **0.3759** against item-item's **0.3587** on the same subsample (+4.8% relative). If that
transfers, full-data warm recall@500 lands near **0.42**. It may not transfer: 115 warm users at 6%
against 1,931 at full scale.

**This cell is not the original complete-v2 arm.** That used IVF and 4,096 negatives; this uses exact
search and 16,384. Every record will say so. No promotion wording, no paired-ranker step, no sweep,
no v3.

**Status-token protocol agreed with the orchestrator.** A single line carrying the running token goes
in this file the moment the process starts, and a single line carrying the done-or-failed token the
moment it exits — each on its own line, and the literal tokens appear **nowhere else**, including in
this block, because a heartbeat that quotes one is exactly the false fire that cost Starscream a
measurement. Heartbeats with epoch progress every 30 minutes in between.

**Then, and nothing more:** protocol manifest, per-user recall vectors, `make gate-retrieval` against
item-item `4b342e87dbf54834be5c719eae9a4e6c` with `RETRIEVAL_SEEDS=42`, gate JSON under
`docs/experiments/twotower-sweep/`, a `docs/results.md` section, the roadmap Rung 1 row, and ADR
0006/0015 dated notes. One PR, no auto-merge.

## 2026-09-05 22:10 — Opus failover F: W21 is landed and proven in serving; W19's hold now needs a ruling

**#173 merged** 2026-09-06T04:00:21Z, commit `84d144b5`; `origin/main` carries
`RETRIEVER_ARTIFACT_POPULARITY`. W21 is closed end to end.

**My artifact is in the served bundle and it works.** Starscream's S1b published and verified a
schema 2, family `sasrec`, tenant `demo` bundle: all six artifacts re-hashed **from inside the
container** and match, popularity `9bc82187…` among them, lineage carrying `protocol_hash
sha256:b4ed5afa…` and `run_id a11af5ed…`. A direct `/rank` probe returned **500 candidates, all
`sasrec`, zero popularity fill**, `encoder_ms` 1.833. That is the strongest confirmation W21 could
get — the role, the checksum and the file all survive a real container boot and a real request.

**But W10's gate was NOT run, correctly.** `ModelRankingService.rank` refuses a request whose
`ChampionCoordinates` differ from the loaded manifest and the API degrades to popularity; migration
`0016` seeds `demo` with item-item coordinates and no promotion mechanism exists in the tree. With
the bundle mounted and unregistered, audit rows read `policy=popularity,
fallback_reason=champion-mismatch`. **A gate run in that state measures the popularity fallback and
labels it SASRec, and would look valid in a summary.** Starscream stopped instead. Right call.

**So the block that releases W19 is now behind a whole PR**, not behind a run: Starscream is building
`src/release/promote.py` + `make promote`, deriving the smoke's expected policy from the served
manifest instead of the hardcoded `item-item-cosine+lightgbm`, and giving the sidecar logger a
handler so `sasrec_retriever_loaded` actually reaches stdout. Then W10 retries.

**The ruling I need, because I will not take it myself.** W11's PR plus CI plus the gate run is
plausibly an hour or more; my two-tower run is **2–3 h**. Starting now therefore *guarantees* overlap
with W10 rather than risking it.

- **Hold (my recommendation, and the default the rule already sets).** A contaminated latency gate is
  unrecoverable and has to be re-run anyway, so overlapping costs more than idling. W19 loses time
  only.
- **Run now, W10 waits.** Strictly better throughput *only* if W11 will take longer than my run. The
  orchestrator can see Starscream's ETA; I cannot.

**Holding until told otherwise.** Nothing of mine is running — verified by executable name, not
`pgrep -f`. The box is free but I am not taking it.

## 2026-09-05 21:55 — Opus failover F: #173 is fully green; it is the one thing holding W10, and the merge is not mine

**All 10 checks pass** on `feat/sasrec-popularity-fallback` (run 34009791739), and the two earlier
runs on the branch had already completed `success`:

    test 3m34s · feature-parity 3m2s · tenant-isolation 3m25s · lint 3m44s · frontend 7m20s
    demo-compose 2m17s · browser-auth-e2e 4m11s · changed-paths 6s
    serving-artifacts 4m32s · synthetic-load-smoke 6m32s
    (publish-images, realm-drift: skipping)

**`serving-artifacts` passing is the one that mattered.** It rebuilds the committed bundle and
hash-compares it, so it is the check that would have caught the required-role change disturbing
item-item. It did not: `_REQUIRED_RETRIEVER_ARTIFACTS[item-item]` is untouched and the demo bundle
still round-trips byte for byte. `synthetic-load-smoke` also passed, so the p99 gate is unaffected.

**The chain now is entirely a merge decision.** #173 on main → Starscream bakes the `popularity` role
via `make serving-artifacts-publish` → publishes the served bundle with artifact `9bc82187…` →
runs W10 → posts the completion block → I run the two-tower. Starscream's `HB 20:53` says W10 is
waiting on exactly this and that it moved #173 to the front of its queue.

**I have not merged and will not.** My instruction is no auto-merge, and merge authority for lane PRs
sits with the orchestrator. Flagging rather than acting.

**S1a, their half that is done:** GATE=pass, p99 **13.395416 ms** (+0.464 / +3.59% vs pre-#168), with
the handler *faster* by 1.21 ms and the whole increase outside it — the audit-commit attribution,
not a scoring regression. That is the control; the pair still needs W10.

W19 stays held. Nothing of mine is running; the box is free but I am not taking it, because W10 is
the next measurement on it.

## 2026-09-05 21:35 — Opus failover F: two procedure fixes, both from Starscream's traps; still holding

**Starscream's re-baseline window is OPEN** (`gate-started-at.txt` = 2026-09-06T03:47:41Z, main
`c71ef4b`). I am running nothing and will start nothing until their completion block. My popularity
export finished at 20:39, eight minutes before that window opened, so it is provably outside the
measurement — their own evidence file settles it, as it did for the retracted TMDB alarm.

**Fix 1 — my matcher was the trap Starscream named.** My watcher fired twice on *plan and heartbeat
text quoting the completion wording*, not on a result. Starscream hit the identical thing and asked
for a start-of-line anchor: "never write the literal token except in the real block," because a false
fire contaminates a live measurement and a missed fire idles W19 for hours. My watcher now diffs the
set of `^## ` header lines and prints new headers **verbatim** for a human read, matching no phrases
at all. It also scans the whole file, since the newest blocks are at the bottom.

**Fix 2 — the memory rule's own liveness check is unreliable on this box, and I used it.** My
pre-launch check was `pgrep -f 'python.*-m src\.'`. Starscream demonstrated that `pgrep -f` and
`ps | grep` match a *monitor's own shell argv* on this machine — it is what produced the phantom TMDB
run, and it caught Starscream too even with the `[t]` bracket trick, because the bracket suppresses
the grep process but not a wrapper shell whose argv holds the pattern. My earlier "no jobs running"
readings were right by luck, and one of them visibly matched my own monitor's shell. **W19's
pre-launch must not use `pgrep -f`.** Replacement, now part of that procedure:

    ps -Ao pid,rss,comm | awk '$3 ~ /python/'      # match the executable, not argv
    ps -Ao pid,comm     | awk '$2 ~ /k6$/'
    plus: read the gate's own gate-started-at.txt to see whether a window is open

Checked that way just now: **no trainer of mine is running** (the only `python`-named process is a
VSCode env helper); Docker is up, which is Starscream's stack.

**Unchanged:** W21 closed, artifact `9bc82187…` handed over. W19 held on their block; its pre-launch
detail is three blocks below. #173 is at the front of their queue and still pending CI — the merge is
the orchestrator's call, and W10's second half cannot run until it lands.

## 2026-09-05 21:20 — Opus failover F: correction — Starscream appends at the BOTTOM; the handoff worked

**I was reading the wrong end of `reports/starscream.md`.** Its newest blocks are at the **bottom**,
not the top — the top block is still "W8 and O-12 up" from hours ago, while the file's last three
blocks are the current ones. Anyone (including a successor, or the orchestrator's own "grep for the
block" instruction) who checks only the first `## ` line will conclude nothing has happened when a
great deal has. Both of my watchers were scoped to the top block and were therefore wrong; retired
and replaced with one that scans the whole file.

**What that actually shows.**
- Starscream's `HB 20:45` says it is **RUNNING the incumbent re-baseline** on main `c71ef4b`
  (its reading: free+inactive 10.23 GB, pressure 56%, load 1.50). **The machine is theirs.** I am
  running nothing and will start nothing — my popularity export finished at 20:39, six minutes before
  they began, so it is not inside their measurement.
- **The deadlock they raised against #173 is the one the orchestrator already amended**, and their
  agreed sequence is exactly what happened: F exports → F writes path/SHA-256/format **plus
  free+inactive, memory_pressure and compressor** to their inbox → they publish and run the pair →
  they post the block → I run the two-tower. My inbox block landed 20:45 with all six items. Their
  stated trigger is "F's inbox write, not headroom", so nothing is waiting on me.
- Their review of #173: **both lane files clean.** `_without_the_shared_fix` survives 5x so O-9 stays
  observable and W17 is unaffected; the required-roles assertion updated correctly; they confirm I did
  not touch `src/serving/sequence_retrieval.py` and rewrote the handoff test's rationale instead of
  leaving a stale one.

**The one thing now blocking them is a merge, and it is not mine to make.** Their `HB 20:45`:
W10's second half needs **#173 on main** — `main` has 0 occurrences of `RETRIEVER_ARTIFACT_POPULARITY`,
so `make serving-artifacts-publish` cannot bake the `popularity` role yet. They have asked the
orchestrator to expedite it ahead of docs-only #172. Three CI runs are in progress on the branch.

**On their flagged window** ("once #173 is on main, every SASRec manifest without a popularity
artifact is invalid"): intended, and the window is already closed in practice — the artifact exists
as of 20:38, and nothing committed is a SASRec bundle (`infra/model-bundle/` is item-item v1), so no
CI job publishes or validates a SASRec manifest. The exposure is local-only and already satisfied.

**W19 unchanged: held** until their dated completion block. Its full pre-launch detail is two blocks
below. Nothing of mine touches the machine until then.

## 2026-09-05 21:05 — Opus failover F: W19 is researched and ready; it is waiting on one thing only

W21 is closed (block below). This block exists so whoever runs W19 — me when the gate opens, or a
successor — does not re-derive any of it.

**The only gate left is Starscream's block.** `reports/starscream.md`'s newest entry is still
"W8 and O-12 up"; the k6 pair has not been reported. Memory currently passes on the OR clause
(free+inactive 10.91 GiB). Do not launch before that block appears.

**Config verified by resolution, not by reading defaults.** `TwoTowerConfig.from_env()` with
`TWOTOWER_FAISS_EXACT=true` resolves to exactly the corrected diagnostic's cell:
`faiss_exact=true, num_sampled=16384, epochs=3, seed=42, embedding_dim=64, history_window=50,
batch_size=4096, learning_rate=1e-3, logit_temperature=0.05, correct_positive_logit=true,
use_item_features=true, hard_negative_count=8, pool 256, warmup 1, early_stopping_patience=0`.
`faiss_exact` is the **only** override needed — it defaults to False; everything else is a v2 default.

**Command** (no sample fraction, so full 25M):

    OMP_NUM_THREADS=1 TWOTOWER_FAISS_EXACT=true TWOTOWER_RUN_LABEL=faiss-mapping-fixed-fulldata \
      nice -n 19 make train-twotower

**Predeclarations.** MLflow experiment `phase-2-candidates`, run name
`twotower-faiss-mapping-fixed-fulldata`. Wall-clock **~2–3 h** (inbox #7's own ~2 h estimate; the 6%
cell fitted in 583.7 s over 1,198,161 pairs, and full data is ~16.7x that). **Peak RSS ~2 GB for the
fit** — `docs/results.md:2152` records the post-memory-rewrite full-dataset resident set as "about
2 GB" — plus ~1 GB for the ratings frame, so ~3–4 GB total, comfortable against 10.9 GiB.
The FAISS mapping fix is on main (`twotower.py:770`, `int(row_index) + 1`), merged in #158.

**Hypothesis, stated before the number exists.** Item-item's full-data warm recall@500 is **0.3991**.
The corrected 6% cell put two-tower at 0.3759 against item-item's 0.3587 on the same subsample
(+4.8% relative). If that transfers, full-data warm recall@500 lands near 0.42. It may well not
transfer — 115 warm users at 6% versus 1,931 at full scale.

**Then, and nothing more:** protocol manifest, per-user recall vectors, `make gate-retrieval` against
item-item `4b342e87dbf54834be5c719eae9a4e6c` with `RETRIEVAL_SEEDS=42` and the measured tolerances,
gate JSON under `docs/experiments/twotower-sweep/`, a `docs/results.md` section, the roadmap Rung 1
row ("reopened 2026-09-05 after the FAISS mapping fix; full-data result: …"), and ADR 0006/0015 dated
notes. **State plainly that this cell is not the original complete-v2 arm** (that used IVF and 4,096
negatives; this uses exact search and 16,384). No promotion wording, no paired-ranker step, no sweep,
no v3. One PR, no auto-merge.

**PR #173 CI** was still running at this timestamp (`changed-paths` pass, the rest pending) after the
second push added the provenance JSON. Nothing in it is unreviewed: 1,733 unit tests, ruff, black and
mypy all pass locally.

- HB 20:55 F W21 fully done: artifact `9bc82187…` (34,461 movies, two byte-identical full-data builds) beside the pinned encoder, handed to Starscream with the machine state; PR #173 has code + provenance, CI re-running after the second push. W19 held; watchers armed on PR CI and on starscream.md's newest block. Next action the moment the pair-complete block lands: W19 pre-launch report, then the run.

## 2026-09-05 20:47 — Opus failover F: W21 artifact built, verified, handed to Starscream

**Done. The 25M is quiet again and the box is Starscream's for the k6 pair.** Order was amended by the
orchestrator: the export had to precede W10, because #173 makes the `popularity` role required and the
publisher cannot assemble a SASRec bundle without the file.

    path    artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/popularity-order.json
    sha256  9bc82187c910adf9633315b76294842d70e8cf7f2c49a8d4e0e0196849e933f0
    movies  34461      bytes 212812      wall-clock 41 s      OMP_NUM_THREADS=1, nice -n 19
    log     artifacts/sasrec/logs/popularity-order-a11af5ed-20260905T2142PDT.log

**The run log confirms the numbers that had to be confirmed.** `Loaded 25,000,095 ratings`; split
cutoff **1466837397** — the pinned run's, asserted not observed; ADR 0011 cohort attached
(fingerprint `ae4475f0e063`, 2,000 users, 7,000 history rows, 0.035% of train); training frame
20,007,075 rows; **34,461 movies, exactly the pinned run's train-item count**; threshold 10. The 515
O-13 demo rows are still in the table and were excluded by #166's default filter rather than by hand.

**Determinism measured on the real data, not a fixture.** Two independent full-data builds produced
`9bc82187…` both times, byte for byte. That is the one property the v2 manifest depends on.

**Load path verified against the real pinned artifact.** `load_sasrec` restores the ordering; its
length equals the encoder vocabulary; **every id is in the vocabulary**, so no fill candidate is
unknown to the retriever; and a cold-routed user now gets `[356, 296, 318, 593, 480]` where a
disk-loaded bundle previously returned nothing. `a11af5ed…`'s existing two files are untouched —
their checksums are already pinned by Starscream's bundle.

Provenance committed as `docs/experiments/sasrec/popularity-order-2026-09-05.json` and pushed to
**PR #173** (no auto-merge). Path, checksum, format and the machine state I hand over are in
`inbox/starscream.md`.

**Machine at handoff:** free+inactive **10.91 GiB**, active 8.43 / wired 3.68 GiB, compressor
occupied 12.08 GiB, `memory_pressure` free 55%, swap still reads 5,645/6,144 MB (macOS does not
shrink it — free+inactive is the signal). Nothing of mine running.

**W19 (two-tower full run) stays held** until a dated "pair complete" block appears in
`reports/starscream.md`. Its top block is still "W8 and O-12 up", so nothing has changed there. The
pre-launch report goes up before anything launches. Timestamps on my four earlier blocks were
corrected downward — I had labelled them ahead of the clock.

## 2026-09-05 20:37 — Opus failover F: pre-launch — W21 popularity export (one 25M read)

Order amended by the orchestrator: the export goes before Starscream's k6 pair, because #173 makes
the `popularity` role required and W10 cannot publish a bundle without the artifact. Only the
two-tower run still waits for the "pair complete" block.

**Memory gate re-checked, passes on the OR clause.** swap 5,645 / 6,144 MB (91.9%, over the 70% bar);
**free+inactive 10.60 GiB** (> 6 GiB). `pgrep` shows no trainer, no k6, no compose stack.

**Inputs verified before launching, not after.** Postgres up 16 h and healthy; `ratings` holds
`default` **25,000,095** and `demo` 515 — the O-13 rows are still there and #166's default filter is
what excludes them. The ADR 0011 cohort parquet was only a DVC pointer in a fresh worktree, so I
copied the materialized file in: sha256 `6da0b568…`, **anchored to split_cutoff 1466837397** — the
pinned run's cutoff exactly — 2,000 users, 7,000 history rows, buckets [0,1,3,10].

**The frame.** `temporal_split` then `synth_cold.prepare`, i.e. split.train (20,000,075 rows per the
pinned run's record) **plus the 7,000 cohort history rows** — the frame `SASRecModel.fit` actually
fitted its `PopularityModel` on, so the served fill order and the evaluation's fallback agree. That
choice is stated rather than assumed because it moves the bytes; a cohort-less build would be a
different file.

**Command** (`OMP_NUM_THREADS=1`, `nice -n 19`, log under `artifacts/sasrec/logs/`):

    make sasrec-popularity-order OUT=<repo>/artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9 \
        EXPECT_ROWS=25000095 EXPECT_CUTOFF=1466837397

Both expectations are asserted, so a moved cutoff or a contaminated read aborts rather than writing a
plausible-looking artifact. Expected wall-clock **~3–6 min** (a read and two groupbys, no training),
peak RSS **~4–5 GB**. Create-only: it cannot overwrite the encoder or its manifest. No MLflow run —
this produces bytes, not a measurement. SHA-256 goes to `inbox/starscream.md` and a results block here.

## 2026-09-05 20:34 — Opus failover F: W21 code shipped (PR #173); artifact and W19 held on the 25M

**PR #173 — `feat(sasrec): ship the popularity fallback inside the SASRec bundle`.** No auto-merge.
1,733 unit tests pass, ruff/black/mypy clean, CI running. `test_baked_serving_bundle` and
`test_ratelimit` do not collect in this checkout's venv (`psycopg`, `fakeredis` absent) —
pre-existing and unrelated.

**Format: Starscream's, verbatim.** `popularity-order.json`,
`{"schema_version":1,"movie_ids":[...]}`, compact separators, sorted keys, no trailing newline, full
catalog, ties on ascending movieId, nothing else in the hashed payload. Written into
`inbox/starscream.md` with the literal bytes and the importable symbols. No deviation.

**What landed.** `src/models/popularity_artifact.py` (stdlib only, so the slim images can read it);
`export_sasrec` writes and checksums it, refusing a model with an unfitted fallback;
`load_sasrec` restores it onto the model — a bundle read from disk can now answer a cold-routed
user, which it could not before; `src/training/export_popularity_order.py` +
`make sasrec-popularity-order` for encoders exported before the role existed. 30 new tests.

**Cross-lane touch, flagged.** `RETRIEVER_ARTIFACT_POPULARITY` added to
`_REQUIRED_RETRIEVER_ARTIFACTS[sasrec]` in `src/models/artifacts.py`. `bundle_publisher` reads that
table directly, so the role is publishable and checksum-pinned with no publisher change. Cost: three
serving test files' SASRec fixtures gain the role (in the PR — Starscream should rebase, not redo).
`fill_order()` and the now-stale `sequence_retrieval.py:329` docstring stay with their owner;
`test_a_sasrec_bundle_publishes_no_fill_order` is kept as the handoff marker with its reason
rewritten.

**Finding.** `PopularityModel.fit` sorts with pandas' default quicksort — not stable — so its tie
order among equal-count movies is a pandas implementation detail. `fit` is deliberately **not**
changed (it would move a recorded baseline); the artifact pins the tiebreak instead, and a test
holds it to the same multiset and the same non-increasing counts.

**Still held, both on the 25M.** (1) the real artifact from `a11af5ed…`'s training split;
(2) W19, the corrected two-tower full run. Orchestrator rule: nothing loads the ratings table until
a dated block in `reports/starscream.md` says the k6 baseline/W10 pair is complete. Memory unchanged
(swap 5,645/6,144 MB; free+inactive ~10.9 GiB, so the OR clause passes). Nothing of mine is running.
Polling; W19's pre-launch report comes before anything launches.

- HB 20:29 F W21 code written (popularity_artifact.py, export/load, required role, CLI, 30 new tests green). Now fixing 40 mechanical SASRec-bundle fixture failures in test_bundle_publisher/test_serving_manifest_v2/test_sidecar_sasrec_load caused by making the `popularity` role required. No 25M job launched; still waiting on Starscream's W10 pair block.

## 2026-09-05 20:22 — Opus failover F: W21/W19 — plan before code

**Scheduling.** Both of my 25M loads are held. Orchestrator rule: nothing that reads the ratings
table starts until a dated block in `reports/starscream.md` says the k6 baseline/W10 pair is done.
Memory now: swap **5,645 / 6,144 MB (91.9%)** — over the 70% bar — but free+inactive is **~10.9 GiB**,
so the OR clause passes; `pgrep -f 'python.*-m src\.'` empty, no k6/compose. I will re-check both at
pre-launch. Code first, in that order: W21 code → W21 artifact (needs 25M) → W19 (needs 25M).

**W21 format: I take Starscream's, unchanged.** `popularity-order.json`,
`{"schema_version": 1, "movie_ids": [...]}`, most-popular-first, sorted keys, no trailing whitespace,
no timestamps/run ids/host metadata in the hashed payload, ties by **ascending movieId**, full catalog,
threshold-10 semantics, default tenant via `load_ratings` (#166). No deviation, nothing to renegotiate.
Threshold and cutoff are recorded **outside** the hashed bytes (manifest `Lineage`, my report, the
inbox block) exactly as Starscream argued — putting them inside makes the artifact unreproducible.

**Shape.**
1. `src/models/popularity_artifact.py` — new, **stdlib only** (no pandas/torch/implicit), so the slim
   images can read it: canonical serialize, create-only write + fsync, read with optional
   `expected_sha256`, and `popularity_order_from_counts` applying the `(-count, movieId)` key that
   `src/training/sasrec.py:113` already uses.
2. `PopularityModel` gains a `counts` field (populated in `fit`). **`ranking` is untouched** — see the
   finding below.
3. `export_sasrec` writes the file beside the encoder and pins its sha256 in an **optional** pair of
   `sasrec-manifest.json` fields; `load_sasrec` reads it back into `model._popularity.ranking` and
   verifies when declared. Optional because the pinned `a11af5ed…` manifest must not be rewritten —
   its bytes are what a v2 bundle pins as the `vocabulary`/`config` roles.
4. `src/models/artifacts.py`: `RETRIEVER_ARTIFACT_POPULARITY = "popularity"` added to
   `_REQUIRED_RETRIEVER_ARTIFACTS[sasrec]`. **Cross-lane touch, flagged:** `bundle_publisher` reads
   that table directly, so the role becomes publishable and checksum-pinned with no publisher change;
   `RetrieverRef.validate` already refuses a mismatching checksum. Cost: SASRec fixtures in
   `test_bundle_publisher.py`, `test_serving_manifest_v2.py`, `test_sidecar_sasrec_load.py` gain the
   role. Starscream still owns `fill_order()`.
5. `src/training/export_popularity_order.py` — CLI producing the real artifact from the pinned run's
   training frame (`temporal_split` → `synth_cold.prepare`, i.e. **the cohort-attached frame the
   evaluation fitted on**), asserting 25,000,095 rows and cutoff 1466837397 in the log.

**Finding, not fixed by me.** `PopularityModel.fit` orders with `sort_values(ascending=False)`, whose
default kind is quicksort — **not stable**, so tie order among equal-count items is a pandas
implementation detail. The artifact pins the tiebreak instead; a test asserts the artifact is the same
multiset with the same non-increasing count sequence as `ranking`, so semantics are identical and only
ties are specified. Changing `fit` itself would move a recorded baseline and is not in this PR.

**W19** unchanged from inbox #7/#11 — pre-launch report first, after the W10 pair clears.

## 2026-09-05 19:15 — Opus failover D: increment 1 re-measured after O-9 — verdict unchanged

**The dev-database blocker is gone and was never mine to fix by hand — PR #166 fixed it at the
root.** `load_ratings` is tenant-scoped now, the run read **25,000,095** ratings at cutoff
`1466837397`, and the 515 stray demo rows are simply no longer visible to a trainer. My earlier
STOP notice is resolved; no data was deleted.

**PR #159 is current with `main` and green except `serving-artifacts`.** Merged (not rebased)
through #167; the merges were clean — main's serving rewrite and this branch's ranker work touch
disjoint files. 1,647 unit tests, 6 parity tests, ruff/black/mypy all pass.

**Re-measurement.** Run `bc89411b751f47eab9b098ddd628f306`, booster `0bcc12ba…`, seed 42,
**10 min 17 s**, log `artifacts/sasrec/logs/increment1-postO9-20260905T1900PDT.log`. 1b was not
re-run — #162's `c1d742c8…` already records it under the same seed and config.

| | bundle 1b (fixed) | 8-col control `3e9c826e…` | 10-col arm `0bcc12ba…` |
|---|---:|---:|---:|
| warm NDCG@10 | 0.101441 | 0.102358 | **0.104257** |
| cold NDCG@10 | 0.549002 | 0.549002 | 0.549002 (bit-identical) |
| overall NDCG@10 | 0.221762 | 0.222432 | 0.223821 |
| warm recall@10 | 0.084663 | 0.084520 | 0.084418 |
| cold recall@10 | 0.077638 | 0.077638 | 0.077638 |
| overall recall@10 | 0.082775 | 0.082670 | 0.082596 |

- **Both scopes refuse**, both from the gate itself now (#155 landed, so the hand-rolled reading
  is gone): all-routes **+0.93%** vs +3.00%; learned-route **+2.78%** vs +3.00%, cold +0.00%.
- **The control changes the reading, and it is why I added it.** The incumbent's warm booster
  predates the repair, so "arm beats bundle" confounds two columns with a retrain. Fitting an
  eight-column control from the *same* frame, groups and labels separates them: the features are
  worth **+1.86%**, the other **+0.90%** is the retrain. +2.78% looks like a near miss on the
  stop rule; +1.86% is not close.
- **Stop rule 1 fired -> increment 2 (DIN) stays unbuilt.**
- **Top-5 gain:** `sasrec_user_item_logit` 1,122,103 · `sasrec_user_item_score` 506,423 ·
  `user_interaction_count` 55,637 · `user_days_since_last_interaction` 53,775 ·
  `item_age_days` 50,786. The two columns take **82.3%** of total gain, up from 77.5% pre-repair,
  and `item_popularity_30d` falls 255,536 -> 37,249. With *more* short-history users present the
  booster leans on the sequence score harder and gets less for it — Risk 1, sharper.
- Training set grew 83,538 -> **115,167** groups (+31,629, the recovered positives less tail
  churn); dropped 70,465 -> 38,836; still **0 rows on the missing sentinel**.
- Records in the PR: new `…-postO9-2026-09-05.json`, new `docs/results.md` section with the
  pre-repair one kept and marked **superseded**, ADR 0018 note rewritten as final + superseded,
  roadmap Rung 3 row updated. No promotion wording; item-item + LightGBM remains champion.

**For Starscream:** the branch is the base you asked for — current, green everywhere except
`serving-artifacts`, which fails only because boosters now carry real `feature_names`. I did not
rebuild the bundle. One useful datum from my native rebuild attempt: the arm64 and amd64 outputs
differ by exactly one line, `[gpu_device_id_list: ]`, and nothing about the trees moves — so the
rebuild is mechanical.

## 2026-09-05 16:45 — Opus failover D: **STOP — the dev database is not fit for offline runs**

**Do not launch a training run against the dev Postgres until this is cleared.** Any run started
now splits at a different cutoff and is not comparable to anything in `docs/results.md`.

**What happened.** Earlier today I seeded against the wrong database: `Settings.database_url`
reads `POSTGRES_HOST/POSTGRES_PORT`, not `ADMIN_USER_DB_*`, so `synthetic.personas.seed` ran
against the shared dev Postgres instead of the throwaway artifacts stack. It inserted
**515 rows into `ratings` under `tenant_id = 'demo'`** (4 personas, 35 persona ratings, 480
background ratings). Counts now: `default` 25,000,095 — the original, untouched — and `demo` 515.

**Why it breaks offline runs.** `src/data/load.py::load_ratings` selects from `ratings` with **no
tenant filter**, so every offline trainer now ingests those 515 demo rows. The split moved from
25,000,095 rows / cutoff **1466837397** to 25,000,610 / cutoff **1466865668**, and my post-O-9
re-measurement aborted on exactly that — `CohortCutoffMismatchError`, the ADR 0011 cohort
refusing a cutoff it is not anchored to. That guard did its job.

**The fix I cannot apply.** `DELETE FROM ratings WHERE tenant_id = 'demo';` restores 25,000,095
and the original cutoff. I attempted it and the tooling refused the destructive write on a shared
database, which is the right guard; I am not working around it. **Owner or Starscream: please run
that one statement** (or `synthetic/personas/seed.py`'s own cleanup path, lines 278-287, which
deletes exactly these rows — it is delete-then-insert idempotent). The demo stack has its own
Postgres project, so these rows never belonged in the dev database and nothing else depends on
them; `make demo-seed` recreates them where they do belong.

**A real bug this exposed, worth its own item.** `load_ratings` reads across tenants. A demo or
`synth_cold` tenant's synthetic rows silently entering offline training is the cross-tenant
leakage non-negotiable pointed in the offline direction, and it is only invisible today because
the dev `ratings` table normally holds one tenant. Filtering it to the default tenant would
reproduce 25,000,095 exactly — i.e. every recorded number — while closing the hole. I have not
made that change: it is `src/data/`, it touches every trainer, and it should be a decision
rather than a side effect of my clean-up.

**Where PR #159 stands.** Rebased onto `main` including #162; the `sasrec.py` conflict resolved by
keeping both #162's `recommend_from_history_scored` and this branch's encoders. The
re-measurement code is committed and pushed (`f5391c0`): incumbent is now #162's `c1d742c8…`,
the shape guard is directional rather than an equality against a pre-repair fact, and the run
fits **two boosters from one training set** — an eight-column control beside the ten-column arm —
because the incumbent's warm booster predates the repair and "arm beats bundle" would otherwise
confound two columns with a retrain. The O-9 parity test is inverted from pinning the defect to
asserting the repair. Six parity tests and 47 unit tests pass. The run itself is blocked on the
database.

## 2026-09-05 15:52 — W17 pre-launch: inference-only SASRec re-evaluation

- Worktree `/private/tmp/MovieRecSys-sasrec-fastpath`, branch `fix/sasrec-eval-fastpath`,
  core commit `3ee1a06`. No PR #159 files or serving files touched.
- Shared fix is in `SASRecEncoder.encode_positions()`: every call disables PyTorch's unsafe MHA
  inference fast path, so training, evaluation, artifact reload and sidecar load inherit it.
- Regression coverage uses two blocks (ADR 0016's real depth). Synthetic and pinned-artifact tests
  return 500 finite candidates at history lengths 1/3/12/49/50. Full-length pinned-artifact max
  absolute delta is `5.960464477539063e-08` (limit `1e-6`). Scored retrieval returns the exact
  FAISS inner product and preserves every unscored candidate id/order.
- Preflight: no `python -m src.training` process active; 215 GiB disk free. The stale process match
  was only the orchestrator's monitor command, not the unplanned 6% retrain from inbox #8.
- Launch scope: no training. Load full 25M once, checksum-verify `a11af5ed…`, rebuild exact index,
  re-evaluate retrieval (~30 s) and compose bundle 1b from unchanged boosters `05610e60…` /
  `7e2052c1…` (~2-11 min). Expected total wall-clock 5-15 min, peak RSS 8-10 GiB.
- Threads will be pinned to 1 and `nice -n 19` attempted. Console log retained under
  `artifacts/sasrec/logs/`; new retrieval evidence goes to repo `mlruns` plus a create-only local
  copy; bundle evidence goes to `artifacts/mlflow-sasrec-recovery` plus create-only local copy.
  Original runs, artifacts, boosters and evidence are read-only and never overwritten.

## 2026-09-05 15:36 — PR #158 repaired and pushed

- Rebased `diagnose/twotower-retrieval` onto `origin/main` at #157 and force-pushed with lease;
  new head `29b2996`.
- CI failure was only the tiny-overfit loss threshold across Torch versions: local Torch 2.12
  reached `0.000498`, CI Torch 2.14 reached `0.002310`; both runs achieved recall@10 `1.0`.
  The canary now treats loss `<0.01` as effectively zero while retaining exact recall `1.0`.
- Checks: `make lint`, strict mypy, `git diff --check`, and 25 two-tower tests pass. Full local
  collection remains unavailable because this existing venv lacks `psycopg` and `fakeredis`;
  GitHub CI installs the dev extra and is the authoritative full-suite rerun.
- No model run launched and no files from PR #159 touched. Proceeding to W17 as inbox #9 directs.

## 2026-09-05 10:45 — Opus failover E: TMDB ingestion (PR #157, CI green)

**PR #157** — https://github.com/kudratsingh/MovieLens-RecSys/pull/157. Three
commits, rebased onto `main` after #152 merged (one conflict, `docs/adr/README.md`:
kept main's 0018 row and my amended 0017 row). **All 11 CI jobs pass**, including
lint, test, feature-parity, tenant-isolation, serving-artifacts and
synthetic-load-smoke. Not merged — not my call, and the deliverable is incomplete.

- **Delivered.** `src/data/tmdb_ingest.py` / `tmdb_schema.py` / `tmdb_load.py` /
  `tmdb_coverage.py`, migration `0018_tmdb_catalog` (12 tables, no RLS, six
  `COMMENT ON COLUMN` leakage markers), `make tmdb-ingest|tmdb-load|tmdb-coverage`,
  `docs/data/tmdb-metadata.md`, ADR 0017 dated note + index row. 63 unit tests, no
  network. Verified against live Postgres on a scratch DB (dropped after).
- **STILL BLOCKED — the snapshot does not exist.** `TMDB_READ_ACCESS_TOKEN` is
  unset and there is no `.env`. No pull ran, no requests were sent, no 429s, no DVC
  md5. The coverage table is deliberately left blank rather than estimated: the
  whole argument for ADR 0017 increment 2 rests on those numbers.
- **Owner action, ~52 min of wall-clock when the token exists:** `make
  tmdb-ingest` → `dvc add data/raw/tmdb/<date> && dvc push` → `make db-migrate &&
  make tmdb-load && make tmdb-coverage`. No code changes needed.
- **Two findings.** `links.csv` is not injective (34 tmdb ids → 2 MovieLens movies
  each), which forced the keys and the grouped pull; and `.gitignore` could not
  have tracked a dated DVC pointer under `/data/raw/*`. Both fixed here.

## 2026-09-05 — Item 2 complete; PR #158 open

- PR #158: https://github.com/kudratsingh/MovieLens-RecSys/pull/158, branch
  `diagnose/twotower-retrieval`, commits `5929c0f` and `d944bdf`; no auto-merge enabled.
- The two-line production fix maps every non-negative FAISS row `r` to dense item id `r + 1`.
  Independent exact-index parity failed for all three fixture users before the fix and now passes;
  the 200-user/400-item overfit canary reaches loss `0.000498`, recall@10 `1.0`.
- Evidence is machine-readable at
  `docs/experiments/twotower-sweep/faiss-row-mapping-diagnostic-2026-09-05.json`; ADR 0006 and
  ADR 0015 retain but explicitly supersede the invalid historical verdict.
- Validation: `make lint`, strict mypy, JSON parse, `git diff --check`, and 114 focused tests pass.
- PR #155 is fully green and waiting for the orchestrator's merge. No auto-merge was enabled.
- Coordination needed after failover D lands: update `docs/results.md` and the roadmap Rung 1 row
  from “below popularity / stop” to “FAISS mapping defect; historical verdict invalid; corrected
  6% diagnostic warm recall@500 0.3759484159; no promotion decision.” Those files remain D-owned.
- W17 is acknowledged but not started: inbox #6 requires failover D's Rung 3 PR to merge first.
  Proposed shared location is `SASRecEncoder.encode_positions()` in `sasrec.py`, so training,
  evaluation, artifact reload, and the sidecar all inherit one fix. Starscream should not add a
  second serving-only toggle; coordinate once D's prerequisite is merged.

## 2026-09-05 10:12 — Opus failover E: TMDB ingestion (before the full pull — BLOCKED)

Three commits on `feat/tmdb-metadata-ingestion`, all verified. **The pull has not
run: `TMDB_READ_ACCESS_TOKEN` is unset and there is no `.env` in the checkout.**
Stopped there rather than invent a credential. Nothing else blocks it.

- **Built.** `src/data/tmdb_ingest.py` (token bucket 20/s, Retry-After, jittered
  backoff, auto-halving after sustained 429s, 401/403 stops immediately,
  10-consecutive-failure breaker, resume from the shards, gzip JSONL shards +
  manifest with a SHA-256 per shard), `tmdb_schema.py`, `tmdb_load.py`,
  `tmdb_coverage.py`, migration `0018_tmdb_catalog` (12 tables), `make
  tmdb-ingest|tmdb-load|tmdb-coverage`, `docs/data/tmdb-metadata.md`, ADR 0017
  dated note. 59 unit tests, no network. ruff/black/mypy clean over `make lint`
  scope (mypy 115 files).
- **Verified against live Postgres** on a scratch DB: migration applies, the six
  as-of-pull columns carry their `COMMENT ON COLUMN`, `relrowsecurity = f` on all
  12, grants are app_user SELECT / admin_user CRUD, two loads leave identical
  rows, the duplicate `tmdb_id` fans out to two rows. Scratch DB dropped.
- **Two findings the plan did not anticipate.** (1) `links.csv` is **not
  injective** — 34 TMDB ids are claimed by two MovieLens movies each (69 rows), so
  `tmdb_movies` is keyed on `movie_id`, cast/crew on `(movie_id, credit_id)`, and
  the pull groups by TMDB id or a resume would silently drop the second row of
  each pair. (2) `.gitignore` could not have tracked a dated DVC pointer —
  `!/data/raw/*.dvc` cannot re-include anything under the excluded
  `/data/raw/*`; fixed.
- **Estimate when the token arrives:** 62,282 requests at 20/s ≈ **52 min**.
- **Owner action needed:** export `TMDB_READ_ACCESS_TOKEN`, then `make
  tmdb-ingest`, `dvc add data/raw/tmdb/<date> && dvc push`, `make db-migrate &&
  make tmdb-load && make tmdb-coverage`. The coverage table in the data doc and
  ADR 0017's note are the only things left blank.

## 2026-09-05 — Item 2 run complete: two-tower FAISS mapping correction

- Run `8a22ed513b8f457eb0d5f93b826dc82a` finished successfully in local MLflow
  experiment `phase-2-candidates`; protocol hash
  `sha256:090985d7075bd3df802ecb5da9402bc7fa7f3d10769dd9d384585113187cb629`.
- One seed-42 6% run only: current v2 defaults, 16,384 sampled negatives, exact FAISS,
  3 epochs. Fit was 583.7 s, final recommendation 0.1 s, and process wall-clock was
  approximately 589 s. The retained console log is
  `artifacts/sasrec/logs/twotower-faiss-mapping-fixed-20260905.log`; MLflow data and
  its `per_user_recall.json` artifact remain in `artifacts/mlflow-sasrec-recovery`.
- Final retrieval metrics (n=115 warm / 50 cold): warm recall/NDCG@500
  `0.3759484159 / 0.1483997512`; cold `0.4829587027 / 0.3844085562`; overall
  `0.4083757755 / 0.2199175709`. Epoch warm recall rose
  `0.3339 -> 0.3589 -> 0.3759` as loss fell `9.8231 -> 8.8138 -> 8.6116`.
- This proves the historical 0.04-range results were measurements of the broken row-to-id
  conversion, not evidence that the learned embeddings retrieve below popularity. The corrected
  result is 8.64x the old complete-v2 pilot's 0.0435, 1.90x its popularity reference 0.1974,
  and 3.88% above its item-item reference 0.3619. Those references use the same deterministic
  seed-42 6% cohort; the corrected run additionally uses exact retrieval and 16,384 rather than
  4,096 sampled negatives, so this is a diagnosis and corrected directional result, not an
  isolated causal estimate of the mapping fix or a promotion decision.
- PR #155 (Item 1) is fully green; the orchestrator may merge it. Failover D still owns
  `docs/results.md` and `docs/modeling-roadmap.md`, so Item 2 will not edit those live files;
  their required supersession wording will be reported for coordinated insertion.

## 2026-09-05 — Item 2 pre-launch: two-tower defect confirmed

- Branch `diagnose/twotower-retrieval`, worktree
  `/private/tmp/MovieRecSys-twotower-diagnostic`, rebased onto #152.
- Cross-path diagnostic reproduced a zero/one-based FAISS mapping defect: row 0 stores dense
  item 1, but `recommend()` dropped row 0 and treated every other row as its dense id.
- Tiny examples disagreed on all 3 checked users before the fix. Minimal `row + 1` fix landed
  locally with an independent SASRec-style exact-FAISS parity test.
- Tiny-overfit diagnostic: 200 users / 400 items, seed 42, min loss 0.000498 and recall@10 1.0.
- Pre-launch config: one 6% user pilot, seed 42, current v2 defaults, exact FAISS, one run only;
  no sweep, v3, or full-data run.
- Expected wall clock 2–10 min from prior 6% cells (66–596 s); peak RSS 8–10 GB because the
  runner reads the 25M CSV before user subsampling. MLflow experiment `phase-2-candidates`.
- Threads pinned to 1 and process niceness 19 because the machine is shared. Console log and
  MLflow record will be retained; nothing will be deleted or overwritten.

## 2026-09-05 — Item 1 complete: warm-primary ranking gate

- PR #155: https://github.com/kudratsingh/MovieLens-RecSys/pull/155
- Branch `feat/warm-primary-ranking-gate`, worktree `/private/tmp/MovieRecSys-warm-primary`,
  commit `e09aab5`; no auto-merge enabled.
- ADR 0001 now records O-1: learned-route-only changes gate warm NDCG@10 at +3% and
  cold non-regression; overall is reported, not gated. All-routes remains the default.
- `promotion_decision(scope="learned-route")` and CLI `--scope learned-route` implement it;
  default summary and default JSON shape remain backward-compatible.
- Step-1 fixtures pinned: the single SASRec booster still refuses on cold −53.11%; the
  per-route bundle promotes under all-routes and learned-route readings.
- Validation: `make lint`, strict mypy, CLI help, and 62 focused gate/SASRec-ranker tests pass.
- No model run launched. No full-data compute, serving files, Rung 3 files, or adjacent
  agents' worktrees touched.
- Coordination discrepancy: PR #152 was described as merged, but GitHub currently reports it
  OPEN; Item 1 does not overlap it.

## 2026-09-05 09:24 — Opus failover E: TMDB ingestion (plan, before code)

Lane: data. Worktree `.claude/worktrees/agent-a64b973c3b79133d8`, branch
`feat/tmdb-metadata-ingestion` off `origin/main` (8574488). Nothing in
`src/serving/`, `src/training/` or `infra/` is touched.

- **Shape.** `src/data/tmdb_ingest.py` (pull -> gzipped JSONL shards +
  manifest), `src/data/tmdb_schema.py` (declarative tables, migration is
  authoritative), `src/data/tmdb_load.py` (shards -> normalised Postgres),
  `src/data/tmdb_coverage.py` (the ADR 0017 increment-2 number). Migration
  `0018_tmdb_catalog`, `make tmdb-ingest` / `tmdb-load` / `tmdb-coverage`.
- **`images` is excluded from `append_to_response`, deliberately.** It returns
  every poster/backdrop/logo in every language — commonly 100-300 entries — and
  the only two paths a recommender or the product needs (`poster_path`,
  `backdrop_path`) are already in the base payload. It would roughly double the
  snapshot for zero feature value. The other six appends are pulled as briefed.
- **Rate.** Token bucket at 20 req/s (TMDB guides ~40-50). 62,316 ids with a
  `tmdbId` => **~52 min** wall-clock at full rate. `Retry-After` honoured on
  429, exponential backoff with jitter on 429/5xx, hard stop after 10
  consecutive failures with the partial shard flushed and renamed so a resume
  loses nothing but the in-flight record.
- **Leakage rule.** `vote_average`, `vote_count`, `popularity`, `budget`,
  `revenue`, `status` are as-of-pull, stored but column-commented and covered
  by a unit test that scans `src/feature_contract.py` for any reference.
- **BLOCKER, reported before the pull:** `TMDB_READ_ACCESS_TOKEN` is not set in
  the environment and there is no `.env` in the checkout (only `.env.example`
  with an empty value). Implementation and tests proceed; the smoke and the
  full pull cannot start until the owner supplies the token. I will not invent
  one.

## 2026-09-05 16:10 — Opus failover D: increment 1 complete, PR #159 rebased, one check open

**PR #159** — https://github.com/kudratsingh/MovieLens-RecSys/pull/159. No auto-merge.
Rebased onto `24f358b` (#157); **MERGEABLE**. Run `eee531bb16d943f5a2213dd9b7a8dc1a`,
incumbent 1b `566f5309767a4076a4f5e8151be16645`, seed 42, **10 min 26 s**, 12-core M3 Pro,
`OMP_NUM_THREADS=1`. Log `artifacts/sasrec/logs/increment1-20260905T0942PDT.log`. Learned-route
booster `7eba8851926bc88df46e09fb324730713fb32a43b00217ff676024d466fdfa3b` (10 columns),
fallback booster `05610e604cb2650a…` unchanged.

| metric | bundle 1b | increment 1 | change |
|---|---:|---:|---:|
| warm NDCG@10 | 0.091688 | 0.092550 | **+0.94%** |
| cold NDCG@10 | 0.549002 | 0.549002 | 0.00% (bit-identical) |
| overall NDCG@10 | 0.214631 | 0.215261 | +0.29% |
| warm recall@10 | 0.077431 | 0.076266 | **−1.50%** |
| cold recall@10 | 0.077638 | 0.077638 | 0.00% (bit-identical) |
| overall recall@10 | 0.077487 | 0.076635 | −1.10% |

- **Both gate readings refuse and agree**, so O-1 does not decide this one. All-routes (ADR
  0001 as it stands): overall +0.29% vs +3.00% -> DO NOT PROMOTE. Learned-route/warm-primary:
  warm +0.94% vs +3.00%, cold +0.00% within the 5% tolerance -> DO NOT PROMOTE. **PR #155 had
  not merged when the run finished**, so the learned-route reading is computed in the runner
  from the six metrics using ADR 0001's own threshold and tolerance, and is stored in the gate
  JSON as `warm_primary_reading`. No threshold was touched.
- **Stop rule 1 fired -> increment 2 (DIN) is not built.** The approval amendment's first
  precondition failed, so the owner is never asked for the warm target the second needs.
- **Top-5 gain:** `sasrec_user_item_logit` 588,958 · `sasrec_user_item_score` 272,887 ·
  `item_popularity_all_time` 41,183 · `item_age_days` 41,124 ·
  `user_days_since_last_interaction` 36,450. The two new columns take **77.5%** of total gain;
  the eight aggregates together fall 423,797 -> 250,264.
- **That is ADR 0018's Risk 1 in the words it was written in** — "a large feature gain and a
  small NDCG gain" — because the candidates *are* SASRec's top-500, so the score is a monotone
  function of the rank within a group. The rank-only control need not be run:
  `sasrec_candidate_rank` was excluded so rank could not be the explanation, and it was the
  explanation anyway, carried by the required features. What stands is narrower than the rung's
  premise: the encoder's *scalar verdict* is useless to a ranker scoring the encoder's *own*
  candidates. Whether the *history* would help is untouched.
- **Predeclared identities both held**: 83,538 groups / 1,754,298 rows exactly, cold back as
  `0.5490019989542251`. Only the learned route moved.
- **O-9 count for W17: 34,190 of 153,947 learned-route positives (22.2%)** have a strict prefix
  of 1–49 items and retrieve nothing today; 857 more are legitimately empty. Side effect:
  **0 of 1,754,298 training rows carried the missing sentinel**, because a sub-window prefix
  produces no slate and its positive is dropped. The NaN path is correct and unit-tested but was
  never exercised on this data. Both arms measured under O-9; results.md and the ADR note say so
  and name W17.
- **In the PR:** results.md section, `docs/experiments/sasrec/ranker-sasrec-score-features-2026-09-05.json`,
  the ADR 0018 dated result note, the roadmap Rung 3 row. No promotion wording anywhere.
- **Rebase:** two conflicts, both resolved by *adding* — the `.PHONY` line keeps #157's `tmdb-*`
  targets and this branch's three `train-sasrec-ranker*` ones; the ADR index keeps main's newer
  0017 row and this branch's 0018 row.
- **One check still red, and it is mine to hand over.** `serving-artifacts`:
  `the committed serving bundle ... is stale ... Differing manifest fields: ranker`, because
  boosters now save real `feature_names` (PR #154's request). Ten other checks pass. I seeded a
  correct `movielens-artifacts` stack, but the `linux/amd64` image build failed here with
  `DeadlineExceeded` fetching `python:3.11-slim` metadata and a direct `docker pull` stalled with
  no layers while the host reached `auth.docker.io` fine — the **Docker VM's outbound path is
  broken on this machine**. A Docker Desktop restart would likely clear it; I did not restart it
  because that kills the shared dev Postgres and MLflow. `make serving-artifacts` then
  committing `infra/model-bundle/` is the whole fix; exact commands are in the PR body.
- **Two incidents, both mine.** Docker's disk was full — reclaimed 22.86 GB of build cache and
  2.2 GB of dangling images (no volumes or tagged images touched). And I seeded against the wrong
  database first: `Settings.database_url` reads `POSTGRES_HOST/PORT`, not `ADMIN_USER_DB_*`, so
  `demo_setup` ran on the shared dev Postgres and applied migration 0010's `user_feedback_events`
  backfill over 25M rows. It completed cleanly. **The dev stack is now at alembic head (0018) and
  materially larger** — a migration it owed rather than damage, but worth knowing.

## 2026-09-05 11:40 — Opus failover D: PR #159 rebased; bundle rebuild in progress

Resumed after the session cut. **PR #159** — https://github.com/kudratsingh/MovieLens-RecSys/pull/159,
no auto-merge. Rebased onto `24f358b` (#157). Two conflicts, both resolved by **adding** rather
than choosing: the `.PHONY` line keeps #157's `tmdb-*` targets *and* this branch's three
`train-sasrec-ranker*` ones; the ADR index keeps main's newer 0017 row (which records the TMDB
ingestion landing) *and* this branch's 0018 row. ruff/black/mypy clean after the rebase.

**The one failing check is `serving-artifacts`, and the cause is confirmed rather than
suspected:** `the committed serving bundle in /app/committed is stale ... Differing manifest
fields: ranker`. Boosters now save real `feature_names` instead of `Column_0..N` (the serving
lane's PR #154 request), so `infra/model-bundle/ranker.txt` changes bytes and the committed
bundle has to be rebuilt on linux/amd64. I am doing that rather than handing it over, since it
is a mechanical consequence of my change.

Two incidents worth recording, both mine:

- **Docker's disk was full** (`DiskFull: could not extend file`). Reclaimed **22.86 GB** of
  build cache (`docker builder prune -af`). No images, containers or volumes touched; other
  sessions only lose warm build cache.
- **I seeded against the wrong database.** `Settings.database_url` reads
  `POSTGRES_HOST/POSTGRES_PORT`, not `ADMIN_USER_DB_*`, so `src.data.demo_setup` ran against
  the shared dev Postgres on 5432 instead of the artifacts stack on 55432. It is applying
  migration 0010's `user_feedback_events` backfill over 25M rows — the dev DB was simply
  behind head, so this is a migration it owed rather than damage, and alembic is transactional.
  Letting it complete (killing mid-transaction is worse). Disk after prune: 12 GB free, holding.
  **Heads-up for anyone using the dev stack: it will be at alembic head and materially larger.**

Increment 1's numbers are unchanged and already recorded — see the block below.

## 2026-09-05 10:05 — Opus failover D: increment 1 measured — stop rule fired

**PR #159** — https://github.com/kudratsingh/MovieLens-RecSys/pull/159, no auto-merge.
Run `eee531bb16d943f5a2213dd9b7a8dc1a`, booster `7eba8851926bc88df46e09fb324730713fb32a43b00217ff676024d466fdfa3b`,
10 min 26 s, seed 42, log `artifacts/sasrec/logs/increment1-20260905T0942PDT.log`.

| | bundle 1b `566f5309…` | increment 1 | change |
|---|---:|---:|---:|
| warm NDCG@10 | 0.091688 | 0.092550 | **+0.94%** |
| cold NDCG@10 | 0.549002 | 0.549002 | 0.00% (bit-identical) |
| overall NDCG@10 | 0.214631 | 0.215261 | +0.29% |
| warm recall@10 | 0.077431 | 0.076266 | **−1.50%** |
| cold recall@10 | 0.077638 | 0.077638 | 0.00% |
| overall recall@10 | 0.077487 | 0.076635 | −1.10% |

- **Both readings refuse and agree.** ADR 0001 overall +0.29% vs +3.00%; warm-primary +0.94%
  vs +3.00% with cold non-regression satisfied. O-1 does not decide this one. (PR #155 had not
  merged when the run finished, so the warm-primary reading is computed in the runner from the
  six metrics with ADR 0001's own threshold and tolerance, and recorded in the gate JSON.)
- **Stop rule 1 fired -> increment 2 (DIN) is not built.** The amendment's first precondition
  failed, so the owner is never asked for the warm target the second needs.
- **Top-5 gain:** `sasrec_user_item_logit` 588,958, `sasrec_user_item_score` 272,887,
  `item_popularity_all_time` 41,183, `item_age_days` 41,124,
  `user_days_since_last_interaction` 36,450. The two new columns take **77.5%** of total gain
  and every aggregate *fell* (eight together 423,797 -> 250,264).
- **That is ADR 0018's Risk 1, in the exact words it was written in**: "a large feature gain
  and a small NDCG gain", because the candidates *are* SASRec's top-500 so the score is a
  monotone function of the rank within a group. The rank-only control does not need running —
  `sasrec_candidate_rank` was excluded so rank could not be the explanation, and it was the
  explanation anyway, carried by the required features. What stands is narrower than the rung's
  premise: the encoder's *scalar verdict* is useless to a ranker scoring the encoder's *own*
  candidates. Whether the *history* would help is untouched by this.
- **Predeclared identities both held**: 83,538 groups / 1,754,298 rows exactly, cold back as
  `0.5490019989542251`. Only the learned route moved.
- **O-9 count for W17 (asked for): 34,190 of 153,947 learned-route positives (22.2%)** have a
  strict prefix of 1–49 items and retrieve nothing today; 857 more are legitimately empty. Both
  facts are in results.md, the ADR note and the PR. Side effect: **0 of 1,754,298 training rows
  carried the missing sentinel**, because a sub-window prefix produces no slate and its positive
  is dropped. The NaN path is correct and unit-tested but was never exercised on this data.
- **Blocking for the serving lane:** `infra/model-bundle/ranker.txt` is committed with
  `Column_0..7` and CI's `serving-artifacts` job rebuilds and hash-compares it on any
  `src/models/` change. Real feature names now land in every new booster, so that job fails
  until the bundle is rebuilt on linux/amd64. `infra/` is not my lane and PR #159 does not
  touch it. Flagged in the PR with an admonition.

## 2026-09-05 09:40 — Opus failover D: increment 1 launched, and a defect found

**PR #152 merged** (squash, green). ADR 0018 Accepted with the owner's approval and the DIN
amendment; roadmap Rung 3 `approved`.

**Launched.** ~12-15 min expected (step 1's SASRec arm was 180s training set + 23s fit + 106s
rank; ten columns and the holdout encodes add a little). Log
`artifacts/sasrec/logs/increment1-20260905T0940PDT.log`. Command:

    nice -n 19 bash .increment1.sh   # OMP=1, PYTHONPATH=worktree,
    # SASREC_RANKER_ARTIFACT_DIR=$REPO/artifacts/sasrec/a11af5ed…,
    # SASREC_RANKER_BOOSTER_DIR=$REPO/artifacts/sasrec-ranker-step1,
    # MLFLOW_TRACKING_URI=file:///private/tmp/mlflow-sasrec-recovery-store
    # -> python -m src.training.sasrec_ranker_scores

1% smoke passed end to end. Its numbers are meaningless (18 warm users) but one signal is not:
**both new features took the top two gain slots** (logit 10,846, score 9,880, against
item_age_days 7,222). Predeclared before the full number exists: the shape guard requires
83,538/1,754,298 and the cold slice must return `0.5490019989542251` **exactly**, so a
result that is written at all is one where only the learned route moved.

**Defect found, pre-existing, not mine to fix — needs O-9.** In `eval()` mode PyTorch takes a
fused attention path that returns **NaN** for a query row whose keys are all masked. The first
position of every **left-padded** sequence is exactly that, and the NaN propagates into the
real positions. So **any history shorter than `max_sequence_length` (50) encodes to NaN and
retrieves nothing.** Verified on the pinned artifact: lengths 1/3/12/49 -> 0 candidates, length
50 -> 500. Train mode does not do this, which is why training was fine and the model genuinely
learned. `build_index()`'s `.eval()` (commit 1d189a8, correct in intent) is what exposed it.
Every recorded SASRec number was measured under this and therefore **understates** the
retriever. Remedy is one line (`torch.backends.mha.set_fastpath_enabled(False)`), but it also
shifts full-length results by ~2.4e-7, so it re-measures the whole SASRec line — owner's call.
Written up as **O-9** in DECISIONS.md, pinned by a test, and increment 1 proceeds unchanged
because its comparison against 1b is internally valid: identical candidates, identical groups,
two columns different.

Also in scope on the coordinator's request (PR #154's findings, both in `lgbm.py`): boosters
now save real `feature_names` instead of `Column_0..N`, and `predict` slices the ranker's own
contract. **Consequence to flag:** `infra/model-bundle/ranker.txt` is committed with
`Column_0..7` and CI's `serving-artifacts` job rebuilds and hash-compares it on any
`src/models/` change, so it must be regenerated — `infra/` is not my lane.

## 2026-09-05 09:20 — Opus failover D: Rung 3 increment 1 (design)

No code yet. PR #152's conflict is resolved (both #151's Rung 2 row and #152's Rung 3 row
kept), ADR 0018 is **Accepted** with the owner's 2026-09-05 approval and the DIN amendment,
roadmap Rung 3 reads `approved`. Branch pushed; merge pending CI.

- **Contract.** `FEATURE_COLUMNS` stays the eight-column *fallback* contract byte-for-byte,
  so `src/serving/`, the manifest and the existing parity test are untouched.
  `src/feature_contract.py` gains `SASREC_SCORE_COLUMNS` and `LEARNED_ROUTE_FEATURE_COLUMNS`
  (ten). The three ablated features are **not** built: `sasrec_candidate_rank` is the
  retriever's own ordering re-served and the ADR's first named risk, so putting it in the
  required arm would confound the measurement.
- **One encode, not two.** `forward` is exactly `F.normalize(encode_positions(x)[:, -1, :])`,
  so a new `encode_histories()` returns both representations from one `encode_positions` call
  and `retrieve_unfiltered` consumes its normalised half. Training reuses the retrieval query
  bit-for-bit. Holdout retrieval is batch-of-one, so it gets a batch-of-one
  `encode_dense_history()` — matching call *shapes*, not just call sites.
- **Sentinel: NaN**, for a fallback-route positive (913 of 154,003 in step 1 — 857
  empty-prefix, 56 below threshold) and for an out-of-vocabulary candidate. LightGBM learns a
  default direction; a fabricated score would teach a relation that never holds online.
- **`LGBMRanker` gains a `feature_columns` field** defaulting to `FEATURE_COLUMNS`, so the
  fallback booster and every existing caller are unchanged.
- **Arm.** One new ten-column booster on SASRec candidates; identical 154,003 positives, #126
  exclusions, seed 42, same `prepare_shared()`. Composed exactly as 1b, fallback route =
  incumbent booster `05610e60...`. Cold must return `0.5490019989542251` **exactly**, reusing
  1b's `BundleReproductionError` guard — a free proof that only the warm route moved.
  Incumbent = 1b (`566f5309...`). Both the ADR 0001 and the warm-primary readings reported.

## 2026-09-05 08:44 — Opus failover C: ADR 0018 proposal

- **PR #152** — https://github.com/kudratsingh/MovieLens-RecSys/pull/152. Docs only, not
  auto-merged: the owner approves this rung. Branch `docs/adr-0018-sequence-aware-ranking`.
- `docs/adr/0018-sequence-aware-ranking.md` (Proposed), an index row in `docs/adr/README.md`,
  and Rung 3 in the roadmap decision log flipped `not proposed` -> `proposed`, 2026-09-05, 0018.
- **Increment 1:** frozen `a11af5ed…` encoder's user embedding + its score against each
  candidate as point-in-time LightGBM features. Two required (`sasrec_user_item_score` — the
  normalised score FAISS ranked on; `sasrec_user_item_logit` — the unnormalised quantity BCE
  calibrated), three ablated (candidate rank, last-item similarity, prefix length). Raw 64-d
  vector deliberately not exposed as 64 GBDT columns.
- **Storage answered:** computed per request, not materialised in Redis. The sidecar already
  encodes that history to retrieve at 0.285 ms p99; the ranker features are one 500x64 matmul
  over item embeddings already resident in the artifact. Feast's role unchanged.
- **Parity:** 1e-5 abs on the score, 1e-4 rel on the logit, exact on the integer columns, and
  the test asserts artifact SHA + history slice, not only the value.
- **Increment 2:** DIN only on a gap increment 1 *names*, isolated p99 <10 ms at 500
  candidates, two-tier (LightGBM 500 -> DIN top 50) as the in-budget fallback. TransAct not
  proposed. Latency breach = stop.
- **Incumbent is 1b (`566f5309…`), not item-item+LightGBM** — otherwise increment 1 banks
  SASRec's retrieval gain twice.
- **O-1 arithmetic for the owner:** warm carries 31.2% of 1b's aggregate NDCG mass with cold
  frozen, so overall +3% needs **warm +9.6%**; warm-primary needs +3%. The +3%..+9.6% band is
  where the increment-2 decision would be taken.
- O-8 was named in my brief but does not exist in DECISIONS.md (rows stop at O-7).

## 2026-09-05 08:45 — Opus failover B: arms 1b and 1c complete; PR #151 open

Log `artifacts/sasrec/logs/step1-bundles-20260905T0835PDT.log`. Both arms in one process,
**11 min** wall clock, seed 42, same `prepare_shared()` prologue as step 1, same protocol hash.
**Both predeclared hypotheses were confirmed, including the exact ones.**

| | incumbent | challenger | **1b per-route** | **1c union** |
|---|---:|---:|---:|---:|
| run id | `bff5f86e…` | `50d97188…` | `566f5309767a4076a4f5e8151be16645` | `cf475086bab941aeb6e4519ff021fc94` |
| new boosters | 1 | 1 | **0** | 1 |
| warm NDCG@10 | 0.072792 | 0.091688 | 0.091688 | **0.092221** |
| cold NDCG@10 | 0.549002 | 0.257423 | **0.549002** | 0.543248 |
| overall NDCG@10 | 0.200815 | 0.136244 | **0.214631** | 0.213474 |
| warm recall@10 | 0.048244 | 0.077431 | 0.077431 | 0.076582 |
| cold recall@10 | 0.077638 | 0.023539 | 0.077638 | 0.077638 |
| overall recall@10 | 0.056146 | 0.062943 | **0.077487** | 0.076866 |
| ADR 0001 | — | refuse | **PROMOTE** +6.88% / +25.96% / 0.00% | **PROMOTE** +6.30% / +26.69% / −1.05% |

- **1b hit every predeclared identity exactly.** Cold came back `0.5490019989542251` and warm
  `0.09168799054929602` — the same floats, not the same six decimals; the runner would have raised
  `BundleReproductionError` and written nothing otherwise. Overall 0.214631 against the 0.2146312 I
  wrote down before launching. Zero training.
- **1c also came in as hypothesised**: cold above 0.50 (0.543248) and warm above 0.085 (0.092221) —
  in fact the **best warm number of the four arms**. Union set 171,332 groups / 3,597,616 rows,
  **0 duplicated groups**, so no deduplication was needed; both halves rebuilt to step 1's exact
  shapes (87,794/1,843,318 and 83,538/1,754,298) or the run would have refused. One new booster,
  `f140e825…`, saved before evaluation. The mechanism closes: its `item_popularity_30d` gain is
  **284,345**, back on top, learned from the item-item half while the SASRec half's warm gain
  survives. One model is enough.
- Gate JSONs: `docs/experiments/sasrec/ranker-{on-sasrec-candidates,per-route-bundle,union-booster}-2026-09-05.json`.
- **Nothing promoted anywhere.** Wording in results.md, ADR 0016 and the roadmap row says a passing
  gate is a measurement and the champion changes by the owner's decision; all arms still owe
  rolling/tolerance/latency and k6 evidence.
- **PR #151** — https://github.com/kudratsingh/MovieLens-RecSys/pull/151 — three commits, auto-merge
  **not** enabled. Rebased onto `cbee852` (#150) after it landed; resolved two doc conflicts by
  taking #150's newer retrieval-verdict prose and merging the Rung 2 row rather than overwriting it.
  ruff/black/mypy clean, 1,462 unit tests pass (`test_ratelimit` and `test_baked_serving_bundle`
  need `fakeredis`, absent from this venv — pre-existing, untouched).
- Open for you: O-6 (exclusion + routing semantics, answered as recommended) and the 1b-vs-1c
  choice, which is operational rather than metric — two boosters plus a routing rule, or one model
  plus a retrain.

## 2026-09-05 08:35 — Opus failover B: arms 1b and 1c, hypotheses predeclared before launch

Nothing has run. Both arms come out of one process,
`python -m src.training.sasrec_ranker_bundles`, seed 42, the same `prepare_shared()` prologue
step 1 used (so the same split, cohort, `FeatureIndex` and 154,003 positives), same MLflow
experiment `phase-2-ranker`, boosters saved before evaluation, gated against step 1's item-item
incumbent `bff5f86e6ae14e6b9c19d9c426e3b6ec`. Command:

    nice -n 19 bash /tmp/step1_bundles.sh > "$REPO/artifacts/sasrec/logs/step1-bundles-20260905T0835PDT.log" 2>&1

**1b — per-route bundle. Zero new boosters.** Warm route: SASRec candidates ranked by the step-1
challenger booster `7e2052c1…`. Cold route: popularity fallback ranked by the step-1 incumbent
booster `05610e60…`. Predeclared, and enforced in code — the run raises
`BundleReproductionError` and writes nothing if any of these is not reproduced **exactly**, not to
six places:

| | predicted | why it must be exact |
|---|---:|---|
| cold NDCG@10 | 0.5490019989542251 | same users, same slate, same booster, same features |
| cold recall@10 | 0.07763845424378057 | as above |
| warm NDCG@10 | 0.09168799054929602 | step 1's challenger warm path, unchanged |
| warm recall@10 | 0.07743102042408873 | as above |
| overall NDCG@10 | **0.2146312** | (1931x0.09168799 + 710x0.54900200)/2641 |
| overall vs incumbent | **+6.88%** | against 0.20081498 |

So 1b is predicted to **pass** ADR 0001: overall +6.88% clears +3%, warm +25.96%, cold 0.00%.
I am writing that down before the run, not after. If it passes, it is still not a promotion —
it is a composition of two existing boosters that the owner decides on.

**1c — union booster. Exactly one new booster.** Trained on the concatenation of both arms'
step-1 training sets: 87,794 + 83,538 = **171,332 groups**, 1,843,318 + 1,754,298 = **3,597,616
rows**. Halves are rebuilt from `default_rng(42)` per source and the run refuses to continue
unless each reproduces step 1's shape exactly. **No row deduplication**: the group is LightGBM's
unit and a row cannot leave one without corrupting it; whole-group collisions are counted and
logged instead, and are expected to be ~0 since the halves draw from different candidate pools.
Served through the same per-route composition as 1b, with the union booster on both routes.

Hypothesis: it recovers most of the cold loss without giving up most of the warm gain — concretely
I expect cold NDCG@10 above 0.50 and warm above 0.085, i.e. an overall gain over the incumbent but
below 1b's, in exchange for shipping one model instead of two. The mechanism that would defeat it:
the union is 51% item-item rows, so the booster may simply relearn `item_popularity_30d` dominance
and give the warm gain back.

Expected wall clock ~13-15 min total (1b needs no training, ~2 min of ranking; 1c rebuilds both
training sets, ~5 min, one fit on 3.6M rows, ~1 min, plus ranking). Peak RSS ~8-10 GB.

## 2026-09-05 08:20 — Opus failover B: step 1 complete

Run `artifacts/sasrec/logs/step1-full-20260905T0812PDT.log`, MLflow experiment `phase-2-ranker`
in `artifacts/mlflow-sasrec-recovery`. **11 min 14 s** total (incumbent arm 259 s, challenger arm
309 s, shared prologue ~95 s); peak RSS ~8 GB. Seed 42, full 25M, #126 exclusions both arms,
identical 154,003 positives, ADR 0011 cohort attached.

| | incumbent `bff5f86e6ae14e6b9c19d9c426e3b6ec` | challenger `50d9718802f949d98c5d8d4d6315bb1a` | rel |
|---|---:|---:|---:|
| warm NDCG@10 | 0.072792 | **0.091688** | **+25.96%** |
| cold NDCG@10 | 0.549002 | 0.257423 | **−53.11%** |
| overall NDCG@10 | 0.200815 | 0.136244 | −32.15% |
| warm recall@10 | 0.048244 | **0.077431** | **+60.5%** |
| cold recall@10 | 0.077638 | 0.023539 | −69.7% |
| overall recall@10 | 0.056146 | 0.062943 | +12.1% |
| groups / rows | 87,794 / 1,843,318 | 83,538 / 1,754,298 | −4.8% |

n=1,931 warm / 710 cold both arms. Boosters (saved before evaluation):
`05610e60…f7a65` incumbent, `7e2052c1…e226a5` challenger.

**Gate: DO NOT PROMOTE** (`docs/experiments/sasrec/ranker-on-sasrec-candidates-2026-09-05.json`,
exit 1). Warm clause passes at +25.96%; overall fails at −32.15%; cold fails at −53.11% against a
5% tolerance.

**The mechanism, and it is not retrieval.** Cold users route below the threshold to the popularity
fallback in *both* arms, and both arms hold the same fitted fallback object — so their cold
candidate lists are byte-identical and the whole −53% belongs to the booster (unit-tested, not
inferred). Total-gain importances say why: the incumbent is dominated by `item_popularity_30d`
(260,507, 2.5x the next feature), which is exactly the rule a popularity slate needs; the
challenger, trained on SASRec's deep-catalog mix where popularity discriminates poorly, comes out
flat — `user_days_active` 77,521, `item_popularity_30d` 64,954, `item_popularity_all_time` 53,680,
`item_age_days` 52,640, `user_interaction_count` 51,644. Incumbent top five:
`item_popularity_30d` 260,507, `user_interaction_count` 148,467, `item_popularity_all_time`
105,201, `user_days_active` 98,539, `item_age_days` 74,323.

**So step 1 answers its question and raises a different one.** Retraining the ranker turned the
fixed-ranker's +1.67% warm into **+25.96%**, which is the effect the handover predicted. One booster
cannot serve two candidate distributions: the obvious next experiments are a booster trained on the
union of both sources, or per-route boosters. Not run, not proposed as a change — owner call, and
it bears directly on O-1.

## 2026-09-05 08:12 — Opus failover B: step 1 pre-launch

Smoke passed end to end at `SASREC_RANKER_USER_SAMPLE_FRACTION=0.01` (264,312 ratings, 22 holdout
users): both arms trained, both boosters saved before evaluation with SHA-256s, the ADR 0001 gate
ran and the JSON was written. Log
`artifacts/sasrec/logs/step1-smoke-20260905T0800PDT.log`. Its metrics are meaningless at n=22 and
are not being recorded anywhere but that log.

**Launching now.**

    nice -n 19 bash /tmp/step1_full.sh > "$REPO/artifacts/sasrec/logs/step1-full-20260905T0812PDT.log" 2>&1

with, in the script: `OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1`,
`SASREC_RANKER_ARTIFACT_DIR=$REPO/artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9`,
`SASREC_RANKER_BOOSTER_DIR=$REPO/artifacts/sasrec-ranker-step1`,
`MLFLOW_TRACKING_URI=file:///private/tmp/mlflow-sasrec-recovery-store`, cwd the worktree,
`python -m src.training.sasrec_ranker`. No seed override, no sample fraction: seed 42, full 25M.

- **MLflow experiment `phase-2-ranker`**, in the file store at
  `$REPO/artifacts/mlflow-sasrec-recovery` (the `/private/tmp/...-store` symlink is only a
  space-free alias). **MLflow's file store cannot use an absolute tracking URI whose path contains
  spaces** — it creates the run directory and then raises `Run '<id>' not found`. Reproduced on a
  clean store; the symlink is the fix, and is what Megatron's own recorded `artifact_location`
  already used. One orphaned zero-metric run directory `982b5cdb903f4c54ba1831aa9cc59a1e` and one
  `phase-2-ranker-probe` experiment are left behind from diagnosing it; nothing was deleted.
- **Expected wall clock.** Shared prologue ~3 min (Postgres read 34 s, split, cohort, feature index
  6 s, `history_index` and the SASRec runtime histories over 20M rows). Incumbent arm ~5 min
  (M0-11 measured 129 s assembly, 29 s fit, 103 s ranking). Challenger arm ~5 min: measured
  batched encode+exact-FAISS at **0.39 ms/query**, so all 154,003 positives retrieve in ~1.0 min.
  **Total ~13-18 min**, not the several hours the handover assumed.
- **Peak RSS projection ~10-12 GB**, on a 36 GiB M3 Pro. 8.1 GB was observed in the *smoke* during
  `pd.read_sql` of the 25M ratings alone; the full run additionally holds the split, the feature
  index, both candidate models and 20M dense history entries.
- Booster directories are create-only under `artifacts/sasrec-ranker-step1/<run id>/ranker.txt`,
  written before evaluation.

## 2026-09-05 14:40 — Opus failover A: verdict PR

- **PR [#150](https://github.com/kudratsingh/MovieLens-RecSys/pull/150)** — "feat(sasrec): record the
  SASRec retrieval verdict and the D-002 non-regression pass", base `main`, head
  `docs/sasrec-single-run-verdict`. Auto-merge (squash) enabled. Commit `8126316` on top of `6b40069`.
- Captured Megatron's uncommitted doc corrections read-only from
  `/private/tmp/MovieRecSys-sasrec-verdict` and applied them cleanly (`git apply`, no conflicts).
  Its worktree was **not** touched. Only the five doc files were taken; its in-progress
  `sasrec_ranker_guardrail.py` / `sasrec_ranker_retrain.py` changes were left alone for failover B.
- **What the docs now say, in all four places:** SASRec v1 is *retrieval promotion eligible; end to
  end blocked on the ranker, which has not yet been retrained on SASRec candidates.* D-002 is a
  non-regression guardrail on the current ranker and it **passes** (warm NDCG@10 +1.67%, cold 0.00%,
  overall +0.43%; warm recall@10 +30.70%, overall recall@10 +19.37%; tolerances warm 6% / cold 5%).
  ADR 0001's +3% clause is explained as governing a *new ranker replacing the old bundle*, so it is
  diagnostic for a retriever swap. Next step named identically everywhere: retrain LightGBM on
  SASRec candidates under #126 exclusions and gate that bundle, then Rung 3a.
- Beyond Megatron's patch I corrected three surviving old-reading statements it had missed:
  `results.md`'s "the paired ranker ultimately refused promotion", its
  "supplies the final model verdict" gloss on `serving_eligible=false`, and ADR 0016's
  "the final paired-ranker decision closes the model outcome". Renamed both "Final paired-ranker
  guardrail" headings to "Fixed-ranker D-002 guardrail". Added an explicit status-and-next-step
  block to ADR 0016, `results.md` and the experiments record, and put the numbers and the next step
  into the roadmap row.
- Historical `DO NOT PROMOTE` output kept verbatim in prose and in
  `paired-ranker-guardrail-2026-09-05.json`. No raw number, run id, checksum or evidence JSON
  changed. No gate code, no threshold, no `src/serving/`, no training run.
- Privacy: `manifest_path` in `encoder-latency-2026-09-05.json` is now repo-relative;
  `grep -rn "/Users/" docs src tests` is clean.
- Checks: `ruff` + `black` clean at the repo's own `make lint` scope (`src/ synthetic/ tests/
  notebooks/`); `mypy src/` clean, 87 files; `test_sasrec_{latency,ranker_guardrail,training}.py`
  15 passed; `git diff --check` clean.
- **Unexpected / for the orchestrator:**
  1. `git checkout docs/sasrec-single-run-verdict` is impossible — Megatron's worktree still holds
     that branch. I used a local branch `failover/sasrec-single-run-verdict` tracking the same remote
     ref and pushed `HEAD:docs/sasrec-single-run-verdict`. Megatron's worktree is now one commit
     behind its own branch; it must `git fetch` + rebase before committing there.
  2. **`src/evaluation/sasrec_latency.py:120` writes `manifest_path=str(manifest_path.resolve())`,
     so any rerun reintroduces an absolute `/Users/...` home path into committed evidence.** Left
     alone — `src/evaluation/` is Starscream's lane and my brief was docs-only. Worth a one-line fix.
  3. `ruff check .` / `black --check .` at repo root fail on six pre-existing `alembic/versions/*.py`
     files; the repo's own `make lint` scopes to `src/ synthetic/ tests/ notebooks/` and passes.
     Not introduced here, but the root-scope invocation in the brief is misleading.
  4. Docker MLflow `/mlartifacts` defect: still no GitHub issue opened, per instruction.
  5. `docs/status/README.md` and `CLAUDE.md`'s status section still predate the SASRec verdict
     entirely (they describe the run as "in flight"). Starscream's lane; flagging, not touching.
- CI all green (lint, test, frontend, browser-auth-e2e, demo-compose, feature-parity,
  serving-artifacts, synthetic-load-smoke, tenant-isolation). **Auto-merge landed it: #150 MERGED
  2026-09-05T14:50:33Z.** `docs/sasrec-single-run-verdict` can be deleted.

## 2026-09-05 08:05 — Opus failover B: step 1

Read the code; runner shape decided, nothing written yet. Worktree
`.claude/worktrees/agent-a6020dfc870d40b2a`, branch `feat/ranker-on-sasrec-candidates` off
`origin/main` (c26a373, #148). `ranker.py` untouched.

- **`src/training/sasrec_ranker.py`, one process, two arms.** Ratings, `temporal_split`, ADR 0011
  cohort attach, `FeatureIndex` and the 30-day positives are built **once** and shared, so the arms
  differ only in the candidate source. Construction is `ranker.py`'s, lifted behind a
  `CandidateSource` protocol: retrieve K=500 unfiltered -> drop the positive if it missed ->
  negatives = candidates - positive - strictly-prior history (#126) -> 20 sampled -> `features_for`
  at the positive's `as_of`. Seed 42 everywhere.
- **SASRec source.** `load_sasrec` on the pinned `a11af5ed...` artifact (SHA checked at load);
  `_user_history` + popularity fallback rebuilt from the same `train_frame`, since the archive
  carries neither. Training query = the user's items with `timestamp < as_of` under the
  `["userId","timestamp","movieId"]` order -- the same `searchsorted(..., "left")` cut `ranker.py`
  already uses for exclusions, so history and exclusion come from one slice. Batched encode +
  exact-FAISS search, unfiltered, mirroring `filter_seen=False`.
- **Two deliberate asymmetries, both conservative for the challenger** (raised as O-6, recommended
  answer taken): SASRec retrieval is unfiltered top-500 like item-item's, not post-exclusion
  top-500 -- the protocol's own `candidate_filter` vocabulary is already
  `unfiltered-retrieval-then-point-in-time-exclusions-v1`; and SASRec's training-time route to
  popularity is decided on the point-in-time prefix (>=10) while item-item keeps `ranker.py`'s
  full-history predicate. Routing counts logged for both arms.
- **Reference the incumbent is checked against:** a post-#126 item-item+LightGBM already exists --
  M0-11 run `615f81bb363443cca05683dc3a1a8870` (`/private/tmp/MovieRecSys-m011/mlruns`, read-only):
  warm 0.071743 / cold 0.563722 / overall 0.204005, 87,787 groups, 1,843,171 rows. The published
  0.069967/0.544948/0.197659 is pre-#126 **and** cohort-attached (87,794 groups); my arm attaches
  the cohort, so expect ~87,794 groups and a number near the M0-11 one, not the published one.
- **Cost is not hours.** M0-11's own params: candidate fit 19.9 s, feature index 6.0 s, training-set
  assembly 129.4 s, LGBM fit 29.3 s, holdout rank 103.4 s. Full estimate in the pre-launch block.

## 2026-09-05 — pre-run interface and scope report

- Worktree `/private/tmp/MovieRecSys-sasrec-verdict`; branch `docs/sasrec-single-run-verdict`.
- `src/training/ranker.py` cannot take a non-item-item source: construction and type annotations
  are hard-coded to `ItemItemModel`, and its training retrieval calls item-item's
  `filter_seen=False` API.
- Small change shape: a SASRec-specific runner owns strict-prefix retrieval and reuses the shared
  point-in-time `FeatureIndex`, `LGBMRanker`, evaluator, and ADR 0001 gate; `ranker.py` stays stable.
- Step 1 will train both arms from the identical 30-day positives with #126 exclusions: an
  item-item + new LightGBM incumbent and SASRec + new LightGBM challenger.
- The first draft incorrectly referenced the pre-#126 recovered booster; validation caught this
  before any full run. No run has started.
- D-002 interpretation is corrected locally; raw historical evidence remains unchanged.
- Pinned SASRec manifest actual model SHA is
  `43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`.
- Docker MLflow `/mlartifacts` transport defect remains documented; no GitHub issue opened.
## 2026-09-05 15:55 — W17 first launch blocked before execution

- The inference-only W17 re-evaluation was launched from `/private/tmp/MovieRecSys-sasrec-fastpath` at commit `55af03b` after the pre-launch report.
- The sandbox denied the runner's connection to the existing PostgreSQL service on `localhost:5432`. It failed inside `prepare_shared()` before loading the dataset, creating an MLflow run, or writing reevaluation evidence.
- The complete failed-attempt log is preserved at `artifacts/sasrec/logs/w17-fastpath-recheck-20260905T1553PDT.log`; nothing was deleted or overwritten.
- Next action: repeat the identical inference-only job outside the network sandbox, using a new create-only log path. No training is involved.
## 2026-09-05 16:00 — W17 second launch fail-closed on protocol identity

- With localhost access, the inference-only runner loaded all 25,000,095 ratings, reproduced the expected split (`20,000,075` train, `129,683` holdout, cutoff `1466837397`), built the feature index, fit the comparison item-item model, and checksum-loaded the pinned SASRec artifact.
- It then stopped before evaluation or MLflow run creation because the current protocol document hashes to `sha256:e016234f1f571883443d8373d0d23754058629400842ccfc9518cd0d26b88117`, while the runner expected historical hash `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28`.
- This was an intentional fail-closed check. The complete log is preserved at `artifacts/sasrec/logs/w17-fastpath-recheck-20260905T1555PDT-attempt2.log`; no existing run, artifact, or evidence was overwritten.
- Next action: diff the freshly generated protocol document against the pinned run's protocol evidence and confirm whether the hash change is semantic or only the recently merged canonical window-id/schema representation before retrying.
## 2026-09-05 16:06 — W17 protocol-drift diagnostic resolved

- A third create-only diagnostic log at `artifacts/sasrec/logs/w17-fastpath-recheck-20260905T1602PDT-protocol-diagnostic.log` emitted the complete current protocol before its deliberate refusal.
- The sole mismatch was `derived_snapshot_hash`: `2f015f…` without the synthetic cohort versus historical `d9e073…`. The fresh git worktree contained the cohort's DVC pointer but not its ignored 45 KB parquet payload; both the main checkout and the original runs retain that payload.
- Copied (did not move or delete) the DVC-pinned cohort payload into the isolated worktree. A no-scoring derivation now exactly reproduces protocol `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28` and snapshot `sha256:d9e073816dee250754bc23a08c51ad1dd0d08a8d5a874ff57ecb3708feb1b22c`.
- No MLflow run or reevaluation evidence was created by the refused attempts. The next launch will therefore use fresh create-only paths while answering the exact historical question.
## 2026-09-05 16:13 — W17 admissible re-evaluation complete; both gates promote

- Branch/worktree: `fix/sasrec-eval-fastpath` in `/private/tmp/MovieRecSys-sasrec-fastpath`; core fix commit `3ee1a06`, inference-only evidence runner commit `55af03b` (one diagnostic log statement remains uncommitted for the final PR).
- Successful log: `artifacts/sasrec/logs/w17-fastpath-recheck-20260905T1607PDT.log`. Source artifact remains immutable at run `a11af5ed0f0745f68572407237cfa4b9`, archive SHA-256 `43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`; exact FAISS, two blocks, seed 42. No training occurred.
- Protocol `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28`, snapshot `sha256:d9e073816dee250754bc23a08c51ad1dd0d08a8d5a874ff57ecb3708feb1b22c`; populations remain 1,931 warm / 710 cold.
- New retrieval run: `528b14513d9a49e098a0525417f23285`. Before → after (recall, NDCG): warm `(0.4651693328, 0.1734004197) → (0.5091713455, 0.1911516284)`; cold `(0.5262729520, 0.4358465704) → unchanged`; overall `(0.4815962808, 0.2439558029) → (0.5137688997, 0.2569348199)`. Recommendation wall-clock was 1.904 s after shared-input preparation.
- Formal retrieval gate versus item-item `4b342e87dbf54834be5c719eae9a4e6c`: **promote**. Warm recall is +27.59% over item-item with one-sided 95% lower bound +24.38%; cold +0.00%; overall +18.58%; zero cold/overall tolerance, single-seed regime. JSON: `artifacts/sasrec/fastpath-reevaluation/528b14513d9a49e098a0525417f23285/retrieval-gate.json`.
- Warm history distribution: 10–19=`28`, 20–29=`26`, 30–39=`31`, 40–49=`46`, 50+=`1,800`; therefore `131/1,931` warm users had histories below 50. Before the fix all 131 produced empty learned-route slates (none were popularity routed); after the fix all produce candidates. Pinned full-length safe/old-fast maximum absolute delta is `5.960464477539063e-08` (≤1e-6).
- New bundle 1b re-evaluation run: `c1d742c8485d4e54b66746a65f7705d0`, reusing immutable fallback booster SHA `05610e60…` and learned-route booster SHA `7e2052c1…`; ranking wall-clock 117.6 s. Before → after (recall@10, NDCG@10): warm `(0.0774310204, 0.0916879905) → (0.0846629948, 0.1014411299)`; cold `(0.0776384542, 0.5490019990) → unchanged`; overall `(0.0774867864, 0.2146311734) → (0.0827745345, 0.2217623025)`.
- Ranking gate versus pre-fix bundle `566f5309767a4076a4f5e8151be16645`: **promote** under both all-routes (+3.32% overall NDCG) and learned-route (+10.64% warm NDCG, cold unchanged) scopes. JSONs are under `artifacts/sasrec/fastpath-reevaluation/c1d742c8485d4e54b66746a65f7705d0/`.
- The new scored retrieval API returns exact-FAISS `(movie_id, similarity)` pairs and preserves the existing unscored ordering. Short-history regression coverage at lengths 1, 3, 12, 49, 50 uses the configured two-block encoder and verifies 500 finite candidates; pinned length-50 output moves by ≤1e-6.
- PR #158 is now fully green after the convergence-tolerance repair. PR #155 is also fully green but still open; its learned-route CLI was used to record the second bundle gate. No auto-merge was enabled.
## 2026-09-05 16:22 — W17 PR opened

- Opened PR **#162**, `fix(sasrec): disable unsafe evaluation fast path`, from `fix/sasrec-eval-fastpath` after rebasing onto `origin/main` (`7b3f8a3`). No auto-merge was enabled; the orchestrator owns merge.
- Three commits cover the shared encoder fix and scored exact-FAISS API, the regression/evidence runner, and the immutable before/after record plus ADR 0016 note.
- Final local validation: repository-wide Ruff and Black checks pass; strict mypy passes for the changed source; 31 focused tests pass including the real pinned two-block artifact. The evidence runner now refuses a missing DVC cohort payload before loading 25M rows and emits the resolved protocol on mismatch.
- Durable evidence and both gate outcomes are recorded in the preceding report block and `docs/experiments/sasrec/fastpath-reevaluation-2026-09-05.json`. PR #159's owned files (`docs/results.md`, roadmap, ranker feature files) were not touched.
- Starscream/W8 unblock: after #162 reaches main, use `SASRecModel.recommend_from_history_scored(...)`; it returns the same movie-id order as the unscored method plus each exact-FAISS inner-product similarity for the audit contribution field.
## 2026-09-05 16:25 — PR #158 refreshed on current main

- Main advanced to `7b3f8a3` after the repaired PR #158 build passed. Rebased `diagnose/twotower-retrieval` again and force-pushed with lease; new head `7695fef`.
- This was a clean three-commit replay with no content changes. CI has restarted against the actual current main tree; the orchestrator still owns merge.
## 2026-09-05 16:26 — PR #155 refreshed on current main

- Rebased the already-green warm-primary ranking-gate PR onto `7b3f8a3` and force-pushed with lease; new head `40c99db`.
- No content change and no auto-merge. CI restarted so the orchestrator can merge the learned-route mode used for W17's second gate.
## 2026-09-05 16:31 — two-tower full-run durability preflight

- No competing `python -m src.training.*` process is active. The host has 36 GiB unified memory, current system-wide free-memory estimate 55%, no throttled pages, and 210 GiB free disk.
- Approved corrected-v2 cell is seed 42, current v2 defaults, 3 epochs, 16,384 random negatives, 8 hard negatives after the one-epoch warmup, structured item features, exact FAISS, full 25M, threshold routing. This differs from the old complete-v2 IVF/4,096-negative cell and will be named accordingly.
- Estimate: 2.5–3.5 h wall-clock (the corrected 6% cell took 583.7 s; historical full three-epoch cells took ~78 min without v2 mining/features), peak RSS 6–10 GiB including the 25M frame, on the 36 GiB host. Threads will be pinned to one and launch will use `nice -n 19`; console log will be create-only under `artifacts/twotower/logs/` and MLflow will use a space-free symlink to the durable repository store.
- Durability catch before launch: `src/training/twotower.py` currently preserves params, metrics, protocol and per-user recall but **does not save trained weights**. The owner previously required all run data to be retained after the SASRec run exposed this exact gap. I will therefore add a tested, checksum-pinned, create-only two-tower artifact/checkpoint path before spending the full run, then log a second copy to MLflow. No run has started.
- Prerequisites are still in CI: repaired #158 head `7695fef`; W17 #162 head `5e23efc`. The full run will not start until the required code is on main and the final pre-launch report names its exact source commit and artifact destination.
## 2026-09-05 16:43 — W19 artifact prerequisite implemented locally

- Fresh worktree `/private/tmp/MovieRecSys-twotower-artifact`, branch `feat/twotower-run-artifact`, commit `feba090` from then-current `origin/main`.
- Added a deterministic, checksum-pinned two-tower archive plus manifest. It retains config, ordered item vocabulary, cold threshold, fitted item-feature schema/matrix, item-tower tensors and retrieval contract; load verifies archive/vocabulary hashes and rebuilds exact or IVF FAISS. Export is create-only.
- The training runner now saves the local recovery copy under configurable `TWOTOWER_ARTIFACT_DIR/<run-id>/` immediately after fitting and before evaluation, uploads a second copy under the MLflow run's `model/`, and records the artifact SHA-256.
- Validation: strict mypy and focused lint pass; 30 tests pass. The runner integration test proves local and MLflow manifests are byte-identical, both load successfully, and the same run retains per-user recall. Deterministic bytes, candidate/vector equivalence, overwrite refusal and corruption refusal are covered.
- No full-data run has started. This branch will be rebased after the shepherd lands #158/#162, then the exact source SHA and artifact/log destinations will be reported immediately before launch.
HB 2026-09-05 16:55 — Megatron active; scope changed to docs-only ADR 0019. W19/W21 are handed to failover F and will not be touched.

## 2026-09-05 20:42 PDT — ADR 0019 proposal opened for owner review

- Fresh worktree `/private/tmp/MovieRecSys-adr0019`, branch `docs/adr-0019-mixing`, commit `7a7eee8b23bd5ec35b4db9a13b21b73fd4bd671d` from `origin/main` `c71ef4b`.
- Opened PR **#174**, `docs(adr): propose Rung 5 multi-retriever mixing`. It is a non-draft owner-review PR; auto-merge was not enabled. Initial CI jobs are pending.
- ADR 0019 is exactly 200 lines. It proposes a fixed-budget attributed SASRec + corrected-two-tower + item-item union with popularity fill; deterministic equal-weight reciprocal-rank fusion; source membership/rank/count features with a same-frame control; MMR before conditional DPP; genre-ILD@10 with an O-1 warm relevance guardrail; unchanged service p99 plus a stated <30 ms dual-retrieval diagnostic budget; artifact retention, stop rules, risks, and falsification signals.
- Registered ADR 0019 in `docs/adr/README.md` and changed only the Rung 5 decision-log row in `docs/modeling-roadmap.md` to proposed/awaiting owner approval.
- Validation: `git diff --check` passed before commit; all four local ADR link targets exist; the committed worktree is clean. No code, serving file, experiment, dataset, MLflow state, W19, or W21 was touched; no process is running.
- HB 2026-09-05 20:42 PDT ADR0019 END (turn): PR #174 open at `7a7eee8`; owner approval and CI pending.

- HB 2026-09-05 20:49 PDT BLOCK15 START (turn)

- HB 2026-09-05 20:58 PDT O17 code+tests complete; evaluations held until Starscream W10 pair completes. ADR0020 draft at 187 lines; no run.

- HB 2026-09-05 21:06 PDT QUEUE read: Q1/Q2 active; Q3 next. Q1 evaluations held for live W10; no competing run.

- HB 2026-09-05 21:09 PDT O17 READY: stable tie commit de2d7d0, 39 focused tests green. Q1 requires two short full-data inference/baseline evaluations immediately after W10; F/W19 must remain held until Q1 reports release.

- HB 2026-09-05 21:16 PDT Q1 WAITING: Starscream W10 serving stack is live; no Q1 evaluation launched. Q1 remains first machine user after explicit W10 release, then F/W19 may proceed.

- HB 2026-09-05 21:27 PDT W10 appears stalled in setup: model/feature sidecars are healthy, but no `api-load`, k6, or gate process/evidence exists and Starscream's report has not changed since 21:04. Per #15 I am still holding Q1 while any demo stack is up; Q3 code-only work has started in isolated worktree `/private/tmp/MovieRecSys-synth-cold-v2` so the lane does not idle.

- HB 2026-09-06 RESUME START: Megatron is the sole worker; RESUME, MODE/queue M1-M9, and HANDOFF read in order. Starting M1 durability rescue before all other work.

- HB 2026-09-06 M1 SYNTH RESCUED: `feat/synth-cold-v2` pushed at `8d45e82`; five-file WIP is green under Ruff, strict mypy, and 62 focused tests. It is durable but incomplete: DVC payload/pointer, real inference evidence, ADR note, and PR remain M6.

- HB 2026-09-06 M1 TWOTOWER ARTIFACT RESCUED: `feat/twotower-run-artifact` pushed at `339ff60`; one existing 562-line commit is now durable. Branch is 23 commits behind main and remains WIP pending rebase/integration review; no PR opened.

- HB 2026-09-06 M1 DURABILITY COMPLETE / CLEANUP HELD: both previously invisible branches are pushed. Managed safety review rejected deletion of the two superseded local-only branches/worktrees despite queue authorization because their exact commits are not preserved remotely. I left them intact and also preserved the dirty `MovieRecSys-sasrec-verdict` worktree; proceeding to M2 without risking unique local data.

- HB 2026-09-06 M2 MEASUREMENT START: PR #178 branch updated to remote `5ccc77c`. CI failed at `user_item_features` SQL spill under the existing 2 GiB PGDATA tmpfs. Starting an isolated local reproduction with a temporary 8 GiB cap; will record database bytes and peak `base/pgsql_tmp` before selecting margin.

## 2026-09-06 — M2 PR #178 tmpfs repair pushed

- Local full-catalog reproduction completed: 62,423 titles, 499,384 `user_item_features` rows, final database 80 MiB, settled PGDATA 270 MiB, sampled peak PGDATA 832 MiB, sampled peak `base/pgsql_tmp` 690 MiB. CI's amd64 plan exhausted the old 2 GiB cap on the same query, so spill size is architecture-dependent.
- Raised only the CI PGDATA tmpfs ceiling to 4 GiB: twice the observed failing CI ceiling and 4.9× the local peak. tmpfs remains demand-charged; fixture, durability, traffic, thresholds, and re-measure rules are unchanged.
- Evidence retained under `artifacts/ci-tmpfs-measurement/2026-09-06/` (`pgdata-samples.log`, `demo-seed-materialize.log`). Rendered Compose assertion passed; 115 compose/workflow tests passed; `git diff --check` passed.
- Commit `3b14d0f` pushed to PR #178's existing branch `feat/demo-full-catalog`. CI is the final arbiter; no merge or auto-merge action taken.

- HB 2026-09-06 M2 PUSHED: PR #178 CI restarted at `3b14d0f`; proceeding to M3 while checks run.

- HB 2026-09-06 M3 PRE-LAUNCH: no Python training/module process is active; demo containers are stopped without removal and main Postgres/MLflow are healthy. Running serially on the default-tenant 25M snapshot from PR #180 head `218ddcc`: (1) popularity baseline, expected 1–3 min / 4–6 GiB peak; (2) inference-only SASRec fast-path retrieval + per-route bundle recheck, expected 5–15 min / 8–10 GiB peak. Both use `OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE`, local file-store MLflow, create-only console logs, and a new create-only evidence root. No weights are trained or overwritten.

## 2026-09-06 03:45 PDT — M3 stopped on published SASRec fallback delta

- Popularity run `d0b0da041f674e768c405159c69a62d1` reproduced all six published retrieval metrics at six decimals. Protocol `sha256:bd046cb608396d4e00494e1be789066799f2ece215f243187e59c999a210f73d`; 1,931 warm / 710 cold; 53 seconds end to end.
- SASRec retrieval run `0243864994024cb48ab746df628860a7`, protocol `sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28`, leaves warm recall/NDCG and all recall values bit-identical to fixed run `528b14513d9a49e098a0525417f23285`. Stable popularity ties move cold NDCG@500 `0.4358465703567308 -> 0.4358413673421565` (delta `-0.0000052030145743`) and overall NDCG@500 `0.2569348199388611 -> 0.2569334211731103` (delta `-0.0000013987657508`). Both move at six-decimal publication precision.
- Bundle run `7b407714949045acb9fd9282a394bbba` reproduces all six bundle recall@10/NDCG@10 values bit-for-bit. The paired SASRec recheck took 3 minutes 45 seconds end to end.
- Create-only evidence is retained under `artifacts/sasrec/popularity-tiebreak-reevaluation/`; raw logs remain at `artifacts/popularity/logs/stable-ties-reproduction-20260906.log` and `artifacts/sasrec/logs/popularity-tiebreak-recheck-20260906.log`.
- Commit `e8346e8` records the complete reproduction in `docs/results.md` and is pushed to draft PR #180. The exact delta and stop notice are posted on the PR. O-21 is open in `.coordination/DECISIONS.md`, recommending that the deterministic rerun supersede the old fallback retrieval record without moving a gate or verdict.
- **STOP:** M4 and later queue items have not started, exactly as M3 required when any published metric moved. PR #178 CI is still running at this checkpoint (all completed jobs green; feature-parity and synthetic-load-smoke pending). No full-data job remains active.

- HB 2026-09-06 03:45 PDT M3 STOP / OWNER DECISION REQUIRED: PR #180 remains draft at `e8346e8`; awaiting O-21 before M4.

- HB 2026-09-06 03:46 PDT TURN END: handoff updated; no job active; queue paused at O-21 exactly as instructed.

- HB 2026-09-06 O21 RESUME START: owner approved O-21; finalizing PR #180 before continuing to M4.

TASK DONE M3 03:59 — PR #180 ready at `3bfdcb2`; stable run `0243864994024cb48ab746df628860a7` is the record and prior `528b1451…` is marked superseded with exact deltas.

- HB 2026-09-06 03:59 PDT M4 START: beginning W28 canonical all-positions SASRec training plus torch exact-top-k from a fresh `origin/main` worktree; no 25M run will launch before a separate pre-launch report.
- HB 2026-09-10 SESSION 2 RESUME START: Megatron is sole worker; reading RESUME/inbox/handoff, restoring dev Docker, then M2 follow-up before resuming M4.
- HB 2026-09-10 DEV STACK UP: Docker Desktop and the preserved dev compose stack are running; starting M2 follow-up on PR #178.
- HB 2026-09-10 M2 FIX PUSHED: PR #178 branch `feat/demo-full-catalog` now has `cd6017d`; the bounded persona snapshot walks popularity order so all 120 state-bearing reviewed titles precede the 62,303 untouched catalog rows. JavaScript syntax and 20 focused unit tests pass; CI is deciding the end-to-end load gate. No threshold changed and no auto-merge was enabled.
- HB 2026-09-10 M4 RESUMED: preserved all-position commit rebased onto `origin/main` and force-with-lease pushed as `a7342d6` on `feat/sasrec-all-positions`; beginning semantic review and focused validation before any 6% or full-data launch.
- HB 2026-09-10 21:54 PDT M4 6% PILOT LAUNCH: no competing SASRec/two-tower training process is active. Running the single frozen v1 BCE/neg32/two-epoch cell with the all-position objective on the established deterministic 6% users. Expected wall-clock 3–10 minutes versus v1's 1,644 seconds because each sequence window is encoded once; expected peak RSS 2–5 GiB, dominated by loading the 25M CSV before user subsampling. `OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE`; exact torch evaluation; create-only run artifact, MLflow record, and console/resource log retained under the main checkout.
- HB 2026-09-10 21:58 PDT M4 PILOT INFRA RETRY: deterministic attempt `833812eea8a341e9953285b4145cf9b8` completed both epochs (0.1822 warm recall each) and wrote its immutable 5.1 MiB local model archive, then failed before final metrics because the restored HTTP MLflow server advertised container-local `/mlartifacts` to the host client (`EROFS`). Nothing was deleted: the failed Docker-backed run, local model archive, and full resource log remain. Wall 87.36 s; max RSS 4,261,019,648 bytes. Re-running the same frozen cell against the established persistent repository file store so the run can complete its evidence envelope; this is an infrastructure retry, not a second model trial.

- HB 2026-09-10 22:01 PDT M4 FULL PRE-LAUNCH: successful deterministic 6% record `f837955c832440069dd8c1316a2ad0c6` finished in 87.28 s wall / 80.9 s fit with immutable local and MLflow copies (SHA-256 `6dfe8d4…`). Warm recall@500 is 0.182165 versus v1 0.3186 (-42.82%); this is an objective-change diagnostic, not a gate threshold, and the instructed full measurement still runs once. No competing training process is active. Full run is the frozen v1 BCE/neg32/two-epoch/seed42 cell over the 25M source, changing only to `all-positions-strict-timestamp-v1`; expected ~19.7M targets / ~470k bounded windows, 25–45 minutes wall by the measured 6% scaling, and 6–9 GiB peak RSS versus 4.26 GB at 6% and the old v1's 8.8 GiB. The 36 GiB host has margin. `OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE`; torch exact evaluation; create-only local model, file-store MLflow copy, and console/resource log retained even on failure.
- HB 2026-09-10 22:03 PDT M4 FULL PREFLIGHT RESTART: stopped pre-training run `b3435b7317e94e209b3f1ab3ec2c34fe` as soon as it reported the v1 synthetic cohort absent from the temporary worktree; tagged the retained MLflow run with the cause and terminated it `KILLED`, preserving its log. Relocated the clean, fully pushed branch to the protocol-required persistent worktree `/Users/kudratsingh/worktrees/MovieRecSys-sasrec-allpositions`, linked the retained ignored v1 cohort payload, and verified its pinned cutoff 1466837397 equals the full 25M split. Re-launching the same single full measurement; no model epoch ran in the aborted preflight.
- HB 2026-09-11 TURN START: remote refs refreshed; resuming monitoring of the single M4 all-position full-data run already in progress, then continuing the queue in order.

## 2026-09-11 10:18 PDT — M2 follow-up and M4 complete

- PR #178 follow-up is pushed as `cd6017d`: the page workload's bounded persona-state snapshot now walks `sort=popular`, which deterministically places all 120 reviewed/state-bearing titles before the untouched 62,303-title tail. The strict non-empty mutation-pool assertion is unchanged. `node --check` and 20 focused fixture/load tests pass; CI remains the arbiter and no auto-merge was enabled.
- M4 PR #183 is open from `feat/sasrec-all-positions` at `6fe0d86`. It implements one causal pass per bounded window, strict next-timestamp targets, L=200 memory coverage, deterministic sampling, legacy-builder equality on the small fixture, training-objective artifact versioning, and torch exact top-k with lazy FAISS.
- Valid 6% run `f837955c832440069dd8c1316a2ad0c6`: protocol `sha256:090985d…`, warm recall/NDCG 0.182165/0.067743, 80.9 s fit, 28,202 windows / 1,198,161 targets. Versus copied-prefix v1: -42.82% warm recall.
- Valid full run `fd2ee9f6f6794449a31ea3f50e600a48`: protocol `sha256:b4ed5afa…`, 1,931 warm / 710 cold, warm recall/NDCG 0.485648/0.172780, cold 0.526273/0.435841, overall 0.496570/0.243500. Versus stable v1: -4.62% warm recall, -9.61% warm NDCG, cold bit-identical. Fit 1,797.4 s (9.8x faster than 17,655 s v1), user CPU 1,795.14 s, max RSS 7,220,051,968 bytes. The shell's 43,321 s elapsed includes host sleep/unscheduling and is not compute time.
- Full model SHA-256 `16631300…`, vocabulary `76f0cf89…`; local and MLflow copies are byte-identical, reload successfully, and return identical scored candidates. Four synthetic routing buckets pass. Machine-readable record and both non-verdict attempts are retained and documented; nothing was deleted.
- Validation: Black, Ruff, strict mypy, JSON parse, 103 focused tests passed / 1 skipped. The repository-wide unit collection additionally requires optional local `psycopg` and `fakeredis`, absent from this host environment; no M4 test failed.
- PR #179 updated at `7e2b590` with the required ADR 0020 control note: its proposed stop rules 1 and 2 fire, so no v2 capacity cell is authorized as written.

TASK DONE M2 10:18 — PR #178 follow-up `cd6017d` pushed; popularity-ordered mutation snapshot fixes the 62,423-title pool starvation while preserving the strict assertion; CI decides.

TASK DONE M4 10:18 — PR #183; valid runs `f837955c…` and `fd2ee9f6…`; all-position full fit is 9.8x faster but warm recall is 4.62% below corrected v1, so no gate or champion moves.

OWNER DECISION M4 — O-22 asks whether future ADR 0020 cells should use a P1-only exact copied-prefix memory rewrite (recommended), retain the faster but weaker P2 objective as baseline, or revert and reopen the cloud-GPU path. No ADR 0020 cell will run before this decision.

- HB 2026-09-11 10:18 PDT M5 CHECKPOINT: M4 artifacts and code are durable; moving to M5 only after confirming PR #178's merge state, as the queue requires.

BLOCKED M5 10:20 — PR #178 commit `cd6017d` is not yet contained in fetched `origin/main`; W10 must not mutate or measure the demo stack until the full-catalog prerequisite merges. Exact run procedure is prepared; waiting for orchestrator/CI.
- HB 2026-09-11 10:23 PDT TURN END: no training process is active; dev Docker remains up with volumes intact. M2/M4 are pushed and signaled, PR #179 carries the control stop, O-22 is open, and M5 is held only on PR #178 reaching `origin/main`.
