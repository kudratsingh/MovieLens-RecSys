"""
Train the item-item CF candidate generator end-to-end and log to MLflow.

Same skeleton as src/training/cf.py — the candidate-stage models all share
the load → split → fit → recommend → evaluate → log shape so they're
directly comparable in MLflow. The differences from cf.py are intentional
and load-bearing:

  1. Evaluation runs at K_CANDIDATES (~500), not K (=10). Per ADR 0003,
     item-item is a candidate-stage model and its success criterion is
     recall over the retrieved candidate set, not NDCG over the
     recommender's final top-10.
  2. Runs are logged into a new MLflow experiment `phase-2-candidates`,
     not `phase-1-baselines`. The two experiments answer different
     questions — phase-1-baselines was "can we beat random?", phase-2-
     candidates is "which candidate generator wins recall@500?".

Run with ``make train-itemitem`` (or ``python -m src.training.itemitem``)
from project root. Requires Postgres and MLflow to be reachable per
``Settings``.
"""

from __future__ import annotations

import logging
import time

import mlflow
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from src.models.candidates.itemitem import ItemItemModel

logger = logging.getLogger(__name__)

# Item-item runs join the new candidate-stage experiment rather than the
# Phase 1 baselines experiment. Hardcoded here rather than read from
# Settings because the experiment name is part of the experiment's
# *identity* — the operator shouldn't be able to spray runs into the wrong
# one via env var.
PHASE_2_EXPERIMENT = "phase-2-candidates"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()

    logger.info("Loading ratings from Postgres ...")
    engine = create_engine(settings.database_url)
    ratings = load_ratings(engine)
    logger.info("Loaded %s ratings", f"{len(ratings):,}")

    logger.info("Splitting on time per ADR 0001 ...")
    split = temporal_split(ratings)
    logger.info(
        "Train=%s Holdout=%s Test=%s (cutoff=%d)",
        f"{len(split.train):,}",
        f"{len(split.holdout):,}",
        f"{len(split.test):,}",
        split.cutoff,
    )

    logger.info("Fitting item-item (cosine KNN) model ...")
    model = ItemItemModel()
    t0 = time.perf_counter()
    model.fit(split.train)
    fit_seconds = time.perf_counter() - t0
    logger.info(
        "Fit in %.1fs (k_neighbors=%d, %d users x %d items)",
        fit_seconds,
        model.k_neighbors,
        len(model._user_to_index),
        len(model._index_to_item),
    )

    logger.info("Recommending top-%d for each holdout user ...", K_CANDIDATES)
    t1 = time.perf_counter()
    holdout_user_ids = split.holdout["userId"].unique().tolist()
    recommendations = model.recommend_for_users(holdout_user_ids, k=K_CANDIDATES)
    recommend_seconds = time.perf_counter() - t1
    logger.info(
        "Recommended for %d users in %.1fs",
        len(holdout_user_ids),
        recommend_seconds,
    )

    logger.info("Building eval inputs ...")
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = split.train.groupby("userId").size().to_dict()

    logger.info("Evaluating at K_CANDIDATES=%d ...", K_CANDIDATES)
    result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
    logger.info(
        "Warm (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        result.n_warm_users,
        K_CANDIDATES,
        result.warm.recall,
        K_CANDIDATES,
        result.warm.ndcg,
    )
    logger.info(
        "Cold (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        result.n_cold_users,
        K_CANDIDATES,
        result.cold.recall,
        K_CANDIDATES,
        result.cold.ndcg,
    )
    logger.info(
        "Overall:     recall@%d=%.4f ndcg@%d=%.4f",
        K_CANDIDATES,
        result.overall.recall,
        K_CANDIDATES,
        result.overall.ndcg,
    )

    # Per-policy attribution — same partition pattern CFModel established
    # in PR #17. ItemItemModel embeds the same popularity fallback for cold
    # users, so the overall metrics mix two policies; splitting them is the
    # only way to tell whether item-item is doing work beyond what
    # popularity alone delivers.
    holdout_itemitem = {
        uid: items for uid, items in holdout.items() if model.was_served_by_itemitem(uid)
    }
    holdout_fallback = {
        uid: items for uid, items in holdout.items() if not model.was_served_by_itemitem(uid)
    }
    result_itemitem = evaluate(recommendations, holdout_itemitem, train_counts, k=K_CANDIDATES)
    result_fallback = evaluate(recommendations, holdout_fallback, train_counts, k=K_CANDIDATES)
    logger.info(
        "Item-item-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_itemitem),
        K_CANDIDATES,
        result_itemitem.overall.recall,
        K_CANDIDATES,
        result_itemitem.overall.ndcg,
    )
    logger.info(
        "Fallback-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_fallback),
        K_CANDIDATES,
        result_fallback.overall.recall,
        K_CANDIDATES,
        result_fallback.overall.ndcg,
    )

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name="itemitem-cosine"):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "itemitem_cosine",
                "phase": "2",
                "stage": "candidate",
            }
        )
        mlflow.log_params(
            {
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "n_holdout_users": len(holdout_user_ids),
                "k_neighbors": model.k_neighbors,
                "n_users_in_train": len(model._user_to_index),
                "n_items_in_train": len(model._index_to_item),
                "fit_seconds": round(fit_seconds, 1),
                "recommend_seconds": round(recommend_seconds, 1),
            }
        )
        mlflow.log_metrics(
            {
                "warm_recall_at_k_candidates": result.warm.recall,
                "warm_ndcg_at_k_candidates": result.warm.ndcg,
                "cold_recall_at_k_candidates": result.cold.recall,
                "cold_ndcg_at_k_candidates": result.cold.ndcg,
                "overall_recall_at_k_candidates": result.overall.recall,
                "overall_ndcg_at_k_candidates": result.overall.ndcg,
                "n_warm_users": result.n_warm_users,
                "n_cold_users": result.n_cold_users,
                # Per-policy attribution at K_CANDIDATES.
                "itemitem_served_recall_at_k_candidates": result_itemitem.overall.recall,
                "itemitem_served_ndcg_at_k_candidates": result_itemitem.overall.ndcg,
                "itemitem_served_warm_recall_at_k_candidates": result_itemitem.warm.recall,
                "itemitem_served_warm_ndcg_at_k_candidates": result_itemitem.warm.ndcg,
                "itemitem_served_cold_recall_at_k_candidates": result_itemitem.cold.recall,
                "itemitem_served_cold_ndcg_at_k_candidates": result_itemitem.cold.ndcg,
                "n_itemitem_served_users": len(holdout_itemitem),
                "fallback_served_recall_at_k_candidates": result_fallback.overall.recall,
                "fallback_served_ndcg_at_k_candidates": result_fallback.overall.ndcg,
                "n_fallback_served_users": len(holdout_fallback),
            }
        )
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
