# ADR 0003 — Two-Stage Architecture: Candidate Generator + Ranker

**Status:** Accepted
**Date:** 2026-05-31

## Context

Phase 1 closed with two complete recommenders — popularity ([PR #12](https://github.com/kudratsingh/MovieRecSys-MachineLearningProject/pull/12)) and CF/ALS ([PR #14](https://github.com/kudratsingh/MovieRecSys-MachineLearningProject/pull/14)) — that each score every (user, item) pair from a single model. Phase 2 needs to decide whether the production architecture inherits that shape (one model, scored globally) or splits into a *candidate generator → ranker* pipeline.

[CLAUDE.md](../../CLAUDE.md) names two-stage as the locked-in choice and lists "two-stage over single-stage" as one of the ADR-deserving decisions; this document fills that slot, pinning *why* before the Phase 2 code arrives.

## Decision

**Two stages.**

- **Candidate generator** retrieves ~500 candidates from a precomputed index of the full catalog (item-item similarity in the first cut; two-tower embeddings + ANN later in Phase 2).
- **Ranker** (LightGBM) scores those 500 candidates with rich per-(user, item) features pulled from the feature store and returns the top K.

The Phase 1 baselines (popularity, CF) become legitimate candidate generators under this architecture rather than full recommenders — they slot into the candidate stage as new options to compare against item-item and two-tower.

## Rationale

1. **The latency SLO forbids single-model global scoring.** Non-negotiable #4 pins p99 < 100ms. Scoring all ~62 000 catalog movies per request with a feature-rich ranker doesn't fit any plausible budget:
   - LightGBM inference: ~1 µs per prediction × 62 000 items ≈ 62 ms of *just inference*, before any IO.
   - Online feature lookup (Redis, ~1 ms round-trip per batch of features): even amortized across the catalog, the per-request feature payload is enormous. Real production ranker features are O(10–100) per pair; at 62 k items that's 600 k–6 M feature reads per request.
   - Network + serialization on top.

   Single-model scoring blows the budget by 1–2 orders of magnitude. A precomputed-index candidate stage cuts the ranker's workload from 62 k to ~500 — a 124× reduction that puts the SLO back in reach.

2. **Recall and precision optimize differently.** Candidate generation answers "did the right items make the cut?" (recall-oriented; per ADR 0001 the metric is recall@K_candidates over a sparse, global view of interaction data). Ranking answers "in what order should I show these K?" (precision-oriented; per ADR 0001 the metric is NDCG@10, fed rich per-pair features). The same model can't be excellent at both — recall wants broad coverage across the long tail, precision wants laser-focus on the head. Splitting the stages lets each be optimized for its own metric.

3. **Cost profiles favor different optimizations per stage.** Candidate generation can amortize the heavy lifting offline — item-item similarity precomputes a K-nearest-neighbors index, two-tower precomputes embeddings + ANN structure. Each request becomes O(query × top-N retrieval). Ranking can't precompute much: its features (user history aggregates, popularity windows, recency, genre affinities) are per-(user, item)-pair and time-sensitive. But the ranker only operates on the 500 surviving candidates, so it can afford much higher per-item cost. The two stages have complementary cost budgets that only make sense when they're separate.

4. **Independent iteration cycles.** Decoupled stages mean we can sweep candidate-generator hyperparameters without retraining the ranker (and vice versa). Phase 4's evaluation gate becomes simpler — promote a new candidate generator on its recall@K_candidates threshold; promote a new ranker on its NDCG@10 threshold; the other stage stays pinned. With a single model every change is a full retraining of everything.

5. **Industry convergence.** Every production recommender of meaningful scale (Google, Netflix, Spotify, YouTube, TikTok) uses multi-stage retrieval. The exceptions are narrow special cases (very small catalogs or unusually generous latency SLOs). For a project whose purpose is to confront the technologies and scenarios mid-to-senior ML engineers actually face, the two-stage pattern is the one to build muscle around.

## Alternatives considered

- **Single LightGBM scoring all (user, item) pairs.** Rejected on latency math above. Also has a subtle modeling problem — without an upstream candidate-selection signal, training has to use negative samples drawn from the full catalog. Popular items appear in more user histories, so they end up over-represented as positives *and* over-represented as the hard negatives the model has to discriminate against, creating a popularity-prior leak that's painful to correct.
- **Single two-tower with dense ANN retrieval, no ranker.** Some production systems do this (early TikTok, some Spotify surfaces) — the two-tower returns top-K directly via ANN, no second-stage ranking. Defensible at certain scales, but throws away the LightGBM ranker's strength: explicit feature engineering on tabular per-(user, item) signals (recency, popularity windows, user history aggregates). For a portfolio project whose explicit goal is to "confront the technologies a mid-to-senior ML engineer actually deals with," omitting the ranker omits half the muscle group.
- **Heuristic candidate generation → LightGBM ranker.** Take Phase 1's popularity + history-based candidates and hand them to a LightGBM ranker. Defensible as a Phase 1.5 intermediate but loses the entire candidate-modeling story (item-item, two-tower) — and that's exactly what Phase 2 is meant to teach.
- **Three+ stages (recall set → coarse ranker → fine ranker).** Used in some very-large-catalog systems. Overkill for MovieLens (62 k items). Adds operational complexity without latency relief at this scale. Rejected.

## Consequences

- **Code layout.** `src/models/candidates/` and `src/models/ranker/` are the two stage homes. `PopularityModel` and `CFModel` already live in `candidates/`; in Phase 2 they're joined by item-item and two-tower implementations and remain valid candidate-stage options for comparison and A/B testing.
- **Evaluation per stage.** `src/evaluation/protocol.py` already supports the metrics we need (recall@k for candidates, NDCG@k for ranker). Phase 2 needs a small extension: evaluate both stages in the same harness run — recall@K_candidates against holdout (does the candidate set contain the relevant items?) and NDCG@10 against the same holdout after ranking (are they ordered well?).
- **Training pipelines.** Phase 2 introduces two new pipelines mirroring the Phase 1 shape (`src/training/itemitem.py`, `src/training/two_tower.py`, `src/training/ranker.py`), each logging to the same MLflow experiment family so candidate-stage models are comparable to each other and ranker runs are comparable to each other.
- **Serving (Phase 3).** The FastAPI handler becomes `request → candidates → feature store lookup → ranker → top-K`. The handler owns the orchestration; neither stage's model class knows about the other.
- **Promotion gate (Phase 4).** Per-stage thresholds — candidate generators promoted on recall@K_candidates; rankers promoted on NDCG@10. The cross-stage interaction (does a better candidate generator make the ranker's job easier?) is measured but doesn't gate promotion of either side.
- **Cold-start policy.** ADR 0001's popularity fallback is a stage-level concern, not architecture-level. The candidate generator falls back to popularity for cold users; the ranker passes the popularity list through unchanged. `CFModel`'s embedded fallback (ADR 0002 / PR #14) is the Phase 1 expression of this; Phase 3 may lift the fallback up to the orchestration layer.
- **Future ADRs in this lineage.** "LightGBM over neural ranker" (specific ranker choice), "Item-item before two-tower" (candidate-stage progression), and "Feast as the feature store" each deserve their own ADRs — they inherit from this one but don't belong in it.
