from __future__ import annotations

import pandas as pd
import pytest
import torch

from src.models.candidates.item_features import (
    build_item_feature_matrix,
    fit_item_feature_schema,
)


def _movies() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"movieId": 10, "title": "Older (1990)", "genres": "Drama|Action"},
            {"movieId": 20, "title": "Newer (2010)", "genres": "Comedy"},
            {"movieId": 30, "title": "Unknown year", "genres": "(no genres listed)"},
        ]
    )


def test_schema_is_deterministic_and_serializable() -> None:
    first = fit_item_feature_schema(_movies())
    second = fit_item_feature_schema(_movies().sample(frac=1.0, random_state=9))

    assert first == second
    assert first.genres == ("Action", "Comedy", "Drama")
    assert first.release_year_mean == 2000.0
    assert first.release_year_std == 10.0
    assert first.to_dict()["feature_names"] == list(first.feature_names)


def test_matrix_is_dense_id_aligned_with_zero_padding() -> None:
    movies = _movies()
    schema = fit_item_feature_schema(movies)
    matrix = build_item_feature_matrix(
        movies,
        item_to_index={20: 1, 10: 2, 30: 3, 999: 4},
        schema=schema,
    )

    assert matrix.shape == (5, 6)
    assert torch.equal(matrix[0], torch.zeros(6))
    # Dense id 1 is movie 20: Comedy and a +1 normalized release year.
    assert matrix[1].tolist() == [0.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    # Dense id 2 is movie 10: Action + Drama and a -1 year.
    assert matrix[2].tolist() == [1.0, 0.0, 1.0, 0.0, -1.0, 0.0]
    # Movie 30 and missing catalog movie 999 carry explicit missing bits.
    assert matrix[3].tolist() == [0.0, 0.0, 0.0, 1.0, 0.0, 1.0]
    assert matrix[4].tolist() == [0.0, 0.0, 0.0, 1.0, 0.0, 1.0]


def test_missing_columns_fail_loudly() -> None:
    with pytest.raises(ValueError, match="title"):
        fit_item_feature_schema(pd.DataFrame({"movieId": [1], "genres": ["Drama"]}))


def test_constant_or_absent_years_use_safe_unit_scale() -> None:
    constant = pd.DataFrame([{"movieId": 1, "title": "One (2001)", "genres": "Drama"}])
    missing = pd.DataFrame([{"movieId": 1, "title": "One", "genres": "Drama"}])

    assert fit_item_feature_schema(constant).release_year_std == 1.0
    assert fit_item_feature_schema(missing).release_year_std == 1.0
