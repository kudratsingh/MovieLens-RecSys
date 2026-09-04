"""
Train the popularity baseline end-to-end and log the run to MLflow.

This is the first runnable training pipeline in the repo. The shape is the
same one Phase 1's CF baseline and Phase 2's two-tower / LightGBM will
follow:

    load → temporal_split → fit → recommend → evaluate → log

Importantly, evaluation goes through ``src.evaluation.protocol.evaluate``,
not ad-hoc pandas in this file (non-negotiable #5). That's how the
warm/cold-sliced metrics stay comparable across every model we ever train.

Run with ``make train-popularity`` (or ``python -m src.training.popularity``)
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
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    PER_USER_RECALL_ARTIFACT,
    K,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates.popularity import PopularityModel
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

# The model identity every consumer keys off — the MLflow tag and the per-user
# recall artifact. One constant so they cannot drift apart.
MODEL_TYPE = "popularity"


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

    # ADR 0011's cold-start cohort joins the training frame here, if this
    # machine has it. At most 7 000 rows against ~20 M, and none of its users
    # appear in holdout, so the warm/cold numbers below are unmoved — the
    # cohort exists to be routed and scored, not to shift an existing metric.
    train_frame, cohort = synth_cold.prepare(split, logger=logger)

    logger.info("Fitting popularity model ...")
    t0 = time.perf_counter()
    model = PopularityModel().fit(train_frame)
    fit_seconds = time.perf_counter() - t0
    logger.info("Fit in %.1fs (%d items in ranking)", fit_seconds, len(model.ranking))

    logger.info("Recommending top-%d for each holdout user ...", K)
    t1 = time.perf_counter()
    holdout_user_ids = split.holdout["userId"].unique().tolist()
    cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
    recommendations = model.recommend_for_users(holdout_user_ids + cohort_user_ids, k=K)
    recommend_seconds = time.perf_counter() - t1
    logger.info(
        "Recommended for %d users in %.1fs",
        len(holdout_user_ids) + len(cohort_user_ids),
        recommend_seconds,
    )

    logger.info("Building eval inputs ...")
    # The harness expects: recommendations[user]→list, holdout[user]→set,
    # train_interaction_counts[user]→int. Build them once here and feed in.
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = split.train.groupby("userId").size().to_dict()

    logger.info("Evaluating ...")
    # No routing predicate here, deliberately. PopularityModel *is* the
    # fallback — it has no learned path to route to, so there is no routing
    # claim to assert and ``synth_cold_fallback_served_h*`` would be a
    # tautology. What its buckets are for is the bar: a fallback-served user
    # in any other model's run is being served exactly this policy, so these
    # per-bucket numbers are what that model's h0/h1/h3 should be read against.
    result = evaluate(
        recommendations,
        holdout,
        train_counts,
        synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
    )
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

    if cohort is not None:
        synth_cold.log_summary(result, logger=logger, k=K)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    with mlflow.start_run(run_name="popularity-baseline") as run:
        mlflow.set_tags(
            {
                "model_family": "baseline",
                "model_type": MODEL_TYPE,
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
                "n_ranking_items": len(model.ranking),
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
        # The recall behind those means, one row per holdout user. This run is
        # the bar every other model's fallback-served users are read against,
        # and reading it per user is what turns "the fallback did as well" into
        # a claim about the same people.
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run.info.run_id,
                model_type=MODEL_TYPE,
                # Nothing here is stochastic: the ranking is a count.
                seed=None,
                configuration_id="popularity-train-window-count",
            ),
            PER_USER_RECALL_ARTIFACT,
        )
        if cohort is not None:
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K))
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
