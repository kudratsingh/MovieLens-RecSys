from __future__ import annotations

from pathlib import Path

import mlflow
import pandas as pd
import pytest

from src.models.candidates.sasrec import SASRecConfig
from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, load_sasrec
from src.training.sasrec import retrieval_diagnostics, run_once
from src.training.twotower import PHASE_2_EXPERIMENT


def test_retrieval_diagnostics_measure_coverage_rank_and_target_reach() -> None:
    diagnostics = retrieval_diagnostics(
        recommendations={1: [10, 30], 2: [20, 30]},
        holdout={1: {10, 20}, 2: {40}},
        item_popularity={10: 100, 20: 50, 30: 25, 40: 5},
        catalog_size=4,
    )

    assert diagnostics == {
        "retrieved_unique_items": 3.0,
        "catalog_coverage": 0.75,
        "mean_retrieved_item_popularity_rank": 2.25,
        "holdout_target_reachability": 1 / 3,
    }


def test_retrieval_diagnostics_handle_empty_policy_slice() -> None:
    diagnostics = retrieval_diagnostics({}, {}, {}, catalog_size=0)

    assert diagnostics == {
        "retrieved_unique_items": 0.0,
        "catalog_coverage": 0.0,
        "mean_retrieved_item_popularity_rank": 0.0,
        "holdout_target_reachability": 0.0,
    }


def test_run_keeps_local_artifact_and_logs_mlflow_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ratings = pd.DataFrame(
        [(user, user * 100 + item, item) for user in range(1, 5) for item in range(8)],
        columns=["userId", "movieId", "timestamp"],
    )
    config = SASRecConfig(
        max_sequence_length=5,
        hidden_dim=8,
        num_blocks=1,
        num_heads=2,
        feedforward_dim=16,
        dropout=0.0,
        negative_count=2,
        batch_size=8,
        epochs=1,
        faiss_exact=True,
    )
    previous_uri = mlflow.get_tracking_uri()
    try:
        # MLflow 3.8 requires an explicit opt-in for its maintenance-mode file
        # backend. This isolated test uses it so the artifact copy has a
        # directly inspectable path and never reaches an external service.
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        run_once(ratings, config, run_label="artifact-test", artifact_root=tmp_path / "durable")
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name(PHASE_2_EXPERIMENT)
        assert experiment is not None
        runs = client.search_runs([experiment.experiment_id])
        assert len(runs) == 1
        run = runs[0]
        durable_manifest = tmp_path / "durable" / run.info.run_id / MANIFEST_FILENAME
        mlflow_manifest = (
            tmp_path
            / "mlruns"
            / experiment.experiment_id
            / run.info.run_id
            / "artifacts"
            / "model"
            / MANIFEST_FILENAME
        )
        assert durable_manifest.is_file()
        assert mlflow_manifest.is_file()
        assert durable_manifest.read_bytes() == mlflow_manifest.read_bytes()
        assert run.data.tags["sasrec_artifact_sha256"]
        load_sasrec(durable_manifest)
        load_sasrec(mlflow_manifest)
    finally:
        mlflow.set_tracking_uri(previous_uri)
