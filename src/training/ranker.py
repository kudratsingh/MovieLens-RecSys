"""
Train the LightGBM ranker end-to-end and log to MLflow.

Follows the same skeleton as ``src/training/itemitem.py`` and
``src/training/twotower.py`` — load → candidate generation → sample
training pairs → compute features → fit ranker → predict on holdout →
evaluate → log. Runs land in a new ``phase-2-ranker`` MLflow experiment
(distinct from ``phase-2-candidates``) — the two experiments answer
different questions and shouldn't share a metric axis.

Design notes (called out per ADR 0005):

  1. **Candidate model.** The default candidate model is ItemItemModel —
     cheapest to fit, and PR #19 already benchmarked its recall@500 on
     holdout. A follow-up run swaps in TwoTowerModel once its number
     clears item-item on the ADR 0004 gate.

  2. **Training positive sampling.** Positives are sampled from the
     trailing window of train (the RANKER_POSITIVE_WINDOW_DAYS most
     recent days). This keeps the ranker's training-time feature
     distribution close to what holdout looks like — user histories,
     item popularities, and genre-affinity fractions reflect the
     "recent past" rather than a mix of the whole training era.

  3. **Candidate-leakage compromise.** For simplicity this first-pass
     script fits the candidate model on the full train (including the
     window we sample positives from). The strictly-correct alternative
     (fit candidate model on ``train_early``, sample positives from
     ``train_late``) is a small refactor and belongs in a follow-up.
     The compromise is called out in the MLflow run tags so a future
     analysis can filter these runs out.

  4. **Feature point-in-time correctness** is enforced by
     ``FeatureIndex`` regardless — features for a query at time ``t``
     use only rows with ``timestamp < t``. That's the leakage class the
     canary test in ``tests/unit/test_features.py`` guards.

Run with ``make train-ranker`` (or ``python -m src.training.ranker``).
Requires Postgres and MLflow reachable per ``Settings``.
"""

from __future__ import annotations

import logging
import time

import mlflow
import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, K, evaluate
from src.features import FeatureIndex
from src.models.candidates.itemitem import ItemItemModel
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig

logger = logging.getLogger(__name__)

PHASE_2_RANKER_EXPERIMENT = "phase-2-ranker"

# Sampling knobs for ranker training. Held as module-level constants so a
# sweep is a code edit, not a magic-number hunt.
RANKER_POSITIVE_WINDOW_DAYS = 30  # sample positives from the last N days of train
RANKER_POSITIVE_LIMIT = 20_000  # cap training set size for fast iteration
NEGATIVES_PER_POSITIVE = 20  # each LambdaRank group is 1 positive + N negatives
RANKER_SEED = 42

_SECONDS_PER_DAY = 24 * 3600


def _load_movies(engine: Engine) -> pd.DataFrame:
    """Load the movies table for genre features."""
    return pd.read_sql('SELECT "movieId", genres FROM movies', engine)


def _sample_training_positives(
    train: pd.DataFrame,
    n_days: int,
    limit: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Sample (userId, movieId, timestamp) positives from the trailing
    window. Returns at most ``limit`` rows.

    We could sample uniformly from all of train, but the trailing window
    keeps the feature distribution close to holdout's — user-days-active
    and item-popularity-30d at query time ``t`` in the last N days look
    much more like holdout-time features than a mid-train query would.
    """
    max_ts = int(train["timestamp"].max())
    cutoff = max_ts - n_days * _SECONDS_PER_DAY
    window = train[train["timestamp"] >= cutoff]
    if len(window) > limit:
        idx = rng.choice(len(window), size=limit, replace=False)
        window = window.iloc[idx].reset_index(drop=True)
    return window[["userId", "movieId", "timestamp"]].reset_index(drop=True)


def _build_ranker_training_set(
    positives: pd.DataFrame,
    candidate_model: ItemItemModel,
    feature_index: FeatureIndex,
    n_negatives: int,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[int], np.ndarray]:
    """Assemble the (features_df, group_sizes, labels) triple LambdaRank
    consumes.

    For each positive (user, movie, ts):
      1. Ask the candidate model for K_CANDIDATES movies for that user.
      2. Filter out the positive and any items already seen by the user
         in train (avoid leaking "user liked X → so X is a negative for
         X" as a signal).
      3. Sample ``n_negatives`` from the filtered candidates.
      4. Compute features for [positive, neg₁, ..., neg_N] as-of ``ts``.

    Positives whose candidate generator misses (positive not in
    candidates) are dropped — training on such rows would teach the
    ranker patterns the serving stack can't reproduce (the ranker at
    serving time only sees the candidate stage's output).
    """
    feature_rows: list[pd.DataFrame] = []
    labels: list[int] = []
    group_sizes: list[int] = []
    dropped_missing = 0

    for pos in positives.itertuples(index=False):
        user_id = int(pos.userId)
        pos_movie = int(pos.movieId)
        as_of = int(pos.timestamp)

        candidates = candidate_model.recommend(user_id, K_CANDIDATES)
        if pos_movie not in candidates:
            dropped_missing += 1
            continue

        negatives_pool = [c for c in candidates if c != pos_movie]
        if len(negatives_pool) < n_negatives:
            # Pathologically small pool; use what we have.
            sampled_negs = negatives_pool
        else:
            neg_idx = rng.choice(len(negatives_pool), size=n_negatives, replace=False)
            sampled_negs = [negatives_pool[int(i)] for i in neg_idx]

        group_items = [pos_movie, *sampled_negs]
        group_query = pd.DataFrame(
            {
                "userId": [user_id] * len(group_items),
                "movieId": group_items,
                "as_of_timestamp": [as_of] * len(group_items),
            }
        )
        group_features = feature_index.features_for(group_query)
        feature_rows.append(group_features)
        labels.extend([1, *([0] * len(sampled_negs))])
        group_sizes.append(len(group_items))

    logger.info(
        "Built ranker training set: %d groups, %d rows, %d positives dropped",
        len(group_sizes),
        sum(group_sizes),
        dropped_missing,
    )

    features_df = pd.concat(feature_rows, ignore_index=True)
    return features_df, group_sizes, np.array(labels, dtype=np.float64)


def _rank_for_holdout(
    ranker: LGBMRanker,
    candidate_model: ItemItemModel,
    feature_index: FeatureIndex,
    holdout_user_ids: list[int],
    as_of_timestamp: int,
    k_candidates: int,
    k_final: int,
) -> dict[int, list[int]]:
    """End-to-end recommend for each holdout user: candidates → features
    → ranker → top-K. Returned in the shape ``evaluate()`` wants.
    """
    candidates_by_user: dict[int, list[int]] = {}
    features_by_user: dict[int, pd.DataFrame] = {}
    for user_id in holdout_user_ids:
        candidates = candidate_model.recommend(user_id, k_candidates)
        if not candidates:
            candidates_by_user[user_id] = []
            continue
        candidates_by_user[user_id] = candidates
        query = pd.DataFrame(
            {
                "userId": [user_id] * len(candidates),
                "movieId": candidates,
                "as_of_timestamp": [as_of_timestamp] * len(candidates),
            }
        )
        features_by_user[user_id] = feature_index.features_for(query)
    return ranker.rank_candidates(candidates_by_user, features_by_user, k=k_final)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()
    rng = np.random.default_rng(RANKER_SEED)

    logger.info("Loading ratings + movies from Postgres ...")
    engine = create_engine(settings.database_url)
    ratings = load_ratings(engine)
    movies = _load_movies(engine)
    logger.info("Loaded %s ratings, %s movies", f"{len(ratings):,}", f"{len(movies):,}")

    logger.info("Splitting on time per ADR 0001 ...")
    split = temporal_split(ratings)
    logger.info(
        "Train=%s Holdout=%s Test=%s (cutoff=%d)",
        f"{len(split.train):,}",
        f"{len(split.holdout):,}",
        f"{len(split.test):,}",
        split.cutoff,
    )

    logger.info("Fitting candidate model (item-item cosine) ...")
    t0 = time.perf_counter()
    candidate_model = ItemItemModel().fit(split.train)
    candidate_fit_seconds = time.perf_counter() - t0
    logger.info("Candidate fit in %.1fs", candidate_fit_seconds)

    logger.info("Building feature index ...")
    t0 = time.perf_counter()
    feature_index = FeatureIndex.build(split.train, movies)
    feature_build_seconds = time.perf_counter() - t0
    logger.info("Feature index in %.1fs", feature_build_seconds)

    logger.info(
        "Sampling training positives from last %d days of train (limit %d) ...",
        RANKER_POSITIVE_WINDOW_DAYS,
        RANKER_POSITIVE_LIMIT,
    )
    positives = _sample_training_positives(
        split.train,
        n_days=RANKER_POSITIVE_WINDOW_DAYS,
        limit=RANKER_POSITIVE_LIMIT,
        rng=rng,
    )
    logger.info("Sampled %d positives", len(positives))

    logger.info(
        "Building ranker training set (%d negatives per positive) ...",
        NEGATIVES_PER_POSITIVE,
    )
    t0 = time.perf_counter()
    features_df, group_sizes, labels = _build_ranker_training_set(
        positives=positives,
        candidate_model=candidate_model,
        feature_index=feature_index,
        n_negatives=NEGATIVES_PER_POSITIVE,
        rng=rng,
    )
    build_set_seconds = time.perf_counter() - t0
    logger.info("Training set built in %.1fs", build_set_seconds)

    logger.info("Fitting LGBMRanker (LambdaRank) ...")
    config = LGBMRankerConfig(seed=RANKER_SEED)
    ranker = LGBMRanker(config=config)
    t0 = time.perf_counter()
    ranker.fit(features_df, group_sizes, labels)
    ranker_fit_seconds = time.perf_counter() - t0
    logger.info("Ranker fit in %.1fs", ranker_fit_seconds)

    logger.info("Ranking holdout users end-to-end (candidate → features → ranker → top-%d) ...", K)
    t0 = time.perf_counter()
    holdout_user_ids = split.holdout["userId"].unique().tolist()
    # Features at holdout evaluation time use as-of == train cutoff — the
    # last moment before holdout starts. Everything strictly earlier is
    # visible; nothing from holdout leaks.
    recommendations = _rank_for_holdout(
        ranker=ranker,
        candidate_model=candidate_model,
        feature_index=feature_index,
        holdout_user_ids=holdout_user_ids,
        as_of_timestamp=split.cutoff,
        k_candidates=K_CANDIDATES,
        k_final=K,
    )
    rank_seconds = time.perf_counter() - t0
    logger.info(
        "Ranked %d users in %.1fs",
        len(holdout_user_ids),
        rank_seconds,
    )

    logger.info("Evaluating end-to-end at K=%d ...", K)
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = split.train.groupby("userId").size().to_dict()
    result = evaluate(recommendations, holdout, train_counts, k=K)
    logger.info(
        "Warm (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        result.n_warm_users,
        K,
        result.warm.recall,
        K,
        result.warm.ndcg,
    )
    logger.info(
        "Cold (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        result.n_cold_users,
        K,
        result.cold.recall,
        K,
        result.cold.ndcg,
    )
    logger.info(
        "Overall:     recall@%d=%.4f ndcg@%d=%.4f",
        K,
        result.overall.recall,
        K,
        result.overall.ndcg,
    )

    importances = ranker.feature_importances(importance_type="gain")
    logger.info("Feature importances (gain): %s", importances)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(PHASE_2_RANKER_EXPERIMENT)
    with mlflow.start_run(run_name="lgbm-lambdarank-itemitem-candidates"):
        mlflow.set_tags(
            {
                "model_family": "ranker",
                "model_type": "lgbm_lambdarank",
                "candidate_model": "itemitem_cosine",
                "phase": "2",
                "stage": "ranker",
                # Called out in ADR 0005 Consequences — candidate model
                # was fit on all of train including the positive window.
                "candidate_leakage_compromise": "true",
            }
        )
        mlflow.log_params(
            {
                "k_final": K,
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "n_holdout_users": len(holdout_user_ids),
                "ranker_positive_window_days": RANKER_POSITIVE_WINDOW_DAYS,
                "ranker_positive_limit": RANKER_POSITIVE_LIMIT,
                "n_ranker_positives_used": len(group_sizes),
                "negatives_per_positive": NEGATIVES_PER_POSITIVE,
                "n_ranker_training_rows": sum(group_sizes),
                "num_leaves": config.num_leaves,
                "learning_rate": config.learning_rate,
                "min_data_in_leaf": config.min_data_in_leaf,
                "num_boost_round": config.num_boost_round,
                "lambda_l2": config.lambda_l2,
                "seed": config.seed,
                "candidate_fit_seconds": round(candidate_fit_seconds, 1),
                "feature_build_seconds": round(feature_build_seconds, 1),
                "ranker_training_set_seconds": round(build_set_seconds, 1),
                "ranker_fit_seconds": round(ranker_fit_seconds, 1),
                "rank_seconds": round(rank_seconds, 1),
            }
        )
        mlflow.log_metrics(
            {
                "warm_recall_at_k": result.warm.recall,
                "warm_ndcg_at_k": result.warm.ndcg,
                "cold_recall_at_k": result.cold.recall,
                "cold_ndcg_at_k": result.cold.ndcg,
                "overall_recall_at_k": result.overall.recall,
                "overall_ndcg_at_k": result.overall.ndcg,
                "n_warm_users": result.n_warm_users,
                "n_cold_users": result.n_cold_users,
            }
        )
        # Feature importances land as their own metrics so MLflow's
        # comparison view can plot them across runs; the "importance"
        # prefix keeps them grouped.
        mlflow.log_metrics({f"importance_{name}": value for name, value in importances.items()})
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
