"""Train and evaluate SASRec through the shared candidate-stage protocol."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import mlflow
import pandas as pd

from src.config import Settings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from src.models.candidates.sasrec import SASRecConfig, SASRecModel, gbce_beta
from src.models.candidates.sasrec_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    MANIFEST_FILENAME,
    export_sasrec,
)
from src.training import protocol_manifest
from src.training.twotower import (
    INPUT_DIR_ENV_VAR,
    PHASE_2_EXPERIMENT,
    load_inputs,
    subsample_users,
)
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)
RUN_LABEL_ENV_VAR = "SASREC_RUN_LABEL"
SAMPLE_FRACTION_ENV_VAR = "SASREC_USER_SAMPLE_FRACTION"
ARTIFACT_DIR_ENV_VAR = "SASREC_ARTIFACT_DIR"
DEFAULT_ARTIFACT_DIR = Path("artifacts/sasrec")


def resolve_sasrec_sample_fraction() -> float:
    raw = os.environ.get(SAMPLE_FRACTION_ENV_VAR, "").strip()
    return 1.0 if not raw else float(raw)


def resolve_artifact_dir() -> Path:
    raw = os.environ.get(ARTIFACT_DIR_ENV_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_ARTIFACT_DIR


def retrieval_diagnostics(
    recommendations: dict[int, list[int]],
    holdout: dict[int, set[int]],
    item_popularity: dict[int, int],
    *,
    catalog_size: int,
) -> dict[str, float]:
    """Measure reach and head collapse for one explicitly selected policy."""
    retrieved = [item for user_id in holdout for item in recommendations.get(user_id, [])]
    unique_items = set(retrieved)
    popularity_order = sorted(item_popularity, key=lambda item: (-item_popularity[item], item))
    popularity_rank = {item: rank for rank, item in enumerate(popularity_order, start=1)}
    default_rank = len(popularity_rank) + 1
    reached = sum(
        len(targets & set(recommendations.get(user_id, []))) for user_id, targets in holdout.items()
    )
    n_targets = sum(len(targets) for targets in holdout.values())
    return {
        "retrieved_unique_items": float(len(unique_items)),
        "catalog_coverage": len(unique_items) / max(1, catalog_size),
        "mean_retrieved_item_popularity_rank": (
            sum(popularity_rank.get(item, default_rank) for item in retrieved) / len(retrieved)
            if retrieved
            else 0.0
        ),
        "holdout_target_reachability": reached / max(1, n_targets),
    }


def run_once(
    ratings: pd.DataFrame,
    config: SASRecConfig,
    *,
    sample_fraction: float = 1.0,
    run_label: str = "",
    artifact_root: Path | None = None,
) -> None:
    if sample_fraction != 1.0:
        ratings = subsample_users(ratings, sample_fraction, config.seed)
    split = temporal_split(ratings)
    train_frame, cohort = (
        synth_cold.prepare(split, logger=logger) if sample_fraction == 1.0 else (split.train, None)
    )
    train_counts = split.train.groupby("userId").size().to_dict()
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    user_ids = list(holdout)
    cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
    model = SASRecModel(config=config, cold_start_threshold=COLD_START_THRESHOLD)
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(model.cold_start_threshold),
        stage="retrieval",
        k=K_CANDIDATES,
    )

    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    run_name = "sasrec" if not run_label else f"sasrec-{run_label}"
    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "sasrec",
                "stage": "candidate",
                "sweep_label": run_label,
            }
        )
        mlflow.log_params(
            {
                **config.as_params(),
                "user_sample_fraction": sample_fraction,
                "cutoff_timestamp": split.cutoff,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "k_candidates": K_CANDIDATES,
            }
        )
        envelope = protocol_manifest.run_envelope(
            protocol,
            deterministic=False,
            seed=config.seed,
        )
        mlflow.set_tags(envelope.tags)
        mlflow.log_params(envelope.params)

        def on_epoch(epoch: int, loss: float) -> None:
            mlflow.log_metric("train_loss", loss, step=epoch)
            model.build_index()
            recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
            result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
            mlflow.log_metric("epoch_warm_recall_at_k_candidates", result.warm.recall, step=epoch)
            logger.info(
                "Epoch %d loss=%.4f warm recall@%d=%.4f",
                epoch,
                loss,
                K_CANDIDATES,
                result.warm.recall,
            )

        started = time.perf_counter()
        model.fit(train_frame, on_epoch=on_epoch)
        fit_seconds = time.perf_counter() - started
        active_run = mlflow.active_run()
        if active_run is None:
            raise RuntimeError("MLflow run ended before SASRec artifact export")
        artifact_dir = (artifact_root or resolve_artifact_dir()) / active_run.info.run_id
        manifest = export_sasrec(model, artifact_dir)
        # The run-specific local copy is durable even if the tracking upload
        # fails. MLflow receives a second immutable copy for registry lineage.
        mlflow.log_artifacts(str(artifact_dir), artifact_path="model")
        mlflow.set_tags(
            {
                "sasrec_artifact_sha256": manifest.model_sha256,
                "sasrec_vocabulary_sha256": manifest.vocabulary_sha256,
                "sasrec_manifest": f"model/{MANIFEST_FILENAME}",
            }
        )
        recommendations = model.recommend_for_users(user_ids + cohort_user_ids, K_CANDIDATES)
        result = evaluate(
            recommendations,
            holdout,
            train_counts,
            k=K_CANDIDATES,
            synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
            synthetic_cold_served_by=model.was_served_by_sasrec if cohort is not None else None,
        )
        sasrec_holdout = {
            user_id: targets
            for user_id, targets in holdout.items()
            if model.was_served_by_sasrec(user_id)
        }
        diagnostics = retrieval_diagnostics(
            recommendations,
            sasrec_holdout,
            split.train["movieId"].value_counts().to_dict(),
            catalog_size=len(model._index_to_item),
        )
        beta = (
            1.0
            if config.loss == "bce"
            else gbce_beta(
                negative_count=config.negative_count,
                catalog_size=len(model._index_to_item),
                calibration_t=config.calibration_t,
            )
        )
        mlflow.log_params(
            {
                "fit_seconds": round(fit_seconds, 1),
                "n_items_in_train": len(model._index_to_item),
                "n_users_in_train": len(model._user_history),
                "gbce_beta": beta,
                "sasrec_artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
                "n_training_sequences": (
                    model._training_stats.n_sequences if model._training_stats else 0
                ),
                "n_training_targets": (
                    model._training_stats.n_targets if model._training_stats else 0
                ),
                "n_truncated_sequences": (
                    model._training_stats.n_truncated_sequences if model._training_stats else 0
                ),
                "n_truncated_interactions": (
                    model._training_stats.n_truncated_interactions if model._training_stats else 0
                ),
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
                **diagnostics,
            }
        )
        if cohort is not None:
            synth_cold.log_summary(result, logger=logger, k=K_CANDIDATES)
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K_CANDIDATES))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    input_dir_raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
    ratings, _movies = load_inputs(
        settings, input_dir=Path(input_dir_raw) if input_dir_raw else None
    )
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    run_once(
        ratings,
        SASRecConfig.from_env(),
        sample_fraction=resolve_sasrec_sample_fraction(),
        run_label=os.environ.get(RUN_LABEL_ENV_VAR, "").strip(),
    )


if __name__ == "__main__":
    main()
