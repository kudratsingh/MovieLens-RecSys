"""Immutable two-tower artifact coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import torch

from src.models.candidates.twotower import TwoTowerConfig, TwoTowerModel
from src.models.candidates.twotower_artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    TwoTowerArtifactManifest,
    export_twotower,
    load_twotower,
)


def _train() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (user, 100 + item, float(item % 5 + 1), item)
            for user in range(1, 8)
            for item in range(8)
        ],
        columns=["userId", "movieId", "rating", "timestamp"],
    )


def _movies() -> pd.DataFrame:
    return pd.DataFrame(
        [(100 + item, f"Movie {item} ({2000 + item})", "Drama|Comedy") for item in range(8)],
        columns=["movieId", "title", "genres"],
    )


def _model() -> TwoTowerModel:
    return TwoTowerModel(
        config=TwoTowerConfig(
            embedding_dim=8,
            history_window=3,
            batch_size=16,
            num_sampled=4,
            epochs=1,
            hard_negative_count=0,
            faiss_exact=True,
            seed=42,
        ),
        cold_start_threshold=None,
    ).fit(_train(), movies=_movies())


def test_export_load_preserves_item_vectors_and_candidates(tmp_path: Path) -> None:
    model = _model()
    manifest = export_twotower(model, tmp_path / "run")
    loaded = load_twotower(tmp_path / "run" / MANIFEST_FILENAME)

    assert model._item_tower is not None and loaded._item_tower is not None
    with torch.no_grad():
        ids = torch.arange(1, len(model._index_to_item) + 1)
        assert torch.equal(model._item_tower(ids), loaded._item_tower(ids))
    loaded._user_history = model._user_history
    loaded._popularity = model._popularity
    assert loaded.recommend(1, 3) == model.recommend(1, 3)
    assert manifest == TwoTowerArtifactManifest.load(tmp_path / "run" / MANIFEST_FILENAME)


def test_export_is_byte_deterministic_and_never_overwrites(tmp_path: Path) -> None:
    model = _model()
    first = export_twotower(model, tmp_path / "first")
    second = export_twotower(model, tmp_path / "second")

    assert first.model_sha256 == second.model_sha256
    assert (tmp_path / "first" / MODEL_FILENAME).read_bytes() == (
        tmp_path / "second" / MODEL_FILENAME
    ).read_bytes()
    with pytest.raises(FileExistsError, match="overwrite"):
        export_twotower(model, tmp_path / "first")


def test_manifest_rejects_corrupted_model_bytes(tmp_path: Path) -> None:
    model = _model()
    export_twotower(model, tmp_path)
    model_path = tmp_path / MODEL_FILENAME
    model_path.write_bytes(model_path.read_bytes() + b"corrupt")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_twotower(tmp_path / MANIFEST_FILENAME)


def test_manifest_rejects_a_non_boolean_feature_flag(tmp_path: Path) -> None:
    export_twotower(_model(), tmp_path)
    manifest_path = tmp_path / MANIFEST_FILENAME
    raw = json.loads(manifest_path.read_text())
    raw["item_features_fitted"] = "false"
    manifest_path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="must be a boolean"):
        load_twotower(manifest_path)
