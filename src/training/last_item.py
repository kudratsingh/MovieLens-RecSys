"""
Train the last-item transition baseline end-to-end and log to MLflow.

Same skeleton as ``src/training/itemitem.py`` — the candidate-stage models all
share the load → split → fit → recommend → evaluate → log shape so they're
directly comparable in MLflow, and this one exists precisely to be compared.
Decision D-003 holds that a sequential retriever's pilot is a viability gate
rather than promotion evidence, and that the model must beat both same-sample
popularity *and* a last-item nearest-neighbour baseline before anyone believes
sequence modelling added value. Until this run exists beside it, a SASRec
number cannot be interpreted.

Being the control is the whole specification, so everything that could make the
comparison unequal is inherited rather than re-decided: the same
``temporal_split``, the same ``K_CANDIDATES``, the same ``evaluate`` entrypoint,
the same ``phase-2-candidates`` experiment, the same routing threshold, the same
seen-item exclusions, and the same user subsample function the two-tower and
SASRec trainers use. Two variables belong to the run rather than to the model:

  ``LASTITEM_USER_SAMPLE_FRACTION``  a seeded fraction of users to keep, so the
      baseline can be run at whatever sample the SASRec run it controls for
      used. 1.0 (the default) is the full dataset.
  ``LASTITEM_SEED``  the seed that subsample is drawn at. Nothing else in this
      trainer is stochastic — the model has no random component at all — so at
      the default sample fraction this variable changes nothing.

``LASTITEM_RUN_LABEL`` is appended to the MLflow run name, mirroring the two
learned trainers, so a run made for a particular comparison is identifiable
without reading its params.

Beyond the metrics item-item logs, this trainer reports how much of each
candidate list the transitions actually filled. A last item with three recorded
successors produces three transition candidates and 497 popularity ones, and a
recall number that does not say so is not a usable control.

Run with ``make train-last-item`` (or ``python -m src.training.last_item``)
from project root. Requires Postgres and MLflow to be reachable per
``Settings``.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping

import mlflow
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from src.models.candidates import routing
from src.models.candidates.last_item import LastItemTransitionModel
from src.training.twotower import subsample_users
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

# The candidate-stage experiment item-item, the two-tower and SASRec log to.
# Hardcoded rather than read from Settings for the reason itemitem.py states:
# the experiment name is part of the experiment's identity, and an operator
# should not be able to spray runs into the wrong one via env var.
PHASE_2_EXPERIMENT = "phase-2-candidates"

BASE_RUN_NAME = "last-item-transition"

SAMPLE_FRACTION_ENV_VAR = "LASTITEM_USER_SAMPLE_FRACTION"
SEED_ENV_VAR = "LASTITEM_SEED"
RUN_LABEL_ENV_VAR = "LASTITEM_RUN_LABEL"

# The seed every other trainer's default run is drawn at. Matching it means a
# pilot-sized control and a pilot-sized SASRec run see the same users.
DEFAULT_SAMPLE_SEED = 42


def resolve_sample_fraction(env: Mapping[str, str] | None = None) -> float:
    """Read the user subsample fraction for this run out of the environment.

    Validated here rather than left to ``subsample_users`` so the error names
    the variable the operator actually set.
    """
    raw = (env if env is not None else os.environ).get(SAMPLE_FRACTION_ENV_VAR, "").strip()
    if not raw:
        return 1.0
    fraction = float(raw)
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"{SAMPLE_FRACTION_ENV_VAR} must be in (0, 1], got {fraction}")
    return fraction


def resolve_sample_seed(env: Mapping[str, str] | None = None) -> int:
    """Read the seed the user subsample is drawn at."""
    raw = (env if env is not None else os.environ).get(SEED_ENV_VAR, "").strip()
    return DEFAULT_SAMPLE_SEED if not raw else int(raw)


def run_name_for(policy: str, label: str) -> str:
    """MLflow run name: the routing policy suffix first, then any run label.

    The default policy keeps the plain base name — same contract as
    ``routing.run_name_for`` — so the run this baseline is cited by stays
    findable, and a run made under the opt-out policy or for one particular
    comparison can never be mistaken for it.
    """
    base = routing.run_name_for(BASE_RUN_NAME, policy)
    return base if not label else f"{base}-{label}"


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

    sample_fraction = resolve_sample_fraction()
    sample_seed = resolve_sample_seed()
    if sample_fraction != 1.0:
        # The same function the two-tower and SASRec trainers subsample with, so
        # "the same sample" is a shared implementation rather than a claim.
        ratings = subsample_users(ratings, sample_fraction, sample_seed)
        logger.info(
            "Subsampled to %.4f of users at seed %d: %s ratings",
            sample_fraction,
            sample_seed,
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

    # ADR 0011's cold-start cohort joins the training frame here, if this machine
    # has it — and only on a full-data run, because the cohort is anchored to the
    # full split's cutoff. Same condition SASRec's trainer applies.
    train_frame, cohort = (
        synth_cold.prepare(split, logger=logger) if sample_fraction == 1.0 else (split.train, None)
    )

    routing_policy = routing.resolve_policy()
    logger.info("Cold-start routing policy: %s", routing_policy)

    logger.info("Fitting last-item transition counts ...")
    model = LastItemTransitionModel(
        cold_start_threshold=routing.cold_start_threshold_for(routing_policy, COLD_START_THRESHOLD)
    )
    t0 = time.perf_counter()
    model.fit(train_frame)
    fit_seconds = time.perf_counter() - t0
    logger.info(
        "Fit in %.1fs (%s transition events over %s distinct pairs, "
        "%d antecedents, largest timestamp group %d)",
        fit_seconds,
        f"{model.stats.n_transition_events:,}",
        f"{model.stats.n_transition_pairs:,}",
        model.stats.n_antecedents,
        model.stats.max_timestamp_group_size,
    )

    logger.info("Recommending top-%d for each holdout user ...", K_CANDIDATES)
    t1 = time.perf_counter()
    holdout_user_ids = split.holdout["userId"].unique().tolist()
    cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
    recommendations = model.recommend_for_users(holdout_user_ids + cohort_user_ids, k=K_CANDIDATES)
    recommend_seconds = time.perf_counter() - t1
    logger.info(
        "Recommended for %d users in %.1fs",
        len(holdout_user_ids) + len(cohort_user_ids),
        recommend_seconds,
    )

    logger.info("Building eval inputs ...")
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    # Counts come from the real train slice, not the cohort-attached frame: the
    # warm/cold partition is over holdout users, and no synthetic user is ever
    # looked up in it.
    train_counts = split.train.groupby("userId").size().to_dict()

    logger.info("Evaluating at K_CANDIDATES=%d ...", K_CANDIDATES)
    result = evaluate(
        recommendations,
        holdout,
        train_counts,
        k=K_CANDIDATES,
        synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
        synthetic_cold_served_by=model.was_served_by_last_item if cohort is not None else None,
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

    # Per-policy attribution — the same partition item-item and CF report. The
    # overall numbers mix transitions with the popularity fallback in front of
    # them, and splitting them is the only way to say what the transitions did.
    holdout_last_item = {
        uid: items for uid, items in holdout.items() if model.was_served_by_last_item(uid)
    }
    holdout_fallback = {
        uid: items for uid, items in holdout.items() if not model.was_served_by_last_item(uid)
    }
    result_last_item = evaluate(recommendations, holdout_last_item, train_counts, k=K_CANDIDATES)
    result_fallback = evaluate(recommendations, holdout_fallback, train_counts, k=K_CANDIDATES)
    logger.info(
        "Last-item-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_last_item),
        K_CANDIDATES,
        result_last_item.overall.recall,
        K_CANDIDATES,
        result_last_item.overall.ndcg,
    )
    logger.info(
        "Fallback-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_fallback),
        K_CANDIDATES,
        result_fallback.overall.recall,
        K_CANDIDATES,
        result_fallback.overall.ndcg,
    )

    # How much of each list the transitions actually filled, and what the list
    # would have scored without the popularity tail. ``transition_candidates``
    # returns a prefix of ``recommend``, so this costs one extra scoring pass
    # over the served users and no second model.
    transitions_only = {
        uid: model.transition_candidates(uid, K_CANDIDATES) for uid in holdout_last_item
    }
    result_transitions_only = evaluate(
        transitions_only, holdout_last_item, train_counts, k=K_CANDIDATES
    )
    fill_rates = [len(items) / K_CANDIDATES for items in transitions_only.values()]
    mean_fill_rate = sum(fill_rates) / len(fill_rates) if fill_rates else 0.0
    n_without_transitions = sum(1 for items in transitions_only.values() if not items)
    logger.info(
        "Transitions filled %.1f%% of the average served list; %d of %d served users had no "
        "recorded successor at all. Transitions-only: recall@%d=%.4f ndcg@%d=%.4f",
        100.0 * mean_fill_rate,
        n_without_transitions,
        len(holdout_last_item),
        K_CANDIDATES,
        result_transitions_only.overall.recall,
        K_CANDIDATES,
        result_transitions_only.overall.ndcg,
    )

    if cohort is not None:
        synth_cold.log_summary(result, logger=logger, k=K_CANDIDATES)

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(
        run_name=run_name_for(routing_policy, os.environ.get(RUN_LABEL_ENV_VAR, "").strip())
    ):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "last_item_transition",
                "phase": "2",
                "stage": "candidate",
                "cold_start_routing_policy": routing_policy,
            }
        )
        mlflow.log_params(
            {
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cold_start_routing_policy": routing_policy,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "n_holdout_users": len(holdout_user_ids),
                "user_sample_fraction": sample_fraction,
                "sample_seed": sample_seed,
                "backfill_with_popularity": model.backfill_with_popularity,
                "n_transition_events": model.stats.n_transition_events,
                "n_transition_pairs": model.stats.n_transition_pairs,
                "n_antecedents": model.stats.n_antecedents,
                "max_timestamp_group_size": model.stats.max_timestamp_group_size,
                "n_users_in_train": len(model._last_items),
                "n_items_in_train": len(model._popularity.ranking),
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
                "last_item_served_recall_at_k_candidates": result_last_item.overall.recall,
                "last_item_served_ndcg_at_k_candidates": result_last_item.overall.ndcg,
                "last_item_served_warm_recall_at_k_candidates": result_last_item.warm.recall,
                "last_item_served_warm_ndcg_at_k_candidates": result_last_item.warm.ndcg,
                "n_last_item_served_users": len(holdout_last_item),
                "fallback_served_recall_at_k_candidates": result_fallback.overall.recall,
                "fallback_served_ndcg_at_k_candidates": result_fallback.overall.ndcg,
                "n_fallback_served_users": len(holdout_fallback),
                # How much of the served lists the transitions were responsible
                # for, and what they scored on their own.
                "mean_transition_fill_rate": mean_fill_rate,
                "n_users_without_transitions": n_without_transitions,
                "transitions_only_recall_at_k_candidates": (result_transitions_only.overall.recall),
                "transitions_only_ndcg_at_k_candidates": result_transitions_only.overall.ndcg,
            }
        )
        if cohort is not None:
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K_CANDIDATES))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )
    logger.info("MLflow run logged. Done.")


if __name__ == "__main__":
    main()
