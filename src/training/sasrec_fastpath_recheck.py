"""Re-evaluate the pinned SASRec artifact, inference only.

Two scopes, chosen with ``SASREC_RECHECK_SCOPE``:

* ``w17`` (the default) is the 2026-09-05 re-measurement after the eval
  fast-path fix: reload the immutable seed-42 artifact, evaluate retrieval, then
  re-compose bundle 1b from its two immutable boosters. Since WO-2 the encoder
  has no fast path at all, so this now re-measures the same artifact through the
  hand-written encoder.
* ``converter`` is WO-2's inference check (done-criterion 3): reload the same
  artifact through the legacy-layout converter, evaluate retrieval only, and hold
  the result to the recorded run ``528b1451…`` — warm recall@500 to four
  decimals, cold exactly. It loads only what retrieval needs (no item-item fit,
  no feature index, no boosters), records the per-user top-500 lists as
  evidence, and raises after recording if either check fails, so a miss is
  documented before it stops the work.

No model or ranker is trained and no existing run or artifact is modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd

from src.config import Settings
from src.data.split import TemporalSplit, temporal_split
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    EvalResult,
    evaluate,
    per_user_recall_document,
)
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec_artifact import ENCODER_IMPL_HAND_WRITTEN
from src.models.ranker.lgbm import LGBMRanker
from src.training import protocol_manifest
from src.training.candidate_data import INPUT_DIR_ENV_VAR, load_inputs
from src.training.sasrec import (
    MODEL_TYPE,
    _configuration_id,
    retrieval_diagnostics,
)
from src.training.sasrec_ranker import (
    SasrecSource,
    SharedInputs,
    load_pinned_sasrec,
    prepare_shared,
    resolve_artifact_dir,
)
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
from synthetic.cold_start import harness as synth_cold
from synthetic.cold_start.config import COHORT_PARQUET_PATH

logger = logging.getLogger(__name__)

RETRIEVAL_TRACKING_URI_ENV_VAR = "SASREC_FASTPATH_RETRIEVAL_TRACKING_URI"
EVIDENCE_DIR_ENV_VAR = "SASREC_FASTPATH_EVIDENCE_DIR"
DEFAULT_EVIDENCE_DIR = Path("artifacts/sasrec/fastpath-reevaluation")
ORIGINAL_SASREC_RUN_ID = "a11af5ed0f0745f68572407237cfa4b9"
ORIGINAL_BUNDLE_RUN_ID = "566f5309767a4076a4f5e8151be16645"
EXPECTED_PROTOCOL_HASH = "sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28"

SCOPE_ENV_VAR = "SASREC_RECHECK_SCOPE"
SCOPE_W17 = "w17"
SCOPE_CONVERTER = "converter"
# The run every later SASRec v1 number cites, and its two recall values exactly
# as MLflow recorded them (docs/experiments/sasrec/fastpath-reevaluation-2026-09-05.json).
RECORDED_RETRIEVAL_RUN_ID = "528b14513d9a49e098a0525417f23285"
RECORDED_WARM_RECALL = 0.5091713455402272
RECORDED_COLD_RECALL = 0.5262729520330651
TOP_K_LISTS_ARTIFACT = "top500-lists.json"


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


def reproduction_verdict(warm_recall: float, cold_recall: float) -> dict[str, Any]:
    """Hold a re-measured v1 retrieval to WO-2's done-criterion 3.

    Warm must equal the recorded value to four decimals: the converted model
    computes the same function with different float rounding, so a handful of
    near-tied candidates at rank 500 may trade places, but nothing more. Cold
    must be exact, because a cold user is answered by the popularity fallback and
    never reaches the encoder — any cold difference is a defect, not rounding.
    """
    warm_delta = warm_recall - RECORDED_WARM_RECALL
    cold_delta = cold_recall - RECORDED_COLD_RECALL
    warm_matches = round(warm_recall, 4) == round(RECORDED_WARM_RECALL, 4)
    cold_exact = cold_recall == RECORDED_COLD_RECALL
    return {
        "recorded_run_id": RECORDED_RETRIEVAL_RUN_ID,
        "recorded_warm_recall": RECORDED_WARM_RECALL,
        "recorded_cold_recall": RECORDED_COLD_RECALL,
        "warm_recall": warm_recall,
        "cold_recall": cold_recall,
        "warm_delta": warm_delta,
        "cold_delta": cold_delta,
        "warm_matches_to_4_decimals": warm_matches,
        "cold_exact": cold_exact,
        "passed": warm_matches and cold_exact,
    }


def top_k_lists_document(recommendations: Mapping[int, list[int]]) -> dict[str, Any]:
    """Every user's retrieved list, keyed by user id, with a digest over all of them.

    Kept so a later run — or the legacy-encoder comparison in
    ``tests/unit/test_sasrec_transformer.py`` — can say exactly how many users'
    lists changed, rather than inferring it from per-user recall, which cannot see
    a reordering or a swap between two misses.
    """
    lists = {str(user_id): list(items) for user_id, items in sorted(recommendations.items())}
    canonical = json.dumps(lists, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(canonical).hexdigest(), "lists": lists}


def count_changed_lists(
    first: Mapping[int, list[int]], second: Mapping[int, list[int]]
) -> dict[str, int]:
    """How many users' lists differ in membership, and how many only in order."""
    if set(first) != set(second):
        raise ValueError("the two runs retrieved for different users")
    membership = sum(set(first[user]) != set(second[user]) for user in first)
    any_change = sum(first[user] != second[user] for user in first)
    return {
        "n_users": len(first),
        "n_lists_changed": any_change,
        "n_membership_changed": membership,
        "n_order_only_changed": any_change - membership,
    }


@dataclass(frozen=True)
class RetrievalInputs:
    """What re-evaluating retrieval needs, and nothing the ranker needs."""

    split: TemporalSplit
    train_frame: pd.DataFrame
    sasrec: SasrecSource
    sample_fraction: float = 1.0


def prepare_retrieval_inputs() -> RetrievalInputs:
    """Load, split, attach the cohort, and reload the pinned artifact — nothing else.

    ``prepare_shared`` also fits item-item, builds the ranker's feature index and
    samples its positives, none of which retrieval reads. The popularity fallback
    is the one piece shared with that path, and it is fitted the same way
    ``ItemItemModel.fit`` fits it — ``PopularityModel().fit`` on the same frame —
    so cold users get the identical answer.
    """
    require_cohort_payload()
    settings = Settings()
    input_dir_raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
    ratings, _movies = load_inputs(
        settings, input_dir=Path(input_dir_raw) if input_dir_raw else None
    )
    split = temporal_split(ratings)
    train_frame, _cohort = synth_cold.prepare(split, logger=logger)
    popularity = PopularityModel().fit(train_frame)
    sasrec = load_pinned_sasrec(resolve_artifact_dir(), train_frame, popularity)
    return RetrievalInputs(split=split, train_frame=train_frame, sasrec=sasrec)


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


def require_cohort_payload(path: Path = COHORT_PARQUET_PATH) -> None:
    """Fail before loading 25M rows when the pinned snapshot is incomplete."""
    if not path.is_file():
        raise FileNotFoundError(
            f"the recheck requires the DVC-pinned cold-start cohort at {path}; "
            "a pointer without its parquet payload changes the protocol snapshot"
        )


def _log_retrieval(
    inputs: SharedInputs | RetrievalInputs,
    *,
    tracking_uri: str,
    evidence_dir: Path,
    scope: str = SCOPE_W17,
) -> tuple[str, EvalResult, dict[str, int], float]:
    model = inputs.sasrec.model
    if model.config.num_blocks != 2 or not model.config.faiss_exact:
        raise RuntimeError("the recheck requires the pinned two-block exact-FAISS artifact")
    protocol = protocol_manifest.build_protocol(
        split=inputs.split,
        fitted_frame=inputs.train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(model.cold_start_threshold),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    if protocol.semantic_hash != EXPECTED_PROTOCOL_HASH:
        logger.error("Recheck resolved protocol: %s", protocol.canonical_json())
        raise RuntimeError(
            f"recheck protocol drift: {protocol.semantic_hash} != {EXPECTED_PROTOCOL_HASH}"
        )

    holdout = inputs.split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = inputs.split.train.groupby("userId").size().to_dict()
    user_ids = list(holdout)
    distribution = warm_history_length_distribution(train_counts, user_ids)
    started = time.perf_counter()
    recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
    recommend_seconds = time.perf_counter() - started
    result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
    short_warm_users = [
        user_id
        for user_id in user_ids
        if COLD_START_THRESHOLD <= int(train_counts.get(user_id, 0)) < 50
    ]
    empty_short_slates = sum(not recommendations[user_id] for user_id in short_warm_users)
    if empty_short_slates:
        raise RuntimeError(f"{empty_short_slates} short-history warm users retrieved nothing")
    diagnostics = retrieval_diagnostics(
        recommendations,
        {
            user_id: targets
            for user_id, targets in holdout.items()
            if model.was_served_by_sasrec(user_id)
        },
        inputs.split.train["movieId"].value_counts().to_dict(),
        catalog_size=len(model._index_to_item),
    )
    verdict = (
        reproduction_verdict(result.warm.recall, result.cold.recall)
        if scope == SCOPE_CONVERTER
        else None
    )

    if scope == SCOPE_CONVERTER:
        run_name = "sasrec-wo2-converter-reevaluation-seed42"
        scope_tags = {
            "sweep_label": "wo2-converter-reevaluation",
            "reproduces_run_id": RECORDED_RETRIEVAL_RUN_ID,
            "encoder_impl": ENCODER_IMPL_HAND_WRITTEN,
            "artifact_encoder_layout": inputs.sasrec.manifest.encoder_impl,
        }
        scope_params: dict[str, Any] = {}
    else:
        run_name = "sasrec-fastpath-fixed-reevaluation-seed42"
        scope_tags = {
            "sweep_label": "fastpath-fixed-reevaluation",
            "supersedes_run_id": ORIGINAL_SASREC_RUN_ID,
            "inference_fix": "disable-pytorch-mha-fastpath-for-left-padding",
        }
        scope_params = {
            "n_pre_fix_empty_warm_slates": distribution["below_50"],
            "n_popularity_routed_warm_users_before_fix": 0,
        }

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    with mlflow.start_run(run_name=run_name) as run:
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
                **scope_tags,
                **inputs.sasrec.identity(),
            }
        )
        mlflow.log_params(
            {
                **envelope.params,
                **model.config.as_params(),
                "user_sample_fraction": inputs.sample_fraction,
                "cutoff_timestamp": inputs.split.cutoff,
                "n_train_rows": len(inputs.split.train),
                "n_holdout_rows": len(inputs.split.holdout),
                "k_candidates": K_CANDIDATES,
                "configuration_id": _configuration_id(model.config),
                "recommend_seconds": round(recommend_seconds, 3),
                **scope_params,
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
                "n_empty_short_warm_slates": empty_short_slates,
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
        if verdict is not None:
            lists = top_k_lists_document(recommendations)
            mlflow.log_metrics(
                {
                    "warm_recall_delta_vs_recorded": verdict["warm_delta"],
                    "cold_recall_delta_vs_recorded": verdict["cold_delta"],
                }
            )
            mlflow.set_tags(
                {
                    "reproduction_passed": str(verdict["passed"]).lower(),
                    "top500_lists_sha256": lists["sha256"],
                }
            )
            mlflow.log_dict(lists, TOP_K_LISTS_ARTIFACT)
            _write_new(evidence_dir / run_id / TOP_K_LISTS_ARTIFACT, lists)

    _write_new(evidence_dir / run_id / PER_USER_RECALL_ARTIFACT, per_user)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "scope": scope,
        "protocol_hash": protocol.semantic_hash,
        "metrics": _metrics(result),
        "history_length_distribution": distribution,
        "empty_short_warm_slates": empty_short_slates,
        "recommend_seconds": recommend_seconds,
        "diagnostics": diagnostics,
    }
    if verdict is not None:
        summary["artifact_encoder_layout"] = inputs.sasrec.manifest.encoder_impl
        summary["encoder_impl"] = ENCODER_IMPL_HAND_WRITTEN
        summary["reproduction"] = verdict
    else:
        summary["supersedes_run_id"] = ORIGINAL_SASREC_RUN_ID
        summary["pre_fix_empty_warm_slates"] = distribution["below_50"]
        summary["popularity_routed_warm_users_before_fix"] = 0
    _write_new(evidence_dir / run_id / "retrieval-summary.json", summary)
    if verdict is not None and not verdict["passed"]:
        # Recorded first, then stopped: a miss is a finding the owner decides on
        # (WO-2 done-criterion 3), and it must exist on disk and in MLflow before
        # anything else happens.
        raise RuntimeError(f"WO-2 reproduction check failed: {verdict}")
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
    scope = os.environ.get(SCOPE_ENV_VAR, "").strip() or SCOPE_W17
    if scope not in {SCOPE_W17, SCOPE_CONVERTER}:
        raise RuntimeError(f"{SCOPE_ENV_VAR} must be {SCOPE_W17!r} or {SCOPE_CONVERTER!r}")

    require_cohort_payload()
    if scope == SCOPE_CONVERTER:
        retrieval_run, retrieval_result, distribution, retrieval_seconds = _log_retrieval(
            prepare_retrieval_inputs(),
            tracking_uri=retrieval_tracking_uri,
            evidence_dir=evidence_dir,
            scope=SCOPE_CONVERTER,
        )
        logger.info(
            "WO-2 converter recheck complete: retrieval=%s metrics=%s recommend_seconds=%.3f",
            retrieval_run,
            _metrics(retrieval_result),
            retrieval_seconds,
        )
        return

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
