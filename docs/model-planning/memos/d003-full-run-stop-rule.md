# Memo — the advance/stop rule for the full-data SASRec run (D-003)

**Status:** open, owner decision required, and time-boxed. Written 2026-09-04 while the full-data
seed-42 run is still executing. The point of writing it now is that a stop rule chosen after the
number is visible is not a stop rule.

## What is being decided

D-003 is recorded as "SASRec pilot advance/stop margin," needed "before interpreting the 6% pilot."
Events have overtaken that framing: the pilot is finished, its winning arm has been frozen into
`docs/experiments/sasrec/full.json` (on the unmerged `feat/sasrec` line), and the full-data run at
seed 42 is in flight. The live question is narrower and more urgent — **what does a single full-data
seed-42 number authorize?**

## The fact that should drive this

The pilot already contains a same-sample comparison against the incumbent, and it has not been read
as one. All three references below were measured on the same deterministic 6% user subsample,
through the same `src/evaluation/` call:

| Reference on the 6% subsample | Warm recall@500 | Relative to item-item |
|---|---:|---:|
| Chance (500 of 19,005 items) | 0.026309 | 0.07× |
| Popularity | 0.1974 | 0.55× |
| **Item-item cosine** | **0.3619** | 1.00× |
| **SASRec, standard sampled BCE** | **0.3186** | **0.88×** |
| SASRec, gBCE at t=0.5 | 0.2937 | 0.81× |

**SASRec's pilot arm scored 12.0% below item-item on the same users.** The pilot record states it
"clears the pilot stop rule," and that is true as written — ADR 0016's rule was to beat popularity,
which it does by 1.61×. But that rule never named the incumbent, and the incumbent is what ADR 0004's
gate compares against. Nothing about the pilot indicates SASRec was ahead of item-item at any scale.

One caveat, stated because this project's whole comparison machinery exists to catch exactly this
class of slippage: the two-tower pilot reports 116 warm holdout users and the SASRec pilot reports
115. Same subsample, same train rows (1,213,918), same items (19,005), same users (8,316) — the
difference is one holdout user moving across the cold-start routing boundary, the same index-vs-
threshold artifact documented on the full dataset. It does not change a 12% gap, but the two numbers
are not from populations that are byte-identical, and a future reader should know that.

## What the full run is therefore testing

The frozen full-data cell differs from the pilot arm in exactly one respect: `sample_fraction` moves
from 0.06 to 1.0. Same loss, same 32 negatives, same two epochs, same exact retrieval, same seed. So
this is a clean scale experiment, and it tests one hypothesis:

> A 12.0% same-sample deficit against item-item closes, and then reverses into a 3% surplus, on
> 16.7× the data.

That is a ~17% relative swing bought with data alone. It is not absurd — a two-block 64-wide encoder
is plainly capacity-starved on 8,316 users, and sequence models are the model class that benefits
most from scale. But it is a large ask, and naming it as the hypothesis *before* the number lands is
the difference between an experiment and a post-hoc story.

## What evidence will and will not exist when seed 42 finishes

Will exist: warm, cold and overall recall@500 on the full eligible population, and item-item's
full-data warm recall@500 of **0.400144** as a protocol-compatible incumbent.

Will not exist:

- measured cold and overall seed tolerances — the instrument landed today, but it needs runs, and it
  needs per-user recall vectors that no trainer yet exports;
- a last-item transition baseline number — the model landed today, but it has not been run;
- a shuffled-sequence control or a tiny-overfit gate — neither is built, and both sit on SASRec code
  that is locked while the run executes;
- seeds 7 and 13.

The consequence is structural, not a matter of taste: **the retrieval gate returns `incomplete` on
one seed by construction.** Seed 42 cannot promote anything. It can only authorize the remaining two
seeds or stop the line.

## Three candidate rules

### A — plan-faithful

Treat seed 42 purely as a checkpoint. Authorize seeds 7 and 13 unless the result is a "decisive
loss." Faithful to the phase plan, but it defers the whole problem: "decisive" is undefined, and
defining it after seeing the number is the failure this memo exists to prevent.

### B — threshold-anchored (recommended)

Predeclare bands against numbers that are already measured, so the rule can be applied the moment
the run lands and needs nothing that does not yet exist.

| Full-data warm recall@500 | Meaning | Action |
|---|---|---|
| ≥ 0.412148 | clears ADR 0004's floor on one seed | run seeds 7 and 13; the gate decides |
| 0.400144 – 0.412148 | beats the incumbent, short of the floor | run seeds 7 and 13; a three-seed mean can still cross |
| 0.352 – 0.400144 | still behind, but the deficit shrank with scale | scaling is working and has not arrived; authorize only against a named compute budget |
| ≤ 0.352 | relative position no better than the pilot's | **stop**; record the negative result |

The stop band is not arbitrary. 0.352 is the pilot's own ratio carried onto the full incumbent
(0.88035 × 0.400144), so the rule reads: *if 16.7× the data did not improve SASRec's standing
against item-item at all, the scaling hypothesis is refuted on its own terms.*

**Why this needs no measured tolerance.** A 12% gap is not a noise-scale question. The pilot record
itself treats ±0.005 — about 1.5% relative — as the noise floor on a 116-user warm slice, and the
full holdout has 1,939 warm users, so its dispersion is smaller still. The bands are wide enough
that the missing tolerances cannot flip a decision between them. The tolerances remain required for
the actual gate verdict; they are not required for this authorization.

### C — noise-gated

Refuse to decide anything until tolerances are measured. Principled and unworkable in sequence: the
tolerance study itself needs three more full-scale runs plus a per-user export that does not exist,
so this rule spends the compute it is trying to protect before it can be applied.

## Recommendation

**B.** It is applicable on arrival, anchored entirely in measured quantities, and its stop band is
derived from the pilot rather than invented. If the run lands in the third band, that is genuinely a
judgment call and it should be taken as one — with the compute cost of two more full runs named
explicitly under D-004 before it is spent.

## What a pass would, and would not, license

Even a full three-seed gate pass is **retrieval-stage promotion eligibility only**. It is not the
claim that sequence modelling added value. That claim needs the last-item transition baseline and
the shuffled-sequence control, and neither will have run. The two must stay separate in whatever gets
written into `docs/results.md`, because collapsing them is how a recall number quietly becomes a
story about order mattering.

## How we would know this rule is wrong

- **If full-data seed dispersion turns out to be anywhere near 12%.** The bands assume it is far
  smaller. The tolerance instrument is what would reveal this, and if it does, band four has to be
  re-derived before it is trusted.
- **If the deficit closes non-linearly in epochs rather than in data.** Two epochs was chosen at 6%
  scale; the same two epochs over 16.7× the data is a different point on the optimization schedule,
  not only a different sample size. A stop under band four would then be punishing the schedule and
  calling it the architecture. If the run's loss curve is still descending steeply at the end, that
  is the signal, and it argues for one schedule change rather than closure.
- **If the pilot's 116-user warm slice was too small to place SASRec against item-item at all.** A
  12% gap on 116 users is not nothing, but it is one sample. The full run is itself the check.
- **If cold or overall recall moves opposite to warm.** The bands are warm-only because the gate's
  quality clause is warm-primary. A run that clears warm while collapsing cold would pass this
  authorization and then fail the gate's guardrails — the right outcome, but worth seeing coming.

## What I need from the owner

1. A, B, or C — or B with different band edges.
2. If band three: the compute budget for two further full-data seeds, per D-004.
