"""
Train the two-tower candidate generator end-to-end and log to MLflow.

Same skeleton as ``src/training/itemitem.py`` — the candidate-stage models
share the load → split → fit → recommend → evaluate → log shape so they're
directly comparable in MLflow. Differences from itemitem.py:

  1. The model is a learned two-tower per ADR 0006 (PyTorch modules trained
     with sampled softmax + log-uniform correction, FAISS retrieval).
  2. Per-epoch loss is streamed to MLflow via the ``on_epoch`` callback the
     model class accepts.
  3. Per-policy attribution splits holdout users into two-tower-served
     (warm) vs popularity-fallback-served (cold), mirroring the PR #17
     pattern extended to item-item in PR #19.

Runs land in the same ``phase-2-candidates`` experiment as item-item so
the two candidate generators sit on the same recall@K_CANDIDATES axis in
one MLflow view — the direct comparison ADR 0004's promotion gate requires.

Run with ``make train-twotower`` (or ``python -m src.training.twotower``)
from project root. Requires Postgres and MLflow reachable per ``Settings``.
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
from src.models.candidates.twotower import TwoTowerConfig, TwoTowerModel

logger = logging.getLogger(__name__)

# Same experiment item-item logs to — the whole point of phase-2-candidates
# is to hold every candidate generator on one recall axis. Hardcoded so the
# operator can't spray runs into the wrong experiment via env var.
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

    config = TwoTowerConfig()
    model = TwoTowerModel(config=config)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name="twotower-sampled-softmax"):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "two_tower",
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
                "embedding_dim": config.embedding_dim,
                "history_window": config.history_window,
                "batch_size": config.batch_size,
                "num_sampled": config.num_sampled,
                "epochs": config.epochs,
                "learning_rate": config.learning_rate,
                "faiss_nlist": config.faiss_nlist,
                "faiss_nprobe": config.faiss_nprobe,
                "seed": config.seed,
            }
        )

        logger.info("Fitting two-tower model ...")
        t0 = time.perf_counter()

        def _log_epoch(epoch: int, mean_loss: float) -> None:
            # Per-epoch loss so a run's convergence curve is inspectable in
            # MLflow — useful for spotting a diverging run early or noticing
            # that 3 epochs was actually already at a plateau.
            mlflow.log_metric("train_loss", mean_loss, step=epoch)

        model.fit(split.train, on_epoch=_log_epoch)
        fit_seconds = time.perf_counter() - t0
        logger.info(
            "Fit in %.1fs (%d users x %d items, %d epochs)",
            fit_seconds,
            len(model._user_history),
            len(model._index_to_item),
            config.epochs,
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

        # Per-policy attribution: the overall metric mixes two policies
        # (two-tower-served warm users + popularity-served cold users).
        # Splitting them is the only way to tell whether the tower is
        # doing work beyond the fallback — the primary comparison against
        # item-item (per ADR 0006's How-we'd-know-we're-wrong section) is
        # the two-tower-served warm slice, not the mixed overall number.
        holdout_twotower = {
            uid: items for uid, items in holdout.items() if model.was_served_by_twotower(uid)
        }
        holdout_fallback = {
            uid: items for uid, items in holdout.items() if not model.was_served_by_twotower(uid)
        }
        result_twotower = evaluate(recommendations, holdout_twotower, train_counts, k=K_CANDIDATES)
        result_fallback = evaluate(recommendations, holdout_fallback, train_counts, k=K_CANDIDATES)
        logger.info(
            "Two-tower-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
            len(holdout_twotower),
            K_CANDIDATES,
            result_twotower.overall.recall,
            K_CANDIDATES,
            result_twotower.overall.ndcg,
        )
        logger.info(
            "Fallback-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
            len(holdout_fallback),
            K_CANDIDATES,
            result_fallback.overall.recall,
            K_CANDIDATES,
            result_fallback.overall.ndcg,
        )

        mlflow.log_params(
            {
                "n_holdout_users": len(holdout_user_ids),
                "n_users_in_train": len(model._user_history),
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
                "twotower_served_recall_at_k_candidates": result_twotower.overall.recall,
                "twotower_served_ndcg_at_k_candidates": result_twotower.overall.ndcg,
                "twotower_served_warm_recall_at_k_candidates": result_twotower.warm.recall,
                "twotower_served_warm_ndcg_at_k_candidates": result_twotower.warm.ndcg,
                "twotower_served_cold_recall_at_k_candidates": result_twotower.cold.recall,
                "twotower_served_cold_ndcg_at_k_candidates": result_twotower.cold.ndcg,
                "n_twotower_served_users": len(holdout_twotower),
                "fallback_served_recall_at_k_candidates": result_fallback.overall.recall,
                "fallback_served_ndcg_at_k_candidates": result_fallback.overall.ndcg,
                "n_fallback_served_users": len(holdout_fallback),
            }
        )
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
