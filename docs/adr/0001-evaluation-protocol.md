# ADR 0001 — Evaluation Protocol

**Status:** Accepted  
**Date:** 2026-05-18

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
