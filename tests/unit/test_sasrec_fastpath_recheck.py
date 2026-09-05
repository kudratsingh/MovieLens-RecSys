from __future__ import annotations

from src.training.sasrec_fastpath_recheck import (
    full_length_fastpath_delta,
    warm_history_length_distribution,
)
from tests.unit.test_sasrec import _two_block_retrieval_model


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


def test_full_length_artifact_path_moves_by_at_most_one_micro_unit() -> None:
    assert full_length_fastpath_delta(_two_block_retrieval_model()) <= 1e-6
