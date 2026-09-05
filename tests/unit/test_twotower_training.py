"""Training-runner boundaries for the two-tower candidate model."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import Settings
from src.models.candidates.twotower import TwoTowerConfig
from src.training.twotower import _configuration_id, load_inputs


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
