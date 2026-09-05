"""
Train the content-based cold-item retriever end-to-end and log to MLflow.

Same skeleton as ``src/training/itemitem.py`` — the candidate-stage models all
share the load → split → fit → recommend → evaluate → log shape so they are
directly comparable in MLflow. Everything that could make the comparison unequal
is inherited rather than re-decided: the same ``temporal_split``, the same
``K_CANDIDATES``, the same ``evaluate`` entrypoint, the same
``phase-2-candidates`` experiment, the same routing threshold, the same
seen-item exclusions, the same protocol manifest and the same per-user recall
artifact. The model is the only thing that changes.

**What this run is evidence of.** ADR 0017's claim, and the only one the logged
numbers support, is *coverage*: 3,376 catalog movies have no rating at all, no
interaction-derived retriever can reach one of them, and this one can. That is a
property of the mechanism and the metrics below make it countable —
``n_distinct_cold_items_retrieved`` is zero for item-item, the two-tower and
SASRec by construction, and is the number this rung exists to move off zero.

Relevance is the secondary claim and it is weakly measured. The cold-item slice
ADR 0017 specifies is 829 holdout rows over 313 users, and under the 2026-09-05
one-run-per-configuration policy there is no seed spread either. The warm, cold
and overall recall this trainer logs are the ordinary user-sliced numbers — they
say how a content-only retriever does against item-item on the *whole* holdout,
where it is expected to lose, and losing there is not evidence against the
coverage claim. Nobody should read a recall figure from this run as evidence
that users are better served, and the offline population is not the production
one either: offline a cold item is a deep-catalog obscurity, in serving it is a
film released this week, and MovieLens ends in 2019.

**One field of the protocol manifest under-describes this run.**
``protocol_manifest.catalog_fingerprint`` is derived from the fitted frame,
because every other candidate model here ranks only within the items its
training frame contained. This one ranks the whole ``movies`` catalog — that
difference *is* the rung — so the recorded fingerprint names a smaller catalog
than the model can retrieve from. Everything else in the manifest is accurate
and the run stays comparable on it. The coverage params below state the real
retrievable catalog explicitly so the gap is visible in the run rather than only
in this docstring; closing it properly means a content-aware value in
``src/evaluation/``, which is not this PR's to write.

Run with ``make train-content`` (or ``python -m src.training.content``) from
project root. Requires Postgres and MLflow to be reachable per ``Settings``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass

import mlflow
import pandas as pd
from sqlalchemy import Engine, create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import temporal_split
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates import routing
from src.models.candidates.content import ContentSimilarityModel
from src.training import protocol_manifest
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

# The model identity every consumer keys off — the MLflow tag and the per-user
# recall artifact. One constant so they cannot drift apart. Named for the
# representation rather than for the technique, because increment 2 (TMDB
# overviews, keywords, cast) is a different representation scored the same way
# and must not be pooled with these runs.
MODEL_TYPE = "content_genre_year"

BASE_RUN_NAME = "content-genre-year"

# The candidate-stage experiment item-item, the two-tower, SASRec and the
# last-item control log to. Hardcoded rather than read from Settings for the
# reason itemitem.py states: the experiment name is part of the experiment's
# identity, and an operator should not be able to spray runs into the wrong one
# via env var.
PHASE_2_EXPERIMENT = "phase-2-candidates"

_MOVIES_SQL = 'SELECT "movieId", title, genres FROM movies'


@dataclass(frozen=True)
class SlateCoverage:
    """How much of the cold population actually reached a candidate list.

    The counterpart to ``ContentCoverage``, which says what the *representation*
    reaches. This says what the *retrieval* reached, which is the claim a reader
    should be able to check: a model that represents every cold item but never
    surfaces one has not made them reachable in any sense a user would notice.
    """

    n_users: int
    n_distinct_items_retrieved: int
    n_distinct_cold_items_retrieved: int
    # Mean cold items per slate, over the users counted here. Deliberately a
    # count rather than a share of k, so a short slate cannot flatter it.
    mean_cold_items_per_slate: float
    n_users_with_a_cold_candidate: int


def slate_coverage(
    recommendations: Mapping[int, list[int]],
    cold_items: Collection[int],
    user_ids: Collection[int],
) -> SlateCoverage:
    """Count cold items across the slates served to ``user_ids``.

    Scoped to an explicit user list rather than to every key in
    ``recommendations``: the users the popularity fallback answered can never
    contribute a cold item — popularity ranks interaction counts, and a cold item
    has none — so pooling them would dilute the measurement with a population the
    model was not asked about. The trainer passes its content-served users.
    """
    cold = set(cold_items)
    retrieved: set[int] = set()
    retrieved_cold: set[int] = set()
    n_cold_total = 0
    n_users_with_cold = 0

    for user_id in user_ids:
        slate = recommendations.get(user_id, [])
        retrieved.update(slate)
        in_slate = [item for item in slate if item in cold]
        retrieved_cold.update(in_slate)
        n_cold_total += len(in_slate)
        if in_slate:
            n_users_with_cold += 1

    n_users = len(user_ids)
    return SlateCoverage(
        n_users=n_users,
        n_distinct_items_retrieved=len(retrieved),
        n_distinct_cold_items_retrieved=len(retrieved_cold),
        mean_cold_items_per_slate=(n_cold_total / n_users if n_users else 0.0),
        n_users_with_a_cold_candidate=n_users_with_cold,
    )


def load_movies(engine: Engine) -> pd.DataFrame:
    """The catalog this model retrieves from — every film, rated or not.

    Read here rather than through ``src/data/load.py`` because that module is the
    ratings reader and this is the only trainer that needs the title column: the
    release year is parsed off it, and the ranker's own movies query
    (``movieId``, ``genres``) would silently drop the year signal.
    """
    return pd.read_sql(_MOVIES_SQL, engine)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = Settings()

    logger.info("Loading ratings and the movie catalog from Postgres ...")
    engine = create_engine(settings.database_url)
    ratings = load_ratings(engine)
    movies = load_movies(engine)
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

    # ADR 0011's cold-start cohort joins the training frame here, if this machine
    # has it. Same unconditional call item-item makes: at most 7 000 rows against
    # ~20 M, and none of its users appear in holdout, so the warm/cold numbers
    # below are unmoved — the cohort exists to be routed and scored.
    train_frame, cohort = synth_cold.prepare(split, logger=logger)

    routing_policy = routing.resolve_policy()
    logger.info("Cold-start routing policy: %s", routing_policy)

    model = ContentSimilarityModel(
        cold_start_threshold=routing.cold_start_threshold_for(routing_policy, COLD_START_THRESHOLD)
    )

    # Derived before the fit rather than after it: every input the protocol
    # depends on already exists here, and a missing DVC pointer or an unexpected
    # column should cost a minute rather than a completed training run. See the
    # module docstring for the one field this manifest under-describes.
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(model.cold_start_threshold),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    logger.info("Evaluation protocol: %s", protocol.semantic_hash)

    logger.info("Building the content index and per-user histories ...")
    t0 = time.perf_counter()
    model.fit(train_frame, movies)
    fit_seconds = time.perf_counter() - t0
    coverage = model.coverage
    logger.info(
        "Fit in %.1fs. Catalog %s items over %d genres; %s have genres, %s a release year.",
        fit_seconds,
        f"{coverage.n_catalog_items:,}",
        coverage.n_genres,
        f"{coverage.n_items_with_genres:,}",
        f"{coverage.n_items_with_release_year:,}",
    )
    # The honest headline of this rung, logged before any recall number so it
    # cannot be read as a footnote to one.
    logger.info(
        "Cold items (no interaction in train): %s. Of those, %s have genres and %s do not — "
        "the second group cannot be served by this increment at all, by construction.",
        f"{coverage.n_cold_items:,}",
        f"{coverage.n_cold_items_with_genres:,}",
        f"{coverage.n_cold_items_without_genres:,}",
    )
    if coverage.n_history_rows_outside_catalog:
        logger.warning(
            "%s training rows name a movie the catalog does not contain; those interactions "
            "contribute no genre mass to any profile.",
            f"{coverage.n_history_rows_outside_catalog:,}",
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
        # The frame the model was actually fitted on, so the cold-item slice measures
        # novelty against what this run could have learned rather than an assumed
        # catalog. This retriever exists for exactly those items, so it is the one run
        # where the slice is the headline rather than a diagnostic.
        train_items=train_frame["movieId"].unique(),
        synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
        synthetic_cold_served_by=model.was_served_by_content if cohort is not None else None,
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

    # Per-policy attribution — the same partition item-item, CF and the
    # last-item control report. The overall numbers mix content retrieval with
    # the popularity fallback in front of it, and splitting them is the only way
    # to say what the content representation did.
    holdout_content = {
        uid: items for uid, items in holdout.items() if model.was_served_by_content(uid)
    }
    holdout_fallback = {
        uid: items for uid, items in holdout.items() if not model.was_served_by_content(uid)
    }
    result_content = evaluate(recommendations, holdout_content, train_counts, k=K_CANDIDATES)
    result_fallback = evaluate(recommendations, holdout_fallback, train_counts, k=K_CANDIDATES)
    logger.info(
        "Content-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_content),
        K_CANDIDATES,
        result_content.overall.recall,
        K_CANDIDATES,
        result_content.overall.ndcg,
    )
    logger.info(
        "Fallback-served (n=%d): recall@%d=%.4f ndcg@%d=%.4f",
        len(holdout_fallback),
        K_CANDIDATES,
        result_fallback.overall.recall,
        K_CANDIDATES,
        result_fallback.overall.ndcg,
    )

    # The coverage measurement. Item-item's value for every count here is zero,
    # so this is the comparison the rung is actually about.
    slates = slate_coverage(recommendations, model.cold_item_ids.tolist(), list(holdout_content))
    logger.info(
        "Cold-item coverage over %d content-served holdout users: %s distinct cold items "
        "retrieved, %.1f per slate on average, %d users offered at least one.",
        slates.n_users,
        f"{slates.n_distinct_cold_items_retrieved:,}",
        slates.mean_cold_items_per_slate,
        slates.n_users_with_a_cold_candidate,
    )

    # Users routed to content whose history carries no genre at all. Their genre
    # key is constant, so their slate is ordered by era proximity alone — a much
    # weaker answer, and one that should be counted rather than assumed absent.
    n_genreless_profiles = sum(
        1
        for uid in holdout_content
        if (profile := model.content_profile(uid)) is not None and not profile.any()
    )
    if n_genreless_profiles:
        logger.warning(
            "%d content-served holdout users have no genre mass in their profile; their "
            "candidates are ordered by release year alone.",
            n_genreless_profiles,
        )

    if cohort is not None:
        synth_cold.log_summary(result, logger=logger, k=K_CANDIDATES)

    if result.cold_item is not None and result.cold_item.metrics is not None:
        logger.info(
            "Cold-item slice (n=%d users, %d targets): recall@%d=%.4f ndcg@%d=%.4f "
            "— coverage evidence only, gates nothing",
            result.cold_item.n_users,
            result.cold_item.n_targets,
            K_CANDIDATES,
            result.cold_item.metrics.recall,
            K_CANDIDATES,
            result.cold_item.metrics.ndcg,
        )

    logger.info("Logging to MLflow at %s ...", settings.mlflow_tracking_uri)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name=routing.run_name_for(BASE_RUN_NAME, routing_policy)) as run:
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": MODEL_TYPE,
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
                "n_users_in_train": len(model._history_span),
                # The retrievable catalog, stated as a param because the
                # protocol manifest's catalog_fingerprint is derived from the
                # fitted frame and therefore names the smaller interaction
                # catalog. See the module docstring.
                "n_catalog_items": coverage.n_catalog_items,
                "n_items_in_train": coverage.n_interaction_items,
                "n_genres": coverage.n_genres,
                "content_representation": "genres+release_year",
                "fit_seconds": round(fit_seconds, 1),
                "recommend_seconds": round(recommend_seconds, 1),
            }
        )
        # The strict envelope from docs/model-planning/contracts/evaluation-protocol.md.
        # Nothing in this model is stochastic — there is no fit to seed and the
        # ordering is broken to the movie id — so the run is deterministic and
        # records no seed; the retrieval gate rejects a deterministic run that
        # claims one.
        envelope = protocol_manifest.run_envelope(protocol, deterministic=True, seed=None)
        mlflow.set_tags(envelope.tags)
        mlflow.log_params(envelope.params)
        mlflow.log_metrics(
            {
                "warm_recall_at_k_candidates": result.warm.recall,
                "warm_ndcg_at_k_candidates": result.warm.ndcg,
                "cold_recall_at_k_candidates": result.cold.recall,
                "cold_ndcg_at_k_candidates": result.cold.ndcg,
                "overall_recall_at_k_candidates": result.overall.recall,
                # Absent rather than zero when nobody looked: a run that did not compute
                # the slice must not be readable as a run that measured it and found
                # nothing. See ColdItemSlice for why this number gates nothing.
                **(
                    {
                        "cold_item_recall_at_k_candidates": result.cold_item.metrics.recall,
                        "cold_item_ndcg_at_k_candidates": result.cold_item.metrics.ndcg,
                        "n_cold_item_users": float(result.cold_item.n_users),
                        "n_cold_item_targets": float(result.cold_item.n_targets),
                    }
                    if result.cold_item is not None and result.cold_item.metrics is not None
                    else {}
                ),
                "overall_ndcg_at_k_candidates": result.overall.ndcg,
                "n_warm_users": result.n_warm_users,
                "n_cold_users": result.n_cold_users,
                # Per-policy attribution at K_CANDIDATES.
                "content_served_recall_at_k_candidates": result_content.overall.recall,
                "content_served_ndcg_at_k_candidates": result_content.overall.ndcg,
                "content_served_warm_recall_at_k_candidates": result_content.warm.recall,
                "content_served_warm_ndcg_at_k_candidates": result_content.warm.ndcg,
                "n_content_served_users": len(holdout_content),
                "fallback_served_recall_at_k_candidates": result_fallback.overall.recall,
                "fallback_served_ndcg_at_k_candidates": result_fallback.overall.ndcg,
                "n_fallback_served_users": len(holdout_fallback),
                # Representation coverage — what the content vectors reach.
                "n_items_with_genres": coverage.n_items_with_genres,
                "n_items_with_release_year": coverage.n_items_with_release_year,
                "n_cold_items": coverage.n_cold_items,
                "n_cold_items_with_genres": coverage.n_cold_items_with_genres,
                "n_cold_items_without_genres": coverage.n_cold_items_without_genres,
                "n_cold_items_with_release_year": coverage.n_cold_items_with_release_year,
                "n_history_rows_outside_catalog": coverage.n_history_rows_outside_catalog,
                # Retrieval coverage — what actually reached a slate. Zero for
                # every interaction-derived retriever, which is the point.
                "n_distinct_items_retrieved": slates.n_distinct_items_retrieved,
                "n_distinct_cold_items_retrieved": slates.n_distinct_cold_items_retrieved,
                "mean_cold_items_per_slate": slates.mean_cold_items_per_slate,
                "n_users_with_a_cold_candidate": slates.n_users_with_a_cold_candidate,
                "n_genreless_content_profiles": n_genreless_profiles,
            }
        )
        # The recall behind those means, one row per holdout user — the paired
        # per-user numbers the retrieval tolerance study needs and the averages
        # above have already thrown away.
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run.info.run_id,
                model_type=MODEL_TYPE,
                # Deterministic: no seed to report, and the gate refuses a
                # deterministic run that claims one.
                seed=None,
                configuration_id=f"content-genre-year-{routing_policy}",
                protocol=protocol.to_dict(),
            ),
            PER_USER_RECALL_ARTIFACT,
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
