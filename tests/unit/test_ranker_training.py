"""
Unit tests for the ranker training pipeline in ``src/training/ranker.py``.

The pipeline's public shape (``main()``) requires Postgres + MLflow, so
here we exercise ``_build_ranker_training_set`` — the assembly step
whose contract with ``ItemItemModel.recommend`` was silently broken:
positives sampled from train are always in the user's train history, so
the default ``filter_seen=True`` in ``ItemItemModel.recommend`` dropped
100% of them. The regression test drives the real ItemItemModel with a
real FeatureIndex over a small synthetic frame and asserts the training
set comes out non-empty.

The complementary contract test on ``ItemItemModel.recommend`` itself —
seen items return when ``filter_seen=False``, don't return when True —
lives here too so the two halves of the invariant are in one file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features import FeatureIndex
from src.models.candidates.itemitem import ItemItemModel
from src.training.ranker import _build_ranker_training_set, _sample_training_positives


def _tiny_train_frame(seed: int = 0) -> pd.DataFrame:
    """Small dense frame: 20 users × 40 items, ~15 interactions per user.
    Dense enough that KNN finds neighbors, small enough that tests run fast.
    """
    rng = np.random.default_rng(seed)
    n_users, n_items, per_user = 20, 40, 15
    rows = []
    base_ts = 1_500_000_000
    for u in range(n_users):
        items = rng.choice(n_items, size=per_user, replace=False)
        for i, m in enumerate(items):
            rows.append(
                {
                    "userId": int(u),
                    "movieId": int(m),
                    "timestamp": base_ts + u * 1000 + i,
                }
            )
    return pd.DataFrame(rows)


def _tiny_movies_frame(n_items: int = 40) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "movieId": list(range(n_items)),
            "genres": ["Action|Drama"] * n_items,
        }
    )


def test_recommend_filter_seen_true_excludes_seen_items() -> None:
    """Default (serving) behavior: items the user has already interacted
    with in train are excluded from the returned candidates.
    """
    train = _tiny_train_frame()
    model = ItemItemModel().fit(train)

    user_id = int(train["userId"].iloc[0])
    seen = set(train.loc[train["userId"] == user_id, "movieId"].astype(int).tolist())

    recs = model.recommend(user_id, k=10)
    assert set(recs).isdisjoint(seen), "filter_seen=True must exclude items in the user's history"


def test_recommend_filter_seen_false_can_return_seen_items() -> None:
    """When ``filter_seen=False`` the model must be *willing* to return
    seen items. Not every seen item is guaranteed to appear in top-k
    (depends on the KNN scores), so we verify the weaker invariant: at
    least one seen item shows up, which is what the ranker training
    pipeline relies on when the positive is drawn from train.
    """
    train = _tiny_train_frame()
    model = ItemItemModel().fit(train)

    user_id = int(train["userId"].iloc[0])
    seen = set(train.loc[train["userId"] == user_id, "movieId"].astype(int).tolist())

    recs = model.recommend(user_id, k=30, filter_seen=False)
    assert set(recs) & seen, "filter_seen=False must let seen items back into the candidate list"


def test_build_ranker_training_set_is_non_empty_when_positives_are_from_train() -> None:
    """The regression this test guards: positives are sampled from train,
    so they are always in the user's train history. If the pipeline
    calls ``recommend`` with the serving-shape ``filter_seen=True``,
    every positive gets filtered out and the training set is silently
    empty. This test drives the real ItemItemModel + FeatureIndex and
    asserts the assembled training set has at least one group.
    """
    train = _tiny_train_frame()
    movies = _tiny_movies_frame()
    candidate_model = ItemItemModel().fit(train)
    feature_index = FeatureIndex.build(train, movies)

    rng = np.random.default_rng(1)
    positives = _sample_training_positives(train, n_days=365, limit=15, rng=rng)
    assert len(positives) > 0  # sanity check on the fixture

    features_df, group_sizes, labels = _build_ranker_training_set(
        positives=positives,
        candidate_model=candidate_model,
        feature_index=feature_index,
        n_negatives=5,
        rng=rng,
    )

    assert len(group_sizes) > 0, "training set has no groups — positives are being dropped"
    assert sum(group_sizes) == len(features_df) == len(labels)
    # Every group must have exactly one positive; the rest are negatives.
    assert int(labels.sum()) == len(group_sizes)
