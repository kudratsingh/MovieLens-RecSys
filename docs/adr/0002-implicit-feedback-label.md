# ADR 0002 — Treat every rating as a positive implicit-feedback interaction

**Status:** Accepted
**Date:** 2026-05-31

## Context

MovieLens 25M ships explicit ratings on a 0.5–5.0 half-star scale. Phase 1's CF baseline (matrix factorization via `implicit` ALS) needs a binary "did this user interact with this item?" signal as input — the question is **what counts as a positive**.

The EDA findings ([`docs/eda.md`](../eda.md)) showed a heavy positive skew (mode at 4.0, mean ≈ 3.5) with the following thresholds:

| Threshold | % of ratings |
|---|---|
| ≥ 4.0 | 49.81% |
| ≥ 3.5 | 62.52% |
| ≥ 3.0 | 82.11% |
| any rating | 100% |

## Decision

**Every rating is a positive interaction.** Train, holdout, and test all binarize ratings to 1.0; the numeric rating is dropped from the modeling pipeline. The CF model's input is a `(userId, movieId)` interaction list; the rating column is not used.

Same convention applies to the eval harness — a user-movie pair is in the holdout set whether the rating was 0.5 or 5.0.

## Rationale

1. **Production recsys typically optimize for engagement, not rating quality.** Watching, clicking, dwelling all happen *before* the rating event; modeling them is closer to the production reality this project is meant to teach. Explicit ratings are a luxury most platforms don't have.
2. **Throwing away half the data is expensive in the long tail.** A ≥4.0 threshold discards 50% of every user's history. For tail users with few ratings, that cliff matters. Per [EDA section 4](../eda.md), median item popularity is 6 ratings — halving signal at that scale meaningfully degrades the candidate-stage view of niche items.
3. **A 3.0 rating still expresses engagement.** The user picked the movie, watched it, and bothered to rate it. That's signal even if the rating is mediocre. Filtering it out treats "engaged but lukewarm" the same as "never saw it," which is wrong.
4. **Symmetry with the eval boundary.** ADR 0001 defined holdout as "any interaction in the 28-day window." Consistent train/holdout label definitions keep the metric honest — if we trained on ≥4.0 and evaluated against any-rating holdout, we'd be optimizing for one target and scoring on another.

## Alternatives considered

- **Threshold ≥ 4.0 ("positive = liked").** Aligns with intuition ("recommend things the user would rate highly") but contradicts implicit-feedback production practice and throws away 50% of training signal. Rejected as the wrong abstraction for this project's purpose.
- **Threshold ≥ 3.0 ("positive = not actively negative").** Less aggressive but still arbitrary; the choice of 3.0 vs 3.5 vs 4.0 has no principled answer. Rejected — if we're going to throw away data, it should be for a reason, not a guess.
- **Use rating as a continuous confidence weight in ALS.** `implicit` does support per-interaction weights via the data values of the sparse matrix; we could pass `rating` or `1 + α·rating` instead of `1.0`. Defensible but adds a hyperparameter (`α`) and complicates the eval-side labeling (does a rating-as-confidence model count 0.5-star ratings as "positives" or not?). Rejected for Phase 1 simplicity; revisit if CF ablations show it matters.
- **Drop low ratings (<2.0) from training.** Distinguishes "engaged but disliked" from "engaged." Plausibly correct, but only 6.3% of ratings are <2.0 — small effect, large complexity. Rejected.

## Consequences

- **Code:** the rating column drops out of `src/training/cf.py` and `src/models/candidates/cf.py`. The sparse matrix passed to `AlternatingLeastSquares.fit` has values fixed at `1.0`.
- **Eval:** `src/evaluation/protocol.evaluate` is already rating-agnostic — it consumes recommendation lists and a holdout set of item ids. No changes needed.
- **Phase 2 reopening allowed.** When the two-tower / LightGBM ranker comes online, the choice of training label can be revisited per-stage. A candidate generator and a ranker may rationally use different labels (e.g. retrieve on engagement, rank on rating).
- **Cold-start unaffected.** Cold users get popularity-fallback recommendations regardless; this ADR is about what we train on, not what we recommend.
