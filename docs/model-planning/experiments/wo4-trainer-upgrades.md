# Experiment: SASRec trainer upgrades / cell 0b pilot and paired device pilot / wo4-v1

**Status:** cell 0b CPU pilot measured; every predeclared check holds. The `mps` half of the pair is
held until the coordinator releases pilots (a training-quiet gap for a database migration).

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), with its 2026-10-05 amendment (D4, D6,
D7), and [ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-4, approved in full (code, tests and pilots); the parts that
  do not depend on WO-3 to start now.
- The paired `mps`/`cpu` pilot, approved explicitly by the owner (2026-10-05) as the measurement
  that proposes the D6 tolerance.

**Specification version:** wo4-v1 (2026-10-05). The cells are
[`wo4-cell0b-pilot-6pct.json`](../../experiments/sasrec/wo4-cell0b-pilot-6pct.json) (CPU) and
[`wo4-cell0b-mps-pilot-6pct.json`](../../experiments/sasrec/wo4-cell0b-mps-pilot-6pct.json)
(`mps`). Branch `feat/wo4-trainer-upgrades`, cut from `feat/wo2-transformer-from-scratch` at
`bfbd707` (WO-1 and WO-2 included).

## Decision this experiment informs

Two decisions:

1. **Is the trainer ready for WO-5's cells?** Every addition the larger models need must be tested
   before a full run: the sampled and full softmax, the vectorized sampler, early stopping,
   examples-per-step batching with accumulation, the device setting, and per-step logging.
2. **What CPU-versus-GPU difference counts as the same result (D6)?** ADR 0020's amendment requires
   that number before any non-CPU run counts. The paired pilot measures one difference and the
   speed-up; the owner sets the tolerance.

## What was built

| Addition | Where | How it is tested |
|---|---|---|
| Sampled softmax (`loss: "sampled-softmax"`, `negative_count` negatives) and full softmax over every movie (`"full-softmax"`) | `src/models/candidates/sasrec_objectives.py`; wired into both objectives in `sasrec.py` | `tests/unit/test_sasrec_softmax_objectives.py` |
| Vectorized uniform sampler: `count` distinct negatives per row, never padding, the target or a history item; one NumPy pass per step (about 17 ms for 512 × 1,024 at 34,461 movies, against about 110 ms for the per-example loop at 1,024) | `sample_uniform_negatives` | sampler-probability, exclusion, seeding and exhaustion tests |
| The per-example sampler kept for the BCE family only | `SASRecModel._draw_negatives` | v1 bit-for-bit tests below |
| Early stopping: a seeded 1% of training users each give up their last training example; recall@500 on that probe after every pass; stop on less than 0.5% relative improvement over the best earlier pass; 3 to 5 passes | `src/models/candidates/sasrec_early_stopping.py`; `SASRecModel._end_epoch` | `tests/unit/test_sasrec_early_stopping.py` |
| `batch_size` is examples per optimizer step; `microbatch_size` splits a step into passes and accumulates gradients; both logged with the accumulation count | `SASRecConfig`, `_strict_prefix_step`, `_all_positions_step` | accumulated-gradient equality tests, both objectives, all three losses |
| `device`: `cpu` (default, bit-reproducible), `mps`, `cuda`; the encoder is built on the CPU, trained on the device, and moved back to the CPU before it scores anything | `resolve_device`, `SASRecModel.fit`, `_scoring_on_cpu` | device tests (the `mps` ones skip where there is no Mac GPU) |
| Per-step training loss and gradient norm to MLflow (`train_step_loss`, `train_step_grad_norm`, batched 450 steps per call), and `training/loss_curve.png` with each run | `src/training/sasrec_training_log.py`, `run_once` | logging and `run_once` tests |

**Choices the ADR left open, made here:**
- **Logits.** Both softmax losses score the raw dot product v1 trains on (unnormalized encoder output
  against unnormalized item embedding). Retrieval still searches the normalized vectors.
- **How the sampled logits are computed.** One matrix product of the step's encodings with the whole
  item table, then the target's and the negatives' columns are picked. It is the same arithmetic as
  gathering 1,024 item vectors per example. On the CPU it is much cheaper: a micro-benchmark at cell
  0b's shape put the gather path about 30% over the encoder and this path about 4% over it.
- **What a negative may not be.** v1's rule: not padding, not the target, not an item in the history
  the encoder read. The full softmax masks the same items, so it is the sampled softmax with every
  eligible negative drawn. The two agree to float rounding when that is done on a tiny frame.
- **No log-Q correction.** The proposal is uniform over each row's eligible items, so the correction
  is the same constant for every candidate and cancels in the softmax.
- **"Improves by less than 0.5%"** is read against the best earlier pass, so a pass that falls back
  also stops. The final weights are kept; nothing is restored.
- **The probe draws from its own seed** (`early_stopping_probe_seed`, 42), so a seed sweep varies the
  model and not the data.

**v1 is unchanged.** `tests/unit/test_sasrec_trainer_upgrades.py` pins it two ways:
- Against a frozen copy of the pre-WO-4 loop, sampler and loss kept in the test file. The first
  three batches (histories, targets and negatives, by digest), every step's loss, both epoch losses
  and the trained weights' digest must match exactly. This runs everywhere.
- Against values captured from `bfbd707` before any WO-4 code existed (torch 2.13.0, Darwin arm64).
  These are checked wherever that environment matches.

The configuration ids of every recorded cell are unchanged too (`full.json`, the WO-1 cells, the
all-positions cells): each WO-4 field is left out of the id while it holds its default.

## Hypothesis and falsifier

**Hypothesis (trainer):** cell 0b runs end to end on the CPU under the objective of record with every
WO-4 addition active, on O-25's 6% partition, and the predeclared checks hold.

**Falsified when any of these fails:**
- the run's protocol hash is not `sha256:faf2828d…`;
- cold recall@500 is not exactly 0.5427033422 (cold users are popularity-routed);
- the probe's latest timestamp is at or after the cutoff;
- the run takes more than twice its projected time.

**Hypothesis (device):** training on `mps` gives a model whose CPU-scored warm recall@500 differs from
the CPU run's by no more than seed-to-seed variation does. On this partition, WO-1's four seeds have
sd 0.0150 and range 0.0325. `mps` is expected to be faster per pass. Neither expectation is a gate.
The measured difference is reported as the proposal for the D6 tolerance.

## Baselines and controls

| Model/control | Why it is required | Run/spec reference |
|---|---|---|
| v1 cell, pre-WO-4 code | WO-4 must not move v1 | unit tests above (frozen loop, captured values) |
| WO-1 reference pilots s42/s7/s13/s21 (BCE, 32 negatives, 2 passes) | Direction only: what v1's loss reads on this partition | `7baeb7d0…`, `d71f0fa6…`, `2d9f3cc1…`, `8668ca0c…` |
| Cell 0b on the CPU | The control for the `mps` run | `wo4-cell0b-pilot-6pct.json` |

## Changed and fixed axes

**Changed:**
- *Cell 0b against v1:* the loss (sampled softmax with 1,024 negatives instead of BCE with 32) and
  the pass budget (3 to 5 with early stopping, as ADR 0020 fixes for every cell).
- *The `mps` run against the CPU run:* the device only.

**Fixed:**
- hidden 64, 2 blocks, 2 heads, feed-forward 256, dropout 0.2, L = 50;
- 512 examples per optimizer step, one pass per step; Adam 1e-3;
- objective `strict-prefix-final-position-v1`; seed 42; probe seed 42;
- exact search; O-25's 6% partition (subsample seed 42); threshold 10; K = 500.

WO-3 has not run. Its outcome may change the objective WO-5 trains on; this pilot uses the objective
of record.

## Protocol identity

| Field | Value |
|---|---|
| DVC/raw revision | `md5:c3ce6309…` (ml-25m CSV, 25,000,095 rows) |
| Train cutoff / holdout window | 1466837397 / [1466837397, 1469256597) (O-25: the full split's) |
| Rolling window(s) | w0 only |
| Partition(s) read | `holdout` (see below) |
| Label contract | implicit positive, every rating (ADR 0002 as v1) |
| Cold threshold / routing | 10 / train-history count ≥ 10 |
| Exclusion policy | the user's train history |
| Stage / K | retrieval / 500 |
| Sequence contract | strict prefix, latest 50, equal timestamps never in one another's prefix |
| Protocol hash | expected `sha256:faf2828d08a0b0ecf23993fcfaf037134359017e20c7601b53da7e2ebecc22bc`, checked before any number is read |

## Partition declaration

| Field | Value | How a reviewer checks it |
|---|---|---|
| Partition(s) read | `holdout` | logged `latest_scored_timestamp` |
| Owner unseal approval | not applicable | — |
| Sealed boundary this run used | `holdout_end` = 1469256597 (2016-07-23 06:49:57 UTC) | logged `holdout_end_timestamp` and `sealed_boundary_timestamp` |
| Feature source and its as-of | raw interactions, strict prefix per example | — |
| Latest event timestamp that entered fitting | CPU run: 1466819964 (2016-06-25 01:59:24 UTC); the probe's latest target 1464506906 (2016-05-29 07:28:26 UTC) | logged `latest_fit_timestamp` and `early_stopping_probe_latest_timestamp` |
| Latest event timestamp that entered scoring | CPU run: 1469247943 (2016-07-23 04:25:43 UTC) | logged `latest_scored_timestamp` |

**Affirmation.** Claude (WO-4 implementer), 2026-10-05: the CPU run read no interaction at or after
the sealed boundary above, and its early-stopping probe read only rows of the fitted frame, all
before the cutoff 1466837397.

## Metrics

**Primary:** warm recall@500 through `src/evaluation/protocol.evaluate`, scored on the CPU.

**Guardrail:** cold recall@500 must equal 0.5427033422 exactly.

**Diagnostics:**
- probe recall@500 per pass, and the stopping pass;
- warm recall@500 on the holdout per pass (logged, never used to stop);
- seconds per pass (`epoch_train_seconds`) and fit seconds;
- peak RSS;
- per-step loss and gradient norm, and the loss curve;
- catalog coverage, mean retrieved popularity rank, and target reachability.

## Grid and seeds

| Cell | Changed fields | Seed | Rule |
|---|---|---|---|
| `wo4-cell0b-pilot6-ssm-neg1024-cpu` | loss, negatives, passes with early stopping | 42 | completes; the predeclared checks hold |
| `wo4-cell0b-pilot6-ssm-neg1024-mps` | the same, plus `device: mps` | 42 | completes; the same checks; the speed-up and the warm difference are recorded |

## Compute and storage budget

- **Hardware:** the laptop (8 cores, `mps` available), `caffeinate -i`, `OMP_NUM_THREADS=1`.
- **Concurrency:** at most 3 6% pilots in flight machine-wide, leaving 2 cores free. `pgrep -fl
  src.training` is checked before each run. The two WO-4 pilots run one after the other, never
  together, so neither's timing includes the other.
- **Projection:** from 60 steps of this cell on the real partition before the runs: CPU 0.416 s per
  step and 2,318 steps per pass, so 50–85 min for 3–5 passes. `mps` 0.135 s per step, so 18–30
  min. Stop beyond twice the upper figure (170 and 60 min). The figures are in the cells JSONs.
- **Spend:** none.

## Pre-run correctness checklist

- [x] Governing ADR is approved for this work (ADR 0020 and its 2026-10-05 amendment; brief WO-4).
- [ ] Protocol fingerprint is generated and matches baselines (checked from the run before any
      number is read).
- [x] Temporal and equal-time leakage tests pass (unit suite).
- [x] Model-specific correctness gates pass: ADR 0020's battery for the new losses, early-stopping
      isolation, v1 bit-for-bit.
- [x] Baseline and control implementations are fixed.
- [x] Stop rule and compute cap are approved (brief WO-4; the coordinator's ground rules).
- [x] The partition declaration's pre-run rows are filled in.
- [x] The feature source is point-in-time per row.
- [x] Output paths and MLflow store are known: a local file store in the WO-4 worktree.
- [ ] Another running worktree or training process will not be disturbed (checked before each run).

## Commands

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WT=$MAIN-wt-wo4
cd $WT && caffeinate -i /usr/bin/time -l env OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE \
  MLFLOW_ALLOW_FILE_STORE=true MLFLOW_TRACKING_URI=file://$WT/mlruns \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m SASREC_ARTIFACT_DIR=$WT/models \
  $MAIN/.venv/bin/python -m src.training.sasrec_sweep docs/experiments/sasrec/wo4-cell0b-pilot-6pct.json
# then the same with docs/experiments/sasrec/wo4-cell0b-mps-pilot-6pct.json
```

## Results — cell 0b on the CPU

Run `bd1b04082e5b4c1db354d5df1b3cdcd0`, 2026-10-05 13:52:29–14:56:49 UTC, commit `88b5d6b`. The
protocol hash read `sha256:faf2828d…`, equal to the cells file's, and was checked before any number
was read. 108 warm and 39 cold users.

| Metric | Cell 0b (sampled softmax, 1,024 negatives, 5 passes) | WO-1 references (BCE, 32 negatives, 2 passes) |
|---|---:|---:|
| Warm recall@500 | **0.4556720321** | 0.3611–0.3936 (mean 0.3722) |
| Warm NDCG@500 | 0.1481656414 | 0.1253–0.1483 |
| Cold recall@500 | 0.5427033422 | 0.5427033422 |
| Overall recall@500 | 0.4787619715 | 0.4093–0.4332 |
| Catalog coverage | 36.85% (6,920 of 18,778 movies) | not recorded |
| Mean retrieved popularity rank | 2,792.5 | not recorded |
| Fit | 3,848.0 s (wall 3,859.8 s, 64.3 min) | 1,080.4–1,099.0 s |
| Peak RSS | 2,147,074,048 bytes (2.00 GiB) | 2.92–3.04 GB |

**The predeclared checks all hold:**
- the protocol hash matches;
- cold recall equals the references exactly, user by user;
- the probe's latest target (1464506906) is before the cutoff (1466837397);
- the run took 64.3 min against a projection of 50–85 min.

**Read the warm number as direction, not as a result.**
- The cell changes two things against WO-1's references: the loss, and the pass count (5 against 2).
  It cannot attribute the gain between them.
- The per-pass holdout diagnostic speaks to the loss alone, at matched passes. It is logged, never
  used to stop. After pass 2 it read 0.4106, where WO-1's seed-42 run ended at 0.3625. That is one
  seed on 108 users, where one user is 0.93% of the slice.
- Per user against WO-1 s42: 44 warm users higher, 30 lower, 34 identical, mean +0.0931.

**Early stopping, as it ran:**

| Pass | Train loss | Probe recall@500 (82 users) | Holdout warm recall@500 (diagnostic) | Seconds |
|---:|---:|---:|---:|---:|
| 1 | 4.7493 | 0.6098 | 0.3435 | 969.5 |
| 2 | 4.1203 | 0.6463 | 0.4106 | 812.5 |
| 3 | 3.8675 | 0.6829 | 0.4221 | 688.4 |
| 4 | 3.6961 | 0.7561 | 0.4524 | 686.5 |
| 5 | 3.5829 | 0.7073 | 0.4557 | 687.7 |

- The probe improved by 5.7% at pass 3 and 10.7% at pass 4, so training continued to the cap of 5.
- Pass 5 fell back below pass 4. The rule would have stopped there, but pass 5 is the last allowed
  anyway: `stopped_early` is false and `epochs_completed` is 5.
- At 82 users one user moves the probe by 1.2%, so at pilot scale the probe is coarse. The full
  data's 1% is about 1,600 users.
- 82 targets were dropped from training: 1,186,765 examples against WO-1's 1,186,847.
- Passes 1 and 2 overlapped two of WO-2's pilots; from pass 3 the run was alone, at 0.297 s per step.

**Telemetry.**
- Per-step loss and gradient norm are logged for all 11,590 steps (5 × 2,318, 512 examples each,
  no accumulation).
- `training/loss_curve.png` is on the run. Its legend overlaps a long run title. Commit `4eacbd2`
  fixed the layout after this run; the data is unaffected.

**Artifact.**
- Exported to `models/bd1b04082e5b4c1db354d5df1b3cdcd0/` in the WO-4 worktree (git-ignored).
- Archive SHA-256 `8d7eece9…`; weights digest `sha256:3867fa4c…`.

## Verdict

*Complete only after the runs.*
