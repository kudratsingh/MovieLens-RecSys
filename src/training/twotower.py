"""
Train the two-tower candidate generator end-to-end and log to MLflow.

Same skeleton as ``src/training/itemitem.py`` — the candidate-stage models
share the load → split → fit → recommend → evaluate → log shape so they're
directly comparable in MLflow. Differences from itemitem.py:

  1. The model is a learned two-tower per ADR 0006 (PyTorch modules trained
     with sampled softmax + log-uniform correction, FAISS retrieval).
  2. Per-epoch loss *and* per-epoch warm recall are streamed to MLflow via
     the ``on_epoch`` callback the model class accepts. The loss curve alone
     says the model stopped moving; only recall says whether where it
     stopped is any good, and the 2026-08-30 v1 run needed both to be read
     together before anyone could tell which.
  3. Per-policy attribution splits holdout users into two-tower-served
     (warm) vs popularity-fallback-served (cold), mirroring the PR #17
     pattern extended to item-item in PR #19.

Runs land in the same ``phase-2-candidates`` experiment as item-item so
the two candidate generators sit on the same recall@K_CANDIDATES axis in
one MLflow view — the direct comparison ADR 0004's promotion gate requires.

**Every hyperparameter is env-driven** through ``TwoTowerConfig.from_env``
(``TWOTOWER_LEARNING_RATE``, ``TWOTOWER_EPOCHS``, ``TWOTOWER_NUM_SAMPLED``,
``TWOTOWER_LOGIT_TEMPERATURE``, …). Unset means ADR 0006's default, so
``make train-twotower`` with a clean environment uses ADR 0015's v2 default;
set ``TWOTOWER_LOGIT_TEMPERATURE=1.0`` to reproduce v1. Two further variables
belong to the run rather than the model:

  ``TWOTOWER_USER_SAMPLE_FRACTION``  a seeded fraction of users to keep, for
      cheap pilot runs. 1.0 (the default) is the full dataset.
  ``TWOTOWER_RUN_LABEL``  appended to the MLflow run name so a sweep cell is
      identifiable without reading its params.

Run with ``make train-twotower`` (or ``python -m src.training.twotower``)
from project root. Requires Postgres and MLflow reachable per ``Settings``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping

import mlflow
import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from src.models.candidates import routing
from src.models.candidates.twotower import TwoTowerConfig, TwoTowerModel
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

# Same experiment item-item logs to — the whole point of phase-2-candidates
# is to hold every candidate generator on one recall axis. Hardcoded so the
# operator can't spray runs into the wrong experiment via env var.
PHASE_2_EXPERIMENT = "phase-2-candidates"

SAMPLE_FRACTION_ENV_VAR = "TWOTOWER_USER_SAMPLE_FRACTION"
RUN_LABEL_ENV_VAR = "TWOTOWER_RUN_LABEL"


def subsample_users(
    ratings: pd.DataFrame,
    fraction: float,
    seed: int,
) -> pd.DataFrame:
    """Keep every interaction of a seeded random subset of users.

    Users rather than rows: the user tower is a mean-pool over a history, so
    thinning rows would shorten every history and change the thing being
    measured. Thinning users leaves each surviving history exactly as long as
    it was, which is what makes a pilot's loss curve mean the same thing as
    the full run's.

    Deterministic given ``(fraction, seed)`` — the user ids are sorted before
    the draw, so the subset does not depend on row order in Postgres.
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"{SAMPLE_FRACTION_ENV_VAR} must be in (0, 1], got {fraction}")
    if fraction == 1.0:
        return ratings

    user_ids = np.sort(ratings["userId"].unique())
    n_keep = max(1, int(round(len(user_ids) * fraction)))
    rng = np.random.default_rng(seed)
    keep = rng.choice(user_ids, size=n_keep, replace=False)
    return ratings[ratings["userId"].isin(set(keep.tolist()))].reset_index(drop=True)


def resolve_sample_fraction(env: Mapping[str, str] | None = None) -> float:
    raw = (env if env is not None else os.environ).get(SAMPLE_FRACTION_ENV_VAR, "").strip()
    return 1.0 if not raw else float(raw)


def run_name_for(policy: str, label: str) -> str:
    """MLflow run name: the historical name, plus policy and sweep labels.

    The default policy with no label keeps the exact name
    ``docs/results.md`` already cites, so the runs on that page stay
    findable by the name it gives them.
    """
    name = routing.run_name_for("twotower-sampled-softmax", policy)
    return f"{name}-{label}" if label else name


def run_once(
    ratings: pd.DataFrame,
    movies: pd.DataFrame,
    config: TwoTowerConfig,
    *,
    sample_fraction: float = 1.0,
    run_label: str = "",
    routing_policy: str | None = None,
) -> None:
    """One MLflow run: split, fit, recommend, evaluate, log.

    Takes the ratings frame rather than reading it so a sweep can pay the
    25 M-row ``read_sql`` once and spend the rest of its budget training.
    """
    policy = routing.resolve_policy() if routing_policy is None else routing_policy
    logger.info("Cold-start routing policy: %s", policy)

    if sample_fraction != 1.0:
        before_users = ratings["userId"].nunique()
        ratings = subsample_users(ratings, sample_fraction, config.seed)
        logger.info(
            "Pilot subsample at fraction %.4f: %s of %s users kept, %s rows",
            sample_fraction,
            f"{ratings['userId'].nunique():,}",
            f"{before_users:,}",
            f"{len(ratings):,}",
        )

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
    #
    # A subsampled run skips it: the parquet is anchored to the full split's
    # cutoff and `prepare` rightly refuses to attach itself to a different
    # one. A pilot is a comparison against its own siblings, so it loses
    # nothing it needs.
    if sample_fraction == 1.0:
        train_frame, cohort = synth_cold.prepare(split, logger=logger)
    else:
        train_frame, cohort = split.train, None
        logger.info("Subsampled run: ADR 0011 cohort not attached (it is cutoff-anchored)")

    model = TwoTowerModel(
        config=config,
        cold_start_threshold=routing.cold_start_threshold_for(policy, COLD_START_THRESHOLD),
    )

    # Eval inputs are built before the fit so the per-epoch callback can score
    # recall without rebuilding them every epoch.
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    # Counts come from the real train slice, not the cohort-attached frame:
    # the warm/cold partition is over holdout users, and no synthetic user
    # is ever looked up in it.
    train_counts = split.train.groupby("userId").size().to_dict()
    holdout_user_ids = split.holdout["userId"].unique().tolist()

    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name=run_name_for(policy, run_label)):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "two_tower",
                "phase": "2",
                "stage": "candidate",
                "cold_start_routing_policy": policy,
                "sweep_label": run_label,
            }
        )
        mlflow.log_params(
            {
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cold_start_routing_policy": policy,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "user_sample_fraction": sample_fraction,
                "run_label": run_label,
                **config.as_params(),
            }
        )

        logger.info("Fitting two-tower model ...")
        t0 = time.perf_counter()
        # Mid-training scoring is not fitting; it is subtracted so fit_seconds
        # stays comparable with runs made before this callback existed.
        epoch_eval_seconds = 0.0
        epochs_run = 0

        def _log_epoch(epoch: int, mean_loss: float) -> None:
            nonlocal epoch_eval_seconds, epochs_run
            epochs_run = epoch
            mlflow.log_metric("train_loss", mean_loss, step=epoch)

            t_eval = time.perf_counter()
            model.build_index()
            epoch_recs = model.recommend_for_users(holdout_user_ids, k=K_CANDIDATES)
            epoch_result = evaluate(epoch_recs, holdout, train_counts, k=K_CANDIDATES)
            spread = model.embedding_spread()
            epoch_eval_seconds += time.perf_counter() - t_eval
            mlflow.log_metrics(
                {
                    "epoch_warm_recall_at_k_candidates": epoch_result.warm.recall,
                    "epoch_overall_recall_at_k_candidates": epoch_result.overall.recall,
                    **{f"epoch_{name}": value for name, value in spread.items()},
                },
                step=epoch,
            )
            logger.info(
                "Epoch %d: loss=%.4f warm_recall@%d=%.4f item_cos_mean=%.4f item_cos_std=%.4f",
                epoch,
                mean_loss,
                K_CANDIDATES,
                epoch_result.warm.recall,
                spread.get("item_cosine_mean", float("nan")),
                spread.get("item_cosine_std", float("nan")),
            )

        model.fit(train_frame, movies=movies, on_epoch=_log_epoch)
        mlflow.log_params(model.item_feature_params())
        fit_seconds = time.perf_counter() - t0 - epoch_eval_seconds
        logger.info(
            "Fit in %.1fs (%d users x %d items, %d of %d epochs; %.1fs of per-epoch eval excluded)",
            fit_seconds,
            len(model._user_history),
            len(model._index_to_item),
            epochs_run,
            config.epochs,
            epoch_eval_seconds,
        )

        logger.info("Recommending top-%d for each holdout user ...", K_CANDIDATES)
        t1 = time.perf_counter()
        cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
        recommendations = model.recommend_for_users(
            holdout_user_ids + cohort_user_ids, k=K_CANDIDATES
        )
        recommend_seconds = time.perf_counter() - t1
        logger.info(
            "Recommended for %d users in %.1fs",
            len(holdout_user_ids) + len(cohort_user_ids),
            recommend_seconds,
        )

        logger.info("Evaluating at K_CANDIDATES=%d ...", K_CANDIDATES)
        result = evaluate(
            recommendations,
            holdout,
            train_counts,
            k=K_CANDIDATES,
            synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
            synthetic_cold_served_by=(model.was_served_by_twotower if cohort is not None else None),
        )
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
                "epochs_run": epochs_run,
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
                **model.hard_negative_stats(),
            }
        )
        if cohort is not None:
            synth_cold.log_summary(result, logger=logger, k=K_CANDIDATES)
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K_CANDIDATES))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )


def load_inputs(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    logger.info("Loading ratings and movie metadata from Postgres ...")
    engine = create_engine(settings.database_url)
    try:
        ratings = load_ratings(engine)
        movies = pd.read_sql('SELECT "movieId", title, genres FROM movies', engine)
    finally:
        engine.dispose()
    logger.info("Loaded %s ratings and %s movies", f"{len(ratings):,}", f"{len(movies):,}")
    return ratings, movies


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()
    ratings, movies = load_inputs(settings)

    config = TwoTowerConfig.from_env()
    logger.info("Config: %s", config)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    run_once(
        ratings,
        movies,
        config,
        sample_fraction=resolve_sample_fraction(),
        run_label=os.environ.get(RUN_LABEL_ENV_VAR, "").strip(),
    )
    logger.info("MLflow run logged. Done.")


__all__ = [
    "PHASE_2_EXPERIMENT",
    "load_inputs",
    "main",
    "resolve_sample_fraction",
    "run_name_for",
    "run_once",
    "subsample_users",
]


if __name__ == "__main__":
    main()
