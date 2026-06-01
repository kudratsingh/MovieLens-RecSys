"""
Unit tests for the CF baseline.

ALS is stochastic — we set a random_state for determinism but still avoid
asserting exact rankings (the property we care about is "model returns
valid recommendations that respect the contract," not "user 1 gets exactly
movie X first"). The contract tests below survive any seed / hyperparam
change as long as the model behaves correctly.
"""

from __future__ import annotations

import pandas as pd

from src.models.candidates.cf import CFModel


def _ratings(rows: list[tuple[int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["userId", "movieId"])


# Eight users with overlapping tastes — enough signal for ALS to converge
# meaningfully on a small synthetic dataset.
_SYNTHETIC_TRAIN = _ratings(
    [
        # "action fans" rate the action canon
        (1, 100),
        (1, 101),
        (1, 102),
        (2, 100),
        (2, 101),
        (2, 103),
        (3, 100),
        (3, 102),
        (3, 104),
        # "drama fans" rate the drama canon
        (4, 200),
        (4, 201),
        (4, 202),
        (5, 200),
        (5, 201),
        (5, 203),
        (6, 200),
        (6, 202),
        (6, 204),
        # cross-genre users
        (7, 100),
        (7, 200),
        (8, 101),
        (8, 201),
    ]
)


def test_fit_returns_self_for_chaining() -> None:
    model = CFModel(iterations=2).fit(_SYNTHETIC_TRAIN)
    assert isinstance(model, CFModel)


def test_recommendations_are_valid_movie_ids() -> None:
    # Every returned id must be one that existed in train — the model can't
    # invent items. Catches index→id mapping bugs that would surface as
    # KeyErrors at serving time.
    model = CFModel(iterations=5).fit(_SYNTHETIC_TRAIN)
    catalog = set(_SYNTHETIC_TRAIN["movieId"].unique())
    recs = model.recommend(user_id=1, k=5)
    assert all(item in catalog for item in recs)


def test_recommendations_exclude_already_seen_items() -> None:
    # User 1's history is {100, 101, 102}; the recommender's filter must hide them.
    # This is the leak-prevention guarantee for CF: a user can't be told to watch
    # what they already watched.
    model = CFModel(iterations=5).fit(_SYNTHETIC_TRAIN)
    seen = {100, 101, 102}
    recs = model.recommend(user_id=1, k=10)
    assert not (set(recs) & seen)


def test_returns_at_most_k_items() -> None:
    model = CFModel(iterations=5).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=3)
    assert len(recs) <= 3


def test_unknown_user_falls_through_to_popularity() -> None:
    # ADR 0001 fallback path. User 999 was never in train; the recommender
    # must still return something, and it should match what the embedded
    # popularity model would return for an unknown user.
    model = CFModel(iterations=5).fit(_SYNTHETIC_TRAIN)
    cf_recs = model.recommend(user_id=999, k=3)
    pop_recs = model._popularity.recommend(user_id=999, k=3)
    assert cf_recs == pop_recs
    assert len(cf_recs) > 0  # fallback actually has items to return


def test_empty_train_handles_gracefully() -> None:
    # Operator footgun: temporal_split could in principle return an empty
    # train slice on weird data. The model shouldn't crash — it should
    # quietly produce no recommendations.
    model = CFModel(iterations=5).fit(_ratings([]))
    assert model.recommend(user_id=1, k=10) == []


def test_recommend_for_users_returns_one_list_per_user() -> None:
    model = CFModel(iterations=5).fit(_SYNTHETIC_TRAIN)
    out = model.recommend_for_users(user_ids=[1, 2, 999], k=3)
    assert set(out.keys()) == {1, 2, 999}
    assert all(len(v) <= 3 for v in out.values())


def test_determinism_with_fixed_random_state() -> None:
    # Two models with the same seed + hyperparams on the same data should
    # produce the same recommendations. If this breaks, something is
    # picking up entropy outside our control — surface it loudly.
    a = CFModel(iterations=5, random_state=42).fit(_SYNTHETIC_TRAIN)
    b = CFModel(iterations=5, random_state=42).fit(_SYNTHETIC_TRAIN)
    assert a.recommend(user_id=1, k=5) == b.recommend(user_id=1, k=5)
