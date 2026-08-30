# Which slice does the promotion gate read?

**Date:** 2026-08-29
**Status:** open — a decision for the owner. Nothing in [ADR 0001](adr/0001-evaluation-protocol.md) is amended by this document.
**Companion:** [`cold-start-routing-decision.md`](cold-start-routing-decision.md), the other ADR 0001 question the same measurement session surfaced.

## The question

[ADR 0001](adr/0001-evaluation-protocol.md) pins the promotion gate as:

> A challenger model is only promoted if it beats the incumbent by ≥ +3% relative
> NDCG@10 **on the holdout**. Concretely: challenger must score at least
> `champion_score * 1.03`. … Gate is automated via the evaluation module — never
> eyeballed.

The same ADR also pins, two sections earlier:

> Metrics reported separately for cold vs. warm users on every eval run —
> aggregating them hides cold-start failure modes.

"The holdout" is one number; "reported separately" produces three. The gate does
not say which of the three it reads, and until [`results.md`](results.md) that
was a hypothetical. It is not any more: on the first full-dataset comparison the
answer decides the verdict.

No new run was made for this document. Every figure below comes from the MLflow
runs already recorded in [`results.md`](results.md) — `cf-als-baseline`
(`d961e6d9ba214edb9283266777aebf40`) and `lgbm-lambdarank-itemitem-candidates`
(`1d898b02fcc842b6a7283dc6eb9117ad`), both measured 2026-08-29 on MovieLens 25M
over an identical holdout of 1,939 warm and 702 cold users.

## What the numbers say

The two-stage ranker against the CF/ALS incumbent, NDCG@10, per ADR 0001's own
slicing:

| Slice | Users | CF/ALS | Ranker | Relative | vs. the +3% gate |
|---|---:|---:|---:|---:|---|
| Warm | 1,939 | 0.057850 | 0.055444 | **−4.16%** | fails |
| Cold | 702 | 0.487981 | 0.563104 | +15.39% | clears |
| Overall | 2,641 | 0.172182 | 0.190384 | **+10.57%** | clears |

Recall@10 over the same runs, for context — it moves the other way, and the two
metrics disagreeing is the whole shape of the finding:

| Slice | CF/ALS | Ranker | Relative |
|---|---:|---:|---:|
| Warm | 0.033841 | 0.039422 | +16.49% |
| Cold | 0.063780 | 0.079337 | +24.39% |
| Overall | 0.041799 | 0.050032 | +19.70% |

**The two-stage path finds more of the right titles for a warm user and orders
them slightly worse.** That is a coherent result rather than a paradox: item-item
retrieval hands the ranker 500 candidates holding 40.0% of a warm user's holdout
items, so more of them reach the top ten; LightGBM's ordering of the ten it
picks is, on this single seeded run, marginally behind ALS's.

## Why the aggregate clears when the slice it mostly describes does not

`overall` is the mean over all 2,641 users, so it is a user-weighted average of
the two slices. Warm users are **73.4%** of the holdout. But NDCG@10 on the cold
slice is an order of magnitude larger than on the warm one — 0.56 against 0.055
— for a reason that is a property of the metric, not of the model: a cold user
holds out few items and NDCG's ideal DCG is truncated at K, so a single popular
hit near the top scores near 1.0, while a warm user with forty holdout items is
scored against an ideal ten of them.

Decomposing the aggregate into each slice's contribution to the total NDCG mass:

| | Warm mass | Cold mass | Total | Cold's share |
|---|---:|---:|---:|---:|
| CF/ALS | 112.17 | 342.56 | 454.73 | 75.3% |
| Ranker | 107.51 | 395.30 | 502.80 | **78.6%** |
| Change | **−4.66** | **+52.74** | +48.07 | — |

So **26.6% of the users carry 78.6% of the aggregate**, and **109.7% of the
improvement the gate would read comes from the cold slice while the warm slice
subtracts 9.7%.** Two counterfactuals make the dependence exact:

- Had the warm slice merely held flat at CF/ALS's value and only cold moved, the
  aggregate would read **+11.60%** — better than what actually happened.
- Had the cold slice held flat and only warm moved, the aggregate would read
  **−1.03%** — the gate fails on everything except the cold slice.

The gate as written is therefore, on this data, close to a cold-slice-only gate
wearing an aggregate's name. That is the mirror image of the failure ADR 0001's
per-slice reporting rule was written to prevent: the rule exists so the cold
slice cannot be hidden by the warm majority, and here the warm slice is being
hidden by the cold minority.

One thing the table should not be read as saying: the ranker is not *bad* on warm
users. Against the popularity baseline the same warm slice is **+79.2% NDCG@10**
and **+141.2% recall@10**. The regression is against ALS specifically, and it is
4%.

## The options

### (a) Gate on warm NDCG@10

**Today it would say:** rejected (−4.16%, needs ≥ +3%).

The warm slice is the only place two personalization policies actually compete —
every cold user gets the popularity fallback under one model and the popularity
fallback re-ranked under the other, so the cold column is largely a measurement
of the fallback, not of the challenger. It is also the slice whose users a
retrained model is meant to serve better.

Against it: a challenger that genuinely improved cold-start — the thing
non-negotiable #3 exists for — would be rejected for not helping the warm
majority, and this project has an entire ADR (0011) and a 2,000-user cohort
devoted to cold-start quality.

### (b) Gate on overall NDCG@10 (the status quo reading)

**Today it would say:** promoted (+10.57%).

It is what "on the holdout" most naturally means, it is one number, and one
number is the easiest thing for a Prefect task to compare. It is also, as the
mass decomposition above shows, a number that on this dataset is 79% about 27%
of the users — so it would promote a model that made the warm majority's
ordering worse, and would do so silently.

### (c) Gate on overall, with a per-slice no-regression clause

**Today it would say:** rejected, if the clause is "no slice may regress at all";
promoted, if the clause allows a 5% band.

The shape most production gates end up at: the aggregate is the headline, and no
individual slice may go backwards by more than a stated tolerance. It answers
both objections above — a cold-start win still promotes, a warm regression still
blocks — at the cost of a second threshold that has to be chosen, and of a gate
whose verdict is no longer a single comparison.

The tolerance is the whole question and it should be set from noise, not from
taste. This project cannot set it from noise yet: `results.md` is one run per
model at one seed, so there is no variance estimate, and a −4.16% move cannot
currently be distinguished from a re-seeded −4.16%-sized wobble. A tolerance
pinned today would be a guess wearing a number's clothes.

### (d) Gate on both slices independently, each at +3%

**Today it would say:** rejected (warm fails).

Strictest, and the simplest to state — no aggregate, no weights, no tolerance.
Rejected here for the same reason (a) is: a model that is 15% better for
cold-start users and 4% worse for warm ones is not obviously a model to refuse,
and this rule refuses it without letting anyone say so.

## Recommendation

**Adopt (c): the gate reads overall NDCG@10 at +3%, and no slice may regress by
more than a stated tolerance — and set that tolerance from measured variance,
not from judgement.** Concretely, in this order:

1. **Amend the gate wording now** to say that it reads the aggregate *and*
   asserts per-slice non-regression, leaving the tolerance a named `TODO` with
   this document as its reference. The wording is the part that is wrong today;
   "the holdout" is ambiguous and the ambiguity has already produced a verdict
   nobody chose.
2. **Before Phase 4 automates it, measure the noise floor** — the same model,
   the same data, three to five seeds, and the spread of warm NDCG@10 across
   them. `src/evaluation/` already produces the number; nothing new needs
   building. Set the tolerance at the observed spread rounded up, and record it
   in the ADR the way the +3% was recorded.
3. **Until that number exists, the gate stays documented rather than
   automated** — which is what `pipelines/` being empty already means in
   practice. Nothing has been promoted on the strength of these figures and
   nothing should be.

The reasoning for (c) over (b) is that (b) is not measuring what its own ADR says
it measures: "aggregating them hides cold-start failure modes" is the argument
for per-slice reporting, and the same argument, run the other way, is the
argument against a promotion decision taken on the aggregate alone. The reasoning
for (c) over (a) and (d) is that a strict per-slice gate makes cold-start
improvement unpromotable, and this project has spent an ADR and a cohort on
cold-start being a first-class outcome.

**And whichever option is taken, the run this decision is being made about should
be re-measured on more than one seed before anything is promoted on it.** A 4%
warm regression at n=1 is a direction, not a result.

## The exact ADR text change each option implies

All four are edits to ADR 0001's **Promotion threshold (Phase 4)** section, plus
the matching line in **Consequences** ("The Phase 4 Prefect promotion DAG reads
NDCG@10 from MLflow and enforces the +3% gate automatically"). ADR 0001 stays
**Accepted** in every case; this is a clarification of a rule that was always
meant to be unambiguous, not a reversal.

- **(a)** — replace "on the holdout" with "on the **warm** holdout slice
  (`warm_ndcg_at_k`)", and add a sentence saying the cold slice is reported but
  not gated, because under every model in this repository it is the popularity
  fallback and therefore not a comparison between challengers.
- **(b)** — replace "on the holdout" with "on the **overall** holdout
  (`overall_ndcg_at_k`)". This changes no behaviour; it removes the ambiguity by
  ratifying the reading `results.md` already used, and should carry a note that
  the aggregate is user-weighted and, on MovieLens 25M, 78.6% cold by NDCG mass.
- **(c) — recommended** — replace "on the holdout" with "on the overall holdout
  (`overall_ndcg_at_k`), **and no slice may regress by more than T% relative**",
  add `T` to the section with its derivation (the measured seed-to-seed spread
  of `warm_ndcg_at_k`), and extend the Consequences line to say the DAG reads
  `warm_ndcg_at_k`, `cold_ndcg_at_k` and `overall_ndcg_at_k` and fails on any of
  the three.
- **(d)** — replace the threshold sentence with "the challenger must clear +3%
  relative NDCG@10 on **each** of the warm and cold holdout slices", and note
  that the aggregate is reported but not gated.

## What this document does not claim

- **It does not claim the ranker is worse than CF/ALS.** It is better on five of
  the six figures in the two tables and worse on one. What it claims is that the
  gate, as written, does not say which of those six it reads.
- **It does not claim the warm regression is real.** One run, one seed. It is
  large enough to name and too small to trust, which is exactly why step 2 of
  the recommendation exists.
- **It does not compare the ranker to item-item.** Those are different stages at
  different K and are not comparable — see `results.md`, "How to read this".
- **Nothing here is automated.** `pipelines/` is empty, no gate has ever run, and
  no model has been promoted.
