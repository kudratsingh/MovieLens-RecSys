"""Training-runner boundaries for the two-tower candidate model."""

from __future__ import annotations

import json
from pathlib import Path

import mlflow
import pandas as pd
import pytest

from src.config import Settings
from src.models.candidates.twotower import TwoTowerConfig
from src.models.candidates.twotower_artifact import MANIFEST_FILENAME, load_twotower
from src.training.twotower import (
    PHASE_2_EXPERIMENT,
    _configuration_id,
    load_inputs,
    resolve_artifact_dir,
    run_once,
)


def test_configuration_identity_excludes_only_training_seed() -> None:
    baseline = TwoTowerConfig(seed=7)
    assert _configuration_id(baseline) == _configuration_id(TwoTowerConfig(seed=42))
    assert _configuration_id(baseline) != _configuration_id(
        TwoTowerConfig(seed=7, embedding_dim=baseline.embedding_dim + 1)
    )


def test_load_inputs_can_use_local_movielens_csvs(tmp_path: Path) -> None:
    pd.DataFrame(
        {
            "userId": [1],
            "movieId": [10],
            "rating": [4.0],
            "timestamp": [100],
        }
    ).to_csv(tmp_path / "ratings.csv", index=False)
    pd.DataFrame(
        {
            "movieId": [10],
            "title": ["Example (2000)"],
            "genres": ["Drama"],
        }
    ).to_csv(tmp_path / "movies.csv", index=False)

    ratings, movies = load_inputs(Settings(), input_dir=tmp_path)

    assert ratings.to_dict("records") == [
        {"userId": 1, "movieId": 10, "rating": 4.0, "timestamp": 100}
    ]
    assert movies.to_dict("records") == [
        {"movieId": 10, "title": "Example (2000)", "genres": "Drama"}
    ]


def test_artifact_root_is_configurable_and_defaults_to_a_durable_path() -> None:
    assert resolve_artifact_dir({}) == Path("artifacts/twotower")
    assert resolve_artifact_dir({"TWOTOWER_ARTIFACT_DIR": "/tmp/run-artifacts"}) == Path(
        "/tmp/run-artifacts"
    )


def test_run_keeps_local_model_and_mlflow_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ratings = pd.DataFrame(
        [(user, user * 100 + item, 4.0, item) for user in range(1, 5) for item in range(8)],
        columns=["userId", "movieId", "rating", "timestamp"],
    )
    movies = pd.DataFrame(
        [
            (user * 100 + item, f"Movie {user}-{item} (2000)", "Drama")
            for user in range(1, 5)
            for item in range(8)
        ],
        columns=["movieId", "title", "genres"],
    )
    config = TwoTowerConfig(
        embedding_dim=8,
        history_window=3,
        batch_size=8,
        num_sampled=2,
        epochs=1,
        hard_negative_count=0,
        faiss_exact=True,
        seed=42,
    )
    previous_uri = mlflow.get_tracking_uri()
    try:
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        run_once(
            ratings,
            movies,
            config,
            sample_fraction=0.99,
            run_label="artifact-test",
            artifact_root=tmp_path / "durable",
        )
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name(PHASE_2_EXPERIMENT)
        assert experiment is not None
        runs = client.search_runs([experiment.experiment_id])
        assert len(runs) == 1
        run = runs[0]
        local_manifest = tmp_path / "durable" / run.info.run_id / MANIFEST_FILENAME
        mlflow_manifest = (
            tmp_path
            / "mlruns"
            / experiment.experiment_id
            / run.info.run_id
            / "artifacts"
            / "model"
            / MANIFEST_FILENAME
        )
        recall_artifact = (
            tmp_path
            / "mlruns"
            / experiment.experiment_id
            / run.info.run_id
            / "artifacts"
            / "per_user_recall.json"
        )
        assert local_manifest.read_bytes() == mlflow_manifest.read_bytes()
        assert json.loads(recall_artifact.read_text())["run_id"] == run.info.run_id
        assert run.data.tags["twotower_artifact_sha256"]
        load_twotower(local_manifest)
        load_twotower(mlflow_manifest)
    finally:
        mlflow.set_tracking_uri(previous_uri)
