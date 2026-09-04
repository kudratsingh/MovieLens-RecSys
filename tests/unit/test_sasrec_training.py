from __future__ import annotations

from src.training.sasrec import retrieval_diagnostics


def test_retrieval_diagnostics_measure_coverage_rank_and_target_reach() -> None:
    diagnostics = retrieval_diagnostics(
        recommendations={1: [10, 30], 2: [20, 30]},
        holdout={1: {10, 20}, 2: {40}},
        item_popularity={10: 100, 20: 50, 30: 25, 40: 5},
        catalog_size=4,
    )

    assert diagnostics == {
        "retrieved_unique_items": 3.0,
        "catalog_coverage": 0.75,
        "mean_retrieved_item_popularity_rank": 2.25,
        "holdout_target_reachability": 1 / 3,
    }


def test_retrieval_diagnostics_handle_empty_policy_slice() -> None:
    diagnostics = retrieval_diagnostics({}, {}, {}, catalog_size=0)

    assert diagnostics == {
        "retrieved_unique_items": 0.0,
        "catalog_coverage": 0.0,
        "mean_retrieved_item_popularity_rank": 0.0,
        "holdout_target_reachability": 0.0,
    }
