from __future__ import annotations

from pathlib import Path

import pytest

from src.training.sasrec_fastpath_recheck import (
    RECORDED_COLD_RECALL,
    RECORDED_WARM_RECALL,
    count_changed_lists,
    reproduction_verdict,
    require_cohort_payload,
    top_k_lists_document,
    warm_history_length_distribution,
)


def test_warm_history_distribution_counts_the_fastpath_failure_population() -> None:
    counts = {
        1: 9,
        2: 10,
        3: 19,
        4: 20,
        5: 29,
        6: 30,
        7: 39,
        8: 40,
        9: 49,
        10: 50,
        11: 100,
    }

    assert warm_history_length_distribution(counts, list(counts)) == {
        "10_19": 2,
        "20_29": 2,
        "30_39": 2,
        "40_49": 2,
        "50_plus": 2,
        "below_50": 8,
        "total_warm": 10,
    }


def test_warm_history_distribution_only_counts_holdout_users() -> None:
    assert warm_history_length_distribution({1: 10, 2: 49, 3: 50}, [1, 3]) == {
        "10_19": 1,
        "20_29": 0,
        "30_39": 0,
        "40_49": 0,
        "50_plus": 1,
        "below_50": 1,
        "total_warm": 2,
    }


def test_the_recorded_v1_numbers_reproduce_themselves() -> None:
    verdict = reproduction_verdict(RECORDED_WARM_RECALL, RECORDED_COLD_RECALL)

    assert verdict["passed"] is True
    assert verdict["warm_delta"] == 0.0
    assert verdict["cold_delta"] == 0.0


def test_warm_may_move_inside_the_fourth_decimal_but_not_past_it() -> None:
    inside = reproduction_verdict(RECORDED_WARM_RECALL + 3e-5, RECORDED_COLD_RECALL)
    outside = reproduction_verdict(RECORDED_WARM_RECALL + 1e-4, RECORDED_COLD_RECALL)

    assert inside["passed"] is True
    assert inside["warm_delta"] == pytest.approx(3e-5)
    assert outside["warm_matches_to_4_decimals"] is False
    assert outside["passed"] is False


def test_any_cold_difference_fails_because_cold_never_reaches_the_encoder() -> None:
    verdict = reproduction_verdict(RECORDED_WARM_RECALL, RECORDED_COLD_RECALL + 1e-12)

    assert verdict["cold_exact"] is False
    assert verdict["passed"] is False


def test_changed_lists_separate_membership_from_order() -> None:
    before = {1: [10, 11, 12], 2: [20, 21, 22], 3: [30, 31, 32]}
    after = {1: [10, 11, 12], 2: [21, 20, 22], 3: [30, 31, 33]}

    assert count_changed_lists(before, after) == {
        "n_users": 3,
        "n_lists_changed": 2,
        "n_membership_changed": 1,
        "n_order_only_changed": 1,
    }
    with pytest.raises(ValueError, match="different users"):
        count_changed_lists(before, {1: [10]})


def test_top_k_lists_document_is_order_independent_and_digest_pinned() -> None:
    first = top_k_lists_document({2: [5, 6], 1: [3, 4]})
    second = top_k_lists_document({1: [3, 4], 2: [5, 6]})

    assert first == second
    assert list(first["lists"]) == ["1", "2"]
    assert top_k_lists_document({1: [4, 3], 2: [5, 6]})["sha256"] != first["sha256"]


def test_recheck_refuses_a_missing_cohort_before_loading_data(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="pointer without its parquet payload"):
        require_cohort_payload(tmp_path / "users.parquet")
