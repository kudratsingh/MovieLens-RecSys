"""Deterministic structured item features for two-tower v2 (ADR 0015)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch

_MISSING_GENRES = "(no genres listed)"
_YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")


def _genres(raw: object) -> tuple[str, ...]:
    value = str(raw) if raw is not None else ""
    if not value or value == _MISSING_GENRES or value == "nan":
        return ()
    return tuple(sorted(set(value.split("|"))))


def _release_year(title: object) -> int | None:
    match = _YEAR_PATTERN.search(str(title))
    return int(match.group(1)) if match is not None else None


@dataclass(frozen=True)
class ItemFeatureSchema:
    """Fitted preprocessing values that travel with the model artifact."""

    genres: tuple[str, ...]
    release_year_mean: float
    release_year_std: float

    @property
    def feature_names(self) -> tuple[str, ...]:
        return (
            *(f"genre:{genre}" for genre in self.genres),
            "genre:missing",
            "release_year:normalized",
            "release_year:missing",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "genres": list(self.genres),
            "release_year_mean": self.release_year_mean,
            "release_year_std": self.release_year_std,
            "feature_names": list(self.feature_names),
        }


def fit_item_feature_schema(movies: pd.DataFrame) -> ItemFeatureSchema:
    """Fit vocabulary and year scaling from the supplied training catalog."""
    required = {"movieId", "title", "genres"}
    missing = required - set(movies.columns)
    if missing:
        raise ValueError(f"movies is missing required columns: {sorted(missing)}")

    genre_vocabulary = tuple(sorted({genre for raw in movies["genres"] for genre in _genres(raw)}))
    years = [year for title in movies["title"] if (year := _release_year(title)) is not None]
    if years:
        mean = float(np.mean(years))
        std = float(np.std(years))
        if not math.isfinite(std) or std == 0.0:
            std = 1.0
    else:
        mean, std = 0.0, 1.0
    return ItemFeatureSchema(
        genres=genre_vocabulary,
        release_year_mean=mean,
        release_year_std=std,
    )


def build_item_feature_matrix(
    movies: pd.DataFrame,
    *,
    item_to_index: dict[int, int],
    schema: ItemFeatureSchema,
) -> torch.Tensor:
    """Return features aligned to dense ids, including zero padding row 0."""
    width = len(schema.feature_names)
    matrix = np.zeros((max(item_to_index.values(), default=0) + 1, width), dtype=np.float32)
    genre_position = {genre: index for index, genre in enumerate(schema.genres)}
    missing_genre_position = len(schema.genres)
    normalized_year_position = missing_genre_position + 1
    missing_year_position = normalized_year_position + 1

    rows_by_movie = movies.drop_duplicates("movieId", keep="last").set_index("movieId")
    for movie_id, dense_id in item_to_index.items():
        if movie_id not in rows_by_movie.index:
            matrix[dense_id, missing_genre_position] = 1.0
            matrix[dense_id, missing_year_position] = 1.0
            continue
        row = rows_by_movie.loc[movie_id]
        genres = _genres(row["genres"])
        if genres:
            for genre in genres:
                position = genre_position.get(genre)
                if position is not None:
                    matrix[dense_id, position] = 1.0
        else:
            matrix[dense_id, missing_genre_position] = 1.0

        year = _release_year(row["title"])
        if year is None:
            matrix[dense_id, missing_year_position] = 1.0
        else:
            matrix[dense_id, normalized_year_position] = (
                float(year) - schema.release_year_mean
            ) / schema.release_year_std

    return torch.from_numpy(matrix)
