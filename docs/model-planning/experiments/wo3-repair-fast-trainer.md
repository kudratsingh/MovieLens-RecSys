# Experiment: SASRec v1 / repairing the all-positions trainer / wo3-v1

**Status:** proposed. Code, tests and cells are committed; runs are held until the owner's
coordinator releases them (2026-10-05, a database migration needs the machine quiet first).

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), amendment 2026-10-05, D4 ("try to
repair the fast trainer before paying for the slow one"), with stop rules 1 and 2 unchanged.
Also [ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-3: up to 8 pilots, 3 seed repeats and 1 full run, with the
  brief's pass checks and stop rule. Approved in full once WO-2 was accepted.
- WO-2 accepted and merged 2026-10-05 (`c07b4e0`, PR #196). This branch starts there.
- Runs: **held**. To be filled in when the coordinator sends "runs approved".

**Specification version:** wo3-v1 (2026-10-05). The cells, written before any run:

| Pilot | File | What it changes from the baseline |
|---|---|---|
| 1 | [`wo3-allpos-baseline-6pct-s42.json`](../../experiments/sasrec/wo3-allpos-baseline-6pct-s42.json) | nothing (the #183 cell on the O-25 partition) |
| 2 | [`wo3-overlap-6pct-s42.json`](../../experiments/sasrec/wo3-overlap-6pct-s42.json) | `window_stride` 25 (objective `all-positions-overlap-v2`) |
| 3 | [`wo3-wide-6pct-s42.json`](../../experiments/sasrec/wo3-wide-6pct-s42.json) | `windows_per_step` 512 (objective `all-positions-wide-v2`) |
| 4 | [`wo3-passes-6pct-s42.json`](../../experiments/sasrec/wo3-passes-6pct-s42.json) | `epochs` 8; the 4-pass result is read at pass 4 |
| 5–8 | written after pilots 1–4, under the rules below | at most 4 more |
| seeds | [`wo3-repaired-6pct-seeds.json`](../../experiments/sasrec/wo3-repaired-6pct-seeds.json) | the winner at seeds 7, 13, 21; **held, cells provisional** |
| full | [`wo3-repaired-full.json`](../../experiments/sasrec/wo3-repaired-full.json) | the winner on full data, seed 42; **held, cells provisional** |

## Decision this experiment informs

Whether the larger models in WO-5 can train on a cheap all-positions objective, or must train on
the original copied-prefix objective (and then, under D6, on the Mac's GPU or a rented one).

## Hypothesis and falsifier

**Hypothesis.** The all-positions trainer scores lower for reasons in how it feeds the model, not
in the objective's idea. The brief names three, and the 6% partition measures the first two
before any training:

| Cause | What the recorded trainer does on the 6% partition | The pilot that removes it |
|---|---|---|
| Short history | Windows are cut back to back. A target sees 24.2 movies on average, against 42.2 under the copied-prefix objective; 52.4% see fewer than 25, and 1.9% see exactly one | Overlapping windows (stride 25): mean 34.3, and only the 16.1% whose user has fewer than 25 movies see fewer than 25, the same share as the copied-prefix objective |
| Narrow batches | 2,422 steps per pass, each reading 11.5 windows on average | Each step's 512 targets from 512 window visits |
| Too few passes | 2 passes; full-data recall rose 0.4652 to 0.4856 from pass 1 to pass 2 | 4 and 8 passes |

**Falsified when** no variant's seed-42 pilot reaches the pilot floor within 8 pilots, or the
winner's four-seed mean misses it, or its full run lands outside ±1% of 0.5091713455.

## Baselines and controls

| Model/control | Why it is required | Run/spec reference |
|---|---|---|
| WO-1 reference pilots, seeds 42/7/13/21 (copied-prefix objective, same partition) | The target. Four-seed mean warm recall@500 0.3721735878, sample sd 0.0150130 | `7baeb7d0…`, `d71f0fa6…`, `2d9f3cc1…`, `8668ca0c…`; WO-2 reproduced all four exactly |
| All-positions baseline, seed 42, on the O-25 partition (pilot 1) | The gap to explain. The recorded 6% value (0.1822, `f837955c…`) read the sealed window and is not comparable | `wo3-allpos-baseline-6pct-s42.json` |
| Full-data v1 (copied-prefix) | The full-data check | `528b14513d9a49e098a0525417f23285`, warm recall@500 0.5091713455 |
| Full-data all-positions (#183) | The full-data gap: 0.4856483771, −4.62%, 1,797 s | `fd2ee9f6f6794449a31ea3f50e600a48` |

## Changed and fixed axes

**Changed, one per single-cause pilot:** `window_stride`, `windows_per_step`, `epochs`. Pilots 5–8
may combine causes, under the rules in "Grid and seeds".

**Fixed:** everything else in the #183 cell: hidden 64, 2 blocks, 2 heads, feed-forward 256,
dropout 0.2, L = 50, BCE with 32 uniform negatives from the per-target sampler, 512 targets per
step, Adam 1e-3, seed 42; the hand-written encoder (WO-2); the O-25 6% partition; threshold 10;
exclusions; K = 500; exact search; CPU, `OMP_NUM_THREADS=1`.

**What every variant keeps:** every target the copied-prefix objective trains on is scored exactly
once per pass. Overlap and the wide sampler change which windows exist and how a step is drawn
from them, never which targets are trained or how often.

## The code (built, not run)

- `window_stride` (`SASRecConfig`, default 0). `build_overlapping_all_position_training_data` packs
  timestamp groups into chunks of at most `window_stride` movies by the recorded builder's own rule.
  Each chunk becomes a window of the latest 50 movies that ends where the chunk ends. A window
  scores only its chunk's targets, so every scored position has at least `max_sequence_length −
  window_stride` movies behind it, unless the user has fewer (`min_window_context`, logged).
- `windows_per_step` (default 0). `_wide_batches` splits each window's targets at random into
  visits of at most `ceil(512 / windows_per_step)` targets. It shuffles the visits across the pass
  and packs them into steps of at most 512 targets. A window is encoded once per visit.
- `epochs`: unchanged. Pass k of an N-pass run is the k-pass run bit for bit, including the per-pass
  evaluation, so pilot 4 also reports 4 passes.
- **Names.** Each setting has its own objective name, because an archive's manifest records the
  objective but not the settings: `all-positions-overlap-v2`, `all-positions-wide-v2` and
  `all-positions-overlap-wide-v2`. `SASRecConfig.validate` refuses a name that does not match its
  settings, and refuses either setting on `strict-prefix-final-position-v1`.
  `all-positions-strict-timestamp-v1` keeps meaning exactly what #183 measured. **The repaired
  objective is whichever of these wins, with its settings. That choice is pending the pilots. The
  ADR 0020 dated note should name it.**
- **Logged per run:** `train_steps_per_epoch`, `train_encoded_windows_per_step`,
  `train_targets_per_step` and `min_window_context`.
- **Configuration ids.** New fields leave the id while they hold their defaults, so every recorded
  id is unchanged (pinned for five committed cells files).
- **A held cells file cannot run:** `sasrec_sweep` exits 3 before loading data.

**Unchanged, and how that is pinned** (`tests/unit/test_sasrec_fast_trainer_repair.py`):
- **The objective of record.** WO-1's bit-for-bit test against the `89520be^` loop still passes.
  The data-gated check reproduces, on the O-25 6% partition, these values from the code before
  WO-3 (`c07b4e0`):
  - WO-1's recorded example digest `62cc7e7a…` (1,186,847 × 50);
  - the first three steps' inputs and negatives (`fd748a85…`);
  - their losses, 0.7993386984, 0.7917272449 and 0.7706018686.
- **The recorded all-positions loop.** Bit for bit against a copy of #183's loop, at three
  configurations. On the real partition, its first three steps (`34406263…`, losses 0.8027490377,
  0.8081477284, 0.8050366640) match the code before WO-3.

## Protocol identity

| Field | Pilots | Full run |
|---|---|---|
| DVC/raw revision | `data/raw/ml-25m.dvc`, md5 `c3ce6309…` | same |
| Derived snapshot | 6% user subsample (`SUBSAMPLE_SEED` 42), cut at the full split's cutoff (O-25); 8,240 users in train, 1,202,444 train rows, 18,778 items, 1,186,847 targets | all 25,000,095 ratings, with the ADR 0011 cohort |
| Train cutoff / holdout window | 1466837397 / [1466837397, 1469256597) | same |
| Rolling window(s) | w0 only | w0 only |
| Cold threshold / routing | 10 / popularity below 10; 108 warm / 39 cold | 10 / 1,931 warm / 710 cold |
| Stage / K | retrieval / 500 | retrieval / 500 |
| Sequence contract | oldest to newest, left-zero padding, L = 50, strict timestamp prefix | same |
| Protocol hash | **`sha256:faf2828d…`** must be on every pilot before a number is read | **`sha256:b4ed5afa…`** |

## Partition declaration

| Field | Value | How a reviewer checks it |
|---|---|---|
| Partition(s) read | `holdout` (pilots: the full split's 28-day window, restricted to the 6% users) | `split.test` is never used by the trainer |
| Owner unseal approval | not applicable | — |
| Sealed boundary this run used | `holdout_end` = 1469256597 (2016-07-23 06:49:57 UTC), from the full 25M frame | logged `sealed_boundary_timestamp` and `holdout_end_timestamp` |
| Feature source and its as-of | none; raw ratings, strict timestamp prefix | — |
| Latest event timestamp that entered fitting | to be copied from each run (expected 1466819964 on the pilots, as WO-1 and WO-2) | `latest_fit_timestamp` < 1469256597; `run_once` refuses otherwise |
| Latest event timestamp that entered scoring | to be copied from each run (expected 1469247943 on the pilots) | `latest_scored_timestamp` < 1469256597 |

**Affirmation:** to be made after the runs, from their logged timestamps.

## Metrics

**Primary:** warm recall@500 through `src/evaluation/protocol.evaluate`, 108 warm users on pilots.

**Guardrail:** cold recall@500 equal to 0.5427033422 on every pilot (popularity-routed), and on the
full run equal to 0.5262729520.

**Diagnostics:** per-pass warm recall and loss; steps, encoded windows and targets per step;
`min_window_context`; windows built; fit seconds; peak RSS; warm NDCG@500.

**How a cause's share is reported.** `gap = 0.3721735878 − P1`. The share a pilot recovers is
`(P_k − P1) / gap`. The same is reported against WO-1's seed-42 run (0.3625487076). One warm
user is 0.93% of the slice, and WO-1's seed sd is 0.0150. A single-seed movement smaller than that
is reported as not distinguishable from seed noise.

## Grid and seeds

Pilots 1–4 run as written. Expectations, stated before the runs. A result against them stops the
thread for the owner:
- **Pilot 1** is expected well below WO-1's range. If it lands at or above 0.3610084, there is no gap
  to repair at pilot scale, and the diagnosis stops there.
- **Pilots 2 and 3** are expected at or above pilot 1. A fall of more than 0.015 below it
  contradicts the expectation.
- **Pilot 4:** pass-4 recall is expected above pass 2. Passes 5–8 may plateau or fall at this scale
  (overfitting on 1.19M targets), which is a result, not a contradiction.

Pilots 5–8, decided from pilots 1–4 by these rules, each cells file committed before it runs:
1. If one single-cause pilot reaches the floor 0.3610084, it is the candidate. The next pilot is its
   cheapest form that is still at the floor, only if a cheaper form exists. An example: a smaller
   `windows_per_step` if pilot 3 is the one.
2. Otherwise, combine the causes that each recovered at least 0.015, at their cheapest settings
   (overlap 25 and the smallest pass count that helped), and price any wide-batch contribution at a
   width that keeps the trainer at least 3× faster than the copied-prefix trainer.
3. If overlap helps but stride 25 is not enough, one pilot at a smaller stride (more context, more
   windows) is allowed.
4. The first variant whose seed-42 pilot reaches the floor gets the seed repeats (7, 13, 21; held
   file rewritten to it). **Pilot check:** four-seed mean ≥ 0.3610084 (the brief rounds it
   0.361009). One set of repeats is budgeted. A miss is reported, not retried.
5. If the pilot check passes, one full run: warm recall@500 within ±1% of 0.5091713455, that is in
   [0.5040796, 0.5142631]. Any other result stops for an owner decision.

**Stop rule.** At most 8 pilots and one full run. If no variant passes, tuning stops and WO-5 trains
on the original objective (D6).

## Compute and storage budget

**Projections** (solo, the hand-written encoder). They come from the recorded pilots:
- #183's 80.9 s and the copied-prefix 1,080.8 s give a per-window cost of about 432 µs, ×1.235 for
  the hand-written encoder (WO-2's measured solo slowdown), so about 533 µs.
- About 23.6 µs per target, mostly the per-target negative sampler.
- The window counts above.

Pilot 1 re-measures both costs, and pilots 2–8 are re-projected from it before they start.

| Run | Encoded windows per pass | Fit (projected) | Wall (projected) | Killed past |
|---|---:|---:|---:|---:|
| P1 baseline | 27,940 | ≈ 90 s | ≈ 2 min | 4 min |
| P2 overlap 25 | 52,093 | ≈ 115 s | ≈ 2.5 min | 5 min |
| P3 wide 512 | 1,186,847 | ≈ 22 min | ≈ 23 min | 46 min |
| P4 8 passes | 27,940 × 8 | ≈ 6 min | ≈ 7 min | 14 min |
| Full run, overlap 25 at 2 passes (provisional) | ≈ 870,000 | ≈ 40 min | ≈ 41 min | 80 min |

The full-run projection scales #183's 1,797 s by the hand-written encoder and the extra windows.
Each extra pass adds about half of it, so a 4-pass winner projects to about 80 min. The projection
is re-stated before the run.

**Rules:**
- **Concurrency:** CPU only, `OMP_NUM_THREADS=1`, `caffeinate -i`. At most three 6% pilots in flight
  machine-wide, with 2 of 8 cores left free. `pgrep -fl src.training` is counted before each start.
  The full run goes alone.
- **Timing:** pilot 1 runs solo, so the cost model is measured cleanly. Pilots 2–4 may then run
  together, and the record notes the contention.
- **Memory:** about 3 GB peak per pilot (the real-data check peaked at 2.1 GB). The full run is
  expected below #183's 7.2 GB.
- **Spend:** none.

## Pre-run correctness checklist

- [x] Governing ADR is approved for this work (ADR 0020 amendment 2026-10-05, D4; brief WO-3).
- [ ] Protocol fingerprint matches its baseline (`faf2828d…` on pilots), confirmed on each run.
- [x] Temporal/equal-time leakage tests pass: each overlapped target's visible prefix is a suffix of
      its strict timestamp prefix, at five (L, stride) pairs, with equal-timestamp groups and an
      oversized group.
- [x] Mechanism tests pass:
  - the overlap mask;
  - the wide sampler's coverage, width and alignment;
  - the pass count;
  - name/settings validation;
  - export and reload under the repaired name.
- [x] Baseline and control implementations are fixed and pinned unchanged (above).
- [x] Stop rule and compute cap are approved (brief WO-3).
- [x] The partition declaration's pre-run rows are filled in. Every run goes through `run_once`'s
      sealed-partition guard.
- [x] The feature source is point-in-time per row (raw ratings).
- [x] Output paths and MLflow store are known: a local file store at `<wo3 worktree>/mlruns`, with
      model archives under `artifacts/wo3/models/` and logs under `artifacts/wo3/logs/` in the main
      checkout.
- [ ] `pgrep -fl src.training` is counted before each run.

## Commands

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WT=$MAIN-wt-wo3
PY=$MAIN/.venv/bin/python
LOGS=$MAIN/artifacts/wo3/logs && mkdir -p $LOGS $MAIN/artifacts/wo3/models
RUN_ENV="OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE MLFLOW_ALLOW_FILE_STORE=true \
  MLFLOW_TRACKING_URI=file://$WT/mlruns TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m \
  SASREC_ARTIFACT_DIR=$MAIN/artifacts/wo3/models"

# Pilot 1, solo.
pgrep -fl src.training
cd $WT && caffeinate -i env $RUN_ENV /usr/bin/time -l $PY -m src.training.sasrec_sweep \
  docs/experiments/sasrec/wo3-allpos-baseline-6pct-s42.json > $LOGS/wo3-p1-baseline.log 2>&1
# Pilots 2-4, at most three training Pythons machine-wide (WO-2's counter: it counts the
# interpreters, not the caffeinate and time wrappers around them, and any other worker's runs).
for name in overlap wide passes; do
  while [ "$(ps -axo command | grep -cE '^/opt/homebrew/\S+/Python -m src\.training\.')" -ge 3 ]; do sleep 30; done
  ( cd $WT && caffeinate -i env $RUN_ENV /usr/bin/time -l $PY -m src.training.sasrec_sweep \
      docs/experiments/sasrec/wo3-$name-6pct-s42.json > $LOGS/wo3-p-$name.log 2>&1 ) &
  sleep 45
done; wait

# Data-gated check that nothing recorded moved (about 30 s):
cd $WT && SASREC_WO3_UNCHANGED_CHECK=1 TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m OMP_NUM_THREADS=1 \
  $PY -m pytest tests/unit/test_sasrec_fast_trainer_repair.py -k first_real_pilot_steps
```

## Expected artifacts

- **MLflow file store** `<wo3 worktree>/mlruns`, experiment `phase-2-candidates`. Each run carries
  params (including `window_stride`, `windows_per_step`, `min_window_context`), the six @500
  metrics, per-pass warm recall and loss, the batch-shape metrics, `per_user_recall.json`, the
  protocol envelope and the partition timestamps.
- **Model archives** under `artifacts/wo3/models/<run-id>/`, each with its weights digest.
- **Logs** under `artifacts/wo3/logs/`.
- **`docs/results.md`:** a short record of which cause explains how much of the gap, the pilot
  check, and the full-data check.
- Each cells file's `result` block, filled in after its run.

## Verdict

Complete only after the runs.

**Validity:**

**Partition affirmation:**

**Decision:**

**Rule application:**

**Runs:**

**What is not authorized next:** no gate threshold, champion or serving change in any case. If the
full run passes, WO-4 onward may train on the repaired objective; the ADR 0020 note names it.
