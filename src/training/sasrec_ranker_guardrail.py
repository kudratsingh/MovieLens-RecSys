"""Measure the champion LightGBM ranker on item-item versus SASRec candidates.

The historical full-data ranker runs retained six-decimal metrics and exact
training-set sizes in ``docs/results.md``, but neither weights nor an MLflow
record found in the current project stores. This runner reconstructs the
deterministic seed-42 incumbent,
refuses to use it unless all retained evidence matches, persists the recovered
booster immediately, and then changes only the holdout candidate source. It is
the paired end-to-end guardrail required by ADR 0016.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

from src.config import Settings
from src.data.split import temporal_split
from src.evaluation.gate import promotion_decision
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    EvalResult,
    K,
    evaluate,
)
from src.features import FeatureIndex
from src.models.artifacts import file_sha256
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec import SASRecModel
from src.models.candidates.sasrec_artifact import load_sasrec
from src.models.candidates.twotower import build_user_history
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training.ranker import (
    NEGATIVES_PER_POSITIVE,
    PHASE_2_RANKER_EXPERIMENT,
    RANKER_POSITIVE_LIMIT,
    RANKER_POSITIVE_WINDOW_DAYS,
    _build_ranker_training_set,
    _sample_training_positives,
    _trailing_window,
)
from src.training.twotower import load_inputs
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

REFERENCE_RANKER_RUN_ID = "517fdc75136842e188018ae0a9210c20"
SASREC_RUN_ID = "a11af5ed0f0745f68572407237cfa4b9"
RANKER_SEED = 42
REFERENCE_DECIMAL_PLACES = 6
REFERENCE_POSITIVES = 154_003
REFERENCE_GROUPS = 87_794
REFERENCE_TRAINING_ROWS = 1_843_674
RANKER_FILENAME = "ranker.txt"
RESULT_FILENAME = "paired-ranker-guardrail.json"
DEFAULT_ARTIFACT_ROOT = Path("artifacts") / "sasrec-ranker-guardrail"

REFERENCE_METRICS = {
    "warm_recall_at_k": 0.048867,
    "warm_ndcg_at_k": 0.069967,
    "cold_recall_at_k": 0.077805,
    "cold_ndcg_at_k": 0.544948,
    "overall_recall_at_k": 0.056647,
    "overall_ndcg_at_k": 0.197659,
}


def result_metrics(result: EvalResult) -> dict[str, float]:
    """Return the six stored ranker metrics in their MLflow vocabulary."""
    return {
        "warm_recall_at_k": result.warm.recall,
        "warm_ndcg_at_k": result.warm.ndcg,
        "cold_recall_at_k": result.cold.recall,
        "cold_ndcg_at_k": result.cold.ndcg,
        "overall_recall_at_k": result.overall.recall,
        "overall_ndcg_at_k": result.overall.ndcg,
    }


def require_published_reference(
    result: EvalResult,
    stored_metrics: Mapping[str, float] = REFERENCE_METRICS,
) -> None:
    """Fail unless all six reconstructed metrics match retained precision."""
    missing = sorted(set(REFERENCE_METRICS) - set(stored_metrics))
    if missing:
        raise ValueError(f"reference ranker run is missing metrics {missing}")
    actual = result_metrics(result)
    mismatches = {
        name: {
            "published": f"{float(stored_metrics[name]):.{REFERENCE_DECIMAL_PLACES}f}",
            "reconstructed": f"{value:.{REFERENCE_DECIMAL_PLACES}f}",
        }
        for name, value in actual.items()
        if f"{float(stored_metrics[name]):.{REFERENCE_DECIMAL_PLACES}f}"
        != f"{value:.{REFERENCE_DECIMAL_PLACES}f}"
    }
    if mismatches:
        raise RuntimeError(
            "reconstructed item-item ranker does not reproduce the retained metrics: "
            + json.dumps(mismatches, sort_keys=True)
        )


def require_reference_training_shape(positives: int, groups: int, rows: int) -> None:
    """Fail before fitting if the reconstructed LambdaRank set has drifted."""
    actual = {"positives": positives, "groups": groups, "rows": rows}
    expected = {
        "positives": REFERENCE_POSITIVES,
        "groups": REFERENCE_GROUPS,
        "rows": REFERENCE_TRAINING_ROWS,
    }
    if actual != expected:
        raise RuntimeError(
            "reconstructed ranker training shape does not match the run of record: "
            + json.dumps({"expected": expected, "actual": actual}, sort_keys=True)
        )


def _rank_for_holdout(
    ranker: LGBMRanker,
    candidate_model: ItemItemModel | SASRecModel,
    feature_index: FeatureIndex,
    user_ids: list[int],
    *,
    as_of_timestamp: int,
) -> dict[int, list[int]]:
    candidates_by_user: dict[int, list[int]] = {}
    features_by_user: dict[int, pd.DataFrame] = {}
    for user_id in user_ids:
        candidates = candidate_model.recommend(user_id, K_CANDIDATES)
        candidates_by_user[user_id] = candidates
        if not candidates:
            continue
        features_by_user[user_id] = feature_index.features_for(
            pd.DataFrame(
                {
                    "userId": [user_id] * len(candidates),
                    "movieId": candidates,
                    "as_of_timestamp": [as_of_timestamp] * len(candidates),
                }
            )
        )
    return ranker.rank_candidates(candidates_by_user, features_by_user, k=K)


def _result_document(result: EvalResult) -> dict[str, object]:
    return {
        "k": result.k,
        "n_warm_users": result.n_warm_users,
        "n_cold_users": result.n_cold_users,
        "metrics": result_metrics(result),
    }


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite existing evidence: {path}") from None


def _copy_new(source: Path, destination: Path) -> None:
    """Copy a recovered artifact without permitting replacement."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite recovered artifact: {destination}") from None


def run_guardrail(
    ratings: pd.DataFrame,
    movies: pd.DataFrame,
    *,
    sasrec_manifest: Path,
    cohort_path: Path,
    artifact_root: Path,
    recovered_ranker: Path | None = None,
    recovered_ranker_sha256: str | None = None,
) -> str:
    """Recover the ranker artifact, reproduce it, and measure both systems."""
    if (recovered_ranker is None) != (recovered_ranker_sha256 is None):
        raise ValueError("recovered_ranker and recovered_ranker_sha256 must be supplied together")
    if recovered_ranker is not None:
        actual_sha256 = file_sha256(recovered_ranker)
        if actual_sha256 != recovered_ranker_sha256:
            raise ValueError(
                "recovered ranker checksum mismatch: "
                f"expected {recovered_ranker_sha256}, got {actual_sha256}"
            )

    split = temporal_split(ratings)
    train_frame, cohort = synth_cold.prepare(split, logger=logger, path=cohort_path)
    if cohort is None:
        raise FileNotFoundError(f"the versioned cold-start cohort is required: {cohort_path}")

    positive_limit = RANKER_POSITIVE_LIMIT
    window_rows = len(_trailing_window(split.train, RANKER_POSITIVE_WINDOW_DAYS))
    if window_rows > positive_limit:
        raise RuntimeError(
            f"ranker positive limit {positive_limit} binds a {window_rows}-row window; "
            "the recovered ranker would not be the full-window incumbent"
        )

    mlflow.set_experiment(PHASE_2_RANKER_EXPERIMENT)
    with mlflow.start_run(run_name="sasrec-paired-ranker-guardrail-seed42") as active:
        run_id = str(active.info.run_id)
        mlflow.set_tags(
            {
                "model_family": "ranker_guardrail",
                "model_type": "lgbm_lambdarank",
                "candidate_model_train": "itemitem_cosine",
                "candidate_model_challenger": "sasrec",
                "reference_ranker_run_id": REFERENCE_RANKER_RUN_ID,
                "reference_source": "docs/results.md",
                "reference_precision": "six-decimal-published",
                "sasrec_run_id": SASREC_RUN_ID,
                "stage": "paired_ranker_guardrail",
                "train_seed": str(RANKER_SEED),
                "artifact_write_policy": "create-only",
                "ranker_reused_after_upload_failure": str(recovered_ranker is not None).lower(),
            }
        )
        mlflow.log_params(
            {
                "k_final": K,
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "ranker_positive_window_days": RANKER_POSITIVE_WINDOW_DAYS,
                "ranker_positive_window_rows": window_rows,
                "ranker_positive_limit": positive_limit,
                "negatives_per_positive": NEGATIVES_PER_POSITIVE,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
            }
        )

        started = time.perf_counter()
        itemitem = ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(train_frame)
        itemitem_fit_seconds = time.perf_counter() - started

        started = time.perf_counter()
        feature_index = FeatureIndex.build(train_frame, movies)
        feature_build_seconds = time.perf_counter() - started

        if recovered_ranker is None:
            rng = np.random.default_rng(RANKER_SEED)
            positives = _sample_training_positives(
                split.train,
                n_days=RANKER_POSITIVE_WINDOW_DAYS,
                limit=positive_limit,
                rng=rng,
            )
            started = time.perf_counter()
            features, groups, labels = _build_ranker_training_set(
                positives=positives,
                candidate_model=itemitem,
                feature_index=feature_index,
                # The run of record predates #126. Its training negatives did
                # not exclude strictly-prior history, so an empty history is
                # required to reconstruct that booster rather than today's
                # trainer default.
                training_history=train_frame.iloc[0:0],
                n_negatives=NEGATIVES_PER_POSITIVE,
                rng=rng,
            )
            training_set_seconds = time.perf_counter() - started
            n_positives = len(positives)
            n_groups = len(groups)
            n_training_rows = sum(groups)
            require_reference_training_shape(n_positives, n_groups, n_training_rows)

            ranker = LGBMRanker(LGBMRankerConfig(seed=RANKER_SEED))
            started = time.perf_counter()
            ranker.fit(features, groups, labels)
            ranker_fit_seconds = time.perf_counter() - started
        else:
            # The first attempt persisted the booster before its MLflow copy
            # failed. Reuse those checksum-pinned bytes; retraining would be a
            # different recovery attempt and needlessly spend another cycle.
            ranker = LGBMRanker.load_model(
                recovered_ranker,
                config=LGBMRankerConfig(seed=RANKER_SEED),
            )
            n_positives = REFERENCE_POSITIVES
            n_groups = REFERENCE_GROUPS
            n_training_rows = REFERENCE_TRAINING_ROWS
            training_set_seconds = 0.0
            ranker_fit_seconds = 0.0

        # Shape evidence must survive even if a later artifact transport or
        # evaluation step fails. MLflow params are immutable once written.
        mlflow.log_params(
            {
                "n_positives_sampled": n_positives,
                "n_ranker_positives_used": n_groups,
                "n_ranker_training_rows": n_training_rows,
            }
        )

        # Persist before evaluation: a later failure still leaves the exact
        # reconstructed booster recoverable and associated with this run.
        artifact_dir = artifact_root / run_id
        ranker_path = artifact_dir / RANKER_FILENAME
        if ranker_path.exists():
            raise FileExistsError(f"refusing to overwrite ranker artifact: {ranker_path}")
        if recovered_ranker is None:
            ranker.save_model(ranker_path)
        else:
            _copy_new(recovered_ranker, ranker_path)
        ranker_sha256 = file_sha256(ranker_path)
        mlflow.log_artifact(str(ranker_path), artifact_path="model")
        mlflow.set_tag("ranker_artifact_sha256", ranker_sha256)

        sasrec: SASRecModel = load_sasrec(sasrec_manifest)
        sasrec._popularity = PopularityModel().fit(train_frame)
        sasrec._user_history = build_user_history(train_frame, sasrec._item_to_index)

        holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
        train_counts = split.train.groupby("userId").size().to_dict()
        user_ids = list(holdout)

        started = time.perf_counter()
        incumbent_recommendations = _rank_for_holdout(
            ranker,
            itemitem,
            feature_index,
            user_ids,
            as_of_timestamp=split.cutoff,
        )
        incumbent_rank_seconds = time.perf_counter() - started
        incumbent_result = evaluate(incumbent_recommendations, holdout, train_counts, k=K)
        require_published_reference(incumbent_result)

        started = time.perf_counter()
        candidate_recommendations = _rank_for_holdout(
            ranker,
            sasrec,
            feature_index,
            user_ids,
            as_of_timestamp=split.cutoff,
        )
        candidate_rank_seconds = time.perf_counter() - started
        candidate_result = evaluate(candidate_recommendations, holdout, train_counts, k=K)
        gate = promotion_decision(candidate_result, incumbent_result)

        mlflow.log_params(
            {
                "itemitem_fit_seconds": round(itemitem_fit_seconds, 3),
                "feature_build_seconds": round(feature_build_seconds, 3),
                "ranker_training_set_seconds": round(training_set_seconds, 3),
                "ranker_fit_seconds": round(ranker_fit_seconds, 3),
                "incumbent_rank_seconds": round(incumbent_rank_seconds, 3),
                "candidate_rank_seconds": round(candidate_rank_seconds, 3),
            }
        )
        mlflow.log_metrics(
            {
                **{
                    f"incumbent_{name}": value
                    for name, value in result_metrics(incumbent_result).items()
                },
                **{
                    f"sasrec_{name}": value
                    for name, value in result_metrics(candidate_result).items()
                },
                "n_warm_users": float(candidate_result.n_warm_users),
                "n_cold_users": float(candidate_result.n_cold_users),
            }
        )
        mlflow.set_tag("paired_ranker_gate_promote", str(gate.promote).lower())

        document: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_id,
            "reference_ranker_run_id": REFERENCE_RANKER_RUN_ID,
            "reference_source": "docs/results.md",
            "reference_decimal_places": REFERENCE_DECIMAL_PLACES,
            "sasrec_run_id": SASREC_RUN_ID,
            "ranker_artifact": {
                "filename": RANKER_FILENAME,
                "sha256": ranker_sha256,
            },
            "ranker_training_shape": {
                "positives": n_positives,
                "groups": n_groups,
                "rows": n_training_rows,
            },
            "recovered_ranker_source_run_id": (
                recovered_ranker.parent.name if recovered_ranker is not None else None
            ),
            "incumbent_reproduced_to_published_precision": True,
            "incumbent": _result_document(incumbent_result),
            "sasrec": _result_document(candidate_result),
            "gate": gate.to_dict(),
        }
        result_path = artifact_dir / RESULT_FILENAME
        _write_new_json(result_path, document)
        mlflow.log_artifact(str(result_path))
        logger.info("%s", gate.summary())
        logger.info("Retained paired guardrail at %s", result_path)
        return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.training.sasrec_ranker_guardrail",
        description=(
            "Recover the full ranker artifact and compare item-item with SASRec candidates."
        ),
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--sasrec-manifest", required=True, type=Path)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--tracking-uri", required=True)
    parser.add_argument("--recovered-ranker", type=Path)
    parser.add_argument("--recovered-ranker-sha256")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mlflow.set_tracking_uri(args.tracking_uri)
    ratings, movies = load_inputs(Settings(), input_dir=args.input_dir)
    try:
        run_id = run_guardrail(
            ratings,
            movies,
            sasrec_manifest=args.sasrec_manifest,
            cohort_path=args.cohort,
            artifact_root=args.artifact_root,
            recovered_ranker=args.recovered_ranker,
            recovered_ranker_sha256=args.recovered_ranker_sha256,
        )
    except (OSError, RuntimeError, ValueError, mlflow.exceptions.MlflowException):
        logger.exception("SASRec paired-ranker guardrail failed")
        return 2
    logger.info("Paired-ranker MLflow run: %s", run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
