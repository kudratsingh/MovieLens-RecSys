from __future__ import annotations

import pandas as pd

from src.features import FEATURE_COLUMNS
from src.models.artifacts import CandidateIndex
from src.training.demo_artifacts import build_ranker_training_data


def test_ranker_training_rows_are_point_in_time_and_candidate_aligned() -> None:
    ratings = pd.DataFrame(
        [
            {"user_id": 30, "item_id": 1, "rating": 4.0, "timestamp": 80},
            {"user_id": 20, "item_id": 1, "rating": 4.0, "timestamp": 90},
            {"user_id": 10, "item_id": 1, "rating": 5.0, "timestamp": 100},
            {"user_id": 20, "item_id": 2, "rating": 5.0, "timestamp": 150},
            {"user_id": 30, "item_id": 3, "rating": 5.0, "timestamp": 180},
            {"user_id": 10, "item_id": 2, "rating": 4.5, "timestamp": 200},
        ]
    )
    movies = pd.DataFrame(
        [
            {"movieId": 1, "genres": "Action"},
            {"movieId": 2, "genres": "Action|Drama"},
            {"movieId": 3, "genres": "Comedy"},
        ]
    )
    index = CandidateIndex.build(
        {10: {1, 2}, 20: {1, 2}, 30: {1, 3}},
        max_neighbors=10,
    )

    features, groups, labels = build_ranker_training_data(
        ratings,
        movies=movies,
        candidate_index=index,
        negatives_per_positive=2,
    )

    assert list(features.columns) == FEATURE_COLUMNS
    assert groups == [2, 2, 2]
    assert labels.tolist() == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    assert features.iloc[0]["user_interaction_count"] == 1.0
    # The first positive is item 2 at t=150; its t=150 event is not allowed
    # to influence its own popularity or the user's pre-event feature count.
    assert features.iloc[0]["item_popularity_all_time"] == 0.0
    assert features.iloc[0]["user_genre_affinity"] == 1.0
    assert features.iloc[1]["user_genre_affinity"] == 0.0
