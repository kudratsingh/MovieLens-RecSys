"""Truth table for the retrieval-specific, protocol-bound promotion gate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.evaluation.manifest import PROTOCOL_SCHEMA_VERSION, ProtocolManifest
from src.evaluation.protocol import EvalResult, UserMetrics
from src.evaluation.retrieval_gate import (
    ITEMITEM_MODEL_TYPE,
    MIN_WARM_RELATIVE_GAIN,
    MLFLOW_DETERMINISTIC_PARAM,
    MLFLOW_MODEL_TYPE_TAG,
    MLFLOW_SEED_PARAM,
    REQUIRED_SEEDS,
    RetrievalGateStatus,
    RetrievalRun,
    RetrievalRunNotUsableError,
    RetrievalRunSet,
    RetrievalTolerance,
    retrieval_promotion_decision,
    retrieval_run_from_mlflow,
)


def _protocol(**changes: object) -> ProtocolManifest:
    values: dict[str, object] = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "raw_data_revision": "md5:c3ce6309.dir",
        "derived_snapshot_hash": "sha256:split-v1",
        "event_schema_version": "movielens-rating-v1",
        "train_cutoff": 1_000,
        "holdout_start": 1_000,
        "holdout_end": 2_000,
        "backtest_window_id": "fixed-holdout-v1",
        "timestamp_unit": "unix-seconds",
        "timezone": "UTC",
        "label_contract_version": "implicit-positive-v1",
        "relevance_definition": "every-rating-is-positive",
        "eligible_user_policy": "at-least-one-holdout-positive",
        "catalog_fingerprint": "sha256:catalog-v1",
        "unknown_item_policy": "exclude",
        "cold_start_threshold": 10,
        "learned_routing_policy": "history-count-gte-threshold",
        "fallback_policy": "training-popularity",
        "positive_history_filter": "strictly-before-prediction",
        "seen_item_filter": "exclude-all-positive-history",
        "dismissal_filter": "exclude-known-dismissals",
        "target_filter": "exclude-target-from-context",
        "candidate_filter": "eligible-training-catalog",
        "feature_contract_version": "candidate-v1",
        "point_in_time_semantics": "strictly-earlier-event-time",
        "stage": "retrieval",
        "primary_metric": "recall",
        "metric_contract_version": "evaluation-v1",
        "metric_aggregation": "unweighted-user-mean",
        "k": 500,
        "slice_definition": "warm-gte-10;cold-lt-10;overall=union",
    }
    values.update(changes)
    return ProtocolManifest(**values)  # type: ignore[arg-type]


def _result(
    warm: float,
    cold: float,
    overall: float,
    *,
    n_warm: int = 1_939,
    n_cold: int = 702,
    k: int = 500,
) -> EvalResult:
    return EvalResult(
        warm=UserMetrics(recall=warm, ndcg=0.0),
        cold=UserMetrics(recall=cold, ndcg=0.0),
        overall=UserMetrics(recall=overall, ndcg=0.0),
        n_warm_users=n_warm,
        n_cold_users=n_cold,
        k=k,
    )


def _run(
    run_id: str,
    seed: int | None,
    result: EvalResult,
    *,
    deterministic: bool = False,
    model_type: str = "sasrec",
    protocol: ProtocolManifest | None = None,
) -> RetrievalRun:
    return RetrievalRun(
        run_id=run_id,
        model_type=model_type,
        seed=seed,
        deterministic=deterministic,
        protocol=protocol or _protocol(),
        result=result,
    )


def _candidate(
    warm: float = 0.414,
    cold: float = 0.525,
    overall: float = 0.430,
) -> RetrievalRunSet:
    return RetrievalRunSet(
        model_id="sasrec",
        deterministic=False,
        runs=tuple(
            _run(f"sasrec-{seed}", seed, _result(warm, cold, overall)) for seed in REQUIRED_SEEDS
        ),
    )


def _incumbent(
    warm: float = 0.400,
    cold: float = 0.530,
    overall: float = 0.435,
) -> RetrievalRunSet:
    return RetrievalRunSet(
        model_id=ITEMITEM_MODEL_TYPE,
        deterministic=True,
        runs=(
            _run(
                "itemitem",
                None,
                _result(warm, cold, overall),
                deterministic=True,
                model_type=ITEMITEM_MODEL_TYPE,
            ),
        ),
    )


TOLERANCE = RetrievalTolerance(cold=0.02, overall=0.02)


def test_retrieval_gate_passes_three_seed_candidate_against_deterministic_incumbent():
    decision = retrieval_promotion_decision(_candidate(), _incumbent(), tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.PROMOTE
    assert decision.promote is True
    assert decision.stage == "retrieval"
    assert decision.metric == "recall"
    assert decision.k == 500
    assert decision.required_seeds == (42, 7, 13)
    assert decision.protocol_hash == _protocol().semantic_hash
    assert decision.serving_eligible is False
    assert "LightGBM" in decision.summary()


def test_candidate_mean_is_used_for_the_positive_claim():
    runs = (
        _run("sasrec-42", 42, _result(0.390, 0.530, 0.435)),
        _run("sasrec-7", 7, _result(0.420, 0.530, 0.435)),
        _run("sasrec-13", 13, _result(0.426, 0.530, 0.435)),
    )
    candidate = RetrievalRunSet(model_id="sasrec", deterministic=False, runs=runs)

    decision = retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE)

    warm = next(clause for clause in decision.clauses if clause.name == "warm")
    assert warm.candidate == pytest.approx(0.412)
    assert decision.status is RetrievalGateStatus.PROMOTE


def test_warm_gain_boundary_is_inclusive():
    candidate = _candidate(warm=0.400 * 1.03, cold=0.530, overall=0.435)
    decision = retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.PROMOTE
    assert MIN_WARM_RELATIVE_GAIN == 0.03


@pytest.mark.parametrize(
    ("candidate", "failed_slice"),
    [
        (_candidate(warm=0.411, cold=0.530, overall=0.435), "warm"),
        (_candidate(warm=0.414, cold=0.500, overall=0.435), "cold"),
        (_candidate(warm=0.414, cold=0.530, overall=0.420), "overall"),
    ],
)
def test_each_quality_clause_can_refuse(candidate: RetrievalRunSet, failed_slice: str):
    decision = retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.REFUSE
    assert any(clause.name == failed_slice and not clause.passed for clause in decision.clauses)


def test_non_regression_boundaries_are_inclusive():
    candidate = _candidate(warm=0.414, cold=0.530 * 0.98, overall=0.435 * 0.98)
    assert (
        retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE).status
        is RetrievalGateStatus.PROMOTE
    )


def test_missing_seed_is_incomplete_not_a_negative_scientific_result():
    candidate = _candidate()
    candidate = replace(candidate, runs=candidate.runs[:-1])

    decision = retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert decision.clauses == ()
    assert "missing required seeds [13]" in decision.reasons[0]


def test_duplicate_seed_and_duplicate_run_id_are_incomplete():
    duplicated = _run("same", 42, _result(0.414, 0.530, 0.435))
    candidate = RetrievalRunSet(
        model_id="sasrec",
        deterministic=False,
        runs=(duplicated, duplicated, _run("third", 7, _result(0.414, 0.530, 0.435))),
    )

    decision = retrieval_promotion_decision(candidate, _incumbent(), tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("duplicate seeds" in reason for reason in decision.reasons)
    assert any("duplicate run ids" in reason for reason in decision.reasons)


def test_deterministic_side_requires_exactly_one_seedless_run():
    incumbent = _incumbent()
    incumbent = replace(incumbent, runs=incumbent.runs * 2)
    decision = retrieval_promotion_decision(_candidate(), incumbent, tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert "exactly one run" in decision.reasons[0]


def test_gate_requires_stochastic_candidate_and_itemitem_incumbent():
    deterministic_candidate = RetrievalRunSet(
        model_id="sasrec",
        deterministic=True,
        runs=(
            _run(
                "sasrec-deterministic",
                None,
                _result(0.414, 0.530, 0.435),
                deterministic=True,
            ),
        ),
    )
    wrong_incumbent = replace(
        _incumbent(),
        model_id="popularity",
        runs=(
            replace(
                _incumbent().runs[0],
                model_type="popularity",
            ),
        ),
    )

    candidate_decision = retrieval_promotion_decision(
        deterministic_candidate, _incumbent(), tolerance=TOLERANCE
    )
    incumbent_decision = retrieval_promotion_decision(
        _candidate(), wrong_incumbent, tolerance=TOLERANCE
    )

    assert candidate_decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("candidate must be stochastic" in reason for reason in candidate_decision.reasons)
    assert incumbent_decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("incumbent model must be" in reason for reason in incumbent_decision.reasons)


def test_gate_rejects_mixed_model_identity_and_overlapping_run_ids():
    candidate = _candidate()
    candidate = replace(
        candidate,
        runs=(
            replace(candidate.runs[0], model_type="two_tower"),
            *candidate.runs[1:],
        ),
    )
    incumbent = _incumbent()
    incumbent = replace(
        incumbent,
        runs=(replace(incumbent.runs[0], run_id=candidate.runs[0].run_id),),
    )

    decision = retrieval_promotion_decision(candidate, incumbent, tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("does not match run-set model" in reason for reason in decision.reasons)
    assert any("same run ids" in reason for reason in decision.reasons)


def test_custom_seed_policy_is_reported_even_when_evidence_is_incomplete():
    decision = retrieval_promotion_decision(
        _candidate(),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(1, 2, 3),
    )

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert decision.required_seeds == (1, 2, 3)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("catalog_fingerprint", "sha256:other-catalog"),
        ("train_cutoff", 999),
        ("seen_item_filter", "none"),
        ("cold_start_threshold", 5),
    ],
)
def test_protocol_mismatch_refuses_comparison(field: str, value: object):
    candidate = _candidate()
    changed_protocol = replace(_protocol(), **{field: value})
    changed_runs = tuple(replace(run, protocol=changed_protocol) for run in candidate.runs)

    decision = retrieval_promotion_decision(
        replace(candidate, runs=changed_runs), _incumbent(), tolerance=TOLERANCE
    )

    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert field in decision.reasons[0]


def test_wrong_k_or_stage_is_not_comparable():
    wrong_k = _candidate()
    wrong_k = replace(
        wrong_k,
        runs=tuple(
            replace(run, protocol=replace(run.protocol, k=10), result=replace(run.result, k=10))
            for run in wrong_k.runs
        ),
    )
    assert (
        retrieval_promotion_decision(wrong_k, _incumbent(), tolerance=TOLERANCE).status
        is RetrievalGateStatus.NOT_COMPARABLE
    )


def test_population_mismatch_is_not_comparable():
    candidate = _candidate()
    changed = tuple(
        replace(run, result=replace(run.result, n_warm_users=1_940)) for run in candidate.runs
    )
    decision = retrieval_promotion_decision(
        replace(candidate, runs=changed), _incumbent(), tolerance=TOLERANCE
    )
    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert "slice population mismatch" in decision.reasons[0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_metric_is_not_comparable(value: float):
    candidate = _candidate()
    changed = tuple(
        replace(run, result=replace(run.result, warm=replace(run.result.warm, recall=value)))
        for run in candidate.runs
    )
    decision = retrieval_promotion_decision(
        replace(candidate, runs=changed), _incumbent(), tolerance=TOLERANCE
    )
    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE


def test_zero_incumbent_value_refuses_an_undefined_relative_claim():
    decision = retrieval_promotion_decision(_candidate(), _incumbent(warm=0.0), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.REFUSE
    assert (
        next(clause for clause in decision.clauses if clause.name == "warm").relative_change is None
    )


def test_tolerances_have_no_unmeasured_default_and_reject_invalid_values():
    with pytest.raises(TypeError):
        RetrievalTolerance()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="finite fraction"):
        RetrievalTolerance(cold=-0.01, overall=0.02)
    with pytest.raises(ValueError, match="finite fraction"):
        RetrievalTolerance(cold=0.01, overall=float("nan"))
    with pytest.raises(ValueError, match="finite fraction"):
        RetrievalTolerance(cold=1.01, overall=0.02)


class _Info:
    def __init__(self, run_id: str = "run", status: str = "FINISHED") -> None:
        self.run_id = run_id
        self.status = status


class _Data:
    def __init__(
        self,
        *,
        params: dict[str, str],
        tags: dict[str, str],
        metrics: dict[str, float],
    ) -> None:
        self.params = params
        self.tags = tags
        self.metrics = metrics


class _MlflowRun:
    def __init__(
        self,
        *,
        params: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
        metrics: dict[str, float] | None = None,
        status: str = "FINISHED",
    ) -> None:
        protocol = _protocol()
        default_tags = {
            **protocol.mlflow_tags(),
            MLFLOW_MODEL_TYPE_TAG: "sasrec",
        }
        self.info = _Info(status=status)
        self.data = _Data(
            params=(
                params
                if params is not None
                else {
                    **protocol.mlflow_params(),
                    MLFLOW_DETERMINISTIC_PARAM: "false",
                    MLFLOW_SEED_PARAM: "42",
                }
            ),
            tags=tags if tags is not None else default_tags,
            metrics=(
                metrics
                if metrics is not None
                else {
                    "warm_recall_at_k_candidates": 0.414,
                    "cold_recall_at_k_candidates": 0.525,
                    "overall_recall_at_k_candidates": 0.430,
                    "n_warm_users": 1_939.0,
                    "n_cold_users": 702.0,
                }
            ),
        )


def test_mlflow_reader_recalculates_protocol_hash_and_reads_seed():
    run = retrieval_run_from_mlflow(_MlflowRun())
    assert run.seed == 42
    assert run.deterministic is False
    assert run.model_type == "sasrec"
    assert run.protocol == _protocol()
    assert run.result.warm.recall == pytest.approx(0.414)


def test_mlflow_reader_rejects_legacy_run_without_protocol():
    with pytest.raises(RetrievalRunNotUsableError, match="missing the 'evaluation_protocol'"):
        retrieval_run_from_mlflow(_MlflowRun(tags={"model_type": "sasrec"}))


def test_mlflow_reader_requires_model_identity():
    with pytest.raises(RetrievalRunNotUsableError, match="model identity tag"):
        retrieval_run_from_mlflow(_MlflowRun(tags=_protocol().mlflow_tags()))


def test_mlflow_reader_rejects_tampered_protocol_hash():
    protocol = _protocol()
    params = {
        **protocol.mlflow_params(),
        "evaluation_protocol_hash": "sha256:wrong",
        MLFLOW_DETERMINISTIC_PARAM: "false",
        MLFLOW_SEED_PARAM: "42",
    }
    with pytest.raises(RetrievalRunNotUsableError, match="protocol hash mismatch"):
        retrieval_run_from_mlflow(_MlflowRun(params=params))


def test_mlflow_reader_rejects_schema_param_that_disagrees_with_payload():
    protocol = _protocol()
    params = {
        **protocol.mlflow_params(),
        "evaluation_protocol_schema_version": "999",
        MLFLOW_DETERMINISTIC_PARAM: "false",
        MLFLOW_SEED_PARAM: "42",
    }
    with pytest.raises(RetrievalRunNotUsableError, match="protocol schema mismatch"):
        retrieval_run_from_mlflow(_MlflowRun(params=params))


def test_mlflow_reader_rejects_unfinished_or_partial_run():
    with pytest.raises(RetrievalRunNotUsableError, match="only FINISHED"):
        retrieval_run_from_mlflow(_MlflowRun(status="RUNNING"))
    with pytest.raises(RetrievalRunNotUsableError, match="missing required metrics"):
        retrieval_run_from_mlflow(_MlflowRun(metrics={"warm_recall_at_k_candidates": 0.4}))


def test_mlflow_reader_rejects_fractional_population_counts():
    metrics = {
        "warm_recall_at_k_candidates": 0.414,
        "cold_recall_at_k_candidates": 0.525,
        "overall_recall_at_k_candidates": 0.430,
        "n_warm_users": 1_939.5,
        "n_cold_users": 702.0,
    }
    with pytest.raises(RetrievalRunNotUsableError, match="finite non-negative integer"):
        retrieval_run_from_mlflow(_MlflowRun(metrics=metrics))


def test_mlflow_reader_requires_explicit_determinism_and_seed_contract():
    protocol = _protocol()
    with pytest.raises(RetrievalRunNotUsableError, match="must set model_deterministic"):
        retrieval_run_from_mlflow(_MlflowRun(params=protocol.mlflow_params()))

    deterministic_params = {
        **protocol.mlflow_params(),
        MLFLOW_DETERMINISTIC_PARAM: "true",
        MLFLOW_SEED_PARAM: "42",
    }
    with pytest.raises(RetrievalRunNotUsableError, match="must not set train_seed"):
        retrieval_run_from_mlflow(_MlflowRun(params=deterministic_params))
