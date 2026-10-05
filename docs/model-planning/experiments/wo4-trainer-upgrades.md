# Experiment: SASRec trainer upgrades / cell 0b pilot and paired device pilot / wo4-v1

**Status:** measured and decided. All three pilots are valid and every predeclared check holds. The
D6 tolerance proposed below is the owner's to accept or change.

**Governing ADR:** [ADR 0020](../../adr/0020-sasrec-v2.md), with its 2026-10-05 amendment (D4, D6,
D7), and [ADR 0016](../../adr/0016-sasrec-sequential-retrieval.md).

**Owner approval:**
- Next-Phase Build Brief 2026-10-05, WO-4, approved in full (code, tests and pilots); the parts that
  do not depend on WO-3 to start now.
- The paired `mps`/`cpu` pilot, approved explicitly by the owner (2026-10-05) as the measurement
  that proposes the D6 tolerance.

**Specification version:** wo4-v1 (2026-10-05). The cells are:
- [`wo4-cell0b-pilot-6pct.json`](../../experiments/sasrec/wo4-cell0b-pilot-6pct.json), cell 0b on
  the CPU;
- [`wo4-cell0b-pair-cpu-6pct.json`](../../experiments/sasrec/wo4-cell0b-pair-cpu-6pct.json), the
  device pair's CPU half;
- [`wo4-cell0b-mps-pilot-6pct.json`](../../experiments/sasrec/wo4-cell0b-mps-pilot-6pct.json), its
  `mps` half.

 Branch `feat/wo4-trainer-upgrades`. It was first cut from `feat/wo2-transformer-from-scratch`
at `bfbd707`, where the cell 0b CPU pilot ran. It was then rebased onto `main` at `c07b4e0`, which
holds WO-1 (#194) and WO-2 (#196) squashed, including WO-2's training-parity fix.

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
- Against values captured from `c07b4e0` before any WO-4 code existed (torch 2.13.0, Darwin arm64).
  These are checked wherever that environment matches. The first capture, at `bfbd707` before WO-2's
  dropout-layout fix, had the same batches but different losses and weights. The fix changes the
  same-seed trajectory, so the pin was re-captured on the new base. WO-4 on `c07b4e0` reproduces
  it bit for bit.

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
| Latest event timestamp that entered fitting | 1466819964 (2016-06-25 01:59:24 UTC) on all three runs; the probe's latest target 1464506906 (2016-05-29 07:28:26 UTC) | logged `latest_fit_timestamp` and `early_stopping_probe_latest_timestamp` |
| Latest event timestamp that entered scoring | 1469247943 (2016-07-23 04:25:43 UTC) on all three runs | logged `latest_scored_timestamp` |

**Affirmation.** Claude (WO-4 implementer), 2026-10-05: none of the three runs read an interaction
at or after the sealed boundary above. Each run's early-stopping probe read only rows of its fitted
frame, all before the cutoff 1466837397.

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
| `wo4-cell0b-pair6-ssm-neg1024-cpu` | none against the row above; it reruns cell 0b on the fixed encoder as the pair's CPU half | 42 | the same checks |
| `wo4-cell0b-pilot6-ssm-neg1024-mps` | the pair's `mps` half: `device: mps` | 42 | completes; the same checks; the speed-up and the warm difference against the pair's CPU half are recorded |

## Compute and storage budget

- **Hardware:** the laptop (8 cores, `mps` available), `caffeinate -i`, `OMP_NUM_THREADS=1`.
- **Concurrency:** at most 3 6% pilots in flight machine-wide, leaving 2 cores free. `pgrep -fl
  src.training` is checked before each run. The WO-4 pilots run one after the other, never together,
  so no pilot's timing includes another of them. Other workers' pilots did overlap some passes; this
  is recorded below.
- **Projection:** from 60 steps of this cell on the real partition before the runs: CPU 0.416 s per
  step and 2,318 steps per pass, so 50–85 min for 3–5 passes. `mps` 0.135 s per step, so 18–30
  min. Stop beyond twice the upper figure (170 and 60 min). The figures are in the cells JSONs.
- **Spend:** none.

## Pre-run correctness checklist

- [x] Governing ADR is approved for this work (ADR 0020 and its 2026-10-05 amendment; brief WO-4).
- [x] Protocol fingerprint is generated and matches baselines (`faf2828d…` on all three runs,
      checked before any number was read).
- [x] Temporal and equal-time leakage tests pass (unit suite).
- [x] Model-specific correctness gates pass: ADR 0020's battery for the new losses, early-stopping
      isolation, v1 bit-for-bit.
- [x] Baseline and control implementations are fixed.
- [x] Stop rule and compute cap are approved (brief WO-4; the coordinator's ground rules).
- [x] The partition declaration's pre-run rows are filled in.
- [x] The feature source is point-in-time per row.
- [x] Output paths and MLflow store are known: a local file store in the WO-4 worktree.
- [x] Another running worktree or training process will not be disturbed (checked before each run).

## Commands

```bash
MAIN=/Users/kudratsingh/Machine-Learning-Projects/movielens-recsys
WT=$MAIN-wt-wo4
cd $WT && caffeinate -i /usr/bin/time -l env OMP_NUM_THREADS=1 KMP_DUPLICATE_LIB_OK=TRUE \
  MLFLOW_ALLOW_FILE_STORE=true MLFLOW_TRACKING_URI=file://$WT/mlruns \
  TWOTOWER_INPUT_DIR=$MAIN/data/raw/ml-25m SASREC_ARTIFACT_DIR=$WT/models \
  $MAIN/.venv/bin/python -m src.training.sasrec_sweep docs/experiments/sasrec/wo4-cell0b-pilot-6pct.json
# the device pair, after "pilots released", one after the other on the rebased branch (ce4b314):
#   the same with docs/experiments/sasrec/wo4-cell0b-pair-cpu-6pct.json
#   the same with docs/experiments/sasrec/wo4-cell0b-mps-pilot-6pct.json
```

## Results — cell 0b on the CPU

Run `bd1b04082e5b4c1db354d5df1b3cdcd0`, 2026-10-05 13:52:29–14:56:49 UTC, commit `88b5d6b` (its
rebased counterpart is `e68fd95`; the code is the same). The
protocol hash read `sha256:faf2828d…`, equal to the cells file's, and was checked before any number
was read. 108 warm and 39 cold users.

**Encoder version.** Commit `88b5d6b` sits on `bfbd707`, before WO-2's training-parity fix. That
fix corrected the memory layout in which the residual dropout after attention drew its mask; outputs
and gradients were already correct. This run therefore trained on the pre-fix hand-written encoder:
a statistically identical model on a different same-seed trajectory. The result stands as WO-4's
cell 0b CPU pilot and is not re-run. The paired `cpu`/`mps` device pilot runs on the rebased, fixed
encoder, so both of its halves share one code version.

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
- `training/loss_curve.png` is on the run. Its legend overlaps a long run title. Commit `aad3f9f`
  fixed the layout after this run (`4eacbd2` before the rebase); the data is unaffected.

**Artifact.**
- Exported to `models/bd1b04082e5b4c1db354d5df1b3cdcd0/` in the WO-4 worktree (git-ignored).
- Archive SHA-256 `8d7eece9…`; weights digest `sha256:3867fa4c…`.

## Results — the device pair (CPU half, then `mps`)

Both halves ran cell 0b at seed 42 on the rebased branch at `ce4b314`: `main` plus WO-4, after
WO-2's parity fix. They ran one after the other:
- CPU from 15:53:18 to 16:53:34 UTC; one WO-3 pilot overlapped its passes 1–2.
- `mps` from 16:54:34 to 17:15:56 UTC; it ran alone.

Both protocol hashes read `sha256:faf2828d…` before any number was read. Both runs:
- passed every predeclared check;
- scored cold recall@500 exactly 0.5427033422, user by user;
- had their probe's latest target at 1464506906, before the cutoff;
- ran all 5 passes, with the probe improving by at least 0.5% until the cap.

| | CPU half `9b0d499430474fde9de6a5305bcda649` | `mps` half `faeb03a68e514941b8d37d1f74574318` — **not a result of record; measured to propose the D6 tolerance** |
|---|---:|---:|
| Warm recall@500 | **0.4563981551** | 0.4526139728 |
| Warm NDCG@500 | 0.1512466398 | 0.1493656176 |
| Cold recall@500 | 0.5427033422 | 0.5427033422 |
| Probe recall@500 by pass | 0.585 / 0.646 / 0.707 / 0.744 / 0.720 | 0.622 / 0.646 / 0.683 / 0.744 / 0.768 |
| Seconds per pass | 792.0 / 735.0 / 687.8 / 687.6 / 688.4 | 254.2 / 252.5 / 252.3 / 252.2 / 253.6 |
| Fit / wall | 3,593.6 s / 3,603.4 s (60.1 min; projected 40–65) | 1,267.5 s / 1,276.0 s (21.3 min; projected 18–30) |
| CPU user time | 3,582.9 s | 226.2 s |
| Peak RSS | 2,601,156,608 bytes | 2,920,480,768 bytes |
| Catalog coverage | 36.00% | 35.80% |

**Speed-up from `mps`:**
- **Per pass:** 2.72× on the passes where both runs had the machine to themselves (passes 3–5:
  687.9 s against 252.7 s, or 0.297 s against 0.109 s per step). Averaged over all five passes it is
  2.84×; CPU passes 1–2 overlapped a WO-3 pilot.
- **Fit seconds:** 2.84×. This includes the per-pass probe and holdout scoring, which both runs do on
  the CPU.
- **Wall clock:** 2.82×.
- **CPU left free:** the `mps` run used 226 s of CPU time against 3,583 s.

**Difference in warm recall@500, `mps` minus CPU:**
- −0.0037842, or **−0.83%** relative.
- Per user: 23 of 108 warm users higher, 22 lower, 63 identical. One user is 0.93% of the slice.

For scale, the two CPU runs of this same seed are `bd1b0408` (pre-fix encoder) and `9b0d4994`
(fixed). A trajectory change alone moved warm recall by +0.16%, with 20 users higher, 21 lower and 67
identical. WO-1's four seeds on this partition span 8.74% relative, with sd 4.0% of their mean. The
`mps` difference is a fifth of a seed sd, inside what a reseed does. That is the expectation stated
above: the device run draws dropout from another generator and, from pass 2, sees examples in
another order, so it is a reseed as much as a change of arithmetic.

### Proposed D6 tolerance (for the owner)

This number is not a result of record. ADR 0020's amendment asks for the accepted difference to be
written before any non-CPU run counts. The owner decides it; the proposal:

1. **Tolerance: ±1.0% relative warm recall@500 at full scale.** It is the band ADR 0020 already uses
   for "the same model" at full data (stop rule 2, cell 0 against `528b1451…`).
   - The pilot's −0.83% fits inside it.
   - At full scale per-user effects average over 1,931 warm users instead of 108, so a reseed-like
     difference should shrink. If it scaled like seed noise, that would be by about √(1931/108) ≈
     4.2×. That shrinkage is an expectation, not a measurement.
2. **A calibration pair at full scale before any `mps` cell counts.** Run cell 0b once on each
   device, against ±1.0%.
   - CPU: 38,554 steps at 0.297 s is 3.2 h per pass, 9.5–16 h for 3–5 passes.
   - `mps`: at 0.109 s per step it is 1.2 h per pass, 3.5–5.8 h.
   - If the pair falls outside ±1.0%, no `mps` cell counts. The cells go back to the CPU or to the
     owner under D6.
3. **Near-threshold confirmation.** An `mps`-trained cell whose warm gain against v1 lands within 1%
   of gate 1's +3% bar (between +2% and +4%) is confirmed by a CPU run before its verdict is
   recorded.
4. **Unchanged:** every `mps` model is scored on the CPU from its saved weights (the trainer already
   does this), and every published number stays a CPU number.

The alternative is a looser band at the seed spread itself, several percent at pilot scale. It would
let device noise straddle gate 1's +3% bar, which is why it is not proposed.

**Artifacts** (git-ignored, under `models/` in the WO-4 worktree):
- CPU half: archive `b0ac2c57…`, weights digest `sha256:d3f9fc1a…`.
- `mps` half: archive `4da1a7d7…`, weights digest `sha256:c502d879…`.

## Deviations

- **The sampled-softmax logits path changed before any pilot.** After the first projection timing
  (0.657 s per CPU step under load), the sampled logits moved from gathering 1,024 item vectors per
  example to one product with the item table plus a column gather. The arithmetic is the same and the
  step is cheaper. The cells JSONs carry the re-timed projection (0.416 s per step), and every pilot
  ran the cheaper path.
- **Cell 0b ran on the pre-fix encoder**, so the device pair re-ran its CPU half on the fixed one
  (coordinator-approved). That is three pilots in total, within WO-4's 2 to 3.
- **Overlap with other workers' pilots:**
  - cell 0b, passes 1–2: WO-2's s13 and s21;
  - the pair's CPU half, passes 1–2: one WO-3 pilot;
  - the `mps` half: none.
  The speed-up is therefore quoted on passes 3–5 as well as on all five.
- **The pilots waited** about 55 minutes on a training-quiet gap the coordinator held for a database
  migration.

## Verdict

**Validity:** valid. All three runs.

**Partition affirmation:** intact.
- On all three runs the latest fitted timestamp is 1466819964 and the latest scored is 1469247943,
  both below 1469256597.
- The probe's latest target is 1464506906.

**Decision:**
- **Trainer: advance.** Every WO-4 addition ran end to end under the objective of record and passed
  its predeclared checks. The trainer is ready for WO-5 on the CPU.
- **`mps`: proposed for use under the D6 tolerance above,** pending the owner's decision and the
  full-scale calibration pair.

**Rule application:**
- Protocol hash `faf2828d…` on all three runs.
- Cold recall exactly 0.5427033422 on all three.
- Probe before the cutoff on all three.
- Wall times inside their projections: 64.3 min against 50–85, 60.1 against 40–65, and 21.3 against
  18–30.
- WO-4's own done-when:
  - ADR 0020's correctness checks pass as unit tests;
  - cell 0b completed on the CPU;
  - the `mps`/`cpu` pair records a speed-up of 2.72× per pass and a warm difference of −0.83%.

**Runs:**
- `bd1b04082e5b4c1db354d5df1b3cdcd0` (cell 0b, CPU).
- `9b0d499430474fde9de6a5305bcda649` (pair, CPU).
- `faeb03a68e514941b8d37d1f74574318` (pair, `mps`; not a result of record).

All three are in a local MLflow file store in the WO-4 worktree (`mlruns/`).

**What is not authorized next:**
- Citing the `mps` run's recall anywhere but this record.
- Treating any `mps`-trained model as a result before the owner sets the D6 tolerance in ADR 0020's
  amendment.
- Starting any WO-5 cell. WO-5 is owner-gated and depends on WO-3's outcome for its objective.
