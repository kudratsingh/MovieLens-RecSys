"""
Train the CF (implicit ALS) baseline end-to-end and log to MLflow.

Same skeleton as src/training/popularity.py; only the model class changes.
Logs into the same ``phase-1-baselines`` experiment so the two baselines
sit side by side in MLflow's UI for direct comparison.
"""

from __future__ import annotations

import logging
import time

import mlflow
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K, evaluate
from src.models.candidates.cf import CFModel

logger = logging.getLogger(__name__)


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

    logger.info("Fitting CF (ALS) model ...")
    model = CFModel()
    t0 = time.perf_counter()
    model.fit(split.train)
    fit_seconds = time.perf_counter() - t0
    logger.info(
        "Fit in %.1fs (factors=%d, iters=%d, %d users x %d items)",
        fit_seconds,
        model.factors,
        model.iterations,
        len(model._user_to_index),
        len(model._index_to_item),
    )

    logger.info("Recommending top-%d for each holdout user ...", K)
    t1 = time.perf_counter()
    holdout_user_ids = split.holdout["userId"].unique().tolist()
    recommendations = model.recommend_for_users(holdout_user_ids, k=K)
    recommend_seconds = time.perf_counter() - t1
    logger.info(
        "Recommended for %d users in %.1fs",
        len(holdout_user_ids),
        recommend_seconds,
    )

    logger.info("Building eval inputs ...")
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = split.train.groupby("userId").size().to_dict()

    logger.info("Evaluating ...")
    result = evaluate(recommendations, holdout, train_counts)
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

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    with mlflow.start_run(run_name="cf-als-baseline"):
        mlflow.set_tags(
            {
                "model_family": "baseline",
                "model_type": "cf_als",
                "phase": "1",
            }
        )
        mlflow.log_params(
            {
                "k": K,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "n_holdout_users": len(holdout_user_ids),
                # ALS hyperparameters
                "factors": model.factors,
                "regularization": model.regularization,
                "iterations": model.iterations,
                "random_state": model.random_state,
                "n_users_in_train": len(model._user_to_index),
                "n_items_in_train": len(model._index_to_item),
                "fit_seconds": round(fit_seconds, 1),
                "recommend_seconds": round(recommend_seconds, 1),
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
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
