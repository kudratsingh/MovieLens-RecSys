"""Train and evaluate SASRec through the shared candidate-stage protocol."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import resource
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd

from src.config import Settings
from src.data.split import TemporalSplit, sealed_test_boundary, temporal_split
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates.sasrec import (
    LEGACY_TRAINING_OBJECTIVE,
    SASRecConfig,
    SASRecModel,
    gbce_beta,
)
from src.models.candidates.sasrec_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    MANIFEST_FILENAME,
    export_sasrec,
)
from src.training import protocol_manifest
from src.training.candidate_data import (
    INPUT_DIR_ENV_VAR,
    PHASE_2_EXPERIMENT,
    load_inputs,
    subsample_users,
)
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)
RUN_LABEL_ENV_VAR = "SASREC_RUN_LABEL"
SAMPLE_FRACTION_ENV_VAR = "SASREC_USER_SAMPLE_FRACTION"

# Which users a subsample keeps is a property of the *experiment*, not of the model's
# randomness, so it is drawn from its own fixed seed rather than from the training seed.
# Sharing them made a seed sweep at pilot scale meaningless: each seed scored a different
# 6% of users, so the spread mixed training stochasticity with sample variation, and the
# tolerance study refused the runs outright on its population-equality check. Holding the
# population fixed while the model's randomness varies is the whole point of a seed sweep.
#
# The value is 42 because that is the seed every existing subsampled run already used, so
# those runs are reproduced byte for byte and nothing already measured is invalidated.
SUBSAMPLE_SEED = 42
ARTIFACT_DIR_ENV_VAR = "SASREC_ARTIFACT_DIR"
DEFAULT_ARTIFACT_DIR = Path("artifacts/sasrec")
MODEL_TYPE = "sasrec"
EVALUATION_INDEX = "torch-exact-inner-product-v1"


class SealedPartitionError(RuntimeError):
    """A run would fit on or score a rating at or after the sealed-test boundary."""


def _configuration_id(config: SASRecConfig, *, sample_fraction: float = 1.0) -> str:
    """Identify a model configuration independently of its training seed.

    The training seed is excluded on purpose: the id answers "which experiment is
    this", and a seed sweep is several draws of one experiment. The *sample*
    fraction is the opposite case and belongs in the id, because a run over 6% of
    users and a run over all of them are different experiments that happen to
    share every model hyper-parameter.

    Leaving it out had a concrete cost. The tolerance study distinguishes a
    surrogate derivation from a same-configuration one by comparing this id
    against the gate's, and a 6% surrogate carried the *same* id as the full-data
    gate run — so a study declaring `surrogate_delta` was refused as
    self-contradictory, and the surrogate route the tolerance protocol documents
    could not be used end to end for any model. The subsample seed is included
    for the same reason: two 6% runs drawn at different seeds score different
    users and are not one experiment either.

    A full-data run keeps a distinct id from any subsampled one, but its id still
    changes with this commit, so ids recorded before it do not compare equal to
    ids recorded after. That is correct rather than unfortunate — the earlier ids
    could not express the distinction — and it is why the protocol hash, not this
    id, is what binds a comparison.

    The training objective is part of the experiment, so it is in the id — except
    for the objective of record, whose ids were all minted before the field
    existed. Leaving it out there keeps a restored-loop v1 run carrying the same
    id as run 528b1451 (it is the same experiment), while an all-positions run
    keeps the id PR #183 gave it.
    """
    parameters = config.as_params()
    parameters.pop("seed")
    if parameters["training_objective"] == LEGACY_TRAINING_OBJECTIVE:
        parameters.pop("training_objective")
    parameters["sample_fraction"] = sample_fraction
    if sample_fraction != 1.0:
        parameters["subsample_seed"] = SUBSAMPLE_SEED
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    return f"sasrec-sha256:{hashlib.sha256(canonical).hexdigest()}"


def resolve_sasrec_sample_fraction() -> float:
    raw = os.environ.get(SAMPLE_FRACTION_ENV_VAR, "").strip()
    return 1.0 if not raw else float(raw)


def resolve_artifact_dir() -> Path:
    raw = os.environ.get(ARTIFACT_DIR_ENV_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_ARTIFACT_DIR


def peak_rss_bytes() -> int:
    """This process's peak resident set size so far, in bytes.

    ``ru_maxrss`` is bytes on macOS and kibibytes on Linux. It is a high-water
    mark for the whole process — data loading included — which is the number
    that decides whether a run fits on the machine.
    """
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


def encoder_weights_sha256(arrays: Mapping[str, Any]) -> str:
    """Digest of encoder weights alone: names, dtypes, shapes, and raw bytes.

    The archive's own SHA-256 also covers its metadata — the config, which now
    carries ``training_objective`` — so two archives of identical weights from
    two code versions hash differently. This digest is what "same weights"
    means. It accepts a live ``state_dict`` or the arrays an exported archive
    holds, so a model from any commit can be compared with one from this one.
    """
    digest = hashlib.sha256()
    for name in sorted(arrays):
        value = arrays[name]
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        array = np.ascontiguousarray(value)
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(array.dtype).encode())
        digest.update(repr(array.shape).encode())
        digest.update(array.tobytes())
    return f"sha256:{digest.hexdigest()}"


def sealed_partition_params(
    full_ratings: pd.DataFrame,
    split: TemporalSplit,
    fitted_frame: pd.DataFrame,
) -> dict[str, int]:
    """Refuse a run that would touch the sealed partition; return what it did touch.

    The boundary is derived from the full frame, before any user subsample, so a
    pilot is held to the same sealed window as a full run: a subsample computes
    its own 80th-percentile cutoff, and its 28-day holdout could otherwise reach
    past the full split's ``holdout_end``. The returned timestamps fill the
    experiment record's partition declaration.
    """
    boundary = sealed_test_boundary(full_ratings)
    latest_fit = int(fitted_frame["timestamp"].max()) if not fitted_frame.empty else 0
    latest_scored = int(split.holdout["timestamp"].max()) if not split.holdout.empty else 0
    if latest_fit >= boundary or latest_scored >= boundary:
        raise SealedPartitionError(
            f"run would read the sealed partition: boundary {boundary}, latest fitted "
            f"timestamp {latest_fit}, latest scored timestamp {latest_scored}"
        )
    return {
        "sealed_boundary_timestamp": boundary,
        "holdout_end_timestamp": split.holdout_end,
        "latest_fit_timestamp": latest_fit,
        "latest_scored_timestamp": latest_scored,
    }


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
    full_ratings = ratings
    if sample_fraction != 1.0:
        ratings = subsample_users(ratings, sample_fraction, SUBSAMPLE_SEED)
    split = temporal_split(ratings)
    train_frame, cohort = (
        synth_cold.prepare(split, logger=logger) if sample_fraction == 1.0 else (split.train, None)
    )
    partition = sealed_partition_params(full_ratings, split, train_frame)
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
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": MODEL_TYPE,
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
                "evaluation_index": EVALUATION_INDEX,
                **partition,
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
            model.build_exact_tensor_index()
            recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
            result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
            mlflow.log_metric("epoch_warm_recall_at_k_candidates", result.warm.recall, step=epoch)
            mlflow.log_metric("epoch_peak_rss_bytes", peak_rss_bytes(), step=epoch)
            logger.info(
                "Epoch %d loss=%.4f warm recall@%d=%.4f",
                epoch,
                loss,
                K_CANDIDATES,
                result.warm.recall,
            )

        started = time.perf_counter()
        model.fit(train_frame, on_epoch=on_epoch, retrieval_backend="torch")
        fit_seconds = time.perf_counter() - started
        fit_peak_rss = peak_rss_bytes()
        logger.info(
            "Fit %.1fs objective=%s example store %.3f GiB, peak RSS after fit %.3f GiB",
            fit_seconds,
            config.training_objective,
            model._training_example_bytes / 2**30,
            fit_peak_rss / 2**30,
        )
        active_run = mlflow.active_run()
        if active_run is None:
            raise RuntimeError("MLflow run ended before SASRec artifact export")
        artifact_dir = (artifact_root or resolve_artifact_dir()) / active_run.info.run_id
        manifest = export_sasrec(model, artifact_dir)
        # The run-specific local copy is durable even if the tracking upload
        # fails. MLflow receives a second immutable copy for registry lineage.
        mlflow.log_artifacts(str(artifact_dir), artifact_path="model")
        assert model._encoder is not None
        mlflow.set_tags(
            {
                "sasrec_weights_sha256": encoder_weights_sha256(model._encoder.state_dict()),
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
                "n_training_windows": (
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
                "n_warm_users": result.n_warm_users,
                "n_cold_users": result.n_cold_users,
                "training_example_store_bytes": model._training_example_bytes,
                "fit_peak_rss_bytes": fit_peak_rss,
                "peak_rss_bytes": peak_rss_bytes(),
                **diagnostics,
            }
        )
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run.info.run_id,
                model_type=MODEL_TYPE,
                seed=config.seed,
                configuration_id=_configuration_id(config, sample_fraction=sample_fraction),
                protocol=protocol.to_dict(),
            ),
            PER_USER_RECALL_ARTIFACT,
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
