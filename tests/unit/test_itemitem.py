"""
Unit tests for the item-item CF baseline.

Cosine item-item is deterministic given the data (no learned parameters,
no random initialization), so unlike the ALS tests we can in principle
assert exact orderings — but the production-relevant contract is the same
shape CFModel exposes: returns valid catalog items, filters seen items,
falls through to popularity for cold users, and the predicate matches the
recommend() routing. Tests below mirror test_cf.py one-to-one so the two
candidate models are held to the same bar.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.candidates.itemitem import ItemItemModel


def _ratings(rows: list[tuple[int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["userId", "movieId"])


# Same synthetic train set as test_cf.py — eight users with overlapping
# tastes across an "action" and a "drama" cluster, plus cross-genre users.
# Holding both models to the same fixture keeps their behavior directly
# comparable when reading test failures.
# Every user in the fixture below has three interactions, which is under
# ADR 0001's `COLD_START_THRESHOLD` — the constructor default since the owner's
# 2026-08-30 decision. A model built with the default answers every one of them
# from its popularity fallback, so these tests pass `cold_start_threshold=None`,
# the documented index-membership opt-out, and keep measuring retrieval rather
# than the fallback in front of it. Where the *default* sends this fixture is
# asserted by the routing test below, and exhaustively in
# `tests/unit/test_candidate_routing.py`.
_SYNTHETIC_TRAIN = _ratings(
    [
        (1, 100),
        (1, 101),
        (1, 102),
        (2, 100),
        (2, 101),
        (2, 103),
        (3, 100),
        (3, 102),
        (3, 104),
        (4, 200),
        (4, 201),
        (4, 202),
        (5, 200),
        (5, 201),
        (5, 203),
        (6, 200),
        (6, 202),
        (6, 204),
        (7, 100),
        (7, 200),
        (8, 101),
        (8, 201),
    ]
)


def test_fit_returns_self_for_chaining() -> None:
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert isinstance(model, ItemItemModel)


def test_recommendations_are_valid_movie_ids() -> None:
    # Same invariant as CF: every returned id must be one that existed in
    # train. Catches index→id mapping bugs that would surface as KeyErrors
    # at serving time.
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    catalog = set(_SYNTHETIC_TRAIN["movieId"].unique())
    recs = model.recommend(user_id=1, k=5)
    assert all(item in catalog for item in recs)


def test_recommendations_exclude_already_seen_items() -> None:
    # User 1's history is {100, 101, 102}; the post-filter must hide them.
    # Same leak-prevention guarantee CFModel has — a user can't be told to
    # watch what they already watched.
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    seen = {100, 101, 102}
    recs = model.recommend(user_id=1, k=10)
    assert not (set(recs) & seen)


def test_returns_at_most_k_items() -> None:
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=3)
    assert len(recs) <= 3


def test_unknown_user_falls_through_to_popularity() -> None:
    # ADR 0001 fallback path. User 999 was never in train; the recommender
    # must still return something, and it should match what the embedded
    # popularity model would return.
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    ii_recs = model.recommend(user_id=999, k=3)
    pop_recs = model._popularity.recommend(user_id=999, k=3)
    assert ii_recs == pop_recs
    assert len(ii_recs) > 0


def test_empty_train_handles_gracefully() -> None:
    # temporal_split could in principle return an empty train slice on
    # edge-case data. The model shouldn't crash — it should quietly
    # produce no recommendations.
    model = ItemItemModel(cold_start_threshold=None).fit(_ratings([]))
    assert model.recommend(user_id=1, k=10) == []


def test_recommend_for_users_returns_one_list_per_user() -> None:
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    out = model.recommend_for_users(user_ids=[1, 2, 999], k=3)
    assert set(out.keys()) == {1, 2, 999}
    assert all(len(v) <= 3 for v in out.values())


def test_was_served_by_itemitem_matches_recommend_routing() -> None:
    # The predicate is the contract the training pipeline relies on for
    # per-policy MLflow metrics. It must mirror the exact branch in
    # recommend(), under whichever policy the model was built with.
    index_model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert index_model.was_served_by_itemitem(1) is True
    assert index_model.was_served_by_itemitem(999) is False

    # And under the shipped default, this fixture's three-interaction users are
    # below the threshold, so the fallback answers them and the predicate says
    # so rather than reporting a learned serve the model did not make.
    default_model = ItemItemModel().fit(_SYNTHETIC_TRAIN)
    assert default_model.was_served_by_itemitem(1) is False
    assert default_model.recommend(user_id=1, k=3) == default_model._popularity.recommend(
        user_id=1, k=3
    )


def test_was_served_by_itemitem_false_for_empty_train() -> None:
    # No KNN index means every user routes to the popularity fallback,
    # so the predicate must return False even for an id the caller
    # might think is "known."
    model = ItemItemModel(cold_start_threshold=None).fit(_ratings([]))
    assert model.was_served_by_itemitem(1) is False


def test_determinism() -> None:
    # Item-item has no learned parameters, no random initialization.
    # Two models fit on the same data must produce identical
    # recommendations — if not, something is non-deterministic and we
    # want it surfaced loudly.
    a = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    b = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert a.recommend(user_id=1, k=5) == b.recommend(user_id=1, k=5)


def test_action_user_recommendations_lean_action() -> None:
    # User 1 is an "action fan" (history = {100, 101, 102}). Other action
    # items in the synthetic catalog are {103, 104}. Item-item should
    # surface those before the drama items {200..204}, because the
    # co-occurrence signal from users 2 and 3 connects 103 and 104 to
    # user 1's history. This is the only behavioral test — the rest are
    # contract tests — and it's the property that makes item-item worth
    # building at all.
    model = ItemItemModel(cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=4)
    action_items = {103, 104}
    drama_items = {200, 201, 202, 203, 204}
    # At least one action item in the top results before any drama item.
    # We don't lock the exact order — cosine over a 22-row matrix is
    # sensitive enough that strict ordering is brittle.
    assert action_items & set(recs)
    first_action = next((i for i, r in enumerate(recs) if r in action_items), len(recs))
    first_drama = next((i for i, r in enumerate(recs) if r in drama_items), len(recs))
    assert first_action < first_drama


def test_the_subsample_matches_the_control_trainers_default_seed() -> None:
    """A pilot-sized incumbent must see the same users as the candidate it is compared to.

    The tolerance study refuses a study whose incumbent and study runs score
    different populations, so a 6% candidate cannot be met by a full-data
    incumbent. Sharing `DEFAULT_SAMPLE_SEED` with the last-item control is what
    makes a pilot-sized comparison well posed at all.
    """
    from src.training import itemitem as itemitem_training
    from src.training import last_item as last_item_training

    assert itemitem_training.DEFAULT_SAMPLE_SEED == last_item_training.DEFAULT_SAMPLE_SEED


def test_sample_fraction_defaults_to_the_whole_dataset_and_rejects_nonsense() -> None:
    from src.training import itemitem as itemitem_training

    assert itemitem_training.resolve_sample_fraction({}) == 1.0
    assert (
        itemitem_training.resolve_sample_fraction({"ITEMITEM_USER_SAMPLE_FRACTION": "0.06"}) == 0.06
    )
    for bad in ("0", "-0.1", "1.5"):
        with pytest.raises(ValueError, match="ITEMITEM_USER_SAMPLE_FRACTION"):
            itemitem_training.resolve_sample_fraction({"ITEMITEM_USER_SAMPLE_FRACTION": bad})
