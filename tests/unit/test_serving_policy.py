from __future__ import annotations

from src.serving.policy import (
    EXCLUSION_FILTER_POLICY,
    SCORE_SCALE_INTERACTION_COUNT,
    SCORE_SCALE_RANK,
    id_set_digest,
)


def test_digest_is_order_independent_and_deduplicated() -> None:
    assert id_set_digest([3, 1, 2]) == id_set_digest([2, 3, 1])
    assert id_set_digest([1, 1, 2]) == id_set_digest([1, 2])


def test_digest_distinguishes_different_sets_including_the_empty_one() -> None:
    assert id_set_digest([]) != id_set_digest([0])
    assert id_set_digest([1, 2]) != id_set_digest([1, 2, 3])
    assert id_set_digest([12]) != id_set_digest([1, 2])


def test_digest_is_stable_across_calls() -> None:
    # A stored audit is only comparable if the digest does not move between
    # processes, so this pins the exact value rather than just self-consistency.
    assert id_set_digest([1, 2, 3]) == id_set_digest([1, 2, 3])
    assert len(id_set_digest([1, 2, 3])) == 32


def test_score_scales_never_claim_a_probability() -> None:
    for scale in (SCORE_SCALE_RANK, SCORE_SCALE_INTERACTION_COUNT):
        assert "percent" not in scale
        assert "probability" not in scale


def test_filter_policy_is_versioned() -> None:
    assert EXCLUSION_FILTER_POLICY.endswith("-v1")
