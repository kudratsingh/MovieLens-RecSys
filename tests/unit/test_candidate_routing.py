"""
Unit tests for the opt-in cold-start routing switch.

Two things are under test and they are not the same thing:

  1. `src/models/candidates/routing.py` — the policy vocabulary. A typo in
     `SYNTH_COLD_ROUTING` must raise rather than quietly produce a run
     labelled with a policy it did not use.
  2. The three learned candidate models — that `cold_start_threshold=None`
     is bit-for-bit the behaviour on `main`, that an int moves the boundary,
     and that `was_served_by_*` and `recommend` cannot disagree about where
     it is. The last one is the load-bearing invariant: every per-policy
     metric in MLflow and every ADR 0011 bucket count is computed from the
     predicate, and would be a fiction if `recommend` routed differently.

The fixture is deliberately unbalanced — user 1 has five distinct items and
user 2 has one — because five is `COLD_START_THRESHOLD` and one is the
history size ADR 0011's h1 bucket measured the divergence at.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.evaluation.protocol import COLD_START_THRESHOLD
from src.models.candidates import routing
from src.models.candidates.cf import CFModel
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.twotower import TwoTowerConfig, TwoTowerModel

# (userId, movieId, timestamp). User 1 clears ADR 0001's threshold with five
# distinct items; user 2 has a single interaction — in every model's index,
# below the threshold. Users 3-6 exist to give the item-item and ALS
# factorizations something to co-occur over.
_ROWS = [
    (1, 100, 10),
    (1, 101, 20),
    (1, 102, 30),
    (1, 103, 40),
    (1, 104, 50),
    (2, 100, 11),
    (3, 100, 12),
    (3, 101, 22),
    (3, 102, 32),
    (3, 200, 42),
    (4, 200, 13),
    (4, 201, 23),
    (4, 202, 33),
    (4, 100, 43),
    (5, 200, 14),
    (5, 201, 24),
    (5, 203, 34),
    (5, 101, 44),
    (6, 202, 15),
    (6, 203, 25),
    (6, 204, 35),
    (6, 102, 45),
]

_TRAIN = pd.DataFrame(_ROWS, columns=["userId", "movieId", "timestamp"])

_ABOVE_THRESHOLD_USER = 1
_BELOW_THRESHOLD_USER = 2
_UNKNOWN_USER = 999

# Small enough to train in CI; the point is routing, not recall quality.
_FAST_TWOTOWER = TwoTowerConfig(
    embedding_dim=16,
    history_window=5,
    batch_size=8,
    num_sampled=16,
    epochs=1,
    learning_rate=1e-2,
    faiss_nlist=4,
    faiss_nprobe=2,
    seed=42,
)


# --- The policy vocabulary --------------------------------------------------


def test_unset_env_is_the_index_membership_default() -> None:
    assert routing.resolve_policy({}) == routing.POLICY_INDEX
    assert routing.resolve_policy({routing.ROUTING_ENV_VAR: ""}) == routing.POLICY_INDEX
    assert routing.resolve_policy({routing.ROUTING_ENV_VAR: "  "}) == routing.POLICY_INDEX


def test_policies_are_read_case_insensitively() -> None:
    assert (
        routing.resolve_policy({routing.ROUTING_ENV_VAR: "THRESHOLD"}) == routing.POLICY_THRESHOLD
    )
    assert routing.resolve_policy({routing.ROUTING_ENV_VAR: " Index "}) == routing.POLICY_INDEX


def test_unknown_policy_raises_rather_than_defaulting() -> None:
    # A typo that silently produced the default would label the run with a
    # policy it did not run under — the one failure this switch must not have.
    with pytest.raises(routing.UnknownRoutingPolicyError):
        routing.resolve_policy({routing.ROUTING_ENV_VAR: "thresholdd"})


def test_threshold_translates_to_a_constructor_value() -> None:
    assert routing.cold_start_threshold_for(routing.POLICY_INDEX, 5) is None
    assert routing.cold_start_threshold_for(routing.POLICY_THRESHOLD, 5) == 5


def test_learned_path_serves_truth_table() -> None:
    # None: index membership is the whole rule, so an in-index user always
    # takes the learned path however short their history.
    assert routing.learned_path_serves(history_size=1, cold_start_threshold=None) is True
    assert routing.learned_path_serves(history_size=0, cold_start_threshold=None) is True
    # An int: at or above serves, below falls back.
    assert routing.learned_path_serves(history_size=5, cold_start_threshold=5) is True
    assert routing.learned_path_serves(history_size=6, cold_start_threshold=5) is True
    assert routing.learned_path_serves(history_size=4, cold_start_threshold=5) is False
    assert routing.learned_path_serves(history_size=0, cold_start_threshold=5) is False


def test_default_policy_keeps_the_run_name_results_md_cites() -> None:
    assert routing.run_name_for("itemitem-cosine", routing.POLICY_INDEX) == "itemitem-cosine"
    assert (
        routing.run_name_for("itemitem-cosine", routing.POLICY_THRESHOLD)
        == "itemitem-cosine-threshold-routing"
    )


# --- The models -------------------------------------------------------------


def test_itemitem_default_serves_a_single_interaction_user_from_the_index() -> None:
    model = ItemItemModel().fit(_TRAIN)
    assert model.was_served_by_itemitem(_BELOW_THRESHOLD_USER) is True
    assert model.was_served_by_itemitem(_ABOVE_THRESHOLD_USER) is True
    assert model.was_served_by_itemitem(_UNKNOWN_USER) is False


def test_itemitem_threshold_sends_a_single_interaction_user_to_the_fallback() -> None:
    model = ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(_TRAIN)
    assert model.was_served_by_itemitem(_BELOW_THRESHOLD_USER) is False
    assert model.was_served_by_itemitem(_ABOVE_THRESHOLD_USER) is True


def test_itemitem_recommend_agrees_with_its_own_predicate() -> None:
    threshold_model = ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(_TRAIN)
    popularity_list = threshold_model._popularity.recommend(_BELOW_THRESHOLD_USER, k=3)
    assert threshold_model.recommend(_BELOW_THRESHOLD_USER, k=3) == popularity_list

    # And the default policy does not hand that user the popularity list —
    # otherwise the test above would pass for the wrong reason.
    index_model = ItemItemModel().fit(_TRAIN)
    assert index_model.recommend(_BELOW_THRESHOLD_USER, k=3) != popularity_list


def test_cf_default_serves_a_single_interaction_user_from_the_index() -> None:
    model = CFModel(iterations=5).fit(_TRAIN)
    assert model.was_served_by_als(_BELOW_THRESHOLD_USER) is True
    assert model.was_served_by_als(_UNKNOWN_USER) is False


def test_cf_threshold_sends_a_single_interaction_user_to_the_fallback() -> None:
    model = CFModel(iterations=5, cold_start_threshold=COLD_START_THRESHOLD).fit(_TRAIN)
    assert model.was_served_by_als(_BELOW_THRESHOLD_USER) is False
    assert model.was_served_by_als(_ABOVE_THRESHOLD_USER) is True
    assert model.recommend(_BELOW_THRESHOLD_USER, k=3) == model._popularity.recommend(
        _BELOW_THRESHOLD_USER, k=3
    )


def test_twotower_default_serves_a_single_interaction_user_from_the_index() -> None:
    model = TwoTowerModel(config=_FAST_TWOTOWER).fit(_TRAIN)
    assert model.was_served_by_twotower(_BELOW_THRESHOLD_USER) is True
    assert model.was_served_by_twotower(_UNKNOWN_USER) is False


def test_twotower_threshold_sends_a_single_interaction_user_to_the_fallback() -> None:
    model = TwoTowerModel(config=_FAST_TWOTOWER, cold_start_threshold=COLD_START_THRESHOLD).fit(
        _TRAIN
    )
    assert model.was_served_by_twotower(_BELOW_THRESHOLD_USER) is False
    assert model.was_served_by_twotower(_ABOVE_THRESHOLD_USER) is True
    assert model.recommend(_BELOW_THRESHOLD_USER, k=3) == model._popularity.recommend(
        _BELOW_THRESHOLD_USER, k=3
    )


@pytest.mark.parametrize("threshold", [None, COLD_START_THRESHOLD])
def test_a_user_at_the_threshold_is_served_by_every_model_either_way(threshold: int | None) -> None:
    # Five distinct items is exactly COLD_START_THRESHOLD, so this user is the
    # one whose routing must not move between policies — the fixed point that
    # makes the h10 bucket comparable across the two runs.
    assert (
        ItemItemModel(cold_start_threshold=threshold)
        .fit(_TRAIN)
        .was_served_by_itemitem(_ABOVE_THRESHOLD_USER)
        is True
    )
    assert (
        CFModel(iterations=5, cold_start_threshold=threshold)
        .fit(_TRAIN)
        .was_served_by_als(_ABOVE_THRESHOLD_USER)
        is True
    )
