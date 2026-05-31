# EDA — MovieLens 25M

**Source:** MovieLens 25M (GroupLens), DVC-tracked under `data/raw/ml-25m/` and ingested into the local Postgres `movielens` database. See [ADR 0001](adr/0001-evaluation-protocol.md) for the evaluation contract that drives the split numbers below. Re-run any time with `make eda` — every figure is reproducible from `notebooks/eda.py`, which expresses everything as SQL aggregations against Postgres rather than pandas-ing 25M rows.

**Snapshot taken:** 2026-05-31

## Headlines

- **Scale:** 25,000,095 ratings, 162,541 users, 59,047 movies ever rated (62,423 in catalog). Sparsity = **0.2605%** — squarely "very sparse," standard recsys territory.
- **Cold-start in this dataset is dominated by *brand-new* users, not low-activity ones.** MovieLens pre-filters out users with fewer than 20 ratings, so the ADR 0001 "<5 training interactions" definition effectively collapses to "no training interactions at all." 701 of 2,641 holdout users (~26.6%) are brand new — meaningful enough that the warm/cold slicing in `protocol.py` clearly earns its keep.
- **Rating distribution is heavily skewed positive** — mode at 4.0 (26.6%), only 1.6% at 0.5. The choice of "positive interaction" threshold for Phase 2 labels needs an explicit decision (its own ADR).
- **Item popularity is the classic long-tail.** Median movie has 6 ratings; mean has 423; max is 81,491 (Forrest Gump). 3,376 movies (5.4% of catalog) have zero ratings — item cold-start is real.
- **Temporal split cutoff T = 2016-06-25 06:49:57 UTC.** Train hits 80.00% on the nose (20,000,075 rows), holdout is 0.52% (129,683 rows across 28 days), test is 19.48% (4.87M rows reserved across ~3.4 years).

## 1. Scale & sparsity

| Metric | Value |
|---|---|
| Ratings | 25,000,095 |
| Movies (catalog) | 62,423 |
| Tags | 1,093,360 |
| Links | 62,423 |
| Distinct users (in ratings) | 162,541 |
| Distinct movies rated | 59,047 |
| Movies in catalog never rated | 3,376 |
| Sparsity (filled cells) | 0.2605% |

`Sparsity = ratings / (users × movies_rated)`. At 0.26% filled there are ~9.6B candidate `(user, movie)` cells, of which only 0.26% are observed — exactly the regime that motivates collaborative filtering plus a candidate-generation stage rather than scoring the whole catalog per user.

The 3,376 catalog movies with zero ratings is the **item cold-start** population — the candidate generator needs a fallback path for them (likely content-based once we have features).

## 2. Rating distribution

| Rating | Count | % |
|---|---|---|
| 0.5 | 393,068 | 1.57 |
| 1.0 | 776,815 | 3.11 |
| 1.5 | 399,490 | 1.60 |
| 2.0 | 1,640,868 | 6.56 |
| 2.5 | 1,262,797 | 5.05 |
| 3.0 | 4,896,928 | 19.59 |
| 3.5 | 3,177,318 | 12.71 |
| **4.0** | **6,639,798** | **26.56** |
| 4.5 | 2,200,539 | 8.80 |
| 5.0 | 3,612,474 | 14.45 |

Strong positive skew; users overwhelmingly rate things they liked. Implication for label construction:

| Threshold for "positive" | % of ratings |
|---|---|
| ≥ 4.0 | 49.81% |
| ≥ 3.5 | 62.52% |
| ≥ 3.0 | 82.11% |
| Any rating (binarize) | 100% |

The decision is deferred to Phase 2 and deserves its own ADR. The "any rating = positive" choice mirrors implicit-feedback production reality (a click is a click) and is probably the most defensible; an explicit ≥4.0 threshold gives a cleaner signal but throws away ~half the data.

## 3. User activity distribution (ratings per user)

| min | p25 | p50 | p75 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|
| 20 | 36 | 71 | 162 | 554 | 1,228 | 32,202 | 153.8 |

**The minimum is 20** — MovieLens explicitly filters users with fewer than 20 total ratings out of the public release. This is a meaningful artifact:

- The ADR 0001 cold-start threshold (`<5 training interactions`) was specified for a typical recsys distribution. In this dataset, the only way to have `<5` training interactions is to have done most of your rating *after* the cutoff — true "low-activity" users barely exist.
- This doesn't invalidate the threshold (the eval slicing logic still works), but it means cold-start in practice ≈ "no training history at all." Section 9 confirms with a 701-vs-1 split.

Distribution is heavily right-skewed (mean 153.8 ≫ median 71; p99/p50 = 17×) — the long-tail user behavior is preserved despite the 20-rating floor.

## 4. Item popularity distribution (ratings per movie)

| min | p25 | p50 | p75 | p95 | p99 | max | mean |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 6 | 36 | 1,504 | 9,943 | 81,491 | 423.4 |

Even more skewed than user activity. **Half of all rated movies have ≤6 ratings.** Implications:

- The popularity baseline will be dominated by ~10k blockbuster films (everything above p95).
- Tail items barely have signal for collaborative filtering. Content features (genres, tags, possibly the `genome-*` CSVs we have on disk but didn't ingest) will likely matter for ranking them — relevant for Phase 2.
- The p99 (9,943) vs. max (81,491) gap suggests a power-law tail. Worth a log-log plot if we revisit this in Phase 2.

## 5. Top 10 most-rated movies

| Title | # ratings | avg rating |
|---|---:|---:|
| Forrest Gump (1994) | 81,491 | 4.05 |
| Shawshank Redemption, The (1994) | 81,482 | 4.41 |
| Pulp Fiction (1994) | 79,672 | 4.19 |
| Silence of the Lambs, The (1991) | 74,127 | 4.15 |
| Matrix, The (1999) | 72,674 | 4.15 |
| Star Wars: Episode IV - A New Hope (1977) | 68,717 | 4.12 |
| Jurassic Park (1993) | 64,144 | 3.68 |
| Schindler's List (1993) | 60,411 | 4.25 |
| Braveheart (1995) | 59,184 | 4.00 |
| Fight Club (1999) | 58,773 | 4.23 |

These will be the popularity baseline's top recommendations. Two patterns worth keeping in mind:

- **Popularity correlates with quality** — avg ratings 3.68–4.41, all clearly positive. A popularity baseline therefore wins on aggregate quality "for free." Beating it requires *personalization*, not just better global ranking.
- **Heavy 90s skew.** 9 of 10 are 1991–1999; the platform launched in 1995, so movies from around that era have accumulated the longest tail of ratings. Newer items are systematically under-represented at the head even though the dataset runs to 2019.

## 6. Temporal range

| Earliest | Latest | Span (days) | Span (years) |
|---|---|---:|---:|
| 1995-01-09 11:46:49 UTC | 2019-11-21 09:15:03 UTC | 9,081 | 24.9 |

A quarter-century of interaction history. The volume isn't uniform across that span — see the velocity calculation in section 8.

## 7. Split boundary T (per ADR 0001)

| Variable | Epoch | Date (UTC) |
|---|---:|---|
| Cutoff T | 1,466,837,397 | 2016-06-25 06:49:57 |
| Holdout end (T + 28d) | 1,469,256,597 | 2016-07-23 06:49:57 |

T is the timestamp of the 80th-percentile interaction. The SQL `percentile_disc(0.8)` and Python `np.quantile(method="lower")` both deliberately pick a value present in the data — and both implementations agree on this number (cross-check via [`src/data/split.py`](../src/data/split.py)).

**Train period:** 1995-01-09 → 2016-06-25 (~21.5 years)
**Holdout window:** 2016-06-25 → 2016-07-23 (28 days)
**Test buffer:** 2016-07-23 → 2019-11-21 (~3.4 years, reserved for end-of-project evaluation per ADR 0001).

## 8. Split sizes

| Slice | Rows | % of total |
|---|---:|---:|
| Train | 20,000,075 | 80.00 |
| Holdout | 129,683 | 0.52 |
| Test | 4,870,337 | 19.48 |
| **Total** | **25,000,095** | **100.00** |

Train hits 80.00% exactly (down to the row) — the `method="lower"` quantile landed cleanly. Holdout looks tiny as a fraction but is 130k interactions across 2,641 users (next section) — plenty of signal for offline evaluation.

Rating velocity sanity check:

| Period | Years | Ratings | Rate (per year) |
|---|---:|---:|---:|
| Pre-T (train) | ~21.5 | 20.0M | ~0.93M |
| Post-T (holdout + test) | ~3.4 | 5.0M | ~1.47M |

**Post-cutoff velocity is ~60% higher** than pre-cutoff. MovieLens activity was growing through 2016–2019; the test period is *not* a fading-platform tail.

## 9. Cold-start sizing

| Bucket | Users |
|---|---:|
| Warm in train (≥5 ratings before T) | 137,840 |
| Cold in train (<5 ratings before T) | 43 |
| Total users with any holdout activity | 2,641 |
| → New in holdout (no training history) | 701 |
| → Cold in holdout (<5 training ratings, ≥1 holdout) | 1 |
| → Warm in holdout (≥5 training ratings, ≥1 holdout) | 1,939 |

**This is the most consequential section for Phase 1.** It's exactly what `src/evaluation/protocol.py` will see when run against the real data:

- **Out of 2,641 holdout users, 702 are cold (701 + 1) ≈ 26.6%.** Reporting cold and warm metrics separately is plainly the right call — averaging them would let a quarter of users vanish into the warm population.
- **The "<5 training ratings" definition collapses to "0 training ratings" in practice** (701 vs. 1). The MovieLens min-20-ratings floor means there's no realistic population of "low-activity users with some history." `protocol.py` doesn't need to change, but the *interpretation* of cold-start in this project is "user is brand new at eval time."
- 137,840 warm-in-train users × ~59k movies sets the scale the two-tower will train against in Phase 2.
- `162,541 − (137,840 + 43) = 24,658` users have no train activity at all. 701 of them appear in holdout; the other 23,957 only appear in the test buffer (post-T+28d) and won't be evaluated until/unless we touch the test slice at the end of the project.

## Implications for the next steps

- **Step 9 — popularity baseline.** Section 5 *is* the baseline. It'll have strong global ratings ("popular = liked") but no personalization. Cold-start users (701) will receive exactly this list. Expect honest but unflattering NDCG@10 numbers — beating it requires real per-user signal.
- **Step 10 — CF baseline (ALS / LightFM).** Trains on 20M `(user, movie, rating)` triples — 138k warm users × 59k movies. Min-20-ratings floor means even the weakest user has enough signal for matrix factorization to place them meaningfully.
- **Phase 2 — candidate generator.** The 3,376 zero-rating movies + the ~30k near-zero tail are the item cold-start case. Content path (genres, tags, possibly genome features once ingested) needed for them.
- **Phase 2 — labeling decision (ADR).** Still owed: what counts as a positive interaction? Threshold ≥ 4.0 (~50% positives) vs. binarize-all (100% positives). Has downstream effects on both training and evaluation.

## How to reproduce

```bash
make infra-up   # if Postgres isn't already running
make eda        # ~30s; prints the same tables this file is sourced from
```

Every figure here is from `notebooks/eda.py`. SQL-only — no 25M-row pandas frames. The cutoff `percentile_disc(0.8)` in SQL is verified to match `np.quantile(..., method="lower")` in `src/data/split.py`.
