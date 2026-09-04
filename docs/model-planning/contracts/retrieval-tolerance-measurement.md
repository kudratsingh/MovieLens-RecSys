# Retrieval tolerance measurement protocol

## Purpose

[ADR 0004](../../adr/0004-item-item-before-two-tower.md)'s 2026-09-04 amendment made the retrieval
gate executable except for two numbers: the cold and overall non-regression tolerances. They have no
defaults on purpose, so `make gate-retrieval` today cannot return a verdict at all — it is
structurally incomplete, and D-001 records the measurement as outstanding.

This document is the instrument, not the number. It states exactly what gets measured, on what
evidence, and the rule that converts measurements into the two tolerances. Running it is a separate
act that produces a proposal; adopting the proposal is the owner's, recorded wherever thresholds are
recorded. Nothing here may be short-circuited by picking a value that looks reasonable.

The executable half is [`src/evaluation/tolerance_study.py`](../../../src/evaluation/tolerance_study.py)
and `make retrieval-tolerance-study`.

## What the tolerance actually is

`RetrievalTolerance` validates two finite fractions in `[0, 1]`, and
`retrieval_gate._non_regression_clause` consumes them as:

```
change = (candidate_mean_slice_recall - incumbent_slice_recall) / incumbent_slice_recall
clause passes when change >= -tolerance
```

So a tolerance is **a fraction of the incumbent's own mean recall@500 on that slice, under that
protocol**. `cold = 0.05` means "mean candidate cold recall@500 may fall to 0.95 × the incumbent's
cold recall@500 and no further". It is not a fraction of overall recall, not an absolute recall
delta, and not a fraction of the candidate's own score.

Three consequences follow directly, and they are the reason the denominator is worth stating twice:

- **The tolerance is incumbent-specific.** It is denominated in a particular item-item run's slice
  score. Refit the item-item index, change its top-N, change the split, and the denominator moves;
  the tolerance derived against the old one no longer means what it said. The harness records the
  incumbent run id in its proposal for exactly this reason.
- **The same absolute noise buys a larger tolerance on a low-scoring slice.** A ±0.002 wobble is
  0.5% of a slice scoring 0.40 and 4% of a slice scoring 0.05. Two slices with identical measurement
  precision therefore get different tolerances, and that is correct rather than an artifact.
- **The cold and overall tolerances have no reason to be equal**, and probably will not be. Cold has
  roughly a third of the users (more population noise) but may have near-zero training-seed noise if
  most cold users route to a deterministic fallback — which is precisely what
  [`docs/results.md`](../../results.md) observed for CF/ALS, whose cold NDCG moved 0.04% across three
  seeds because 701 of 702 cold users were served by the deterministic popularity path.

The warm slice is deliberately out of scope. Warm carries a *positive* claim (+3% relative), not a
non-regression clause, and a positive claim is not established by a tolerance. The harness will
report warm dispersion when the evidence contains it, labelled as diagnostic, and it never feeds a
gate input.

## Which variation must the tolerance cover

A non-regression tolerance exists to separate "this model is worse on this slice" from "this number
moved and we cannot tell why". So the tolerance must be at least as large as the noise floor of the
statistic the gate reads, under the null hypothesis that candidate and incumbent are exactly equally
good on that slice. Three candidate sources of variation, taken one at a time.

### 1. Training/seed stochasticity — in scope, and historically the large one

The candidate is stochastic; the gate averages its recall over seeds 42, 7 and 13. Re-running the
whole comparison therefore produces a different number even with every other input pinned. This is
not hypothetical on this pipeline: `docs/results.md`'s 2026-08-30 session measured the LightGBM
ranker's warm NDCG@10 moving **28.68% of its own mean** across three seeds, because the seed was
choosing the training sample rather than a tie-break. That collapsed to 1.68% once the sample covered
the whole window, which is the shape of the risk — seed noise here is mostly *sampling inside
training*, and it is large exactly when the training sample is small relative to the data.

The incumbent contributes nothing to this term. `itemitem_cosine` is deterministic, `retrieval_gate`
enforces that it is seedless and appears exactly once, and re-running it produces the same index. The
seed term is a property of the candidate side alone.

Because the gate reads the mean of `m` seeds and not a single run, the relevant quantity is the
standard error of that mean, `σ_seed/√m`, not the spread of individual runs.

### 2. Evaluation-population sampling — in scope, for a different reason

The holdout is a finite set of users. Even with the model held perfectly fixed, the mean of a
per-user metric over ~2,600 users is an estimate with a standard error, and a candidate-minus-
incumbent difference smaller than that error is not evidence of a regression.

There is a real argument for excluding this term, and it deserves to be answered rather than
ignored: the protocol *pins* the holdout. Its identity is inside the semantic hash, the gate refuses
to compare runs whose slice populations differ, and no legitimate re-run of the gate draws a
different user set. Under that reading the only thing that varies between two applications of the
gate is the seed, and covering seed noise alone would make the verdict reproducible.

That reading is right about reproducibility and wrong about meaning. Reproducibility is not the only
job — the gate is asked whether a model is *worse*, and a −1.5% cold difference measured on ~700
users may be indistinguishable from zero. Refusing a model on a difference the evidence cannot
resolve is the same error as promoting one on noise, pointed the other way. So the population term is
in scope, and it is measured **paired**: both models score the same users, so the quantity to
bootstrap is the per-user difference `d_u = recall_candidate(u) − recall_incumbent(u)`, not the two
means separately. Pairing matters a great deal here — easy users are easy for both models, the two
per-user vectors are strongly positively correlated, and an unpaired estimate would inflate this term
substantially. An inflated term produces a larger tolerance, which is the permissive direction.

### 3. Fixed-seed run-to-run nondeterminism — deliberately excluded

Two runs at the same seed, same data version and same code should produce the same artifact;
non-negotiable #5 says so and `make train` is the check. If that term is nonzero it is a bug to find,
not noise to widen a threshold around. The harness does not measure it and does not accept it as an
input. The operator checklist below keeps the corresponding control — one repeated seed, byte-equal
metrics — as a precondition rather than a component.

### How the two in-scope terms combine, and which dominates

They are independent: which seed you drew does not affect which users are in the holdout. Variances
add, standard deviations do not, so the combined half-width is the quadrature sum

```
H = sqrt(A² + B²)
```

where `A` is the seed half-width and `B` the paired-population half-width, both expressed relative to
the incumbent's slice recall.

The reason quadrature is licensed here rather than merely convenient is worth spelling out. The usual
worry with a variance decomposition is double-counting: if each seeded run were evaluated on its own
freshly drawn user sample, the observed across-seed spread would already contain population noise and
adding a bootstrap term on top would count it twice. That does not happen here **because the protocol
fixes the holdout**. All `m` seeded runs are scored on the identical user set, so their spread
estimates the seed term *conditional on* that user set, and the bootstrap estimates the population
term *conditional on* a trained model. The two are estimated at fixed levels of each other, which is
what makes adding them the right composition instead of an approximation to apologise for.

**Neither term is dominant a priori and the protocol does not assert one.** The harness reports both
and labels which is larger. What can be said in advance is the practical consequence of quadrature:
once one term is about twice the other, the smaller adds under 12% to the total, so the operator's
effort belongs entirely on the larger one. If seed noise dominates, the fix is more training data per
run or a less sample-sensitive training configuration — not more seeds, which only buys `√m`. If
population noise dominates, the fix is a larger or better-powered evaluation slice, and no amount of
retraining helps.

## The circularity, and how it is resolved

The tolerance describes the candidate's noise. The candidate's noise cannot be measured before the
candidate has been run. But the operator checklist in
[`evaluation-protocol.md`](evaluation-protocol.md) requires the tolerance to be derived and published
*independently*, and never tuned to the candidate's result. Taken naively these cannot all hold.

The resolution is that the tolerance needs the candidate's **dispersion**, not its **level**, and
dispersion can be measured on runs the gate never reads. Two admissible ways to do that, one strongly
preferred.

### Derivation B — same configuration, seeds disjoint from the gate's (preferred)

Run the final, frozen candidate configuration at `m ≥ 3` seeds drawn from **outside** `{42, 7, 13}` —
for example 101, 202, 303 — on the full gate protocol. Derive the tolerance from those, publish it,
*then* run the gate's three seeds.

This makes no transfer assumption at all. It costs three extra full-configuration runs, which is the
honest price of a defensible threshold, and it is what should be done whenever the compute budget
(D-004) allows.

It is not magic: an operator who wanted to game the gate could still run the study, dislike the
answer, and re-run it. The protection against that is procedural, not computational — the study and
its tolerance are published before the gate's seeds are run, and the publication order is what makes
the claim checkable by someone else later. Code cannot enforce an ordering it cannot observe, and
pretending otherwise would be worse than saying so.

### Derivation A — surrogate configuration (when B is unaffordable)

Run a cheaper configuration of the *same model family* — fewer epochs, fewer training interactions, a
smaller hidden size — at `m ≥ 3` seeds, and transfer its dispersion to the full configuration.

**The transfer assumption, stated plainly: relative seed dispersion of slice recall does not increase
when moving from the surrogate to the full configuration.** The grounds are empirical and local to
this repository. Seed noise on this pipeline has been dominated by *which training examples the seed
selects*, and that source shrinks as the training sample grows — the ranker's warm spread fell from
28.68% to 1.68% on exactly that change, with the architecture untouched. A surrogate trained on less
data or for less time should therefore be at least as noisy as the full configuration, making the
transferred estimate conservative in the sense of "not too small".

**One thing may be cheapened and one may not.** The surrogate may cheapen *training*, never
*evaluation*. Evaluating on a subsample would shrink the user count, inflate the population term by
`√(n_gate/n_study)`, and require modelling the correction — so the protocol forbids it outright:
every study run's semantic protocol hash and slice populations must equal the incumbent's, and the
incumbent in the study is the same item-item run the gate will later be given. This sits exactly on
`manifest.py`'s existing separation of concerns — training configuration is run identity, the
evaluation question is the semantic protocol — so a legitimate surrogate changes the former and
leaves the latter byte-identical.

**How we would know the transfer assumption is wrong.** When the gate's three seeds land, compute
their observed relative range per slice. If it exceeds the surrogate study's, the assumption failed
and the published tolerance was too tight. The correct response is to redo the study — not to
re-derive the tolerance from the gate's own seeds, which is the tuning the checklist forbids. Both the
falsified and the replacement numbers get published. A verdict already issued under the falsified
tolerance stands as a refusal on record and is recomputed openly rather than quietly amended.

### Rejected alternatives

- **Borrow ADR 0001's warm 6% / cold 5%.** Forbidden by the amendment and wrong on the merits
  independently of that. Different stage, different metric, different model pipeline, and — most
  concretely — different noise *scale by construction*: NDCG@10 over a 10-item list moves in coarse
  jumps per user, while recall@500 over a 500-item list is a much smoother per-user quantity. Those
  two metrics do not have comparable per-user variance, so a tolerance measured on one says nothing
  about the other. The incumbent's determinism differs too: the ranking tolerances were doubled
  explicitly because *both* sides were stochastic there.
- **Population-only: bootstrap one run, skip the seed term.** Cheapest, needs no extra training, and
  makes no transfer assumption. Rejected because the ranker episode is a direct counterexample —
  seed noise on this pipeline reached 28.68% relative, an order of magnitude beyond anything a
  user bootstrap over thousands of users would produce, and a tolerance blind to it would be far
  too tight for a stochastic candidate.
- **Seed-only: skip the bootstrap.** Rejected for the reason given in §2 above; it makes the verdict
  reproducible without making it meaningful, and the cold slice is small enough for the omission to
  matter.
- **Derive the tolerance from the gate's own seeds 42/7/13.** This is the circularity, unresolved.
  It is the one thing the harness refuses mechanically rather than by prose: a study whose
  configuration matches the gate's and whose seeds intersect `REQUIRED_SEEDS` is rejected as
  insufficient evidence.
- **Pick a round number and defend it later.** The failure this whole document exists to prevent.

## The rule

Stated once and applied mechanically. For each gating slice `s ∈ {cold, overall}`, given `m` study
runs at distinct seeds with slice recalls `c_1..c_m`, the incumbent's slice recall `I_s > 0`, and
per-user paired recall vectors over `n_s` users:

```
A_s = t(0.95, m-1) * stdev(c_1..c_m) / sqrt(m) / I_s        seed half-width, relative
B_s = 1.6449 * SE_bootstrap(d_u) / I_s                      population half-width, relative
H_s = sqrt(A_s**2 + B_s**2)                                 combined one-sided 95% half-width

tolerance_s = max(0.005, ceil_to_0.1_percentage_point(H_s))
refuse to propose when H_s > 0.03
```

`SE_bootstrap` is the standard deviation of the resampled means of `d_u = c_u − i_u`, over a fixed
number of user-level bootstrap replicates at a fixed seed. When more than one study run carries
per-user vectors, the per-run standard errors are averaged and their min/max reported.

### Why each piece

- **Student's `t` on the seed term, not `z`.** With three seeds the standard deviation estimate is
  itself very uncertain, and `t(0.95, 2) = 2.920` against `z = 1.645` is the honest price of that.
  It has an uncomfortable consequence, stated rather than hidden: *not knowing the noise well makes
  the tolerance larger, which makes the gate weaker.* That is why the cap below exists, and why the
  right response to a wide interval is more seeds rather than a bigger number.
- **`/√m`, because the gate reads a mean.** The gate's statistic is the mean of `m` seeded runs. A
  half-width built from the spread of individual runs would describe a statistic nobody computes.
- **Quadrature, per the composition argument above.** Note the two half-widths use different
  multipliers (`t` and `z`), so their quadrature sum is slightly wider than the exact joint 95%
  bound would be. That excess is accepted rather than corrected away; it errs permissive, which the
  cap is there to bound.
- **Rounded up to the next 0.1 percentage point.** Coarse enough to be a number a human types into
  `make gate-retrieval` and a document quotes, fine enough that the rounding is not itself a policy
  decision.
- **Floored at 0.5%.** Two reasons. A tolerance below the resolution at which these numbers are
  published and reproduced is not a real constraint. And a tolerance of exactly zero — which a
  degenerate study can produce — would make the clause fail on floating-point dust.
- **Capped at 3%, and the cap is a refusal rather than a clamp.** A guardrail permitting a larger
  relative loss on a supporting slice than the relative gain the gate demands on its primary slice
  cannot distinguish "improved" from "traded cold away for warm". `MIN_WARM_RELATIVE_GAIN` is the
  natural bound because it comes from this same gate rather than from the ranking one. Exceeding it
  is a statement about the measurement, not about the model, and the answer is to reduce the noise —
  not to write down a number that no longer guards anything. Clamping silently to the cap would
  produce a tolerance that does not cover the measured noise, which is the one output this
  instrument must never emit.

### Why not the house rule verbatim

The ranking tolerances came from a rule already written down in `gate.py`: *2× the largest relative
range observed on that slice, rounded up to the next whole percentage point, floored at 0.5%.* Its
three justifications were examined one at a time, and only one transfers.

| House justification | Transfers? |
|---|---|
| `2×` because the gate compares two independently seeded runs, so the difference carries both runs' noise | **No.** The retrieval incumbent is deterministic and contributes exactly zero seed variance. Keeping the factor would double a term that has only one side. |
| Round up, because a tolerance sitting exactly on an observed maximum refuses good models for the next re-seed's wobble | **Partly.** The margin is still wanted, but the `t` multiplier now supplies it explicitly. Rounding to the next *whole* point on top of that would add up to a full percentage point of slack to a raw value that may be 0.3% — a large relative inflation doing a job already done. Hence 0.1 points. |
| A 0.5% floor, because anything finer is below the resolution these numbers are published at | **Yes.** Kept unchanged. |

The range statistic itself is replaced by `sd/√m` for the reason above: the retrieval gate reads a
mean, the ranking derivation was describing single runs.

### Which direction of error is safe

Worth being explicit, because every free choice above was resolved against it. A tolerance that is
**too large** promotes a model with a real cold or overall regression — a quality error that reaches
the served path. A tolerance that is **too small** refuses a model that was fine — a velocity error,
recovered by re-running the study, publishing a correction, and re-running the gate. This project
prefers the velocity error, so where the rule had a defensible choice between estimators it takes the
smaller one (paired rather than unpaired bootstrap; mean rather than max across study runs). What it
never does is go below the measured floor: the point is a tolerance that covers the noise, and
"smaller is safer" is a tie-break among defensible estimators, not a licence to ignore a measurement.

### Illustrative arithmetic — not a measurement, not a proposal

With entirely made-up inputs, to show the rule's shape only. Suppose a cold slice with `I = 0.5000`,
three study runs scoring `0.4950 / 0.5060 / 0.5010` (`sd = 0.00551`), and a paired bootstrap standard
error of `0.0035` over 702 users:

```
A = 2.920 * 0.00551 / sqrt(3) / 0.5000 = 0.01857
B = 1.6449 * 0.0035 / 0.5000           = 0.01151
H = sqrt(0.01857² + 0.01151²)          = 0.02185
tolerance = ceil to 0.1pp              = 0.022        (seed-dominated; under the 3% cap)
```

Every number above is invented. No tolerance is proposed by this document.

## Minimum admissible evidence

The harness consumes one JSON evidence document (`schema_version: 1`) and nothing else. It does not
read MLflow, because per-user recall vectors are not in the MLflow envelope and a study that cannot
be replayed from a file is a study nobody can check.

Required:

- **One incumbent run.** `model_type` exactly `itemitem_cosine`, no seed, a complete canonical
  `ProtocolManifest`, its warm/cold/overall recalls and slice user counts, and per-user recall
  vectors for every gating slice.
- **At least three candidate study runs.** One `model_type` (matching the declared candidate family
  and differing from the incumbent's), one `configuration_id`, distinct integer seeds, the same
  complete protocol, the same slice populations, and per-user recall vectors for at least one of them
  on every gating slice.
- **`gate_configuration_id`** — the configuration the tolerance is destined to gate.
- **`surrogate_delta`** — required, and required to be non-empty, exactly when the study's
  `configuration_id` differs from `gate_configuration_id`. It is the machine-recorded statement of
  what the transfer assumption is being asked to span.
- **`zero_seed_variance_justification`** — required only when a slice's seed spread is exactly zero.

The per-user vectors are not decoration. They are checked against the published slice means: a vector
whose mean does not reproduce the run's reported slice recall means the evidence document does not
describe the run it claims to, and the study refuses rather than bootstrapping the wrong numbers.

### Document shape

```json
{
  "schema_version": 1,
  "model_type": "sasrec",
  "gate_configuration_id": "sasrec-full-25m-v1",
  "surrogate_delta": null,
  "zero_seed_variance_justification": null,
  "incumbent": { "…one run object…" },
  "study_runs": [ "…three or more run objects…" ]
}
```

A run object carries `run_id`, `model_type`, `seed` (null only for the incumbent),
`configuration_id`, the complete canonical `protocol` payload — the same object MLflow stores under
the `evaluation_protocol` tag — a `metrics` object with `warm_recall`, `cold_recall`,
`overall_recall`, `n_warm_users` and `n_cold_users`, and a `per_user_recall` object mapping each
slice name to `{user_id: recall}`.

The bootstrap is drawn from one fixed seed for every run in a slice, so the resample indices are
identical across runs and a spread in the per-run standard errors reflects a real difference in the
paired differences rather than Monte-Carlo wobble. Re-running the harness on the same document
reproduces every digit.

### What the harness does when evidence is missing

It refuses, with a state and a reason, and emits no tolerance. There is no partial mode, no
"population-only" fallback, and no default. `ToleranceStudyReport.as_tolerance()` raises rather than
returning a `RetrievalTolerance` unless the status is `proposed`, so a caller cannot accidentally
carry an unestablished number into the gate.

| State | Meaning | Exit |
|---|---|---:|
| `proposed` | Complete comparable evidence; both gating tolerances derived | 0 |
| `too_noisy` | Measured, but a slice's half-width exceeds the cap; no number is safe to publish | 1 |
| `degenerate` | A slice's seed spread is exactly zero and unexplained — usually a seed not wired through | 1 |
| `insufficient_evidence` | Too few seeds, missing vectors, missing declarations, gate seeds reused | 2 |
| `not_comparable` | Protocol, stage, metric, K, populations, or user sets differ | 2 |

The split between 1 and 2 mirrors `retrieval_gate`: 1 is "we measured and decline", 2 is "we could
not measure".

## Operator procedure

1. Freeze the candidate configuration. A study of a configuration that later changes measures
   nothing.
2. Confirm the fixed-seed control: two runs at one seed produce identical slice metrics. A nonzero
   result here is a reproducibility bug (non-negotiable #5) and blocks the study.
3. Choose derivation B if the compute budget allows, A otherwise, and record which.
4. Run `m ≥ 3` study runs under the gate's exact protocol — same holdout, same slices, same K,
   same eligible population. Cheapen training only.
5. Export per-user recall vectors alongside the slice means into the evidence document.
6. Run `make retrieval-tolerance-study EVIDENCE=<path>` and keep its JSON output.
7. Publish the report — the two tolerances, the derivation, the seeds, both half-widths, and the
   incumbent run id — **before** running the gate's seeds.
8. Run `make gate-retrieval` against that same incumbent run, passing the published tolerances.
9. After the gate's seeds land, recompute their observed relative range and compare it against the
   study's. Record the comparison whether or not it falsifies the transfer assumption.

## How we would know this protocol is wrong

- **The falsification check in step 9 fires repeatedly.** Then surrogate transfer does not hold on
  this pipeline and derivation A should be withdrawn, leaving B as the only admissible route.
- **Every study comes back `too_noisy`.** Then either the cap is the wrong coherence constraint or
  the retrieval pipeline is too noisy to gate on per-slice non-regression at all, and the honest
  response is to change the gate's shape — not to raise the cap until something passes.
- **Both tolerances land at the 0.5% floor.** Then the floor, not the measurement, is setting the
  threshold, and the floor's justification (publication resolution) needs re-examining against
  numbers that are apparently far more stable than the ranking pipeline's.
- **A model passes the gate and then regresses visibly on the cold slice in a paired
  champion/challenger comparison.** Then the tolerance is too permissive in a way the offline study
  did not see, most likely because the study's population term was measured on a surrogate whose
  per-user error structure differs from the full model's.

## Residual gaps, recorded rather than papered over

- **The gate cannot check that the tolerance it was given was measured against the incumbent it is
  comparing to.** The study records the incumbent run id; `retrieval_gate` takes two bare floats and
  has no way to verify their provenance. Closing this means changing the gate's signature, which is
  out of scope here. Until then it is an operator-checklist item (step 8).
- **The publication-order protection is procedural.** See derivation B above.
- **Per-user recall vectors are not currently produced by `src/evaluation/protocol.py`.** `evaluate()`
  returns slice means only; the study consumes vectors as evidence and does not compute them. Wiring
  their export is a separate change to the trainers' evaluation call sites.
- **`m = 3` is a very thin basis for a standard deviation**, and the `t` multiplier makes that
  visible rather than fixing it. A study that can afford five seeds should run five; the harness
  accepts any `m ≥ 3` and the multiplier tightens automatically.
- **The bootstrap assumes users are exchangeable.** They are not perfectly — heavy users contribute
  more targets — and a user-level bootstrap of a user-averaged metric is nonetheless the right
  resampling unit for the estimand the gate reads. A target-weighted metric would need a different
  resampling scheme, which is the workstream's "target reachability" line and not this instrument.
