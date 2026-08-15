from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.features import FEATURE_COLUMNS
from src.models.artifacts import (
    ArtifactRef,
    CandidateIndex,
    ServingArtifactBundle,
    ServingManifest,
    file_sha256,
)
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig


def test_candidate_index_is_deterministic_and_excludes_live_history() -> None:
    histories = {1: {1, 2, 3}, 2: {1, 2, 4}, 3: {1, 4}}

    first = CandidateIndex.build(histories, max_neighbors=10)
    second = CandidateIndex.build(dict(reversed(list(histories.items()))), max_neighbors=10)

    assert first == second
    assert first.retrieve([2], limit=3) == [1, 3, 4]
    assert not set(first.retrieve([1, 4], limit=10)) & {1, 4}


def test_candidate_index_round_trip_is_byte_deterministic(tmp_path: Path) -> None:
    index = CandidateIndex.build({1: {30, 10, 20}, 2: {10, 20}}, max_neighbors=10)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    index.write(first)
    CandidateIndex.load(first).write(second)

    assert first.read_bytes() == second.read_bytes()


def test_manifest_rejects_tampered_artifact(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidates.json"
    ranker_path = tmp_path / "ranker.txt"
    candidate_path.write_text("candidate")
    ranker_path.write_text("ranker")
    manifest = _manifest(candidate_path, ranker_path)
    manifest_path = tmp_path / "manifest.json"
    manifest.write(manifest_path)

    candidate_path.write_text("tampered")

    with pytest.raises(ValueError, match="checksum mismatch"):
        ServingManifest.load(manifest_path)


def test_bundle_loads_candidate_and_ranker_once_from_manifest(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidates.json"
    ranker_path = tmp_path / "ranker.txt"
    CandidateIndex.build({1: {1, 2}, 2: {1, 3}}).write(candidate_path)
    features = pd.DataFrame(np.arange(40, dtype=np.float64).reshape(5, 8), columns=FEATURE_COLUMNS)
    labels = np.array([1, 0, 0, 0, 0], dtype=np.float64)
    LGBMRanker(config=LGBMRankerConfig(num_boost_round=2, min_data_in_leaf=1, seed=0)).fit(
        features, [5], labels
    ).save_model(ranker_path)
    manifest_path = tmp_path / "manifest.json"
    _manifest(candidate_path, ranker_path).write(manifest_path)

    bundle = ServingArtifactBundle.load(manifest_path)

    assert bundle.manifest.tenant_id == "demo"
    assert bundle.candidates.retrieve([1], limit=2) == [2, 3]
    assert bundle.ranker.predict(features).shape == (5,)


def _manifest(candidate_path: Path, ranker_path: Path) -> ServingManifest:
    return ServingManifest(
        tenant_id="demo",
        candidate=ArtifactRef(
            artifact_type="item-item-cosine",
            version="demo-itemitem-v1",
            filename=candidate_path.name,
            sha256=file_sha256(candidate_path),
        ),
        ranker=ArtifactRef(
            artifact_type="lightgbm-lambdarank",
            version="demo-lgbm-v1",
            filename=ranker_path.name,
            sha256=file_sha256(ranker_path),
        ),
        feature_version="feast-phase3-v1",
        trained_at="2026-08-15T00:00:00+00:00",
    )
