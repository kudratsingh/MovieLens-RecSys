"""The cold-*item* slice: holdout targets the training frame never contained.

The harness has always sliced by user history length. This is the slice by item
novelty, and these tests pin the three properties that make it worth reporting:
it picks up exactly the unseen targets and nothing else, it keeps "nothing to
measure" distinguishable from "measured and scored zero", and it leaves every
number that existed before it bit-for-bit unchanged.
"""

import pytest

from src.evaluation.protocol import (
    COLD_ITEM_SLICE,
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    RECALL_SLICES,
    evaluate,
    per_user_recall_document,
)

# Items the model was fitted on. Anything outside this set is cold by definition.
TRAIN_ITEMS = {10, 20, 30}

# One user per case the slice has to get right:
#   1 — every target seen in train, so the slice must ignore them entirely
#   2 — two cold targets, one retrieved: a mid-range recall, not 0 or 1
#   3 — a cold *user* who also has a cold *item*, so the two axes are independent
#   4 — warm hits alongside a cold miss, the masking case the narrowing prevents
RECOMMENDATIONS = {1: [10], 2: [91], 3: [93], 4: [10, 20]}
HOLDOUT = {1: {10}, 2: {91, 92}, 3: {93}, 4: {10, 20, 94}}
TRAIN_COUNTS = {1: COLD_START_THRESHOLD, 2: COLD_START_THRESHOLD, 3: 0, 4: COLD_START_THRESHOLD}


def _measured():
    return evaluate(RECOMMENDATIONS, HOLDOUT, TRAIN_COUNTS, train_items=TRAIN_ITEMS)


def _unmeasured():
    return evaluate(RECOMMENDATIONS, HOLDOUT, TRAIN_COUNTS)


# --- what the slice is over ---------------------------------------------------


def test_slice_holds_exactly_the_users_with_an_unseen_target():
    slice_ = _measured().cold_item
    assert slice_ is not None
    # User 1's only target is in TRAIN_ITEMS, so they are not in the slice at all
    # — the slice is over users with something cold to measure, not over everyone.
    assert set(_measured().per_user_recall[COLD_ITEM_SLICE]) == {2, 3, 4}
    assert slice_.n_users == 3


def test_recall_is_scored_over_cold_targets_only():
    vector = _measured().per_user_recall[COLD_ITEM_SLICE]
    # User 2 holds {91, 92} and retrieved 91.
    assert vector[2] == pytest.approx(0.5)
    # User 3's single cold target was retrieved.
    assert vector[3] == pytest.approx(1.0)


def test_warm_hits_cannot_mask_a_cold_miss():
    """The reason the relevant set is narrowed rather than left whole.

    User 4 retrieved two of their three targets, so their *user*-slice recall is
    2/3. The one they missed is the cold item, and the cold-item slice has to
    report that as a zero rather than let the warm hits average it away.
    """
    result = _measured()
    assert result.per_user_recall["warm"][4] == pytest.approx(2 / 3)
    assert result.per_user_recall[COLD_ITEM_SLICE][4] == 0.0


def test_counts_separate_users_targets_and_distinct_items():
    slice_ = _measured().cold_item
    assert slice_ is not None
    # 3 users, holding 4 cold targets between them ({91,92} + {93} + {94}),
    # over 4 distinct cold items. The mean is per user; the other two counts are
    # what say how thin that mean is.
    assert (slice_.n_users, slice_.n_targets, slice_.n_distinct_items) == (3, 4, 4)


def test_slice_respects_k():
    # The cold target sits at rank 3: invisible at k=2, a hit at k=3.
    recs = {1: [10, 20, 99]}
    holdout = {1: {99}}
    counts = {1: COLD_START_THRESHOLD}

    at_two = evaluate(recs, holdout, counts, k=2, train_items=TRAIN_ITEMS).cold_item
    at_three = evaluate(recs, holdout, counts, k=3, train_items=TRAIN_ITEMS).cold_item
    assert at_two is not None and at_two.metrics is not None
    assert at_three is not None and at_three.metrics is not None
    assert at_two.metrics.recall == 0.0
    assert at_three.metrics.recall == pytest.approx(1.0)


# --- empty vs. zero vs. never looked ------------------------------------------


def test_no_train_items_leaves_the_slice_unmeasured():
    result = _unmeasured()
    assert result.cold_item is None
    # And no vector either — an empty one would read as "no user had a cold
    # target", which is a measurement nobody made.
    assert COLD_ITEM_SLICE not in result.per_user_recall


def test_empty_slice_is_measured_and_distinguishable_from_zero():
    """Every holdout target was seen in train: nothing to measure."""
    result = evaluate({1: [10]}, {1: {10}}, {1: COLD_START_THRESHOLD}, train_items=TRAIN_ITEMS)
    slice_ = result.cold_item

    assert slice_ is not None, "train_items was supplied, so the slice was measured"
    assert slice_.metrics is None, "no cold target existed, so there is no mean to report"
    assert (slice_.n_users, slice_.n_targets, slice_.n_distinct_items) == (0, 0, 0)
    assert result.per_user_recall[COLD_ITEM_SLICE] == {}


def test_scored_zero_is_a_different_object_from_an_empty_slice():
    """The distinction the slice exists for: a real failure, not an absent one."""
    result = evaluate({1: [10]}, {1: {99}}, {1: COLD_START_THRESHOLD}, train_items=TRAIN_ITEMS)
    slice_ = result.cold_item

    assert slice_ is not None and slice_.metrics is not None
    assert slice_.metrics.recall == 0.0
    assert slice_.metrics.ndcg == 0.0
    assert slice_.n_users == 1
    # The three states are mutually exclusive and readable without a flag:
    # None / metrics is None / metrics is a scored UserMetrics.
    empty = evaluate({1: [10]}, {1: {10}}, {1: 10}, train_items=TRAIN_ITEMS).cold_item
    assert _unmeasured().cold_item is None
    assert empty is not None and empty.metrics is None


def test_every_holdout_item_cold_when_train_frame_has_none_of_them():
    result = evaluate(RECOMMENDATIONS, HOLDOUT, TRAIN_COUNTS, train_items=set())
    slice_ = result.cold_item
    assert slice_ is not None
    assert slice_.n_users == 4
    assert slice_.n_targets == sum(len(t) for t in HOLDOUT.values())


# --- the invariant the tolerance study checks ---------------------------------


def test_per_user_vector_mean_reconstructs_the_slice_mean():
    """A vector whose mean does not reproduce the published recall is not this run's.

    The study validates exactly this before it will use a vector, so it is
    pinned here rather than discovered three runs into a study.
    """
    result = _measured()
    vector = result.per_user_recall[COLD_ITEM_SLICE]
    assert result.cold_item is not None and result.cold_item.metrics is not None

    reconstructed = sum(vector.values()) / len(vector)
    assert reconstructed == result.cold_item.metrics.recall
    # (0.5 + 1.0 + 0.0) / 3
    assert reconstructed == pytest.approx(0.5)


def test_vector_length_matches_the_reported_user_count():
    result = _measured()
    assert result.cold_item is not None
    assert len(result.per_user_recall[COLD_ITEM_SLICE]) == result.cold_item.n_users


# --- backward compatibility ---------------------------------------------------


def test_existing_metrics_are_byte_identical_with_and_without_train_items():
    """Adding the slice must not move a single number anyone already publishes."""
    baseline = evaluate(RECOMMENDATIONS, HOLDOUT, TRAIN_COUNTS, k=K_CANDIDATES)
    measured = evaluate(
        RECOMMENDATIONS, HOLDOUT, TRAIN_COUNTS, k=K_CANDIDATES, train_items=TRAIN_ITEMS
    )

    assert measured.warm == baseline.warm
    assert measured.cold == baseline.cold
    assert measured.overall == baseline.overall
    assert measured.n_warm_users == baseline.n_warm_users
    assert measured.n_cold_users == baseline.n_cold_users
    assert measured.k == baseline.k
    assert measured.synthetic_cold_slices == baseline.synthetic_cold_slices
    for name in RECALL_SLICES:
        assert measured.per_user_recall[name] == baseline.per_user_recall[name]


def test_cold_item_is_not_one_of_the_published_recall_slices():
    """RECALL_SLICES is the study's vocabulary and the artifact's shape.

    The study's loader rejects a document carrying a slice it does not know, so
    widening this tuple is a change to that contract and has to be made there.
    """
    assert COLD_ITEM_SLICE not in RECALL_SLICES
    assert RECALL_SLICES == ("warm", "cold", "overall")


def test_published_document_is_unchanged_when_the_slice_is_measured():
    document = per_user_recall_document(
        _measured(),
        run_id="run-1",
        model_type="item-item",
        seed=None,
        configuration_id="config-1",
    )
    assert set(document["per_user_recall"]) == set(RECALL_SLICES)
    assert (
        document["metrics"]
        == per_user_recall_document(
            _unmeasured(),
            run_id="run-1",
            model_type="item-item",
            seed=None,
            configuration_id="config-1",
        )["metrics"]
    )
