"""M2-02: deterministic SASRec save/load/export evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import torch

from src.models.candidates.sasrec import SASRecConfig, SASRecModel
from src.models.candidates.sasrec_artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    SASRecArtifactManifest,
    export_sasrec,
    load_sasrec,
)


def _train() -> pd.DataFrame:
    return pd.DataFrame(
        [(user, 100 + user * 10 + item, item) for user in range(1, 5) for item in range(6)],
        columns=["userId", "movieId", "timestamp"],
    )


def _model() -> SASRecModel:
    return SASRecModel(
        config=SASRecConfig(
            max_sequence_length=5,
            hidden_dim=8,
            num_blocks=1,
            num_heads=2,
            feedforward_dim=16,
            dropout=0.2,
            negative_count=2,
            batch_size=8,
            epochs=1,
            faiss_exact=True,
            seed=42,
        ),
        cold_start_threshold=None,
    ).fit(_train())


def test_export_load_preserves_embeddings_and_candidates(tmp_path: Path) -> None:
    model = _model()
    history = [110, 111, 112]
    excluded = {120}
    expected_embedding = model.encode_movie_history(history)
    expected_candidates = model.recommend_from_history(history, 5, excluded_movie_ids=excluded)

    manifest = export_sasrec(model, tmp_path / "run")
    loaded = load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

    assert torch.equal(loaded.encode_movie_history(history), expected_embedding)
    assert (
        loaded.recommend_from_history(history, 5, excluded_movie_ids=excluded)
        == expected_candidates
    )
    assert not (set(expected_candidates) & (set(history) | excluded))
    assert manifest == SASRecArtifactManifest.load(tmp_path / "run" / MANIFEST_FILENAME)


def test_export_is_byte_deterministic_and_never_overwrites(tmp_path: Path) -> None:
    first_model = _model()
    second_model = _model()
    first = export_sasrec(first_model, tmp_path / "first")
    second = export_sasrec(second_model, tmp_path / "second")

    assert first.model_sha256 == second.model_sha256
    assert (tmp_path / "first" / MODEL_FILENAME).read_bytes() == (
        tmp_path / "second" / MODEL_FILENAME
    ).read_bytes()
    assert (tmp_path / "first" / MANIFEST_FILENAME).read_bytes() == (
        tmp_path / "second" / MANIFEST_FILENAME
    ).read_bytes()
    with pytest.raises(FileExistsError, match="overwrite"):
        export_sasrec(first_model, tmp_path / "first")


def test_manifest_rejects_corrupted_model_bytes(tmp_path: Path) -> None:
    model = _model()
    export_sasrec(model, tmp_path)
    model_path = tmp_path / MODEL_FILENAME
    model_path.write_bytes(model_path.read_bytes() + b"corrupt")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_sasrec(tmp_path / MANIFEST_FILENAME)


def test_manifest_rejects_vocabulary_mismatch(tmp_path: Path) -> None:
    model = _model()
    export_sasrec(model, tmp_path)
    manifest_path = tmp_path / MANIFEST_FILENAME
    raw = json.loads(manifest_path.read_text())
    raw["vocabulary_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="vocabulary fingerprint"):
        load_sasrec(manifest_path)


def test_stateless_boundary_rejects_empty_history(tmp_path: Path) -> None:
    model = _model()
    export_sasrec(model, tmp_path)
    loaded = load_sasrec(tmp_path / MANIFEST_FILENAME)

    with pytest.raises(ValueError, match="at least one"):
        loaded.recommend_from_history([], 5)
