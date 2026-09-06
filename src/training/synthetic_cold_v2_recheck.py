"""Inference-only SASRec and per-route-bundle check on cold cohort v2."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import mlflow

from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, EvalResult, K, evaluate
from src.models.ranker.lgbm import LGBMRanker
from src.training import protocol_manifest
from src.training.sasrec_ranker import PHASE_2_RANKER_EXPERIMENT, SharedInputs, prepare_shared
from src.training.sasrec_ranker_bundles import (
    STEP1_CHALLENGER_RUN,
    STEP1_INCUMBENT_RUN,
    _file_sha256,
    rank_by_route,
)
from src.training.twotower import PHASE_2_EXPERIMENT
from synthetic.cold_start.load import SyntheticColdCohort
from synthetic.cold_start.sequence import (
    SEQUENCE_COHORT_PARQUET_PATH,
    SEQUENCE_COHORT_VERSION,
    load_sequence_cohort,
    sequence_metrics,
    sequence_params,
)

logger = logging.getLogger(__name__)

EVIDENCE_DIR_ENV_VAR = "SYNTH_COLD_V2_EVIDENCE_DIR"
DEFAULT_EVIDENCE_DIR = Path("artifacts/synthetic-cold-v2")


def _write_new(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _slice_document(result: EvalResult) -> dict[str, dict[str, float | int | None]]:
    return {
        f"h{history_size}": {
            "recall": bucket.metrics.recall,
            "ndcg": bucket.metrics.ndcg,
            "n_users": bucket.n_users,
            "n_fallback_served": bucket.n_fallback_served,
        }
        for history_size, bucket in sorted(result.synthetic_cold_slices.items())
    }


def _log_diagnostic(
    *,
    shared: SharedInputs,
    cohort: SyntheticColdCohort,
    result: EvalResult,
    stage: Literal["retrieval", "ranking"],
    k: int,
    suffix: str,
    seconds: float,
    tracking_uri: str,
    extra_tags: dict[str, str],
    extra_params: dict[str, str | int | float],
) -> tuple[str, str]:
    protocol = protocol_manifest.build_protocol(
        split=shared.split,
        fitted_frame=shared.train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(
            shared.sasrec.model.cold_start_threshold
        ),
        stage=stage,
        k=k,
    )
    envelope = protocol_manifest.run_envelope(
        protocol, deterministic=False, seed=shared.sasrec.model.config.seed
    )
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(PHASE_2_EXPERIMENT if stage == "retrieval" else PHASE_2_RANKER_EXPERIMENT)
    with mlflow.start_run(run_name=f"synthetic-cold-v2-{stage}-inference") as run:
        mlflow.set_tags(
            {
                **envelope.tags,
                "model_family": "diagnostic",
                "model_type": "sasrec" if stage == "retrieval" else "sasrec_per_route_bundle",
                "stage": stage,
                "synthetic_cold_version": SEQUENCE_COHORT_VERSION,
                "synth_sequence_cold_routing_ok": str(
                    all(
                        bucket.n_fallback_served
                        == (bucket.n_users if bucket.history_size < COLD_START_THRESHOLD else 0)
                        for bucket in result.synthetic_cold_slices.values()
                    )
                ).lower(),
                "training_performed": "false",
                **shared.sasrec.identity(),
                **extra_tags,
            }
        )
        mlflow.log_params(
            {
                **envelope.params,
                **sequence_params(cohort),
                "k": k,
                "inference_seconds": round(seconds, 3),
                **extra_params,
            }
        )
        mlflow.log_metrics(sequence_metrics(result, suffix=suffix))
        return run.info.run_id, protocol.semantic_hash


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.perf_counter()
    cohort = load_sequence_cohort(SEQUENCE_COHORT_PARQUET_PATH)
    shared = prepare_shared(additional_cohort=cohort)
    tracking_uri = mlflow.get_tracking_uri()
    # Settings is resolved by prepare_shared, but MLflow's module-global URI is
    # already set there. Capture it once so both diagnostic runs share it.

    retrieval_started = time.perf_counter()
    retrieval_recommendations = shared.sasrec.model.recommend_for_users(
        list(cohort.user_ids), K_CANDIDATES
    )
    retrieval_seconds = time.perf_counter() - retrieval_started
    retrieval_result = evaluate(
        retrieval_recommendations,
        {},
        {},
        k=K_CANDIDATES,
        synthetic_cold_users=cohort.targets_by_bucket,
        synthetic_cold_served_by=shared.sasrec.served_by_learned_path,
    )
    retrieval_run, retrieval_protocol = _log_diagnostic(
        shared=shared,
        cohort=cohort,
        result=retrieval_result,
        stage="retrieval",
        k=K_CANDIDATES,
        suffix="at_k_candidates",
        seconds=retrieval_seconds,
        tracking_uri=tracking_uri,
        extra_tags={},
        extra_params={},
    )

    incumbent_path = shared.booster_root / STEP1_INCUMBENT_RUN / "ranker.txt"
    challenger_path = shared.booster_root / STEP1_CHALLENGER_RUN / "ranker.txt"
    incumbent = LGBMRanker.load_model(incumbent_path)
    challenger = LGBMRanker.load_model(challenger_path)
    bundle_started = time.perf_counter()
    bundle_recommendations = rank_by_route(
        warm_source=shared.sasrec,
        warm_ranker=challenger,
        cold_source=shared.itemitem,
        cold_ranker=incumbent,
        feature_index=shared.feature_index,
        user_ids=list(cohort.user_ids),
        as_of_timestamp=shared.split.cutoff,
    )
    bundle_seconds = time.perf_counter() - bundle_started
    bundle_result = evaluate(
        bundle_recommendations,
        {},
        {},
        k=K,
        synthetic_cold_users=cohort.targets_by_bucket,
        synthetic_cold_served_by=shared.sasrec.served_by_learned_path,
    )
    bundle_run, bundle_protocol = _log_diagnostic(
        shared=shared,
        cohort=cohort,
        result=bundle_result,
        stage="ranking",
        k=K,
        suffix="at_k",
        seconds=bundle_seconds,
        tracking_uri=tracking_uri,
        extra_tags={"bundle_composition": "warm=sasrec+challenger;cold=popularity+incumbent"},
        extra_params={
            "warm_booster_run_id": STEP1_CHALLENGER_RUN,
            "warm_booster_sha256": _file_sha256(challenger_path),
            "cold_booster_run_id": STEP1_INCUMBENT_RUN,
            "cold_booster_sha256": _file_sha256(incumbent_path),
        },
    )

    document = {
        "schema_version": 1,
        "cohort_version": SEQUENCE_COHORT_VERSION,
        "cohort_fingerprint": cohort.provenance.fingerprint,
        "retrieval": {
            "run_id": retrieval_run,
            "protocol_hash": retrieval_protocol,
            "seconds": retrieval_seconds,
            "slices": _slice_document(retrieval_result),
        },
        "bundle": {
            "run_id": bundle_run,
            "protocol_hash": bundle_protocol,
            "seconds": bundle_seconds,
            "slices": _slice_document(bundle_result),
        },
        "total_seconds": time.perf_counter() - started,
    }
    evidence_dir = Path(os.environ.get(EVIDENCE_DIR_ENV_VAR, str(DEFAULT_EVIDENCE_DIR)))
    _write_new(evidence_dir / f"{retrieval_run}-{bundle_run}.json", document)
    logger.info("Sequence cohort v2 diagnostic: %s", json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
