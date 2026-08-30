"""ADR 0001's promotion gate, exercised on hand-built EvalResults.

Every case here is a decision the gate has to be able to defend out loud, so
the assertions are on the structured verdict — which clause, by how much — and
not only on the promote/refuse boolean. A gate that says "no" without saying
why is a gate nobody trusts enough to automate.
"""

from __future__ import annotations

import pytest

from src.evaluation.gate import (
    MIN_RELATIVE_GAIN,
    GateInputError,
    SliceTolerance,
    eval_result_from_mlflow_run,
    promotion_decision,
)
from src.evaluation.protocol import EvalResult, UserMetrics

TOLERANCE = SliceTolerance(warm=0.02, cold=0.02)


def _result(
    *,
    warm: float,
    cold: float,
    overall: float,
    n_warm: int = 1_939,
    n_cold: int = 702,
    k: int = 10,
) -> EvalResult:
    """An EvalResult carrying only what the gate reads.

    Recall is filled in with a constant the gate never looks at — deliberately,
    so a test that started passing because the gate began reading recall would
    fail loudly instead of quietly changing meaning.
    """
    return EvalResult(
        warm=UserMetrics(recall=0.0, ndcg=warm),
        cold=UserMetrics(recall=0.0, ndcg=cold),
        overall=UserMetrics(recall=0.0, ndcg=overall),
        n_warm_users=n_warm,
        n_cold_users=n_cold,
        k=k,
    )


# --- the happy path ---------------------------------------------------------


def test_clears_the_aggregate_and_both_slices():
    incumbent = _result(warm=0.050, cold=0.400, overall=0.150)
    candidate = _result(warm=0.055, cold=0.440, overall=0.165)  # +10% everywhere

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    assert decision.promote is True
    assert decision.failures == ()
    assert decision.k == 10
    assert decision.overall.relative_change == pytest.approx(0.10)
    assert all(s.passed and s.comparable for s in decision.slices)


def test_a_slice_that_improves_is_never_constrained_by_its_tolerance():
    incumbent = _result(warm=0.050, cold=0.400, overall=0.150)
    candidate = _result(warm=0.500, cold=0.440, overall=0.165)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    warm = next(s for s in decision.slices if s.name == "warm")
    assert decision.promote is True
    assert warm.relative_change == pytest.approx(9.0)
    assert "improved" in warm.detail


# --- the case the memo was written about ------------------------------------


def test_clears_overall_but_a_slice_regresses_beyond_tolerance():
    """The 2026-08-29 ranker-vs-CF/ALS shape, in miniature.

    The aggregate is carried by a cold slice that improves a lot while the warm
    slice — most of the users — goes backwards. Under the old wording this
    promoted; under the gate it does not.
    """
    incumbent = _result(warm=0.057850, cold=0.487981, overall=0.172182)
    candidate = _result(warm=0.055444, cold=0.563104, overall=0.190384)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    assert decision.promote is False
    assert decision.overall.passed is True
    assert decision.overall.relative_change == pytest.approx(0.1057, abs=1e-4)

    warm = next(s for s in decision.slices if s.name == "warm")
    cold = next(s for s in decision.slices if s.name == "cold")
    assert warm.passed is False
    assert warm.relative_change == pytest.approx(-0.0416, abs=1e-4)
    assert cold.passed is True

    # The refusal names the clause and the margin, not just the outcome.
    assert decision.failures == (warm.detail,)
    assert "warm" in warm.detail and "4.16%" in warm.detail and "2.00% tolerance" in warm.detail


def test_a_regression_inside_the_tolerance_does_not_block():
    """Same shape, a smaller warm move — the noise floor doing its job."""
    incumbent = _result(warm=0.057850, cold=0.487981, overall=0.172182)
    candidate = _result(warm=0.057300, cold=0.563104, overall=0.190384)  # warm −0.95%

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    warm = next(s for s in decision.slices if s.name == "warm")
    assert decision.promote is True
    assert warm.passed is True
    assert warm.comparable is True
    assert warm.relative_change == pytest.approx(-0.0095, abs=1e-4)
    assert "within the 2.00% tolerance" in warm.detail


def test_the_tolerance_boundary_is_inclusive():
    incumbent = _result(warm=0.100, cold=0.400, overall=0.200)
    candidate = _result(warm=0.098, cold=0.400, overall=0.220)  # warm exactly −2%

    decision = promotion_decision(
        candidate, incumbent, slice_tolerance=SliceTolerance(warm=0.02, cold=0.02)
    )

    assert decision.promote is True
    assert next(s for s in decision.slices if s.name == "warm").passed is True


# --- the aggregate clause ---------------------------------------------------


def test_an_aggregate_gain_below_the_threshold_refuses_even_with_no_regression():
    incumbent = _result(warm=0.050, cold=0.400, overall=0.150)
    candidate = _result(warm=0.051, cold=0.404, overall=0.153)  # +2%, under +3%

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    assert decision.promote is False
    assert decision.overall.passed is False
    assert all(s.passed for s in decision.slices)
    assert decision.failures == (decision.overall.detail,)
    assert "required +3.00%" in decision.overall.detail


def test_the_default_aggregate_threshold_is_adr_0001s_three_percent():
    assert MIN_RELATIVE_GAIN == 0.03
    incumbent = _result(warm=0.050, cold=0.400, overall=0.100)
    just_under = _result(warm=0.050, cold=0.400, overall=0.10299)
    just_over = _result(warm=0.050, cold=0.400, overall=0.10301)

    assert promotion_decision(just_under, incumbent, slice_tolerance=TOLERANCE).promote is False
    assert promotion_decision(just_over, incumbent, slice_tolerance=TOLERANCE).promote is True


def test_a_zero_incumbent_aggregate_refuses_rather_than_promoting_on_an_undefined_gain():
    incumbent = _result(warm=0.0, cold=0.0, overall=0.0)
    candidate = _result(warm=0.05, cold=0.40, overall=0.15)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    assert decision.promote is False
    assert decision.overall.relative_change is None
    assert "undefined" in decision.overall.detail


# --- slices that cannot be compared -----------------------------------------


def test_an_incumbent_with_an_empty_slice_reports_it_rather_than_passing_it():
    """A holdout with no cold users at all — the slice clause has nothing to say."""
    incumbent = _result(warm=0.050, cold=0.0, overall=0.050, n_cold=0)
    candidate = _result(warm=0.060, cold=0.0, overall=0.060, n_cold=0)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    cold = next(s for s in decision.slices if s.name == "cold")
    assert decision.promote is True
    assert cold.comparable is False
    assert cold.passed is True
    assert cold.relative_change is None
    assert "no users in it" in cold.detail
    # It does not block, but it is never silently reported as a clean pass.
    assert "not comparable" in cold.detail


def test_an_incumbent_slice_that_scored_zero_is_also_not_comparable():
    incumbent = _result(warm=0.050, cold=0.0, overall=0.050, n_cold=702)
    candidate = _result(warm=0.060, cold=0.30, overall=0.140, n_cold=702)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    cold = next(s for s in decision.slices if s.name == "cold")
    assert cold.comparable is False
    assert "scored 0.000000" in cold.detail


def test_a_candidate_that_empties_a_slice_the_incumbent_served_is_a_regression():
    incumbent = _result(warm=0.050, cold=0.400, overall=0.150)
    candidate = _result(warm=0.060, cold=0.0, overall=0.160, n_cold=0)

    decision = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)

    cold = next(s for s in decision.slices if s.name == "cold")
    assert decision.promote is False
    assert cold.comparable is True
    assert cold.relative_change == pytest.approx(-1.0)


# --- refusals to decide at all ----------------------------------------------


def test_a_k_mismatch_is_refused_rather_than_answered():
    """recall@500 and NDCG@10 answer different questions — EvalResult.k exists for this."""
    incumbent = _result(warm=0.400, cold=0.529, overall=0.434, k=500)
    candidate = _result(warm=0.055, cold=0.563, overall=0.190, k=10)

    with pytest.raises(GateInputError, match="k=10 and the incumbent at k=500"):
        promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE)


def test_a_negative_tolerance_is_refused():
    with pytest.raises(GateInputError, match="non-negative"):
        SliceTolerance(warm=-0.01, cold=0.02)


def test_a_negative_required_gain_is_refused():
    incumbent = _result(warm=0.05, cold=0.4, overall=0.15)
    with pytest.raises(GateInputError, match="non-negative"):
        promotion_decision(incumbent, incumbent, min_relative_gain=-0.01, slice_tolerance=TOLERANCE)


def test_a_tolerance_for_an_unknown_slice_is_refused():
    with pytest.raises(GateInputError, match="no tolerance defined"):
        TOLERANCE.for_slice("synthetic_cold")


# --- the decision object is the artifact ------------------------------------


def test_the_summary_names_every_clause_and_the_refusal():
    incumbent = _result(warm=0.057850, cold=0.487981, overall=0.172182)
    candidate = _result(warm=0.055444, cold=0.563104, overall=0.190384)

    summary = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE).summary()

    assert summary.startswith("DO NOT PROMOTE — ndcg@10")
    assert "overall:" in summary and "warm:" in summary and "cold:" in summary
    assert "refused because:" in summary


def test_the_decision_serializes_with_its_failures():
    incumbent = _result(warm=0.057850, cold=0.487981, overall=0.172182)
    candidate = _result(warm=0.055444, cold=0.563104, overall=0.190384)

    payload = promotion_decision(candidate, incumbent, slice_tolerance=TOLERANCE).to_dict()

    assert payload["promote"] is False
    assert payload["k"] == 10
    assert len(payload["failures"]) == 1
    assert payload["slices"][0]["name"] == "warm"


# --- reading a run back out of MLflow ---------------------------------------


class _FakeRunInfo:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


class _FakeRunData:
    def __init__(self, metrics: dict[str, float], params: dict[str, str]) -> None:
        self.metrics = metrics
        self.params = params


class _FakeRun:
    """The two attributes `eval_result_from_mlflow_run` touches, and no more."""

    def __init__(self, run_id: str, metrics: dict[str, float], params: dict[str, str]) -> None:
        self.info = _FakeRunInfo(run_id)
        self.data = _FakeRunData(metrics, params)


# The 2026-08-29 ranker run of record, as `src/training/ranker.py` logs it.
_RANKER_METRICS = {
    "warm_recall_at_k": 0.039422,
    "warm_ndcg_at_k": 0.055444,
    "cold_recall_at_k": 0.079337,
    "cold_ndcg_at_k": 0.563104,
    "overall_recall_at_k": 0.050032,
    "overall_ndcg_at_k": 0.190384,
    "n_warm_users": 1939.0,
    "n_cold_users": 702.0,
}

# The item-item run of record. The candidate-stage trainers suffix their
# metrics `_at_k_candidates`, which is why K and the suffix have to be
# resolved together rather than independently.
_ITEMITEM_METRICS = {
    "warm_recall_at_k_candidates": 0.400144,
    "warm_ndcg_at_k_candidates": 0.139240,
    "cold_recall_at_k_candidates": 0.528969,
    "cold_ndcg_at_k_candidates": 0.439164,
    "overall_recall_at_k_candidates": 0.434387,
    "overall_ndcg_at_k_candidates": 0.218962,
    "n_warm_users": 1939.0,
    "n_cold_users": 702.0,
}


def test_a_ranker_run_is_read_at_k_final_not_k_candidates():
    """The ranker logs both K params. Reading `k_candidates` would score a
    top-10 result as though it were a top-500 one — the exact confusion the
    gate's K assertion exists to catch, so the resolution order is pinned."""
    run = _FakeRun("abc", _RANKER_METRICS, {"k_final": "10", "k_candidates": "500"})

    result = eval_result_from_mlflow_run(run)

    assert result.k == 10
    assert result.warm.ndcg == pytest.approx(0.055444)
    assert result.n_cold_users == 702


def test_a_baseline_run_is_read_at_k():
    run = _FakeRun("bas", _RANKER_METRICS, {"k": "10", "factors": "64"})
    assert eval_result_from_mlflow_run(run).k == 10


def test_a_candidate_stage_run_is_read_at_k_candidates_with_its_own_metric_names():
    run = _FakeRun("def", _ITEMITEM_METRICS, {"k_candidates": "500", "k_neighbors": "200"})

    result = eval_result_from_mlflow_run(run)

    assert result.k == 500
    assert result.warm.ndcg == pytest.approx(0.139240)
    assert result.warm.recall == pytest.approx(0.400144)


def test_two_candidate_stage_runs_gate_against_each_other_at_500():
    """ADR 0004's comparison: two retrieval models, both at K_CANDIDATES."""
    incumbent = eval_result_from_mlflow_run(
        _FakeRun("itemitem", _ITEMITEM_METRICS, {"k_candidates": "500"})
    )
    challenger = eval_result_from_mlflow_run(
        _FakeRun(
            "twotower",
            {**_ITEMITEM_METRICS, "warm_ndcg_at_k_candidates": 0.014575},
            {"k_candidates": "500"},
        )
    )

    decision = promotion_decision(challenger, incumbent, slice_tolerance=TOLERANCE)

    assert decision.k == 500
    assert decision.promote is False


def test_a_run_with_parameters_but_no_metrics_is_refused():
    """The killed two-tower runs in `phase-2-candidates` are exactly this."""
    run = _FakeRun("ghi", {"epoch_1_loss": 10.35}, {"k_candidates": "500"})

    with pytest.raises(RuntimeError, match="no \\(K parameter, metric suffix\\) pair"):
        eval_result_from_mlflow_run(run)


def test_a_run_that_names_no_k_is_refused_rather_than_assumed():
    run = _FakeRun("jkl", _RANKER_METRICS, {"factors": "64"})

    with pytest.raises(RuntimeError, match="no \\(K parameter, metric suffix\\) pair"):
        eval_result_from_mlflow_run(run)


def test_a_run_missing_one_slice_metric_is_refused():
    partial = {k: v for k, v in _RANKER_METRICS.items() if k != "warm_ndcg_at_k"}
    run = _FakeRun("mno", partial, {"k_final": "10"})

    with pytest.raises(RuntimeError, match="missing warm_ndcg_at_k"):
        eval_result_from_mlflow_run(run)
