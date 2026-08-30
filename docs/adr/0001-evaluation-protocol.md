# ADR 0001 — Evaluation Protocol

**Status:** Accepted  
**Date:** 2026-05-18  
**Amended:** 2026-08-30 — the cold-start threshold is 10, and it is the offline routing rule as well as the online one. See [the amendment](#amendment-2026-08-30--the-cold-start-threshold-is-10-online-and-offline) at the bottom; the "Cold-start slicing" bullets below are superseded by it.

## Context

Before any model is trained, we must pin down the evaluation contract. Getting this wrong and revisiting it mid-project invalidates comparisons across runs.

## Decisions

### Metric(s)

- **Candidates stage:** Recall@10 (did the true next item land in the top 10 retrieved?)
- **Ranker stage:** NDCG@10 (quality of ranking within the candidate set)
- **Baseline comparison:** HR@10 (hit rate) for simplicity in Phase 1

K=10 reflects a realistic number of recommendations shown to a user. Metrics beyond position 10 are not optimized for.

### Split definition

- **Temporal split only.** No random splits on temporal data. Ever.
- Cutoff timestamp T = the point at which 80% of all interactions have occurred.
- Train: all interactions with timestamp < T.
- Holdout: interactions in [T, T + 28 days).
- Test: held out until final evaluation only.

28 days captures weekly viewing patterns without letting the item catalog shift dramatically.

### Negative sampling

- Strategy: popularity-weighted — items sampled with probability proportional to their interaction count in the training set.
- Ratio: 100 negatives per positive.
- Popularity-weighted sampling is harder to game than uniform random; a model that simply recommends popular items will not score well because those items are over-represented as negatives.

### Cold-start slicing

*Superseded 2026-08-30 — see the amendment at the bottom of this file. The original text is kept because the numbers in `docs/results.md` from before that date were measured under it.*

- Users with fewer than 5 interactions in the training window are treated as cold-start users.
- Metrics reported separately for cold vs. warm users on every eval run — aggregating them hides cold-start failure modes.
- Cold-start users fall back to the popularity baseline at serving time until they cross the threshold.

### Promotion threshold (Phase 4)

*Amended 2026-08-30 — see the note at the bottom of this file for the decision, the
measurement behind the tolerance, and what it says about the runs already recorded.*

- A challenger model is only promoted if it beats the incumbent by ≥ +3% relative NDCG@10 on the **overall** holdout (`overall_ndcg_at_k`), **and neither the warm nor the cold slice regresses by more than that slice's tolerance**.
- Concretely: challenger must score at least `champion_score * 1.03` overall, and at least `champion_slice_score * (1 - T_slice)` on each of `warm_ndcg_at_k` and `cold_ndcg_at_k`.
- 3% filters out retraining noise while remaining achievable for genuine architectural improvements (expected 5–15% gains between major changes).
- `T_warm` and `T_cold` are the **measured** seed-to-seed noise floor, not a chosen band — the derivation is in the 2026-08-30 note.
- Gate is automated via the evaluation module — never eyeballed. It is `promotion_decision` in [`src/evaluation/gate.py`](../../src/evaluation/gate.py).

### Reproducibility

- All evaluation runs are seeded.
- Negative sampling uses a fixed seed derived from the experiment ID so the same model evaluated twice gets the same negatives.

## Alternatives considered

- **Random splits:** rejected — temporal leakage inflates metrics by 5–15% in recsys literature.
- **Leave-one-out:** rejected — computationally expensive and not representative of production latency patterns.
- **Uniform random negatives:** rejected — too easy to game with a popularity prior; popularity-weighted is the honest choice.
- **+1% promotion threshold:** considered too small; noise from retraining randomness alone can exceed 1%.

## Consequences

- All training and evaluation code must import from `src/evaluation/` — no ad-hoc metric computation in notebooks or training scripts.
- The evaluation module must log cold-user and warm-user metrics separately to MLflow on every run.
- Any feature using data with timestamp >= T is illegal in training; point-in-time correctness is enforced at the evaluation boundary.
- The Phase 4 Prefect promotion DAG reads `overall_ndcg_at_k`, `warm_ndcg_at_k` and `cold_ndcg_at_k` from MLflow and enforces the gate automatically — the aggregate's +3% and both slices' non-regression clause, failing on any of the three.

## Amendment 2026-08-30 — the cold-start threshold is 10, online *and* offline

**Decided by the owner.** Two things change together, and they are one decision:

1. **`COLD_START_THRESHOLD` is 10, not 5.** Fewer than ten distinct positive
   interactions and the user is cold; ten or more and the learned path may serve
   them. The product statement of it is *"the first ten should be popular, then
   personalized."*
2. **The offline candidate models route on that threshold too**, where before
   they routed on index membership — option (a) of
   [`../cold-start-routing-decision.md`](../cold-start-routing-decision.md).
   `cold_start_threshold=COLD_START_THRESHOLD` is now the constructor default on
   `CFModel`, `ItemItemModel` and `TwoTowerModel`, and `threshold` is the default
   `SYNTH_COLD_ROUTING` policy. `None` / `SYNTH_COLD_ROUTING=index` remains as
   the explicit opt-out, and a run made under it is renamed
   `<base>-index-routing` so its numbers can never be read as a default run's.

### Why the second half, and why it is safe

The original text said cold-start users fall back to popularity *"at serving
time"* and never said what the offline models should do. They did something
else: `was_served_by_*` reduced to "is this user in the fitted index at all?",
so a user with one training interaction was served that single film's cosine
neighbours as though it were a taste profile — offline. The deployed service
sent the same user to the popularity fallback. Offline metrics were therefore
not measuring the policy production runs, which is the same failure the feature
parity test (non-negotiable #2) exists to prevent, applied to routing instead of
to features.

The objection that had blocked closing this was that it would invalidate
everything already measured. That was measured and is false. Both policies were
run on the full dataset through the trainers, and:

- **Every warm figure is identical to the last digit** under the two policies,
  and every cold and overall figure moves by **less than 0.08%** — a fourth
  decimal. The reason is a population fact: of 2,641 holdout users, 702 are cold
  and 701 of those were fallback-served under index membership too, so *exactly
  one* holdout user sits in the disputed band. MovieLens's natural holdout is,
  for this question, empty.
- **The [ADR 0011](0011-cold-start-coverage.md) cohort can see it, and the gap is
  large.** Routing a 1-interaction user to the fallback takes item-item's h1
  recall@500 from **0.144 to 0.460**, and h3 from 0.288 to 0.456. CF at K=10 shows
  the same direction at its own scale.

So the blast radius is confined to the one table built to detect this, and it
moves toward what the protocol says should happen: `synth_cold_routing_ok`
becomes true on every learned run, with fallback counts 500/500/500/0.

The evidence establishes that the two paths *differ* by a large margin below the
threshold, and the direction of the difference. It is not proof that popularity
is the better product answer for a 1-interaction user — the cohort's targets are
popularity-weighted, so every fallback-served bucket is flattered by
construction (ADR 0011's own Risks section). The decision to prefer popularity
below the boundary is the owner's product call; what the measurement settled is
that the choice is consequential and that making it costs nothing already
recorded.

### Why 10 rather than 5

A judgement about the product, not a fitted number: below ten signals the taste
profile is thin enough that a popularity list is the more defensible answer, and
the boundary is where the offline evidence above says the learned path is
weakest. It is deliberately a round, explainable number a viewer can be told.

### What this changes

- **`src/evaluation/protocol.COLD_START_THRESHOLD = 10`** is the single source of
  truth. The warm/cold slicing, the three candidate models' fallback routing,
  `src/serving/orchestration.py`'s live routing and the `serving_policy.threshold`
  it reports, and ADR 0011's `expected_fallback_served` all read it. Nothing
  restates the number.
- **Every number in [`../results.md`](../results.md) dated before 2026-08-30 was
  scored at threshold 5 under index-membership routing.** That file carries a
  dated note saying so. Cross-policy comparisons should use the both-policy
  tables in the routing memo, which were run head to head.
- **The promotion gate is unaffected in mechanism** and *more* coherent in
  meaning: it reads NDCG@10 on a holdout sliced at `COLD_START_THRESHOLD`, and
  the models now route on the same number the slicing uses, so a user reported as
  cold is a user the model actually treated as cold. Which slice the gate should
  read is a separate open question
  ([`../promotion-gate-slice-decision.md`](../promotion-gate-slice-decision.md)).
- **The deployed serving path is unchanged in shape** — it already routed on the
  threshold; only the value moved. The demo personas were re-seeded to stay above
  it (Action Fan and Drama Fan to 12 watched titles each).

### How we'd know we're wrong

If the ADR 0011 cohort is rebuilt with uniformly-sampled rather than
popularity-weighted targets and the fallback's advantage below the boundary
disappears, the direction this decision rests on was an artifact and the
threshold should come back down — or the offline models should go back to index
membership, which is why the opt-out was kept rather than deleted. If online
engagement on the 5–9 signal band turns out materially worse under popularity
than the learned path was, that is the same finding arriving from the other
side, and it is measurable once Phase 6's A/B routing exists.

## 2026-08-30 — amendment: the gate names its slices, and its tolerance is measured

Status stays **Accepted**. Nothing above is retracted. What changes is one
ambiguous sentence in **Promotion threshold (Phase 4)** — amended in place above,
with the reasoning, the measurement and the consequences here.

### What was ambiguous

This ADR gates promotion on *"≥ +3% relative NDCG@10 **on the holdout**"* while
requiring, two sections earlier, that metrics be *"reported separately for cold
vs. warm users on every eval run — aggregating them hides cold-start failure
modes."* Those sentences produce one number and three, and the ADR never said
which the gate reads. For three months that was a hypothetical.

It stopped being one on the first full-dataset comparison
([`results.md`](../results.md), 2026-08-29). The LightGBM ranker against the
CF/ALS incumbent read **+10.57% overall, +15.39% cold, −4.16% warm** — so the
same model was promoted or refused depending on a reading nobody had chosen.
Worse, the aggregate was carried by the minority: 26.6% of the holdout users hold
78.6% of its NDCG mass, and had the cold slice merely held flat the aggregate
would have read **−1.03%**. The gate as written was close to a cold-slice gate
wearing an aggregate's name — the mirror image of the failure the per-slice
reporting rule exists to prevent.

[`promotion-gate-slice-decision.md`](../promotion-gate-slice-decision.md) set out
four readings and recommended (c). The owner took (c) on 2026-08-30.

### The decision

**The gate reads overall NDCG@10 at +3% relative, and refuses any slice that
regresses beyond that slice's tolerance.** The aggregate stays the headline, so a
genuine cold-start win is still promotable — which matters in a project that
spent ADR 0011 and a 2,000-user cohort on cold-start being a first-class outcome.
A warm regression can still block, so the majority slice cannot be silently
traded away.

The three readings not taken, and why:

- **Warm only.** Would refuse a challenger that genuinely improved cold-start,
  the outcome non-negotiable #3 exists for.
- **Overall only (the status-quo reading).** Ratifies exactly the failure above.
- **Each slice independently at +3%.** Strictest and simplest, and refuses the
  cold-start win without letting anyone say so.

The rule is `promotion_decision` in
[`src/evaluation/gate.py`](../../src/evaluation/gate.py) — a pure function over
two `EvalResult`s, so a unit test and Phase 4's Prefect task run the same code —
with a CLI (`make gate CANDIDATE=… INCUMBENT=…`) that reads two MLflow runs. It
asserts `EvalResult.k` equality on both sides, because comparing a candidate
stage's recall@500 result with a ranker's NDCG@10 one would answer a question
nobody asked. A slice the incumbent has no users in, or scored zero in, is
reported as **not comparable** and does not block: the aggregate clause is a
positive claim the challenger has to establish, while a slice clause is a
negative one, and a negative claim that cannot be evaluated has not been
violated.

### The tolerance, measured

The memo was explicit that a tolerance pinned by taste would be *"a guess wearing
a number's clothes"*, so it was measured before it was set. CF/ALS and the
LightGBM ranker were each run three times through the unchanged trainers at seeds
42, 7 and 13, with nothing else varied
([`results.md`](../results.md), 2026-08-30 promotion-gate section; `TRAIN_SEED` in
`src/training/seeds.py`). The popularity baseline and item-item cosine have no
stochastic component and were not re-run. A re-run at seed 42 reproduced both
2026-08-29 runs of record on **every** logged metric — 38 and 36 respectively,
zero differences — so the spread below is seed-to-seed variation and nothing
else.

Relative range of NDCG@10 across the three seeds:

| Model | Warm | Cold | Overall |
|---|---:|---:|---:|
| CF / ALS | 3.12% | 0.04% | 0.75% |
| LightGBM ranker | **28.68%** | 3.17% | **5.81%** |

**Rule:** the tolerance for a slice is **2× the largest relative range observed on
that slice, rounded up to the next whole percentage point, floor 0.5%** — 2×
because the gate reads a difference between two independently seeded runs and so
carries both runs' noise; rounded up because a three-sample range underestimates
the true one. That gives **`T_warm` = 58%** and **`T_cold` = 7%**.

> **Superseded later the same day: `T_warm` = 6%, `T_cold` = 5%.** The rule above
> is unchanged; the measurement it reads is not. `RANKER_POSITIVE_LIMIT` was
> 20,000 against a trailing window holding 154,003 rows, so the seed was choosing
> *which* positives the ranker trained on. With the limit above the window's size
> the three seeds build the identical training set and the ranker's warm range
> falls from 28.68% to 1.68%, which makes CF/ALS at 2.96% the largest warm range
> and gives 6%. **The warm clause is now binding rather than decorative**, and
> the ranker clears the gate against CF/ALS at every seed with the warm slice
> *improving* ~21%. See
> [`docs/results.md`](../results.md#2026-08-30--the-rankers-training-sample-and-the-variance-it-was-buying)
> and [ADR 0005's note](0005-lightgbm-over-neural-ranker.md#note-2026-08-30--the-training-sample-is-the-whole-trailing-window).

### What it says about the run this amendment is about

Under that floor the 2026-08-29 comparison **clears the gate**: the warm
regression is −4.16% against a 58% tolerance, so it is comfortably *within* the
noise floor, and the aggregate's +10.57% clears +3%. The gate would promote.

That is not the reassuring answer it looks like. **The reason the warm clause
does not fire is that it currently cannot**: the ranker's warm NDCG@10 moves by
28.7% of its own mean when only the seed changes, so no tighter warm tolerance
could refuse a real regression without also refusing good models at random. Run
the same challenger-vs-incumbent comparison at each of the three seeds and the
warm effect reads **−4.16%, −17.24% and +13.59%** — its sign is not determined by
the data. The 2026-08-29 figure that made this question urgent was a draw from a
wide distribution.

Two consequences follow, and both are more important than the verdict:

1. **This ADR's +3% is itself inside the ranker's noise.** The threshold was
   chosen in May on the reasoning that *"noise from retraining randomness alone
   can exceed 1%"*. On this pipeline the ranker's **overall** NDCG@10 spread is
   5.81% — nearly six times that, and wider than the threshold the gate applies.
   **A single seeded run of the ranker cannot establish a +3% aggregate
   improvement.** Nothing has been promoted on one and nothing should be. The
   seed-averaged comparison (three runs per model) reads warm −2.76%, cold
   +13.24%, overall **+9.27%** — which does clear 5.81%, and is the comparison a
   Phase 4 DAG should be making.
2. **The floor is a defect to close, not a threshold to live with.** Its cause is
   `RANKER_POSITIVE_LIMIT = 20,000`: the seed decides which positives are sampled
   from the trailing window, ~8,600 are dropped for missing the item-item
   top-500, and ~11,350 LambdaRank groups remain — so a re-seed is a different
   training set. More seeds are not the cheap fix (the standard error of a
   three-seed mean is still 8.4% relative); a larger training sample is. On the
   Phase 3 platform backlog.

Both are now measurable claims rather than suspicions, which is the difference
this amendment is really making.

> **Both were acted on the same day.** Consequence 2 was the cause and it was
> closed by raising `RANKER_POSITIVE_LIMIT` above the trailing window's own row
> count: the three seeds now build the identical training set, the ranker's warm
> range falls to 1.68% and its overall range to 1.71%, and the tolerances become
> 6% / 5%. Consequence 1 stands but with room: **+3% is no longer inside the
> ranker's noise**, and the seed-averaged ranker-vs-CF/ALS comparison reads warm
> **+21.21%**, cold +13.67%, overall **+15.53%** — promoted at every individual
> seed too, with the warm sign now determined. Gating on a seed-averaged
> `EvalResult` remains the right practice and is what `mean_eval_result`
> (`src/evaluation/aggregate.py`) and `make gate CANDIDATE="<id> <id> <id>"`
> exist for.

### How we would know this is wrong

- **If a challenger with a real, reproducible warm regression is promoted.** The
  58% tolerance made that possible, and it was the known cost of setting the
  number from measurement instead of taste. The tell would be a promoted model
  whose warm slice stays down across several seeds. The fix was, as written
  here, to reduce the variance and re-derive — never to tighten below what the
  pipeline can resolve — and that is what happened: the tolerance is now 6%,
  which a real regression would trip.
- ~~**If the tolerance never falls.**~~ **Answered the same day, and the
  diagnosis held.** The test was: if the ranker training sample grows and the
  warm spread stays near 30%, the cause is not sampling. The sample grew from
  20,000 positives to the whole 154,003-row window and the warm spread fell to
  **1.68%** — so it was the sampling, and the tolerance fell to 6% / 5%.
- **If the aggregate and the slices never disagree again.** If every future
  comparison moves all three the same way, the per-slice clause is costing
  complexity for nothing, and the honest response is to say so and simplify back
  to (b) — with the 2026-08-29 run recorded as the one time it mattered.
