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

- A challenger model is only promoted if it beats the incumbent by ≥ +3% relative NDCG@10 on the holdout.
- Concretely: challenger must score at least `champion_score * 1.03`.
- 3% filters out retraining noise while remaining achievable for genuine architectural improvements (expected 5–15% gains between major changes).
- Gate is automated via the evaluation module — never eyeballed.

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
- The Phase 4 Prefect promotion DAG reads NDCG@10 from MLflow and enforces the +3% gate automatically.

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
