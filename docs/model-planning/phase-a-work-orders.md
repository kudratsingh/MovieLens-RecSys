# Phase A work orders — the hand-written transformer, trained larger and compared fairly

**Date:** 2026-10-05. **Source:** the owner's *MovieLens RecSys: Next-Phase Build Brief*
(2026-10-05). **Status:** current; this is the executable state of the modeling track.

This file is the executable spec for Phase A under the 2026-10-05 brief. It transcribes the brief's
goal, decisions, rules and work orders without changing a number, a run id or a path. Where the brief
overrides older text in the repo it says so, and the override is recorded in the place the brief
names (see the decisions table). Where this file adds something the brief does not say, it is marked
as a *verification note* and states only what was checked in the tree on 2026-10-05.

[`CLAUDE.md`](../../CLAUDE.md) still applies in full. Nothing here relaxes a non-negotiable, an ADR,
or a gate threshold.

## Goal

This phase makes the project show deep learning skill: a transformer written by hand, trained at a
larger size, and compared fairly against every existing model.

## What "done" means

Phase A is done when all five are true:

1. The SASRec encoder uses no ready-made PyTorch transformer classes, and it reproduces the old
   encoder's outputs.
2. A larger hand-written model is trained and scored by the same evaluation code as every baseline.
3. Each headline number is backed by 3 training runs and more than one time window.
4. One documented command reproduces each headline number.
5. The README leads with the model comparison table.

## Out of scope

Out of scope for this phase: serving, deployment, latency tests, the champion swap, the frontend,
retriever mixing (ADR 0019), and Phases 4 to 6. Do not open work on any of them.

## Where the project stands

The transformer already trains on every example the training data holds: 19,739,546 next-movie
predictions, seen twice. Example count is not what limits it.

| Training setup today (SASRec v1) | Value |
|---|---|
| Dataset | MovieLens 25M: 25,000,095 ratings, 162,541 users |
| Training data | Oldest 80%: 20,000,075 ratings |
| Training examples | 19,739,546 next-movie predictions |
| Passes over the data | 2 |
| History the model reads | Last 50 movies |
| Movies it can recommend | 34,461 |
| Model size | 64 wide, 2 layers, about 2.3 million weights (about 100,000 in the transformer layers) |
| Training cost | 4.9 hours on the laptop CPU (17,655 s) |

The levers that give the model more to learn from are more passes, a longer history, and a bigger
model. A bigger dataset is a separate decision, listed under open questions.

Numbers of record, all on protocol hash `sha256:b4ed5afa…` with 1,931 warm users (10+ movies of
history) and 710 cold users. Recall@500 is the share of a user's next-28-day movies found in the
model's 500 picks.

| Model | Warm recall@500 | MLflow run |
|---|---:|---|
| Item-item (no learning) | 0.3991 | `4b342e87dbf54834be5c719eae9a4e6c` |
| SASRec v1, original trainer | 0.5092 | `528b14513d9a49e098a0525417f23285` (model from `a11af5ed0f0745f68572407237cfa4b9`) |
| SASRec v1, fast trainer | 0.4856 | `fd2ee9f6f6794449a31ea3f50e600a48` |
| Two-tower v2 | 0.5113 | `2d7f1a49008d4e9d8791d4ca8598d613` |

End to end, SASRec v1 plus a retrained LightGBM ranker scores overall NDCG@10 0.2218 against 0.2008
for item-item plus LightGBM (run `c1d742c8485d4e54b66746a65f7705d0`).

Five problems this phase fixes:

1. `SASRecEncoder` in `src/models/candidates/sasrec.py` wraps PyTorch's `nn.TransformerEncoderLayer`.
2. `make train-sasrec` on `main` runs the fast trainer (0.4856), not the one behind 0.5092. The
   original loop is recoverable with `git show 89520be^:src/models/candidates/sasrec.py`.
3. The trainer is CPU-only, and its negative sampler is a Python loop that runs once per example.
4. Every candidate-model number comes from one run on one 28-day window. Rolling-window code exists
   in `src/data/split.py` and `src/evaluation/backtest.py`, but no trainer uses it.
5. The README is dated 2026-08-29 and shows no transformer result.

## Decisions already made

These seven choices shape every work order. Each one is the owner's to change before handoff; several
override text in the repo, so the implementer must record them where shown. The last column says
where each one was recorded on 2026-10-05.

| # | Decision | Why | Where the implementer records it | Recorded 2026-10-05 |
|---|---|---|---|---|
| D1 | Larger transformer first (Phase A), generative recommender second (Phase B) | Phase B reuses Phase A's hand-written layers, and Phase A alone makes the project resume-ready | Roadmap decision log | [`../modeling-roadmap.md`](../modeling-roadmap.md) decision log, 2026-10-05 rows |
| D2 | Stay on MovieLens 25M for Phase A | A new dataset moves the split and invalidates every existing comparison; the repo's own memo (D-007) recommends staying | No change needed | — |
| D3 | Serving is parked | Owner decision, 2026-10-05: nothing is being served | `CLAUDE.md` current step, `docs/status/README.md`, ADR 0020 Gate 3 note | All three |
| D4 | Try to repair the fast trainer before paying for the slow one | Its pilots cost about 81 seconds each; the slow trainer prices the model grid at 35 to 58 CPU-days | Dated amendment to ADR 0020 (it currently requires the original objective) | [ADR 0020](../adr/0020-sasrec-v2.md), amendment 2026-10-05 |
| D5 | Three training runs for the final headline models only | One run cannot rule out luck; the fast trainer makes repeats cheap | Dated note on the 2026-09-05 one-run policy in `CLAUDE.md` | `CLAUDE.md`, "Purpose of this project" |
| D6 | Hardware order: laptop CPU, then the Mac's own GPU, then a rented GPU | Matches owner decision O-3: rent only when one run costs more than a night | ADR 0020 amendment, with a stated tolerance before any non-CPU run | ADR 0020, amendment 2026-10-05 (the tolerance itself is not yet stated) |
| D7 | "From scratch" has a written definition (below) | Otherwise the claim cannot be checked | ADR 0020 amendment | ADR 0020, amendment 2026-10-05 |

### D7, the from-scratch rule

Allowed building blocks: `nn.Module`, `nn.Parameter`, `nn.Linear`, `nn.Embedding`, `nn.Dropout`, and
plain tensor math including `softmax` and `gelu`. Not allowed anywhere under `src/`:
`nn.Transformer*`, `nn.MultiheadAttention`, `nn.LayerNorm`, `F.scaled_dot_product_attention`,
`F.multi_head_attention_forward`.

## Rules for the implementer

Read `CLAUDE.md` first and follow it; the rules below add to it and do not replace it.

### Process

- One work order is one feature branch and one pull request. Use Conventional Commits, merge only on
  green CI, and never push to `main`.
- Every metric goes through `src/evaluation/`. Every run emits a protocol manifest and fills
  [`experiments/template.md`](experiments/template.md), including which data partition it read.
- Write each run's configuration to a JSON file under `docs/experiments/` before starting it. Record
  the result whether it is good or bad.
- Never change a gate threshold. Never read any rating with timestamp `>= 1469256597` before work
  order 8.
- One training job on the full dataset at a time. Check that no `src.training` process is running
  before starting one.
- Keep every existing test green, including the serving tests. Write no new serving, infrastructure
  or frontend code.

### Stop and ask the owner when

- a stop rule in a work order or in ADR 0020 fires;
- a run takes more than twice its projected time;
- a step would spend money;
- this brief conflicts with an ADR and no amendment covers it.

### Cost limits

- One implementer works the orders in sequence. Do not run them in parallel under an orchestrator;
  the 5 to 11 September wave produced 39 pull requests in one week.
- Budget: 9 pull requests for Phase A. Each one runs the full CI suite, including the load test and
  browser journeys, so small extra pull requests cost waiting time.
- Write no new gate or tooling modules. Reuse `retrieval_gate.py`, `gate.py` and `backtest.py`.
- Documentation is limited to: one ADR 0020 amendment, one new ADR for Phase B, the experiment JSON
  files, `docs/results.md` entries, one walkthrough page, and the README.
- Wrap every long run in `caffeinate -i`. The last full run needed 30 minutes of compute and took 12
  hours on the clock because the laptop slept.

*Verification note.* The ledger counts 33 merged pull requests numbered #149–#183 for that wave;
GitHub lists 47 opened between 5 and 11 September (#137–#183, 45 merged). The brief's 39 is kept as
written; the rule it supports does not depend on the exact count.

## The pilot

A "pilot" is a run on the fixed 6% user sample (`sample_fraction: 0.06` in a cells JSON run by
`python -m src.training.sasrec_sweep`). It has only 115 warm users, so it checks correctness and
direction, never a final result.

## Work orders, Phase A

Nine work orders, done in this order, take the project to resume-ready. Sizes are rough estimates,
not measurements.

| WO | What | Needs | Training runs | Rough size |
|---|---|---|---|---|
| 1 | Restore the trainer of record | Nothing | 5 pilots | 1 day |
| 2 | Hand-written transformer | WO-1 | 1 pilot, 1 inference check | 2 to 3 days |
| 3 | Repair the fast trainer | WO-2 | Up to 8 pilots, 3 seed repeats, 1 full run | 1 to 2 days |
| 4 | Trainer upgrades for larger models | WO-3 | 2 to 3 pilots | 2 days |
| 5 | Train the larger models | WO-4 | 6 full runs | 1 day plus machine time |
| 6 | End-to-end score and speed for the winner | WO-5 | 2 ranker runs | 1 day |
| 7 | Repeat runs and rolling time windows | WO-6 | About 16 runs | 2 days plus machine time |
| 8 | One-time read of the sealed window | WO-7 and owner sign-off | 1 pass per frozen model | Half a day |
| 9 | README and results write-up | WO-8 | None | 1 day |

### WO-1: Restore the trainer of record

**Status:** not started
**Branch / PR:**

**Goal.** `make train-sasrec` can again train the exact objective behind 0.5092, without building the
3.95 GB example table in memory.

**Change**

- Add a `training_objective` field to `SASRecConfig` with the two names already defined in
  `sasrec.py`: `strict-prefix-final-position-v1` (default, the objective of record) and
  `all-positions-strict-timestamp-v1`.
- Bring back the original training loop from `git show 89520be^:src/models/candidates/sasrec.py`.
- Store each user's movie sequence once and cut each example's 50-movie window when the batch is
  built (ADR 0020, step P1).

**Done when**

- A test shows the new data path yields the same examples in the same order as
  `build_strict_prefix_examples_with_stats` on a small dataset.
- A pilot of cell `pilot6-bce-neg32` (seed 42) gives identical model weights (same SHA-256) and
  identical metrics on the old loop recovered from git and on the new data path. If they differ,
  report the difference and stop for a decision.
- Peak memory is logged. The old loop peaked at 8.8 GiB on full data; the new path never builds a
  table of examples times window length.

**New reference values.** Record the four new-path pilots (seeds 42, 7, 13 and 21) in
`docs/results.md`. They replace the old pilot values of 0.3186, 0.3103, 0.3258 and 0.2957. Those were
measured on 2026-09-04 and 05, before the short-history fix (pull request #162) merged, so they
understate the original trainer.

**Runs.** Five pilots of 18 to 27 minutes each: the old loop once, the new path at four seeds.

### WO-2: Hand-written transformer

**Status:** not started
**Branch / PR:**

**Goal.** `SASRecEncoder` is built only from the pieces rule D7 allows, and it behaves exactly like
the old one.

**Change**

- New file `src/models/candidates/transformer.py` containing: layer normalization; attention with a
  causal mask (no looking at later movies) and a padding mask; multi-head attention with its own
  query, key, value and output weights; a feed-forward layer with GELU; a pre-norm residual block; a
  stack of blocks.
- Padding rule: a padded position always outputs zeros and can never produce NaN. This replaces the
  process-wide `torch.backends.mha.set_fastpath_enabled(False)` workaround in `sasrec.py`.
- In `sasrec_artifact.py`, add an `encoder_impl` field and convert old weight names (such as
  `transformer.layers.0.self_attn.in_proj_weight`) to the new layout when loading. Every saved model
  must keep loading.
- Update the code and tests built around the fast-path switch:
  `src/training/sasrec_fastpath_recheck.py`, the guard in `src/serving/sequence_retrieval.py`,
  `tests/unit/test_sasrec.py`, `tests/unit/test_sidecar_sasrec_load.py`. Keep their checks that
  histories of length 1, 3, 12, 49 and 50 each return 500 candidates.
- New page `docs/modeling/transformer-from-scratch.md`: each layer, its tensor shapes and both masks,
  in plain language, so the owner can explain every line.

**Done when**

1. A search of `src/` for the names banned by D7 returns nothing.
2. An equivalence test copies weights from `nn.TransformerEncoder` (kept in `tests/` only) into the
   new encoder; outputs match within 1e-5 for left-padded histories of length 1, 3, 12, 49 and 50.
3. The saved v1 model (`artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/`, SHA-256
   `43320b87e3cb…`) loads through the converter. An inference-only evaluation reproduces warm
   recall@500 0.5091713455 to 4 decimals and cold 0.5262729520 exactly. Report the exact difference
   and how many users' top-500 lists changed.
4. New tests pass: memorization (recall@10 near 1.0 on a tiny dataset), every weight receives a
   gradient, later movies cannot change earlier outputs, padded positions give zeros.
5. A pilot with the new encoder at seed 42 lands inside the range of the four WO-1 reference pilots,
   recorded in `docs/results.md`.

**Runs.** One inference-only evaluation (seconds) and one pilot.

**Note.** After this change, retraining v1 gives an equivalent model, not a bit-identical one. That is
stated in the ADR 0020 amendment of 2026-10-05.

*Verification note.* On 2026-10-05 the banned names appear in `src/` only in
`src/models/candidates/sasrec.py` (`nn.TransformerEncoderLayer`, `nn.TransformerEncoder`,
`nn.LayerNorm`). `artifacts/` is git-ignored, so the saved v1 model is not part of the tree; the
artifact contract in [`../modeling/sasrec-artifacts.md`](../modeling/sasrec-artifacts.md) keeps the
local directory and a copy under the MLflow run's `model/` path, and the full archive SHA-256 is
`43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6`.

### WO-3: Repair the fast trainer

**Status:** not started
**Branch / PR:**

**Goal.** Explain why the fast trainer scores lower, and close the gap, so the larger models can train
cheaply.

**What is known.** The fast trainer predicts every position of a 50-movie window in one pass. It is
9.8 times faster (1,797 s against 17,655 s) but scores 0.4856 against 0.5092 on full data (-4.62%)
and 0.1822 against a recorded 0.3186 on the pilot (-42.82%). That pilot reference predates the
short-history fix, so WO-1's new reference values replace it.

**Test three causes, one at a time, on pilots (about 81 seconds each).** The first two causes are
named as risks in ADR 0020 (step P2) but were never isolated.

| Cause | What happens today | Pilot that tests it |
|---|---|---|
| Short history | Windows are cut back to back, so the first prediction in a window sees 1 movie instead of 50 | Overlap the windows (slide by 25) and score only positions that have 25 or more movies behind them, when the user has that many |
| Narrow batches | The 512 examples in a step come from about 12 windows instead of up to 512 different users | Draw each step's examples from many more windows |
| Too few passes | Recall was still rising after pass 2 (0.4652 to 0.4856 on full data) | 4 and 8 passes with nothing else changed |

**Done when**

- A short record in `docs/results.md` says which cause explains how much of the gap.
- Pilot check: the repaired trainer's mean warm recall@500 over seeds 42, 7, 13 and 21 is within 3%
  of the mean of the four WO-1 reference pilots.
- Full-data check: one full run is within 1% of 0.5091713455 (ADR 0020 stop rule 2). Any other
  result stops for an owner decision.
- The repaired objective has its own name in `SASRecConfig` and in the ADR 0020 amendment.

**Stop rule.** At most 8 pilots and one full run. If no variant passes, stop tuning: the larger models
then train on the original objective, on the Mac's GPU or a rented one (D6).

**Runs.** Up to 8 pilots, 3 seed repeats, 1 full run of roughly 30 to 60 minutes.

### WO-4: Trainer upgrades for larger models

**Status:** not started
**Branch / PR:**

**Goal.** The trainer supports everything the larger models need, and each addition is tested before
a full run.

**Change**

- Two new losses from ADR 0020: sampled softmax with 1,024 negatives, and full softmax over all
  34,461 movies. Give them a vectorized sampler.
- Keep the original per-example sampler for the v1 configuration only, so v1 numbers stay
  reproducible.
- Early stopping exactly as ADR 0020 specifies: hold out the last training example of a seeded 1% of
  training users, stop when recall@500 on it improves by less than 0.5%, run 3 to 5 passes. The
  28-day holdout is never used to decide when to stop.
- Express batch size as examples per step. Where memory forces a smaller batch, accumulate gradients
  and log both numbers.
- Add a `device` setting: `cpu` (default, bit-reproducible), `mps` (the Mac's GPU) or `cuda`.
- Log training loss and gradient size per step to MLflow, and save a loss-curve image with each run.

**Done when**

- ADR 0020's correctness checks pass: causal mask, padding, target exclusion, sampler probabilities,
  sampled and full loss agreeing on a tiny dataset, save-and-reload, and memorization.
- A pilot of cell 0b (v1 shape with sampled softmax) completes on CPU.
- One pilot runs on `mps` and on `cpu` with the same settings. The record states the speed-up and the
  difference in warm recall@500.
- If any non-CPU run is planned, the ADR 0020 amendment states the accepted difference first (owner
  decision O-3 requires this).

**Runs.** 2 to 3 pilots.

### WO-5: Train the larger models

**Status:** not started
**Branch / PR:**

**Goal.** Six predeclared models, one run each, show which change helps: a new loss, more width, more
depth, a longer history, or scoring against every movie.

All other settings are fixed by ADR 0020: feed-forward 4 times the width, dropout 0.2, Adam at 1e-3,
seed 42, exact search at evaluation, 3 to 5 passes with early stopping.

**Before the first cell.** Time one pass of cell A and re-price the whole table from it. ADR 0020
projected 0.07 hours per pass for the control cell; the measured value was 0.25 hours, 3.6 times
slower. ADR 0020 stop rule 7 requires the re-pricing. If any cell would take longer than one night,
bring the hardware options in the run budget to the owner.

| Cell | Width / layers / history | Loss | Weights (approx.) | Changes one thing from |
|---|---|---|---|---|
| 0b | 64 / 2 / 50 | Sampled softmax, 1,024 negatives | 2.3 million | v1 (the loss) |
| A | 128 / 2 / 100 | Sampled softmax, 1,024 negatives | 4.8 million | Anchor for B to E |
| B | 256 / 2 / 100 | Same as A | 10.4 million | A (width) |
| C | 128 / 4 / 100 | Same as A | 5.2 million | A (depth) |
| D | 128 / 2 / 200 | Same as A | 4.8 million | A (history length) |
| E | 128 / 2 / 100 | Full softmax, 34,461 movies | 4.8 million | A (loss) |

**Done when, per cell**

- The run is judged against v1 run `528b14513d9a49e098a0525417f23285` with `make gate-retrieval`. A
  pass needs warm recall@500 up by at least 3% (about 0.5244) with the lower bound of the paired user
  bootstrap also above 3%.
- The record lists, beside recall: catalog coverage, the average popularity rank of retrieved movies,
  weight count, training time, peak memory, and the history-length distribution above 50.
- Cold-user results are identical to v1's. Any difference is a defect, not a result.

**Stop rules (from ADR 0020, unchanged)**

- If A fails, run one extra cell (128 / 2 / 50) to separate width from history length, then stop the
  sweep.
- A cell that raises recall while dropping catalog coverage more than 5 points below v1's 30.70% is
  recorded as a costlier popularity model and is not the winner.
- One combined cell is allowed only if two of B, C, D and E pass on their own, and it combines exactly
  those two.

**Runs.** 6 full runs, plus at most 2 conditional ones.

### WO-6: End-to-end score and speed for the winner

**Status:** not started
**Branch / PR:**

**Goal.** The best cell from WO-5 gets a final-top-10 score and a speed number, using flows that
already exist.

**Change**

- Export the winner's model with the existing artifact code.
- Retrain the LightGBM ranker on the winner's 500 candidates with `make train-sasrec-ranker` and
  `make train-sasrec-ranker-bundles`.
- Measure encoder speed with the unmodified `src/evaluation/sasrec_latency.py`.

**Done when**

- `make gate` with the learned-route scope compares the new bundle with bundle
  `c1d742c8485d4e54b66746a65f7705d0` (warm NDCG@10 0.101441, cold 0.549002, overall 0.221762). A pass
  needs warm NDCG@10 up by at least 3% with cold unchanged.
- If this gate refuses while WO-5's gate passed, record the model as a better candidate finder only
  and stop. Do not train ranker variants to hunt for the difference (ADR 0020 stop rule 6).
- Encoder speed is recorded as a number. Under D3 it is reported, not used as a gate.

**Runs.** 2 ranker runs of about 11 minutes each.

### WO-7: Repeat runs and rolling time windows

**Status:** not started
**Branch / PR:**

**Goal.** No headline number rests on one run or one month.

**Change**

- Seeds: train SASRec v1, the WO-5 winner and two-tower v2 at seeds 42, 7 and 13. Popularity and
  item-item give the same result every time and need no repeats. `make gate-retrieval` already
  accepts a three-seed set.
- Rolling windows: let the candidate trainers accept a window from `backtest_windows` and
  `apply_backtest_window` in `src/data/split.py`. Train popularity, item-item, SASRec v1, the winner
  and two-tower v2 on windows w1 and w2 (w0 is the current holdout). Each window trains on everything
  before its own 28 days.
- Summarize the three windows with `src/evaluation/backtest.py`: mean, spread, worst window, and the
  user-level bootstrap interval.

**Known obstacle.** The synthetic cold-start cohort is tied to the main cutoff and raises
`CohortCutoffMismatchError` on any other. For w1 and w2, skip the cohort metrics and say so in each
record. Do not regenerate the cohort.

**Done when**

- Each headline model has a mean and range over 3 seeds, and a 3-window summary.
- The dated note for D5 is in `CLAUDE.md`. *(Done 2026-10-05.)*
- If the winner's lead over v1 is smaller than the spread between seeds, the write-up says so plainly.

**Runs.** 6 seed repeats and 10 window runs. Two-tower v2 takes about 1 hour 55 minutes per run.

*Verification note.* In the tree on 2026-10-05 the window generator in `src/data/split.py` is named
`rolling_origin_windows` (window 0 reproduces ADR 0001's split; `fixed_holdout_window` returns it);
there is no function named `backtest_windows`. `apply_backtest_window` exists under that name, and
`CohortCutoffMismatchError` is raised from `synthetic/cold_start/harness.py`.

### WO-8: One-time read of the sealed window

**Status:** not started
**Branch / PR:**

**Goal.** One published number per model on data that no decision has ever used.

**Before any code runs, the owner must approve in writing**

- An amendment to `docs/model-planning/memos/sealed-test-and-dataset-policy.md`. Its trigger requires
  a "serving eligible" bundle. Under D3 the proposed wording is: a frozen offline release candidate
  that passed the WO-5 and WO-6 gates. *(Appended to the memo on 2026-10-05 as a proposal, not
  approved.)*
- The release candidate by name, with its configuration, seeds, protocol manifest, thresholds and
  checksums committed.
- The list of models scored in the same pass: the release candidate plus every baseline in the README
  table.

**Procedure**

- Window: the 28 days after the holdout, `[1469256597, 1471675797)`, 2016-07-23 to 2016-08-20, as
  ADR 0001's 2026-09-05 amendment already sets. Everything later stays sealed.
- Retrain each frozen configuration on all ratings before 1469256597, the same rule the rolling
  windows use, then score once.
- Publish every number in `docs/results.md` as it comes out, including a bad one.

**After the read.** No later change may cite this number as its reason, and this window is spent.

### WO-9: README and results write-up

**Status:** not started
**Branch / PR:**

**Goal.** A visitor sees the model comparison first, and can reproduce it.

**Change**

- README leads with one table: model, warm recall@500 (mean and range over seeds), 3-window mean and
  worst window, end-to-end NDCG@10, weights, training time, encoder speed, catalog coverage, and the
  sealed-window score.
- Remove stale statements. The README is dated 2026-08-29 and says the two-tower lost at 0.0466; that
  was the FAISS row-mapping bug fixed in pull request #158.
- Add loss curves, and warm recall broken down by history length (10 to 19, 20 to 29, 30 to 39, 40 to
  49, 50 or more) from the per-user recall exports.
- Add a short "what we found" section: the fast-trainer diagnosis from WO-3 and what the six cells
  showed.
- Add a "reproduce" section with one command per headline number.
- Update `CLAUDE.md` status, `docs/status/README.md`, the roadmap decision log and
  `docs/model-planning/00-current-state.md`.

**Done when.** Every number in the README table links to its MLflow run id and its record in
`docs/results.md`.

## Run budget

Phase A needs about 50 training runs, and only the six larger models in WO-5 are expensive. That
sweep is the one step that may need more than the laptop CPU.

| Work orders | Runs | Laptop CPU time (estimate) | Based on |
|---|---|---|---|
| 1 to 4 | Up to 22 pilots, 1 full run | About 5 hours in total | Recorded pilot times: 81 s on the fast trainer, 18 to 27 min on the original; full fast run 30 min |
| 5 | 6 full runs | 17 to 29 hours if ADR 0020's projection holds; 2.5 to 4.3 days if the measured 3.6 times slowdown holds | ADR 0020's cost table and its one measured run |
| 6 | 2 ranker runs | 22 minutes | 11 min per run, recorded |
| 7 | 6 seed repeats, 10 window runs | Two-tower 7.7 hours; SASRec v1 2 to 4 hours; the winner 4 times its WO-5 time; popularity and item-item minutes | Recorded run times |
| 8 | About 5 passes | About 3 hours plus the winner's training time | Same |

The WO-5 estimate is the uncertain one. The slowdown probably comes from the per-example Python
sampling loop, which WO-4 removes, so re-measure after WO-4 before trusting either figure.

Two costs grow sharply if WO-3 fails to repair the fast trainer. WO-5 on the original trainer is
priced by ADR 0020 at 35 to 58 CPU-days, and each SASRec v1 run in WO-7 goes back to 4.9 hours.

### Hardware options for WO-5 (decision D6)

| Option | Money | Trade-off |
|---|---|---|
| Laptop CPU | Free | Slowest; results are bit-reproducible |
| The Mac's GPU (`mps`) | Free | Speed-up unknown until WO-4 measures it; results not bit-identical |
| One rented GPU | ADR 0020 estimated $10 to $51 for the whole sweep on the original trainer; re-check prices before spending | Fastest; not bit-identical; adds a second machine to the results |

For any model trained off the CPU, re-score its saved weights on the CPU, so every published number
is computed on the same machine.

**Elapsed time.** A realistic estimate for Phase A is 2 to 3 weeks: about 12 working days of building
and review, plus 4 to 8 days of machine time that mostly runs overnight.

## Phase B: generative recommender

Phase B builds a model that writes out the next movie's ID piece by piece, the way a language model
writes the next word. It starts only after WO-9 merges and the owner approves a new ADR.

**How it works.** Each movie gets a short code of 3 or 4 numbers built from its description, so
similar movies share the start of their codes. A transformer reads a user's history as codes and
generates the next movie's code one number at a time. A search keeps the 500 most likely codes that
belong to real movies.

**Why this repo is ready for it.** The TMDB snapshot at `data/raw/tmdb/2026-09-05` has an overview for
98.3% of the 62,423 catalog movies. Phase A's hand-written layers become the generator with small
changes.

| WO | What | Done when |
|---|---|---|
| B1 | ADR 0021 proposal: the decision, alternatives, stop rules and a priced compute budget | Owner approves by merging; the roadmap row for Rung 7 reads "approved" |
| B2 | Text embeddings for every movie from title, overview, genres and keywords, using one frozen pretrained text model | Saved as a versioned artifact; no time-varying TMDB fields (votes, popularity) are used |
| B3 | Movie codes: a hand-written quantizer that turns each embedding into 3 code numbers plus 1 tie-breaker | Every movie has a unique code; code usage and collision rate are reported |
| B4 | The generator: a decoder built from the WO-2 layers, trained to produce the next movie's code, with a search limited to valid codes | Memorization test passes; a pilot returns 500 valid movies per user |
| B5 | Evaluation on the same harness and users, with 3 seeds and rolling windows | Warm recall@500 beside v1, the Phase A winner and two-tower v2; the cold-item slice beside the content retriever |

**What to expect.** The outcome is open, and a loss is still a result worth publishing. The paper that
introduced this approach (TIGER) reports gains, including on new items. A later controlled comparison
(LIGER) found the generative model trailing a standard sequence model on small benchmarks and scoring
close to zero on new items for most datasets.

**Rough size.** 3 to 5 weeks, and it probably needs a GPU. B1 prices it properly.

## Open questions for the owner

Seven items need an answer from the owner; the first three should be settled before handoff.

1. **Confirm decisions D1 to D7.** D4 (repair the fast trainer first) and D5 (three runs for headline
   models) override text now in ADR 0020 and `CLAUDE.md`. *Recorded as decided on 2026-10-05 in the
   places the decisions table names; the owner's confirmation is the merge of that change.*
2. **Who writes WO-2?** The implementer can build it, but interviewers will ask the owner to explain
   attention and masking without notes. The walkthrough page is the minimum; rebuilding the encoder by
   hand once is stronger. *Open.*
3. **Set a spending ceiling for rented GPU time**, in case D6 reaches that step. *Open.*
4. **`.coordination/` is tracked in the public repo**, while its own `RESUME.md` calls those files
   local-only. Keep it public, or stop tracking it? *Answered: no longer tracked — PR #188,
   2026-10-05, removed it from the index; the files stay on disk locally.*
5. **The 436 MB TMDB snapshot exists on one machine with no DVC remote.** Phase B depends on it, so add
   a remote before B2. *Open.*
6. **MovieLens 32M** has 32,000,204 ratings from 200,948 users on 87,585 movies (GroupLens), 28% more
   ratings than 25M. Moving means a new split, a new cold-start cohort, and re-running every baseline;
   old and new numbers cannot be compared. Recommended: not in Phase A. Revisit only if WO-7 shows the
   results are too noisy to separate the models. *Open; D2 keeps 25M for Phase A.*
7. **Optional extra:** reproduce the SASRec paper's published MovieLens-1M score with the hand-written
   model. It proves the implementation against an outside number, but needs a second, paper-style
   scoring setup kept apart from `src/evaluation/`. About 2 to 3 days. *Open.*
