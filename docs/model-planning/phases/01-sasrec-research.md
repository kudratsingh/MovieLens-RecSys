# M1 — Complete SASRec research

## Objective

Finish the already-started ADR 0016 work with a defensible answer to one question: does a
causal sequence encoder add enough retrieval value over simple, correctly matched baselines to
justify a full-data model and, eventually, online inference?

M1 can exit successfully with `measured, not promoted`. A valid negative result is preferable
to a long tuning campaign.

## Stage A — Establish the current pilot record

The repository contains 0.5% and 6% BCE/gBCE specs and several runs across correctness fixes.
Before adding cells:

1. Inventory every SASRec MLflow run by run ID, code SHA, status, config, and metric.
2. Mark runs before unconstrained-logit, exact-retrieval, or inference-dropout fixes as
   superseded for decision purposes without deleting them.
3. Record the final post-fix 6% BCE and gBCE pair with wall-clock and machine.
4. Verify both arms differ only in loss/calibration fields.
5. Do not interpret small arm differences until the pilot rule and controls exist.

Deliverable: a dated SASRec section in `docs/results.md` and a draft scorecard.

## Stage B — Correctness gates

Add tests and evidence for:

- tiny deterministic corpus overfit with a named target-recovery threshold;
- causal masks at all positions, not only one fixture position;
- same-timestamp isolation and boundary behavior around the temporal cutoff;
- target, prefix, padding, duplicate, and out-of-vocabulary negative exclusion;
- deterministic sampling and batch order per seed;
- left-padding, truncation, empty sequence, unknown item, and all-seen behavior;
- exact retrieval equivalence against brute-force normalized dot product;
- train/eval mode transitions and stable repeated inference;
- threshold-10 policy attribution and full-history exclusion beyond the 50-token window.

The overfit test should be small enough for CI and hard enough that a broken mask, target
alignment, or output table fails it.

## Stage C — Baselines and causal controls

Run on the exact same 6% users, split, catalog, exclusions, threshold, K, and evaluator:

1. Popularity@500.
2. Item-item@500.
3. Two-tower v1 lineage reference where comparable.
4. Last-item transition baseline: count next-item transitions from strict training sequences
   and backfill with popularity.
5. Optional recency-weighted item-item baseline if approved as a distinct hypothesis.
6. SASRec with deterministic within-prefix order shuffle, preserving user, items, length, and
   target.

The last-item model establishes whether the Transformer beats a cheap sequential signal. The
shuffle establishes whether any SASRec lift comes from order rather than its parameterization.

## Stage D — Run telemetry and diagnostics

Extend each run with:

- eligible users, items, sequences, targets, timestamp groups, and truncated interactions;
- prefix-length distribution and fraction at maximum length;
- negative draw count, rejection/collision count, sampler distribution, and examples/sec;
- data-build, training, index-build, and evaluation durations;
- peak RSS and estimated full-data memory;
- learned-versus-fallback counts per natural and synthetic slice;
- unique retrieved items and catalog coverage;
- fraction of holdout targets reachable in top 500;
- mean/median popularity rank of retrieved items and target popularity deciles;
- item-age/cold-item diagnostic once its definition is approved;
- embedding norm/spread and duplicate-vector diagnostics;
- protocol fingerprint, code SHA, dependency identity, and baseline run IDs.

Formulas belong in shared evaluation code with hand-built fixtures, not only in the trainer.

## Stage E — Scale redesign

Profile the 6% run by data construction, sampling, forward/backward, index build, and evaluation.
Then replace the dense all-prefix representation before a full run unless measured evidence
shows it fits D-004 comfortably.

Required properties of the replacement:

- memory scales with compact interactions plus batch size, not events × sequence length;
- strict-prefix/equal-time behavior is identical to the current reference builder;
- data order and negative sampling are deterministic per seed;
- workers cannot duplicate or skip examples;
- a resume boundary is explicit if training is interruptible;
- batch-level throughput and peak RSS are reported.

Candidate designs include packed per-user sequences with on-the-fly windows, an iterable dataset,
or a memory-mapped index of prefix offsets. Choose by measurement. Preserve the existing builder
as a small-fixture oracle.

## Stage F — Pilot decision

Apply D-003 after Stages A–E:

- **Stop:** correctness passes, but the best frozen arm loses clearly to popularity or the
  last-item baseline, or matches shuffled order.
- **Repeat narrowly:** the result is within the declared uncertainty band; rerun the best arm
  at seeds 7 and 13 without introducing new axes.
- **Advance:** one arm beats the simple floors, shows order value, fits the compute envelope,
  and has no coverage/head-collapse warning severe enough to invalidate the result.

Only one configuration advances. Freeze it in a committed full-run spec.

## Stage G — Full-data evidence

For the frozen configuration:

1. Run seed 42, then validate run completeness and scale projections.
2. If the predeclared negative stop rule fires by a decisive margin, stop and document it.
3. Otherwise run seeds 7 and 13.
4. Recompute deterministic item-item/popularity baselines only where the protocol changed;
   otherwise bind their run IDs and protocol hashes.
5. Evaluate fixed holdout plus rolling temporal windows.
6. Attach h0/h1/h3/h10 and all diagnostics.
7. Run the retrieval gate and D-002 end-to-end LightGBM guardrail.

Do not open the test partition in M1.

## Deliverables

- Correctness and overfit suite.
- Simple sequential baseline and shuffled-order control.
- Scalable deterministic dataset/sampler path.
- Canonical diagnostics shared by candidate models.
- Frozen pilot and full-data specs.
- MLflow runs with complete provenance.
- `docs/results.md` entry, ADR 0016 dated verdict, roadmap update, and SASRec scorecard.

## Acceptance criteria

- Every run of record has a protocol hash and enough metadata to reproduce it.
- Superseded runs are clearly excluded without being erased.
- Full-data launch respects D-004 and has a measured peak-memory projection.
- Positive promotion evidence uses the approved seed set and retrieval gate.
- A negative early stop quotes the predeclared rule and the margin that triggered it.
- No result depends on seen-item, same-time, future, or target leakage.
- The final status is exactly one of: `measured, not promoted`; `promotion eligible`; or
  `inconclusive` with one named missing decision—not “promising.”

## Suggested PR shape

1. Current pilot record and run invalidation note.
2. Correctness/overfit tests.
3. Sequential baseline plus shuffle control.
4. Evaluation diagnostics and protocol logging.
5. Scalable dataset and sampler.
6. Frozen full-data spec and run-only result PR.
7. Gate verdict, ADR note, roadmap row, and scorecard.
