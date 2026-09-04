"""
Unit tests for the last-item transition baseline.

This model exists to be a control, so the tests are weighted towards the
properties that make a control trustworthy rather than towards recommendation
quality. Three groups carry most of the weight:

  1. *Point-in-time correctness.* Items sharing a timestamp must never be
     treated as following one another, and nothing outside the frame handed to
     ``fit`` may reach the counts. A leak here inflates every metric while
     leaving the model looking healthy, which is the failure CLAUDE.md singles
     out for sequence construction.
  2. *Determinism.* The scores are integers and ties are the common case, so
     the ordering is decided by the tie-break far more often than by the
     counts. The tie-break is therefore asserted directly, and the fit is
     asserted to be invariant to the order rows arrive in.
  3. *Routing parity.* Cold users take the popularity fallback on exactly the
     rule every other candidate model applies, and ``was_served_by_last_item``
     cannot disagree with ``recommend`` about where that boundary sits.

Fixtures are built per test rather than shared, because each one is a specific
transition structure and a shared frame would make the assertions harder to
read than the code under test.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD
from src.models.candidates.last_item import LastItemTransitionModel
from src.models.candidates.popularity import PopularityModel


def _ratings(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    """(userId, movieId, timestamp) triples — the three columns the model reads."""
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


def _open_model(*, backfill: bool = True) -> LastItemTransitionModel:
    """A model on the index-membership opt-out.

    Most fixtures below have histories of two or three items, well under ADR
    0001's threshold, so a model on the default would answer every one of them
    from its popularity fallback and the transition logic would go untested.
    Where the *default* sends these users is asserted by the routing tests at
    the bottom of the file.
    """
    return LastItemTransitionModel(cold_start_threshold=None, backfill_with_popularity=backfill)


def test_fit_returns_self_for_chaining() -> None:
    model = _open_model().fit(_ratings([(1, 100, 1), (1, 101, 2)]))
    assert isinstance(model, LastItemTransitionModel)


def test_transitions_follow_the_timestamp_order() -> None:
    # Two users walk 100 → 101 → 102. A third user whose history stops at 100
    # should be offered 101, the item that most often followed it.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 101, 2),
            (1, 102, 3),
            (2, 100, 1),
            (2, 101, 2),
            (2, 102, 3),
            (3, 100, 9),
        ]
    )
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=3, k=5) == [101]


def test_items_sharing_a_timestamp_never_transition_to_each_other() -> None:
    # User 1 rated 100 and 101 in the same second, then 102 a second later. The
    # data says nothing about whether 100 preceded 101, so the model must not
    # invent an ordering from the movie ids — 101 must be absent from what
    # follows 100.
    train = _ratings([(1, 100, 1), (1, 101, 1), (1, 102, 2), (2, 100, 9)])
    model = _open_model(backfill=False).fit(train)

    recommendations = model.recommend(user_id=2, k=5)
    assert 101 not in recommendations
    assert recommendations == [102]


def test_every_item_of_a_shared_timestamp_precedes_the_next_group() -> None:
    # The other half of the tie rule: co-timestamped items are jointly the
    # antecedents of whatever comes next, so 101 → 102 is counted as well.
    train = _ratings([(1, 100, 1), (1, 101, 1), (1, 102, 2), (2, 101, 9)])
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=2, k=5) == [102]


def test_tied_last_items_sum_their_successors() -> None:
    # User 3's history ends on two items rated in the same second. Neither is
    # "the" last item, so both contribute their successors. 200 outranks 201
    # only because it is the more popular of two items tied at one transition.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 200, 2),
            (2, 101, 1),
            (2, 201, 2),
            (3, 100, 9),
            (3, 101, 9),
            (4, 200, 1),
        ]
    )
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=3, k=5) == [200, 201]


def test_ties_break_by_popularity_then_movie_id() -> None:
    # 300, 301 and 302 each followed 100 exactly once. 301 is the more popular
    # of the three; 300 and 302 are equally popular, so the lower id wins.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 300, 2),
            (2, 100, 1),
            (2, 301, 2),
            (3, 100, 1),
            (3, 302, 2),
            (4, 301, 1),
            (5, 100, 9),
        ]
    )
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=5, k=5) == [301, 300, 302]


def test_transition_count_outranks_the_popularity_tie_break() -> None:
    # Same fixture with one more 100 → 300 walk. Popularity still prefers 301,
    # but the tie-break only decides ties: two transitions beat one.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 300, 2),
            (2, 100, 1),
            (2, 301, 2),
            (3, 100, 1),
            (3, 302, 2),
            (4, 301, 1),
            (5, 100, 9),
            (6, 100, 1),
            (6, 300, 2),
        ]
    )
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=5, k=5) == [300, 301, 302]


def test_already_seen_successors_are_excluded() -> None:
    # 100 → 101 is the only transition in the table, and user 3 has already
    # seen 101, so the transition half of their list is empty.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 101, 2),
            (2, 100, 1),
            (2, 101, 2),
            (3, 101, 1),
            (3, 100, 2),
            (4, 300, 1),
            (4, 301, 2),
        ]
    )
    model = _open_model().fit(train)
    assert model.transition_candidates(user_id=3, k=5) == []
    assert 101 not in model.recommend(user_id=3, k=5)
    assert 100 not in model.recommend(user_id=3, k=5)


def test_nothing_after_the_split_cutoff_reaches_the_counts() -> None:
    # The leakage canary at the split boundary. Item 999 exists only in the
    # holdout window, so a model fitted on the train slice must be unable to
    # retrieve it — for any user, at any k.
    ratings = pd.DataFrame(
        [(user, movie, timestamp) for user in range(1, 11) for movie, timestamp in ((100, 1),)]
        + [(user, 101, 2) for user in range(1, 11)]
        + [(user, 999, 10_000_000) for user in range(1, 11)],
        columns=["userId", "movieId", "timestamp"],
    )
    split = temporal_split(ratings)
    assert 999 in set(split.holdout["movieId"])

    model = _open_model().fit(split.train)
    assert all(999 not in model.recommend(user_id=user, k=500) for user in range(1, 11))


def test_backfill_tops_a_short_list_up_to_k() -> None:
    # 100 has a single successor, so the other slots have to come from
    # somewhere for the list to be comparable at a fixed K.
    train = _ratings([(1, 100, 1), (1, 101, 2), (2, 200, 1), (2, 201, 2), (2, 202, 3), (3, 100, 9)])
    model = _open_model().fit(train)

    recommendations = model.recommend(user_id=3, k=4)
    assert len(recommendations) == 4
    assert recommendations[0] == 101
    assert len(set(recommendations)) == 4
    assert 100 not in recommendations


def test_backfill_can_be_switched_off() -> None:
    train = _ratings([(1, 100, 1), (1, 101, 2), (2, 200, 1), (2, 201, 2), (2, 202, 3), (3, 100, 9)])
    model = _open_model(backfill=False).fit(train)
    assert model.recommend(user_id=3, k=4) == [101]


def test_transition_candidates_are_a_prefix_of_recommend() -> None:
    # The trainer reports a transitions-only recall by truncating the served
    # lists rather than by scoring the model twice, which is only sound while
    # this holds.
    train = _ratings(
        [
            (1, 100, 1),
            (1, 101, 2),
            (1, 102, 3),
            (2, 100, 1),
            (2, 102, 2),
            (3, 200, 1),
            (3, 201, 2),
            (4, 100, 9),
        ]
    )
    model = _open_model().fit(train)
    for user in (1, 2, 3, 4):
        transitions = model.transition_candidates(user, k=10)
        assert model.recommend(user, k=10)[: len(transitions)] == transitions


def test_a_warm_user_with_no_recorded_successors_still_counts_as_served() -> None:
    # Routing and reach are separate claims. This user is above the threshold,
    # so the learned path served them; it simply had nothing to say, which is a
    # fill-rate observation rather than a routing one.
    train = _ratings([(1, 100 + offset, offset) for offset in range(COLD_START_THRESHOLD)])
    model = LastItemTransitionModel().fit(train)

    assert model.was_served_by_last_item(user_id=1) is True
    assert model.transition_candidates(user_id=1, k=5) == []


def test_fit_is_invariant_to_the_order_rows_arrive_in() -> None:
    train = _ratings(
        [
            (1, 100, 1),
            (1, 101, 1),
            (1, 102, 2),
            (2, 100, 1),
            (2, 102, 2),
            (2, 103, 3),
            (3, 103, 1),
            (3, 100, 2),
            (4, 100, 9),
        ]
    )
    shuffled = train.sample(frac=1.0, random_state=0).reset_index(drop=True)

    ordered_model = _open_model().fit(train)
    shuffled_model = _open_model().fit(shuffled)
    for user in (1, 2, 3, 4):
        assert ordered_model.recommend(user, k=10) == shuffled_model.recommend(user, k=10)


def test_repeated_recommendations_are_identical() -> None:
    train = _ratings([(1, 100, 1), (1, 101, 2), (2, 100, 1), (2, 102, 2), (3, 100, 9)])
    model = _open_model().fit(train)
    assert model.recommend(user_id=3, k=5) == model.recommend(user_id=3, k=5)


def test_stats_describe_the_transition_shape() -> None:
    # User 1 contributes two antecedents into one successor; user 2 contributes
    # one pair. The largest timestamp group is user 1's opening pair.
    train = _ratings([(1, 100, 1), (1, 101, 1), (1, 102, 2), (2, 100, 1), (2, 102, 2)])
    model = _open_model().fit(train)

    assert model.stats.n_transition_events == 3
    assert model.stats.n_transition_pairs == 2  # (100, 102) twice and (101, 102) once
    assert model.stats.n_antecedents == 2
    assert model.stats.max_timestamp_group_size == 2


def test_empty_train_produces_an_empty_model() -> None:
    model = _open_model().fit(_ratings([]))
    assert model.recommend(user_id=1, k=5) == []
    assert model.was_served_by_last_item(user_id=1) is False
    assert model.stats.n_transition_events == 0


def test_missing_timestamp_column_is_refused() -> None:
    frame = pd.DataFrame([(1, 100)], columns=["userId", "movieId"])
    with pytest.raises(KeyError, match="timestamp"):
        _open_model().fit(frame)


# --- Routing ----------------------------------------------------------------
# The default constructor applies ADR 0001's threshold, the rule the deployed
# serving path routes on. tests/unit/test_candidate_routing.py holds the other
# candidate models to the same contract.

_ROUTING_TRAIN = _ratings(
    # User 1 sits exactly at the threshold; user 2 has a single interaction and
    # is therefore below it however deep the transition table is.
    [(1, 100 + offset, offset + 1) for offset in range(COLD_START_THRESHOLD)]
    + [(2, 100, 1), (3, 109, 1), (3, 500, 2)]
)


def test_a_user_below_the_threshold_takes_the_popularity_fallback() -> None:
    model = LastItemTransitionModel().fit(_ROUTING_TRAIN)
    popularity = PopularityModel().fit(_ROUTING_TRAIN)

    assert model.was_served_by_last_item(user_id=2) is False
    assert model.recommend(user_id=2, k=5) == popularity.recommend(user_id=2, k=5)


def test_a_user_at_the_threshold_is_served_by_transitions() -> None:
    model = LastItemTransitionModel().fit(_ROUTING_TRAIN)

    assert model.was_served_by_last_item(user_id=1) is True
    # User 1's last item is 109, and user 3 walked 109 → 500.
    assert model.transition_candidates(user_id=1, k=5) == [500]


def test_the_predicate_and_recommend_cannot_disagree() -> None:
    # Every per-policy metric in MLflow and every ADR 0011 bucket count is
    # computed from the predicate, so a predicate that said one thing while
    # recommend did another would make those numbers fiction.
    model = LastItemTransitionModel().fit(_ROUTING_TRAIN)
    popularity = PopularityModel().fit(_ROUTING_TRAIN)

    for user in (1, 2, 3):
        if model.was_served_by_last_item(user):
            continue
        assert model.recommend(user, k=5) == popularity.recommend(user, k=5)


def test_an_unknown_user_takes_the_popularity_fallback() -> None:
    model = LastItemTransitionModel().fit(_ROUTING_TRAIN)
    popularity = PopularityModel().fit(_ROUTING_TRAIN)

    assert model.was_served_by_last_item(user_id=9999) is False
    assert model.recommend(user_id=9999, k=5) == popularity.recommend(user_id=9999, k=5)


def test_the_index_membership_opt_out_serves_a_one_interaction_user() -> None:
    model = _open_model().fit(_ROUTING_TRAIN)
    assert model.was_served_by_last_item(user_id=2) is True
