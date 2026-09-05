from __future__ import annotations

import json
from pathlib import Path

import mlflow
import pandas as pd
import pytest

import src.training.sasrec as sasrec_training
from src.models.candidates.sasrec import SASRecConfig
from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, load_sasrec
from src.training.sasrec import _configuration_id, retrieval_diagnostics, run_once
from src.training.twotower import PHASE_2_EXPERIMENT, subsample_users


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


def test_configuration_identity_excludes_only_training_seed() -> None:
    baseline = SASRecConfig(seed=7)
    assert _configuration_id(baseline) == _configuration_id(SASRecConfig(seed=42))
    assert _configuration_id(baseline) != _configuration_id(
        SASRecConfig(seed=7, negative_count=baseline.negative_count + 1)
    )


def test_run_keeps_local_artifact_and_logs_mlflow_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ratings = pd.DataFrame(
        [(user, user * 100 + item, 4.0, item) for user in range(1, 5) for item in range(8)],
        columns=["userId", "movieId", "rating", "timestamp"],
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
        run_once(
            ratings,
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
        recall_artifact = (
            tmp_path
            / "mlruns"
            / experiment.experiment_id
            / run.info.run_id
            / "artifacts"
            / "per_user_recall.json"
        )
        assert durable_manifest.is_file()
        assert mlflow_manifest.is_file()
        assert recall_artifact.is_file()
        assert durable_manifest.read_bytes() == mlflow_manifest.read_bytes()
        assert run.data.tags["sasrec_artifact_sha256"]
        assert run.data.tags["evaluation_protocol"]
        assert run.data.params["evaluation_protocol_hash"]
        assert run.data.params["train_seed"] == str(config.seed)
        assert run.data.metrics["n_warm_users"] + run.data.metrics["n_cold_users"] == 4
        recall_document = json.loads(recall_artifact.read_text())
        assert recall_document["run_id"] == run.info.run_id
        # 0.99 is the fraction this run used, and the id must say so: before the
        # sample entered it, this passed against the full-data id and a surrogate
        # was indistinguishable from the gate configuration.
        assert recall_document["configuration_id"] == _configuration_id(
            config, sample_fraction=0.99
        )
        assert recall_document["configuration_id"] != _configuration_id(config)
        assert recall_document["protocol"]["k"] == 500
        assert len(recall_document["per_user_recall"]["overall"]) == 4
        load_sasrec(durable_manifest)
        load_sasrec(mlflow_manifest)
    finally:
        mlflow.set_tracking_uri(previous_uri)


def test_the_subsample_population_does_not_move_with_the_training_seed() -> None:
    """A seed sweep must vary the model's randomness, not the users it is scored on.

    Drawing the subsample from the training seed made a pilot-scale seed sweep
    meaningless: every seed kept a different 6% of users, so the observed spread
    mixed training stochasticity with sample variation, and the tolerance study
    refused such runs outright because their slice populations disagreed. This
    pins the separation that makes the noise study possible at all.
    """
    ratings = pd.DataFrame(
        {
            "userId": [u for u in range(1, 41) for _ in range(3)],
            "movieId": [1 + (u * 7 + i) % 25 for u in range(1, 41) for i in range(3)],
            "rating": [4.0] * 120,
            "timestamp": [1_000 + u * 10 + i for u in range(1, 41) for i in range(3)],
        }
    )

    populations = {
        seed: set(subsample_users(ratings, 0.25, sasrec_training.SUBSAMPLE_SEED)["userId"].unique())
        for seed in (42, 7, 13)
    }

    assert (
        len({frozenset(p) for p in populations.values()}) == 1
    ), "every training seed must score the identical subsample population"
    assert 0 < len(populations[42]) < ratings["userId"].nunique()


def test_the_subsample_seed_reproduces_every_run_already_measured() -> None:
    """42 is not an arbitrary constant — it is what every prior run actually used.

    Before the split, the subsample seed *was* the training seed, and every
    subsampled run to date trained at seed 42. Pinning the constant to 42 means
    those runs reproduce byte for byte, so this fix invalidates nothing that has
    already been recorded.
    """
    assert sasrec_training.SUBSAMPLE_SEED == 42


def test_a_surrogate_and_the_gate_configuration_cannot_share_an_id() -> None:
    """The distinction the tolerance study needs to tell derivation A from B.

    A 6% run and a full-data run share every model hyper-parameter, so before the
    sample entered this id they hashed identically — and a study declaring
    `surrogate_delta` was refused as self-contradictory, which made the surrogate
    route in the tolerance protocol unusable end to end for any model.
    """
    config = SASRecConfig(
        max_sequence_length=8,
        hidden_dim=8,
        num_blocks=1,
        num_heads=2,
        feedforward_dim=16,
        dropout=0.0,
        negative_count=2,
        batch_size=4,
        epochs=1,
        faiss_exact=True,
        seed=42,
    )

    full = sasrec_training._configuration_id(config, sample_fraction=1.0)
    surrogate = sasrec_training._configuration_id(config, sample_fraction=0.06)

    assert full != surrogate
    assert full == sasrec_training._configuration_id(config)


def test_the_training_seed_still_does_not_move_the_configuration_id() -> None:
    """A seed sweep is several draws of one experiment, not several experiments.

    This is the other direction of the same contract, and it is what lets the
    study aggregate seeds at all. Pinned because widening the id to cover the
    sample is exactly the change that could break it by accident.
    """
    base = dict(
        max_sequence_length=8,
        hidden_dim=8,
        num_blocks=1,
        num_heads=2,
        feedforward_dim=16,
        dropout=0.0,
        negative_count=2,
        batch_size=4,
        epochs=1,
        faiss_exact=True,
    )
    ids = {
        sasrec_training._configuration_id(SASRecConfig(**base, seed=seed), sample_fraction=0.06)
        for seed in (7, 13, 21)
    }

    assert len(ids) == 1
