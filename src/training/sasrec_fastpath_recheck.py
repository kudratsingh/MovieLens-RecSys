"""Re-evaluate the pinned SASRec artifact after the eval fast-path fix.

This is deliberately an inference-only runner. It reloads the immutable seed-42
artifact, evaluates retrieval, then re-composes bundle 1b from its two immutable
boosters. No model or ranker is trained and no existing run or artifact is
modified.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mlflow
import torch
import torch.nn.functional as F  # noqa: N812

from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    EvalResult,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates.sasrec import SASRecModel
from src.models.ranker.lgbm import LGBMRanker
from src.training import protocol_manifest
from src.training.sasrec import (
    MODEL_TYPE,
    _configuration_id,
    retrieval_diagnostics,
)
from src.training.sasrec_ranker import SharedInputs, prepare_shared
from src.training.sasrec_ranker_bundles import (
    STEP1_CHALLENGER_RUN,
    STEP1_INCUMBENT_COLD_NDCG,
    STEP1_INCUMBENT_COLD_RECALL,
    STEP1_INCUMBENT_RUN,
    _evaluate,
    _file_sha256,
    _holdout_user_ids,
    _log_bundle_run,
    rank_by_route,
)
from src.training.twotower import PHASE_2_EXPERIMENT

logger = logging.getLogger(__name__)

RETRIEVAL_TRACKING_URI_ENV_VAR = "SASREC_FASTPATH_RETRIEVAL_TRACKING_URI"
EVIDENCE_DIR_ENV_VAR = "SASREC_FASTPATH_EVIDENCE_DIR"
DEFAULT_EVIDENCE_DIR = Path("artifacts/sasrec/fastpath-reevaluation")
ORIGINAL_SASREC_RUN_ID = "a11af5ed0f0745f68572407237cfa4b9"
ORIGINAL_BUNDLE_RUN_ID = "566f5309767a4076a4f5e8151be16645"
EXPECTED_PROTOCOL_HASH = "sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28"


def warm_history_length_distribution(
    train_counts: Mapping[int, int], holdout_user_ids: list[int]
) -> dict[str, int]:
    """Count warm holdout users by train-history length around the 50-item boundary."""
    counts = [
        int(train_counts.get(user_id, 0))
        for user_id in holdout_user_ids
        if int(train_counts.get(user_id, 0)) >= COLD_START_THRESHOLD
    ]
    return {
        "10_19": sum(COLD_START_THRESHOLD <= count < 20 for count in counts),
        "20_29": sum(20 <= count < 30 for count in counts),
        "30_39": sum(30 <= count < 40 for count in counts),
        "40_49": sum(40 <= count < 50 for count in counts),
        "50_plus": sum(count >= 50 for count in counts),
        "below_50": sum(count < 50 for count in counts),
        "total_warm": len(counts),
    }


def full_length_fastpath_delta(model: SASRecModel) -> float:
    """Compare old and safe inference paths on one unpadded artifact query."""
    if model._encoder is None:
        raise RuntimeError("SASRec encoder is not loaded")
    encoder = model._encoder
    movie_ids = sorted(model._item_to_index)[: model.config.max_sequence_length]
    dense = [model._item_to_index[movie_id] for movie_id in movie_ids]
    sequence = model._sequence_tensor(dense)
    length = sequence.shape[1]
    positions = torch.arange(length).unsqueeze(0)
    values = encoder.item_embedding(sequence) + encoder.position_embedding(positions)
    causal_mask = torch.triu(torch.ones(length, length, dtype=torch.bool), diagonal=1)
    try:
        torch.backends.mha.set_fastpath_enabled(True)
        with torch.no_grad():
            old = F.normalize(
                encoder.output_norm(
                    encoder.transformer(
                        values,
                        mask=causal_mask,
                        src_key_padding_mask=sequence.eq(0),
                    )
                )[:, -1, :],
                p=2,
                dim=-1,
            )
    finally:
        torch.backends.mha.set_fastpath_enabled(False)
    with torch.no_grad():
        safe = encoder(sequence)
    return float(torch.max(torch.abs(old - safe)).item())


def _metrics(result: EvalResult) -> dict[str, float | int]:
    return {
        "warm_recall": result.warm.recall,
        "warm_ndcg": result.warm.ndcg,
        "cold_recall": result.cold.recall,
        "cold_ndcg": result.cold.ndcg,
        "overall_recall": result.overall.recall,
        "overall_ndcg": result.overall.ndcg,
        "n_warm_users": result.n_warm_users,
        "n_cold_users": result.n_cold_users,
    }


def _write_new(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _log_retrieval(
    shared: SharedInputs,
    *,
    tracking_uri: str,
    evidence_dir: Path,
) -> tuple[str, EvalResult, dict[str, int], float]:
    model = shared.sasrec.model
    if model.config.num_blocks != 2 or not model.config.faiss_exact:
        raise RuntimeError("W17 requires the pinned two-block exact-FAISS artifact")
    protocol = protocol_manifest.build_protocol(
        split=shared.split,
        fitted_frame=shared.train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(model.cold_start_threshold),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    if protocol.semantic_hash != EXPECTED_PROTOCOL_HASH:
        raise RuntimeError(
            f"W17 protocol drift: {protocol.semantic_hash} != {EXPECTED_PROTOCOL_HASH}"
        )

    holdout = shared.split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = shared.split.train.groupby("userId").size().to_dict()
    user_ids = list(holdout)
    distribution = warm_history_length_distribution(train_counts, user_ids)
    full_length_delta = full_length_fastpath_delta(model)
    if full_length_delta > 1e-6:
        raise RuntimeError(f"full-length fast-path delta {full_length_delta} exceeds 1e-6")
    started = time.perf_counter()
    recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
    recommend_seconds = time.perf_counter() - started
    result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
    short_warm_users = [
        user_id
        for user_id in user_ids
        if COLD_START_THRESHOLD <= int(train_counts.get(user_id, 0)) < 50
    ]
    fixed_empty_short_slates = sum(not recommendations[user_id] for user_id in short_warm_users)
    if fixed_empty_short_slates:
        raise RuntimeError(f"fast-path fix left {fixed_empty_short_slates} short warm slates empty")
    diagnostics = retrieval_diagnostics(
        recommendations,
        {
            user_id: targets
            for user_id, targets in holdout.items()
            if model.was_served_by_sasrec(user_id)
        },
        shared.split.train["movieId"].value_counts().to_dict(),
        catalog_size=len(model._index_to_item),
    )

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name="sasrec-fastpath-fixed-reevaluation-seed42") as run:
        run_id = run.info.run_id
        envelope = protocol_manifest.run_envelope(
            protocol, deterministic=False, seed=model.config.seed
        )
        mlflow.set_tags(
            {
                **envelope.tags,
                "model_family": "candidate_generator",
                "model_type": MODEL_TYPE,
                "stage": "candidate",
                "sweep_label": "fastpath-fixed-reevaluation",
                "supersedes_run_id": ORIGINAL_SASREC_RUN_ID,
                "inference_fix": "disable-pytorch-mha-fastpath-for-left-padding",
                **shared.sasrec.identity(),
            }
        )
        mlflow.log_params(
            {
                **envelope.params,
                **model.config.as_params(),
                "user_sample_fraction": shared.sample_fraction,
                "cutoff_timestamp": shared.split.cutoff,
                "n_train_rows": len(shared.split.train),
                "n_holdout_rows": len(shared.split.holdout),
                "k_candidates": K_CANDIDATES,
                "configuration_id": _configuration_id(model.config),
                "recommend_seconds": round(recommend_seconds, 3),
                "n_pre_fix_empty_warm_slates": distribution["below_50"],
                "n_popularity_routed_warm_users_before_fix": 0,
                "full_length_fastpath_max_abs_delta": full_length_delta,
                **{f"n_warm_history_{key}": value for key, value in distribution.items()},
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
                "n_fixed_empty_short_warm_slates": fixed_empty_short_slates,
                **diagnostics,
            }
        )
        per_user = per_user_recall_document(
            result,
            run_id=run_id,
            model_type=MODEL_TYPE,
            seed=model.config.seed,
            configuration_id=_configuration_id(model.config),
            protocol=protocol.to_dict(),
        )
        mlflow.log_dict(per_user, PER_USER_RECALL_ARTIFACT)

    _write_new(evidence_dir / run_id / PER_USER_RECALL_ARTIFACT, per_user)
    _write_new(
        evidence_dir / run_id / "retrieval-summary.json",
        {
            "run_id": run_id,
            "supersedes_run_id": ORIGINAL_SASREC_RUN_ID,
            "protocol_hash": protocol.semantic_hash,
            "metrics": _metrics(result),
            "history_length_distribution": distribution,
            "pre_fix_empty_warm_slates": distribution["below_50"],
            "popularity_routed_warm_users_before_fix": 0,
            "fixed_empty_short_warm_slates": fixed_empty_short_slates,
            "full_length_fastpath_max_abs_delta": full_length_delta,
            "recommend_seconds": recommend_seconds,
            "diagnostics": diagnostics,
        },
    )
    return run_id, result, distribution, recommend_seconds


def _log_bundle_recheck(
    shared: SharedInputs,
    *,
    evidence_dir: Path,
) -> tuple[str, EvalResult, float]:
    incumbent_path = shared.booster_root / STEP1_INCUMBENT_RUN / "ranker.txt"
    challenger_path = shared.booster_root / STEP1_CHALLENGER_RUN / "ranker.txt"
    incumbent = LGBMRanker.load_model(incumbent_path)
    challenger = LGBMRanker.load_model(challenger_path)
    sha256s = {
        "incumbent": _file_sha256(incumbent_path),
        "challenger": _file_sha256(challenger_path),
    }
    started = time.perf_counter()
    recommendations = rank_by_route(
        warm_source=shared.sasrec,
        warm_ranker=challenger,
        cold_source=shared.itemitem,
        cold_ranker=incumbent,
        feature_index=shared.feature_index,
        user_ids=_holdout_user_ids(shared),
        as_of_timestamp=shared.split.cutoff,
    )
    rank_seconds = time.perf_counter() - started
    result = _evaluate(shared, recommendations)
    if result.cold.ndcg != STEP1_INCUMBENT_COLD_NDCG:
        raise RuntimeError("W17 changed the bundle's popularity-routed cold NDCG")
    if result.cold.recall != STEP1_INCUMBENT_COLD_RECALL:
        raise RuntimeError("W17 changed the bundle's popularity-routed cold recall")

    run_id_holder: dict[str, str] = {}
    booster_sha256 = f"warm={sha256s['challenger']};cold={sha256s['incumbent']}"
    importances = challenger.feature_importances(importance_type="gain")
    _log_bundle_run(
        shared,
        name="per-route-bundle-fastpath-fixed",
        result=result,
        booster_sha256=booster_sha256,
        importances=importances,
        params={
            "rank_seconds": round(rank_seconds, 1),
            "n_new_boosters": 0,
            "warm_booster_run_id": STEP1_CHALLENGER_RUN,
            "cold_booster_run_id": STEP1_INCUMBENT_RUN,
            "supersedes_bundle_run_id": ORIGINAL_BUNDLE_RUN_ID,
        },
        tags={
            "bundle_composition": "warm=sasrec+challenger;cold=popularity+incumbent",
            "inference_fix": "disable-pytorch-mha-fastpath-for-left-padding",
            "supersedes_run_id": ORIGINAL_BUNDLE_RUN_ID,
        },
        run_id_holder=run_id_holder,
    )
    run_id = run_id_holder["run_id"]
    _write_new(
        evidence_dir / run_id / "bundle-summary.json",
        {
            "run_id": run_id,
            "supersedes_run_id": ORIGINAL_BUNDLE_RUN_ID,
            "metrics": _metrics(result),
            "rank_seconds": rank_seconds,
            "booster_sha256": booster_sha256,
            "feature_importance_gain": importances,
        },
    )
    return run_id, result, rank_seconds


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    retrieval_tracking_uri = os.environ.get(RETRIEVAL_TRACKING_URI_ENV_VAR, "").strip()
    if not retrieval_tracking_uri:
        raise RuntimeError(f"{RETRIEVAL_TRACKING_URI_ENV_VAR} is required")
    evidence_dir = Path(os.environ.get(EVIDENCE_DIR_ENV_VAR, str(DEFAULT_EVIDENCE_DIR)))

    shared = prepare_shared()
    bundle_tracking_uri = mlflow.get_tracking_uri()
    retrieval_run, retrieval_result, distribution, retrieval_seconds = _log_retrieval(
        shared,
        tracking_uri=retrieval_tracking_uri,
        evidence_dir=evidence_dir,
    )
    mlflow.set_tracking_uri(bundle_tracking_uri)
    bundle_run, bundle_result, bundle_seconds = _log_bundle_recheck(
        shared,
        evidence_dir=evidence_dir,
    )
    logger.info(
        "W17 complete: retrieval=%s metrics=%s distribution=%s recommend_seconds=%.3f; "
        "bundle=%s metrics=%s rank_seconds=%.1f",
        retrieval_run,
        _metrics(retrieval_result),
        distribution,
        retrieval_seconds,
        bundle_run,
        _metrics(bundle_result),
        bundle_seconds,
    )


if __name__ == "__main__":
    main()
