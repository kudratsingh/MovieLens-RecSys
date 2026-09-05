"""Tests for the per-route and union bundle arms.

The arms' headline claims are checked at full scale by the runner itself, which
refuses to write a result that does not reproduce step 1. What is worth testing
here is the machinery those refusals depend on: that routing actually sends each
user to the booster it is supposed to, and that the union's duplicate accounting
says what it claims.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.protocol import COLD_START_THRESHOLD
from src.features import FeatureIndex
from src.models.candidates.itemitem import ItemItemModel
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training.ranker import _sample_training_positives
from src.training.sasrec_ranker import ItemItemSource, build_ranker_training_set, history_index
from src.training.sasrec_ranker_bundles import (
    BundleReproductionError,
    duplicate_group_count,
    rank_by_route,
    step1_incumbent_result,
)
from tests.unit.test_sasrec_ranker import _movies, _sasrec_source, _train_frame


def _fitted_ranker(train: pd.DataFrame, seed: int) -> LGBMRanker:
    feature_index = FeatureIndex.build(train, _movies())
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )
    features, groups, labels, _ = build_ranker_training_set(
        positives=positives,
        source=ItemItemSource(model=ItemItemModel().fit(train)),
        feature_index=feature_index,
        history_by_user=history_index(train),
        n_negatives=5,
        rng=np.random.default_rng(seed),
        k_candidates=30,
        batch_size=8,
    )
    return LGBMRanker(config=LGBMRankerConfig(seed=seed, num_boost_round=8)).fit(
        features, groups, labels
    )


def test_each_user_is_ranked_by_the_booster_its_route_selects() -> None:
    """The composition is the claim; this is what makes it one.

    A warm user must be served the warm source's candidates ordered by the warm
    booster, and a cold user the cold source's ordered by the cold booster. Driven
    with two genuinely different boosters so a mix-up cannot pass by coincidence.
    """
    train = _train_frame()
    feature_index = FeatureIndex.build(train, _movies())
    warm_source = _sasrec_source(train, threshold=COLD_START_THRESHOLD)
    cold_source = ItemItemSource(
        model=ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(train)
    )
    warm_ranker = _fitted_ranker(train, seed=1)
    cold_ranker = _fitted_ranker(train, seed=99)

    as_of = int(train["timestamp"].max()) + 1
    users = sorted({int(u) for u in train["userId"].unique()})
    routed = rank_by_route(
        warm_source=warm_source,
        warm_ranker=warm_ranker,
        cold_source=cold_source,
        cold_ranker=cold_ranker,
        feature_index=feature_index,
        user_ids=users,
        as_of_timestamp=as_of,
    )

    # Every fixture user has 15 interactions, so all of them take the warm route.
    assert all(warm_source.served_by_learned_path(u) for u in users)
    for user_id in users:
        candidates = warm_source.holdout_candidates(user_id, 500)
        query = pd.DataFrame(
            {
                "userId": [user_id] * len(candidates),
                "movieId": candidates,
                "as_of_timestamp": [as_of] * len(candidates),
            }
        )
        expected = warm_ranker.rank_candidates(
            {user_id: candidates}, {user_id: feature_index.features_for(query)}, k=10
        )[user_id]
        assert routed[user_id] == expected


def test_a_cold_route_uses_the_cold_booster_and_the_cold_source() -> None:
    train = _train_frame()
    cold_user = 7
    keep = ~((train["userId"] == cold_user) & (train.groupby("userId").cumcount() >= 4))
    thinned = train[keep].reset_index(drop=True)

    feature_index = FeatureIndex.build(thinned, _movies())
    warm_source = _sasrec_source(thinned, threshold=COLD_START_THRESHOLD)
    cold_source = ItemItemSource(
        model=ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(thinned)
    )
    warm_ranker = _fitted_ranker(thinned, seed=1)
    cold_ranker = _fitted_ranker(thinned, seed=99)
    as_of = int(thinned["timestamp"].max()) + 1

    assert not warm_source.served_by_learned_path(cold_user)
    routed = rank_by_route(
        warm_source=warm_source,
        warm_ranker=warm_ranker,
        cold_source=cold_source,
        cold_ranker=cold_ranker,
        feature_index=feature_index,
        user_ids=[cold_user],
        as_of_timestamp=as_of,
    )
    candidates = cold_source.holdout_candidates(cold_user, 500)
    query = pd.DataFrame(
        {
            "userId": [cold_user] * len(candidates),
            "movieId": candidates,
            "as_of_timestamp": [as_of] * len(candidates),
        }
    )
    expected = cold_ranker.rank_candidates(
        {cold_user: candidates}, {cold_user: feature_index.features_for(query)}, k=10
    )[cold_user]
    assert routed[cold_user] == expected
    # And the two boosters really do disagree, or the test above proves nothing.
    other = warm_ranker.rank_candidates(
        {cold_user: candidates}, {cold_user: feature_index.features_for(query)}, k=10
    )[cold_user]
    assert other != expected


def test_duplicate_group_count_sees_a_repeated_group() -> None:
    features = pd.DataFrame(
        np.array(
            [
                [1.0] * 8,
                [2.0] * 8,
                [3.0] * 8,
                [1.0] * 8,
                [2.0] * 8,
                [3.0] * 8,
                [9.0] * 8,
                [8.0] * 8,
                [7.0] * 8,
            ]
        ),
        columns=[f"f{i}" for i in range(8)],
    )
    assert duplicate_group_count(features, [3, 3, 3]) == 1
    # Same rows, different grouping — not the same group.
    assert duplicate_group_count(features, [9]) == 0


def test_duplicate_group_count_is_zero_on_distinct_groups() -> None:
    features = pd.DataFrame(
        np.arange(24, dtype=np.float64).reshape(3, 8), columns=[f"f{i}" for i in range(8)]
    )
    assert duplicate_group_count(features, [1, 1, 1]) == 0


def test_step1_incumbent_result_is_the_shape_the_gate_reads() -> None:
    """A guard on the hand-entered reference: populations and K must be step 1's."""
    reference = step1_incumbent_result()
    assert (reference.n_warm_users, reference.n_cold_users) == (1931, 710)
    assert reference.k == 10
    # Overall is the population-weighted mean of the two slices by construction,
    # so a typo in any one of the three numbers shows up here.
    weighted = (
        reference.n_warm_users * reference.warm.ndcg + reference.n_cold_users * reference.cold.ndcg
    ) / (reference.n_warm_users + reference.n_cold_users)
    assert weighted == pytest.approx(reference.overall.ndcg, rel=1e-12)


def test_reproduction_error_is_its_own_type() -> None:
    """Callers must be able to tell a reproduction failure from any other crash."""
    assert issubclass(BundleReproductionError, RuntimeError)
