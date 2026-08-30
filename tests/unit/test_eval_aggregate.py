"""Unit tests for `mean_eval_result` — the seed-averaged result the gate reads.

The averaging itself is arithmetic. What is worth testing is what it refuses:
results scored at different K, or over holdouts of different sizes, are not
repeated measurements of one quantity, and a mean over them is a number with no
referent. Those two refusals are the reason the helper exists rather than a
`statistics.mean` at each call site.
"""

from __future__ import annotations

import pytest

from src.evaluation.aggregate import MismatchedResultsError, mean_eval_result
from src.evaluation.protocol import EvalResult, SyntheticColdSlice, UserMetrics


def _result(
    warm: float,
    cold: float,
    overall: float,
    *,
    k: int = 10,
    n_warm: int = 1_939,
    n_cold: int = 702,
) -> EvalResult:
    return EvalResult(
        warm=UserMetrics(recall=warm / 2, ndcg=warm),
        cold=UserMetrics(recall=cold / 2, ndcg=cold),
        overall=UserMetrics(recall=overall / 2, ndcg=overall),
        n_warm_users=n_warm,
        n_cold_users=n_cold,
        k=k,
    )


def test_the_mean_of_the_three_ranker_seeds_is_the_arithmetic_mean():
    results = [
        _result(0.055444, 0.563104, 0.190384),
        _result(0.049222, 0.549324, 0.182153),
        _result(0.065491, 0.545592, 0.193106),
    ]
    averaged = mean_eval_result(results)
    assert averaged.warm.ndcg == pytest.approx((0.055444 + 0.049222 + 0.065491) / 3)
    assert averaged.cold.ndcg == pytest.approx((0.563104 + 0.549324 + 0.545592) / 3)
    assert averaged.overall.ndcg == pytest.approx((0.190384 + 0.182153 + 0.193106) / 3)
    assert averaged.warm.recall == pytest.approx(averaged.warm.ndcg / 2)


def test_the_shared_k_and_slice_sizes_are_carried_through():
    averaged = mean_eval_result([_result(0.1, 0.2, 0.3), _result(0.2, 0.3, 0.4)])
    assert averaged.k == 10
    assert averaged.n_warm_users == 1_939
    assert averaged.n_cold_users == 702


def test_a_single_result_is_returned_unchanged():
    """So a deterministic model does not need a special case at the call site."""
    only = _result(0.1, 0.2, 0.3)
    assert mean_eval_result([only]) is only


def test_no_results_raises():
    with pytest.raises(MismatchedResultsError, match="no results"):
        mean_eval_result([])


def test_results_scored_at_different_k_are_refused():
    with pytest.raises(MismatchedResultsError, match="different K"):
        mean_eval_result([_result(0.1, 0.2, 0.3), _result(0.1, 0.2, 0.3, k=500)])


def test_results_over_different_holdouts_are_refused():
    with pytest.raises(MismatchedResultsError, match="n_warm_users"):
        mean_eval_result([_result(0.1, 0.2, 0.3), _result(0.1, 0.2, 0.3, n_warm=1_940)])
    with pytest.raises(MismatchedResultsError, match="n_cold_users"):
        mean_eval_result([_result(0.1, 0.2, 0.3), _result(0.1, 0.2, 0.3, n_cold=701)])


def test_the_cohort_buckets_are_dropped_rather_than_averaged():
    """ADR 0011's buckets are per-run reporting and are not part of a promotion decision."""
    with_slices = _result(0.1, 0.2, 0.3)
    with_slices.synthetic_cold_slices = {
        0: SyntheticColdSlice(
            history_size=0, metrics=UserMetrics(recall=0.034, ndcg=0.01), n_users=500
        )
    }
    averaged = mean_eval_result([with_slices, _result(0.2, 0.3, 0.4)])
    assert averaged.synthetic_cold_slices == {}


def test_the_averaged_result_is_gateable():
    """The point of the helper: the gate takes it without knowing it is a mean."""
    from src.evaluation.gate import promotion_decision

    incumbent = mean_eval_result([_result(0.05, 0.48, 0.17), _result(0.06, 0.49, 0.175)])
    candidate = mean_eval_result([_result(0.055, 0.55, 0.19), _result(0.058, 0.56, 0.195)])
    decision = promotion_decision(candidate, incumbent)
    assert decision.k == 10
    assert decision.overall.relative_change is not None
