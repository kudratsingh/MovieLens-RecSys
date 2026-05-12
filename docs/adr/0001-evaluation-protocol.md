# ADR 0001 — Evaluation Protocol

**Status:** Draft — decisions need review before any model code is written  
**Date:** TBD

## Context

Before any model is trained, we must pin down the evaluation contract. Getting this wrong and revisiting it mid-project invalidates comparisons across runs.

## Decisions

### Metric(s)

- **Candidates stage:** Recall@500 (did the true next item land in the retrieved set?)
- **Ranker stage:** NDCG@10, MAP@10 (quality of ranking within the candidate set)
- **Baseline comparison:** HR@10 (hit rate) for simplicity in Phase 1

### Split definition

- **Temporal split only.** No random splits on temporal data. Ever.
- Cutoff timestamp T = last 10% of interactions by time.
- Train: all interactions before T.
- Validation: interactions in [T, T + 2 weeks].
- Test: interactions after T + 2 weeks (held out until final eval).

### Negative sampling

- Candidate-stage negatives: uniform random from unseen items per user.
- Ranker negatives: 4:1 negatives:positives sampled from candidates not interacted with.

### Cold-start slicing

- Users with < 5 interactions are tracked separately as "cold" users.
- Metrics reported separately for cold vs. warm users in every eval run.

### Promotion threshold (Phase 4)

- A new model promotes only if it beats the incumbent by ≥ 1% NDCG@10 on the holdout set.
- Gate is automated via the evaluation module — never eyeballed.

## Alternatives considered

- Random splits: rejected — temporal leakage inflates metrics by 5–15% in recsys literature.
- Leave-one-out: rejected — computationally expensive and not representative of production latency patterns.

## Consequences

All training and evaluation code must import from `src/evaluation/` — no ad-hoc metric computation in notebooks or training scripts.
