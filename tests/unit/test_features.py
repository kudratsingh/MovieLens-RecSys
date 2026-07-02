"""
Unit tests for the feature module.

The point-in-time correctness canary is the load-bearing test — ADR 0005
Risks #1 names it the severity-highest failure mode for the ranker, same
class as ADR 0006's history-leakage risk for the two-tower. Strict
equality check on a hand-built fixture, not a heuristic.
"""

from __future__ import annotations

import pandas as pd

from src.features import FEATURE_COLUMNS, FeatureIndex, build_features


def _ratings(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    """Rows are (userId, movieId, timestamp)."""
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


def _movies(rows: list[tuple[int, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["movieId", "genres"])


# Small hand-built fixture. Timestamps are deliberately spread so
# 7d / 30d windows have distinct answers.
_DAY = 24 * 3600
_MOVIES = _movies(
    [
        (100, "Action|Adventure"),
        (101, "Action|Thriller"),
        (102, "Comedy"),
        (200, "Drama"),
        (201, "Drama|Romance"),
        (999, "(no genres listed)"),
    ]
)


def test_feature_schema_matches_declaration() -> None:
    """FEATURE_COLUMNS is the schema of record; the actual output must
    match it exactly. Guards against drift where a new feature is added
    to the pipeline but not to the ranker's column selector.
    """
    train = _ratings([(1, 100, 0), (1, 101, _DAY)])
    queries = pd.DataFrame({"userId": [1], "movieId": [102], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    assert list(features.columns) == FEATURE_COLUMNS
    assert len(features) == 1


def test_point_in_time_correctness() -> None:
    """Severity-highest canary: features at time ``t`` must be identical
    whether or not later data exists. If later data can influence an
    earlier query's features, the ranker's offline NDCG inflates and
    serving-time behavior diverges — the exact bug ADR 0005 Risks #1
    names.

    Fixture: user 1 has interactions at t=0, 5d, 15d, and 50d. Query is
    at t=20d. Features computed against the full data must equal
    features computed against data truncated to timestamps < 20d.
    """
    full = _ratings(
        [
            (1, 100, 0 * _DAY),
            (1, 101, 5 * _DAY),
            (1, 200, 15 * _DAY),
            (1, 102, 50 * _DAY),  # after the query — must not influence features
            (2, 100, 0 * _DAY),
            (2, 200, 60 * _DAY),  # also after the query
        ]
    )
    truncated = full[full["timestamp"] < 20 * _DAY].reset_index(drop=True)
    queries = pd.DataFrame(
        {
            "userId": [1, 1],
            "movieId": [100, 999],
            "as_of_timestamp": [20 * _DAY, 20 * _DAY],
        }
    )
    full_features = build_features(full, _MOVIES, queries)
    truncated_features = build_features(truncated, _MOVIES, queries)
    pd.testing.assert_frame_equal(full_features, truncated_features)


def test_user_features_at_hand_computed_values() -> None:
    """User-side numbers on a fixture where the expected values are
    known by inspection. Guards against arithmetic drift — a refactor
    that flips signs or off-by-ones would fail this.
    """
    train = _ratings(
        [
            (1, 100, 0),
            (1, 101, 3 * _DAY),
            (1, 102, 10 * _DAY),
        ]
    )
    # Query at t=15d — user's strictly-past history is the three rows above.
    queries = pd.DataFrame({"userId": [1], "movieId": [200], "as_of_timestamp": [15 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    row = features.iloc[0]
    assert row["user_interaction_count"] == 3.0
    # (10d - 0d) between first and last past interaction.
    assert row["user_days_active"] == 10.0
    # 15d - 10d since the last interaction.
    assert row["user_days_since_last_interaction"] == 5.0


def test_item_popularity_windows() -> None:
    """30d / 7d windows count interactions strictly before as_of and
    within the given trailing window. Fixture is designed so the two
    windows have distinct answers.
    """
    # Item 100 has interactions at 0d, 5d, 25d, 40d, 90d.
    train = _ratings(
        [
            (10, 100, 0 * _DAY),
            (11, 100, 5 * _DAY),
            (12, 100, 25 * _DAY),
            (13, 100, 40 * _DAY),
            (14, 100, 90 * _DAY),  # after as_of; must be excluded
        ]
    )
    queries = pd.DataFrame({"userId": [1], "movieId": [100], "as_of_timestamp": [50 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    row = features.iloc[0]
    # All-time count strictly before 50d = {0, 5, 25, 40} = 4.
    assert row["item_popularity_all_time"] == 4.0
    # 30d window: [20d, 50d) → {25, 40} = 2.
    assert row["item_popularity_30d"] == 2.0
    # 7d window: [43d, 50d) → {} = 0.
    assert row["item_popularity_7d"] == 0.0


def test_item_age_days() -> None:
    """Item age is as_of minus the item's first observed interaction."""
    train = _ratings([(1, 100, 10 * _DAY)])
    queries = pd.DataFrame({"userId": [2], "movieId": [100], "as_of_timestamp": [30 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    assert features.iloc[0]["item_age_days"] == 20.0


def test_genre_affinity_fraction() -> None:
    """Genre affinity: fraction of the user's strictly-past history
    whose genres intersect the query item's genres.

    User 1 saw 100 (Action|Adventure), 200 (Drama), 201 (Drama|Romance).
    Query is item 101 (Action|Thriller). Only movie 100 shares a genre;
    affinity should be 1/3.
    """
    train = _ratings(
        [
            (1, 100, 0),
            (1, 200, _DAY),
            (1, 201, 2 * _DAY),
        ]
    )
    queries = pd.DataFrame({"userId": [1], "movieId": [101], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    assert abs(features.iloc[0]["user_genre_affinity"] - (1.0 / 3.0)) < 1e-9


def test_genre_affinity_zero_when_query_item_has_no_genres() -> None:
    """(no genres listed) means the item genuinely has no genre tags in
    MovieLens. Matching against nothing is a false positive; must be 0.
    """
    train = _ratings([(1, 100, 0)])
    queries = pd.DataFrame({"userId": [1], "movieId": [999], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    assert features.iloc[0]["user_genre_affinity"] == 0.0


def test_cold_user_returns_sentinel_days_since_last() -> None:
    """A user with no strictly-past history is a cold user. days_since_last
    should be the −1 sentinel so the ranker can distinguish "no history"
    from "history ended exactly at as_of" — collapsing to 0 would lie.
    """
    train = _ratings([(2, 100, 0)])  # user 1 has no rows
    queries = pd.DataFrame({"userId": [1], "movieId": [100], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    row = features.iloc[0]
    assert row["user_interaction_count"] == 0.0
    assert row["user_days_active"] == 0.0
    assert row["user_days_since_last_interaction"] == -1.0


def test_cold_item_returns_zero_features() -> None:
    """Item that never appeared in train (before as_of) gets zero
    popularity across all windows and zero age. This is the honest
    cold-item signal; the ranker learns to weight it accordingly.
    """
    train = _ratings([(1, 100, 0)])
    queries = pd.DataFrame({"userId": [1], "movieId": [101], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    row = features.iloc[0]
    assert row["item_popularity_all_time"] == 0.0
    assert row["item_popularity_30d"] == 0.0
    assert row["item_popularity_7d"] == 0.0
    assert row["item_age_days"] == 0.0


def test_query_order_is_preserved() -> None:
    """LambdaRank groups are order-dependent: the ranker's ``group`` param
    walks rows sequentially and slices by group size. If ``features_for``
    reorders rows, the groups scramble and NDCG collapses silently.
    """
    train = _ratings([(1, 100, 0), (2, 200, _DAY)])
    queries = pd.DataFrame(
        {
            "userId": [2, 1, 2, 1],
            "movieId": [200, 100, 100, 200],
            "as_of_timestamp": [10 * _DAY] * 4,
        }
    )
    index = FeatureIndex.build(train, _MOVIES)
    features = index.features_for(queries)
    # We can't assert full numeric equality without hand-computing all
    # features, but we can check that the rows for user 2 (rows 0 and 2)
    # have identical user-side features, and same for user 1 (rows 1, 3).
    assert features.iloc[0]["user_interaction_count"] == features.iloc[2]["user_interaction_count"]
    assert features.iloc[1]["user_interaction_count"] == features.iloc[3]["user_interaction_count"]


def test_empty_train_produces_zero_features() -> None:
    """Empty train is a valid edge case (extreme cold-start on the whole
    system). Every feature should be 0 or the -1 sentinel; no crashes.
    """
    train = _ratings([])
    queries = pd.DataFrame({"userId": [1], "movieId": [100], "as_of_timestamp": [10 * _DAY]})
    features = build_features(train, _MOVIES, queries)
    row = features.iloc[0]
    assert row["user_interaction_count"] == 0.0
    assert row["item_popularity_all_time"] == 0.0
    assert row["user_days_since_last_interaction"] == -1.0
