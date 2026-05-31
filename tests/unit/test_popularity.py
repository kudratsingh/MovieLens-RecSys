import pandas as pd

from src.models.candidates.popularity import PopularityModel


def _ratings(rows: list[tuple[int, int]]) -> pd.DataFrame:
    """Minimal ratings shape: just the columns the model reads."""
    return pd.DataFrame(rows, columns=["userId", "movieId"])


def test_fit_ranks_by_descending_popularity() -> None:
    # Movie 10 has 3 ratings, movie 20 has 2, movie 30 has 1.
    train = _ratings(
        [
            (1, 10),
            (2, 10),
            (3, 10),
            (1, 20),
            (2, 20),
            (1, 30),
        ]
    )
    model = PopularityModel().fit(train)
    assert model.ranking == [10, 20, 30]


def test_recommend_excludes_seen_items() -> None:
    # User 1 has rated the top-1 item; their recommendation should skip it.
    train = _ratings(
        [
            (1, 10),
            (2, 10),
            (3, 10),  # 10 is most popular
            (2, 20),
            (3, 20),  # 20 is second
            (2, 30),  # 30 is least
        ]
    )
    model = PopularityModel().fit(train)
    recs = model.recommend(user_id=1, k=2)
    assert recs == [20, 30]
    assert 10 not in recs


def test_recommend_truncates_to_k() -> None:
    train = _ratings([(u, m) for u in range(5) for m in range(20)])
    model = PopularityModel().fit(train)
    recs = model.recommend(user_id=999, k=5)  # unknown user → no exclusions
    assert len(recs) == 5


def test_recommend_returns_fewer_when_catalog_exhausted() -> None:
    # 3-item catalog and the user has seen 2 of them → only 1 unseen item exists.
    train = _ratings([(0, 10), (0, 20), (1, 10), (1, 20), (1, 30), (2, 30)])
    model = PopularityModel().fit(train)
    recs = model.recommend(user_id=1, k=10)
    # User 1 has seen {10, 20, 30}, so nothing to recommend.
    assert recs == []


def test_unknown_user_gets_global_ranking() -> None:
    # New user (no training history) → top-k straight from the ranking.
    # This is the cold-start fallback path specified by ADR 0001.
    train = _ratings([(0, 10), (0, 10), (1, 10), (1, 20), (2, 30)])  # 10 > 20 > 30
    model = PopularityModel().fit(train)
    recs = model.recommend(user_id=999, k=2)
    assert recs == [10, 20]


def test_empty_train_returns_empty_model() -> None:
    model = PopularityModel().fit(_ratings([]))
    assert model.ranking == []
    assert model.user_history == {}
    assert model.recommend(user_id=1, k=10) == []


def test_recommend_for_users_returns_one_list_per_user() -> None:
    train = _ratings([(0, 10), (0, 10), (1, 10), (1, 20), (2, 30)])
    model = PopularityModel().fit(train)
    out = model.recommend_for_users(user_ids=[0, 1, 2, 999], k=2)
    assert set(out.keys()) == {0, 1, 2, 999}
    assert all(len(v) <= 2 for v in out.values())


def test_fit_returns_self_for_chaining() -> None:
    # Lets pipelines do `model = PopularityModel().fit(train)` in one line.
    model = PopularityModel().fit(_ratings([(0, 10)]))
    assert isinstance(model, PopularityModel)
