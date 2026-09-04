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
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    PER_USER_RECALL_ARTIFACT,
    K,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates import routing
from src.models.candidates.cf import CFModel
from src.training import seeds
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

# The model identity every consumer keys off — the MLflow tag and the per-user
# recall artifact. One constant so they cannot drift apart.
MODEL_TYPE = "cf_als"


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

    # Default is the index-membership routing this model has always used;
    # SYNTH_COLD_ROUTING=threshold is the opt-in experiment behind
    # docs/cold-start-routing-decision.md.
    routing_policy = routing.resolve_policy()
    logger.info("Cold-start routing policy: %s", routing_policy)

    # ALS initialises its factor matrices at random, so this is the one knob
    # that moves this run's metrics without changing the model, the data or
    # the protocol. TRAIN_SEED unset is the 42 every published run used.
    seed = seeds.resolve_seed()
    logger.info("Seed: %d", seed)

    logger.info("Fitting CF (ALS) model ...")
    model = CFModel(
        random_state=seed,
        cold_start_threshold=routing.cold_start_threshold_for(routing_policy, COLD_START_THRESHOLD),
    )
    t0 = time.perf_counter()
    model.fit(train_frame)
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
    cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
    recommendations = model.recommend_for_users(holdout_user_ids + cohort_user_ids, k=K)
    recommend_seconds = time.perf_counter() - t1
    logger.info(
        "Recommended for %d users in %.1fs",
        len(holdout_user_ids) + len(cohort_user_ids),
        recommend_seconds,
    )

    logger.info("Building eval inputs ...")
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    # Counts come from the real train slice, not the cohort-attached frame:
    # the warm/cold partition is over holdout users, and no synthetic user is
    # ever looked up in it.
    train_counts = split.train.groupby("userId").size().to_dict()

    logger.info("Evaluating ...")
    result = evaluate(
        recommendations,
        holdout,
        train_counts,
        synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
        synthetic_cold_served_by=model.was_served_by_als if cohort is not None else None,
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

    # Per-policy attribution. CFModel embeds a popularity fallback for users
    # not seen in train, so the overall metrics above mix two policies. Split
    # the holdout by which policy actually served each user and re-evaluate
    # each slice through the same harness — that's the only way to tell
    # whether ALS is earning its keep beyond what popularity alone delivers.
    holdout_als = {uid: items for uid, items in holdout.items() if model.was_served_by_als(uid)}
    holdout_fallback = {
        uid: items for uid, items in holdout.items() if not model.was_served_by_als(uid)
    }
    result_als = evaluate(recommendations, holdout_als, train_counts)
    result_fallback = evaluate(recommendations, holdout_fallback, train_counts)
    logger.info(
        "ALS-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_als),
        K,
        result_als.overall.recall,
        K,
        result_als.overall.ndcg,
    )
    logger.info(
        "Fallback-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_fallback),
        K,
        result_fallback.overall.recall,
        K,
        result_fallback.overall.ndcg,
    )

    if cohort is not None:
        synth_cold.log_summary(result, logger=logger, k=K)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    run_name = seeds.run_name_for(routing.run_name_for("cf-als-baseline", routing_policy), seed)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags(
            {
                "model_family": "baseline",
                "model_type": MODEL_TYPE,
                "phase": "1",
                "cold_start_routing_policy": routing_policy,
                "train_seed": str(seed),
            }
        )
        mlflow.log_params(
            {
                "k": K,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cold_start_routing_policy": routing_policy,
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
                # Per-policy attribution: same metrics computed over the
                # subset of holdout users actually served by ALS vs. by the
                # popularity fallback. Sum of als_served + fallback_served
                # user counts equals n_warm_users + n_cold_users overall.
                "als_served_recall_at_k": result_als.overall.recall,
                "als_served_ndcg_at_k": result_als.overall.ndcg,
                "als_served_warm_recall_at_k": result_als.warm.recall,
                "als_served_warm_ndcg_at_k": result_als.warm.ndcg,
                "als_served_cold_recall_at_k": result_als.cold.recall,
                "als_served_cold_ndcg_at_k": result_als.cold.ndcg,
                "n_als_served_users": len(holdout_als),
                "fallback_served_recall_at_k": result_fallback.overall.recall,
                "fallback_served_ndcg_at_k": result_fallback.overall.ndcg,
                "n_fallback_served_users": len(holdout_fallback),
            }
        )
        # The recall behind those means, one row per holdout user. ALS is the
        # one baseline whose seed genuinely moves the metrics, so its runs are
        # the natural material for a seed-dispersion study — and a study needs
        # the users, not the averages.
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run.info.run_id,
                model_type=MODEL_TYPE,
                seed=seed,
                # The seed is deliberately not part of the configuration id:
                # runs at different seeds are the *same* configuration, which
                # is the only thing a dispersion study can compare.
                configuration_id=(
                    f"cf-als-f{model.factors}-i{model.iterations}"
                    f"-reg{model.regularization:g}-{routing_policy}"
                ),
            ),
            PER_USER_RECALL_ARTIFACT,
        )
        if cohort is not None:
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
