"""Truth table for the retrieval-specific, protocol-bound promotion gate.

Most fixtures here give every warm user the slice mean exactly. A constant
per-user vector has zero spread, so the paired bootstrap's standard error is
exactly zero and the warm band collapses to nothing — which is what lets this
file assert the clause arithmetic without an uncertainty term standing in front
of it. The band's own section builds vectors with a spread chosen so its
half-width is known analytically before the bootstrap is run.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import replace
from typing import Any

import pytest

from src.evaluation.manifest import PROTOCOL_SCHEMA_VERSION, ProtocolManifest
from src.evaluation.protocol import EvalResult, UserMetrics, per_user_recall_document
from src.evaluation.retrieval_gate import (
    BAND_POPULATION_ONLY,
    BAND_SEED_AND_POPULATION,
    ITEMITEM_MODEL_TYPE,
    MIN_BOOTSTRAP_REPLICATES,
    MIN_WARM_RELATIVE_GAIN,
    MLFLOW_DETERMINISTIC_PARAM,
    MLFLOW_MODEL_TYPE_TAG,
    MLFLOW_SEED_PARAM,
    ONE_SIDED_Z_95,
    REQUIRED_SEEDS,
    WARM_SLICE,
    BandOptions,
    RetrievalGateDecision,
    RetrievalGateStatus,
    RetrievalRun,
    RetrievalRunNotUsableError,
    RetrievalRunSet,
    RetrievalTolerance,
    SeedRegime,
    per_user_recall_from_artifact,
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
        "sealed_test_boundary": 3_000,
        "backtest_window_id": "rolling-origin-v1:w0:1000-2000",
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


def _wobbled_vector(mean: float, n_users: int, amplitude: float = 0.0) -> dict[int, float]:
    """A warm vector with exactly ``mean`` and a per-user spread of ``amplitude``.

    Users alternate ``mean ± amplitude``, so the population standard deviation is
    the amplitude itself and the paired bootstrap's standard error is
    ``amplitude / sqrt(n)`` analytically — the band's half-width is therefore
    known before the resampler runs. An odd user count parks one user on the
    mean, which keeps the mean exact and moves the spread by far less than the
    bootstrap's own Monte-Carlo error.
    """
    values = {}
    for index in range(n_users):
        if index == n_users - 1 and n_users % 2 == 1:
            values[index + 1] = mean
        else:
            values[index + 1] = mean + (amplitude if index % 2 == 0 else -amplitude)
    return values


def _run(
    run_id: str,
    seed: int | None,
    result: EvalResult,
    *,
    deterministic: bool = False,
    model_type: str = "sasrec",
    protocol: ProtocolManifest | None = None,
    warm_amplitude: float = 0.0,
) -> RetrievalRun:
    return RetrievalRun(
        run_id=run_id,
        model_type=model_type,
        seed=seed,
        deterministic=deterministic,
        protocol=protocol or _protocol(),
        result=result,
        per_user_recall={
            WARM_SLICE: _wobbled_vector(result.warm.recall, result.n_warm_users, warm_amplitude)
        },
    )


def _rescored(run: RetrievalRun, result: EvalResult) -> RetrievalRun:
    """Swap a run's result and rebuild the vector that has to agree with it.

    `dataclasses.replace` on the result alone would leave a vector describing the
    old numbers, which the gate correctly refuses — but as a vector/run mismatch
    rather than as whatever the test meant to exercise.
    """
    return replace(
        run,
        result=result,
        per_user_recall={WARM_SLICE: _wobbled_vector(result.warm.recall, result.n_warm_users)},
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


def _one_run_candidate(
    warm: float = 0.414,
    cold: float = 0.525,
    overall: float = 0.430,
    *,
    seed: int = 42,
) -> RetrievalRunSet:
    """The shape the one-run-per-configuration policy produces."""
    return RetrievalRunSet(
        model_id="sasrec",
        deterministic=False,
        runs=(_run(f"sasrec-{seed}", seed, _result(warm, cold, overall)),),
    )


TOLERANCE = RetrievalTolerance(cold=0.02, overall=0.02)

# The smallest admissible replicate count. Nothing about the band's arithmetic
# depends on it — only the Monte-Carlo error of the population term does, and at
# 1,000 replicates that is a percent or so of a half-width. Tests that assert a
# half-width allow for it; tests that assert a verdict sit nowhere near a
# boundary.
FAST = BandOptions(bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES)


def _gate(
    candidate: RetrievalRunSet, incumbent: RetrievalRunSet, **kwargs: Any
) -> RetrievalGateDecision:
    kwargs.setdefault("band_options", FAST)
    return retrieval_promotion_decision(candidate, incumbent, **kwargs)


def test_retrieval_gate_passes_three_seed_candidate_against_deterministic_incumbent():
    decision = _gate(_candidate(), _incumbent(), tolerance=TOLERANCE)

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
    """The point estimate is still the seed mean — and now says how firm it is.

    These three seeds average to exactly the +3% bar, which under a bare
    comparison was a pass. They also disagree with each other by more than the
    gain they are claiming, so the band the mean carries reaches well below zero
    and the claim is refused. That reversal is the whole point of this change.
    """
    runs = (
        _run("sasrec-42", 42, _result(0.390, 0.530, 0.435)),
        _run("sasrec-7", 7, _result(0.420, 0.530, 0.435)),
        _run("sasrec-13", 13, _result(0.426, 0.530, 0.435)),
    )
    candidate = RetrievalRunSet(model_id="sasrec", deterministic=False, runs=runs)

    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE)

    warm = next(clause for clause in decision.clauses if clause.name == "warm")
    assert warm.candidate == pytest.approx(0.412)
    assert warm.relative_change == pytest.approx(MIN_WARM_RELATIVE_GAIN)
    assert warm.band is not None
    assert warm.band.lower_bound < 0
    assert decision.status is RetrievalGateStatus.REFUSE


def test_warm_gain_boundary_is_inclusive():
    candidate = _candidate(warm=0.400 * 1.03, cold=0.530, overall=0.435)
    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE)
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
    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.REFUSE
    assert any(clause.name == failed_slice and not clause.passed for clause in decision.clauses)


def test_non_regression_boundaries_are_inclusive():
    candidate = _candidate(warm=0.414, cold=0.530 * 0.98, overall=0.435 * 0.98)
    assert _gate(candidate, _incumbent(), tolerance=TOLERANCE).status is RetrievalGateStatus.PROMOTE


def test_missing_seed_is_incomplete_not_a_negative_scientific_result():
    candidate = _candidate()
    candidate = replace(candidate, runs=candidate.runs[:-1])

    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE)

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

    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("duplicate seeds" in reason for reason in decision.reasons)
    assert any("duplicate run ids" in reason for reason in decision.reasons)


def test_deterministic_side_requires_exactly_one_seedless_run():
    incumbent = _incumbent()
    incumbent = replace(incumbent, runs=incumbent.runs * 2)
    decision = _gate(_candidate(), incumbent, tolerance=TOLERANCE)
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

    candidate_decision = _gate(deterministic_candidate, _incumbent(), tolerance=TOLERANCE)
    incumbent_decision = _gate(_candidate(), wrong_incumbent, tolerance=TOLERANCE)

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

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("does not match run-set model" in reason for reason in decision.reasons)
    assert any("same run ids" in reason for reason in decision.reasons)


def test_custom_seed_policy_is_reported_even_when_evidence_is_incomplete():
    decision = _gate(
        _candidate(),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(1, 2, 3),
    )

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert decision.required_seeds == (1, 2, 3)


# --- seed regime ------------------------------------------------------------


def test_a_single_run_reaches_a_verdict_when_the_caller_states_the_policy():
    decision = _gate(
        _one_run_candidate(),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(42,),
    )

    assert decision.status is RetrievalGateStatus.PROMOTE
    assert decision.required_seeds == (42,)
    assert decision.candidate_run_ids == ("sasrec-42",)
    # Everything the seed count does not touch is untouched.
    assert decision.serving_eligible is False
    assert decision.protocol_hash == _protocol().semantic_hash
    assert {clause.name for clause in decision.clauses} == {"warm", "cold", "overall"}


def test_a_single_seed_verdict_says_so_and_says_what_it_cannot_see():
    payload = _gate(
        _one_run_candidate(),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(42,),
    ).to_dict()

    assert payload["seed_regime"] == "single_seed"
    assert payload["required_seeds"] == (42,)
    basis = payload["uncertainty_basis"]
    assert isinstance(basis, str)
    assert "one training run" in basis
    assert "Training stochasticity is unmeasured" in basis


def test_the_single_seed_regime_is_visible_in_the_human_summary_too():
    summary = _gate(
        _one_run_candidate(),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(42,),
    ).summary()

    assert "seed regime: single_seed [42]" in summary
    assert "uncertainty: one training run" in summary


def test_one_run_is_incomplete_until_the_weaker_policy_is_asked_for():
    """The whole no-silent-weakening property: the default is still three seeds."""
    decision = _gate(_one_run_candidate(), _incumbent(), tolerance=TOLERANCE)

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert decision.seed_regime is SeedRegime.MULTI_SEED
    assert any("missing required seeds [7, 13]" in reason for reason in decision.reasons)


def test_a_single_seed_policy_still_pins_which_seed_the_run_used():
    decision = _gate(
        _one_run_candidate(seed=7),
        _incumbent(),
        tolerance=TOLERANCE,
        required_seeds=(42,),
    )

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("missing required seeds [42]" in reason for reason in decision.reasons)
    assert any("unexpected seeds [7]" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    ("candidate", "failed_slice"),
    [
        (_one_run_candidate(warm=0.411), "warm"),
        (_one_run_candidate(cold=0.500), "cold"),
        (_one_run_candidate(overall=0.420), "overall"),
    ],
)
def test_every_quality_clause_still_bites_under_the_single_seed_policy(
    candidate: RetrievalRunSet, failed_slice: str
):
    decision = _gate(candidate, _incumbent(), tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.REFUSE
    assert any(clause.name == failed_slice and not clause.passed for clause in decision.clauses)
    assert decision.seed_regime is SeedRegime.SINGLE_SEED


def test_the_other_checks_are_not_relaxed_along_with_the_seed_count():
    one_run = _one_run_candidate()
    mismatched = replace(
        one_run,
        runs=(replace(one_run.runs[0], protocol=_protocol(catalog_fingerprint="sha256:other")),),
    )
    deterministic = RetrievalRunSet(
        model_id="sasrec",
        deterministic=True,
        runs=(_run("sasrec-once", None, _result(0.414, 0.525, 0.430), deterministic=True),),
    )

    not_comparable = _gate(mismatched, _incumbent(), tolerance=TOLERANCE, required_seeds=(42,))
    stochastic_required = _gate(
        deterministic, _incumbent(), tolerance=TOLERANCE, required_seeds=(42,)
    )

    assert not_comparable.status is RetrievalGateStatus.NOT_COMPARABLE
    assert stochastic_required.status is RetrievalGateStatus.INCOMPLETE
    assert any("candidate must be stochastic" in reason for reason in stochastic_required.reasons)


def test_three_seeds_behave_exactly_as_before_the_single_seed_policy():
    """Regression guard for the regime the standing policy is a pause on."""
    implicit = _gate(_candidate(), _incumbent(), tolerance=TOLERANCE)
    explicit = _gate(_candidate(), _incumbent(), tolerance=TOLERANCE, required_seeds=REQUIRED_SEEDS)

    assert implicit == explicit
    assert implicit.status is RetrievalGateStatus.PROMOTE
    assert implicit.required_seeds == (42, 7, 13)
    assert implicit.seed_regime is SeedRegime.MULTI_SEED
    assert "3 training runs" in implicit.uncertainty_basis
    assert "quadrature" in implicit.uncertainty_basis
    # The clause arithmetic is the part that must not have moved.
    warm = next(clause for clause in implicit.clauses if clause.name == "warm")
    assert warm.required_change == MIN_WARM_RELATIVE_GAIN
    assert warm.candidate == pytest.approx(0.414)
    assert warm.incumbent == pytest.approx(0.400)
    cold = next(clause for clause in implicit.clauses if clause.name == "cold")
    assert cold.required_change == pytest.approx(-0.02)


def test_the_decision_payload_gained_exactly_the_two_regime_keys():
    """A future field lands here deliberately, not by accident."""
    payload = _gate(_candidate(), _incumbent(), tolerance=TOLERANCE)

    assert set(payload.to_dict()) == {
        "status",
        "stage",
        "metric",
        "k",
        "protocol_hash",
        "required_seeds",
        "candidate_run_ids",
        "incumbent_run_ids",
        "clauses",
        "reasons",
        "seed_regime",
        "uncertainty_basis",
        "serving_eligible",
        "promote",
    }


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

    decision = _gate(replace(candidate, runs=changed_runs), _incumbent(), tolerance=TOLERANCE)

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
        _gate(wrong_k, _incumbent(), tolerance=TOLERANCE).status
        is RetrievalGateStatus.NOT_COMPARABLE
    )


def test_population_mismatch_is_not_comparable():
    candidate = _candidate()
    changed = tuple(
        _rescored(run, replace(run.result, n_warm_users=1_940)) for run in candidate.runs
    )
    decision = _gate(replace(candidate, runs=changed), _incumbent(), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert "slice population mismatch" in decision.reasons[0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_metric_is_not_comparable(value: float):
    candidate = _candidate()
    changed = tuple(
        replace(run, result=replace(run.result, warm=replace(run.result.warm, recall=value)))
        for run in candidate.runs
    )
    decision = _gate(replace(candidate, runs=changed), _incumbent(), tolerance=TOLERANCE)
    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE


def test_zero_incumbent_value_refuses_an_undefined_relative_claim():
    decision = _gate(_candidate(), _incumbent(warm=0.0), tolerance=TOLERANCE)
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


# --- the warm claim's uncertainty band ---------------------------------------
#
# The fixtures below are the real 2026-09-05 measurement: SASRec at seed 42
# scored warm recall@500 of 0.465169 against item-item's 0.400144 over 1,939
# warm users, a +16.25% relative gain where the bar is +3%. It is the case the
# design has to be able to promote, so it is the case these tests are built on
# rather than a convenient invention.

ITEMITEM_WARM = 0.400144
SASREC_WARM = 0.465169
WARM_USERS = 1_939


def _measured(
    candidate_warm: float = SASREC_WARM,
    *,
    incumbent_amplitude: float = 0.0,
    candidate_amplitude: float = 0.0,
    seed_recalls: tuple[float, ...] | None = None,
) -> tuple[RetrievalRunSet, RetrievalRunSet]:
    """The measured comparison, with the per-user spread set by the caller.

    A negative amplitude flips the wobble's phase, so passing opposite signs on
    the two sides makes the *paired* difference swing by the sum of the two
    amplitudes — the way to build a large per-user difference spread out of two
    vectors that both stay inside [0, 1].
    """
    recalls = seed_recalls or (candidate_warm,)
    seeds = REQUIRED_SEEDS[: len(recalls)] if len(recalls) > 1 else (42,)
    candidate = RetrievalRunSet(
        model_id="sasrec",
        deterministic=False,
        runs=tuple(
            _run(
                f"sasrec-{seed}",
                seed,
                _result(recall, 0.525, 0.430, n_warm=WARM_USERS),
                warm_amplitude=candidate_amplitude,
            )
            for seed, recall in zip(seeds, recalls, strict=True)
        ),
    )
    incumbent = RetrievalRunSet(
        model_id=ITEMITEM_MODEL_TYPE,
        deterministic=True,
        runs=(
            _run(
                "itemitem",
                None,
                _result(ITEMITEM_WARM, 0.530, 0.435, n_warm=WARM_USERS),
                deterministic=True,
                model_type=ITEMITEM_MODEL_TYPE,
                warm_amplitude=incumbent_amplitude,
            ),
        ),
    )
    return candidate, incumbent


def _warm(decision: RetrievalGateDecision):
    return next(clause for clause in decision.clauses if clause.name == "warm")


def test_the_measured_sasrec_result_promotes_even_at_a_very_wide_per_user_spread():
    """The design has to be able to promote the case it was written against.

    The two vectors wobble in opposite phase at the widest amplitude the metric
    admits on these means, which makes the per-user difference spread 0.8 on a
    scale whose absolute maximum is 1.0. A +16.25% gain over 1,939 users
    survives that comfortably.
    """
    candidate, incumbent = _measured(incumbent_amplitude=0.4, candidate_amplitude=-0.4)

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    warm = _warm(decision)
    assert decision.status is RetrievalGateStatus.PROMOTE
    assert warm.relative_change == pytest.approx(0.162504, rel=1e-5)
    assert warm.band is not None
    assert warm.band.half_width == pytest.approx(
        ONE_SIDED_Z_95 * 0.8 / math.sqrt(WARM_USERS) / ITEMITEM_WARM, rel=0.05
    )
    assert warm.band.lower_bound > MIN_WARM_RELATIVE_GAIN


def test_no_per_user_spread_can_cost_the_measured_sasrec_result_its_promotion():
    """The margin, checked against the arithmetic rather than against a fixture.

    A per-user difference bounded in [-1, 1] cannot have a standard deviation
    above 1, so this is the widest band the measurement could possibly carry.
    The break-even spread is 1.42 — outside the range the metric can produce —
    which is what makes "this design is not too strict for the case it exists to
    judge" a fact about the numbers and not a property of the fixture.
    """
    point = (SASREC_WARM - ITEMITEM_WARM) / ITEMITEM_WARM
    widest = ONE_SIDED_Z_95 * 1.0 / math.sqrt(WARM_USERS) / ITEMITEM_WARM

    assert point - widest > MIN_WARM_RELATIVE_GAIN


def test_a_gain_above_the_bar_is_refused_when_its_band_does_not_reach_the_bar():
    """The discriminating case, and the reason for the strong reading.

    +3.1% over 1,939 users with a wide per-user spread: the point estimate is
    above the bar and the band's lower bound is above *zero*, so the weaker rule
    — "point estimate over 3%, and confident it is better at all" — would have
    promoted this. It is refused because the evidence does not support the claim
    the threshold actually makes, which is a gain of at least 3%.
    """
    candidate, incumbent = _measured(ITEMITEM_WARM * 1.031, candidate_amplitude=0.25)

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    warm = _warm(decision)
    assert decision.status is RetrievalGateStatus.REFUSE
    assert warm.relative_change is not None and warm.relative_change > MIN_WARM_RELATIVE_GAIN
    assert warm.band is not None
    assert 0 < warm.band.lower_bound < MIN_WARM_RELATIVE_GAIN
    assert "one-sided 95% lower bound" in warm.detail


def test_the_population_half_width_is_the_bootstrap_standard_error_it_claims_to_be():
    candidate, incumbent = _measured(candidate_amplitude=0.2)

    band = _warm(_gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))).band

    assert band is not None
    assert band.n_users == WARM_USERS
    assert band.population_half_width == pytest.approx(
        ONE_SIDED_Z_95 * 0.2 / math.sqrt(WARM_USERS) / ITEMITEM_WARM, rel=0.05
    )
    assert band.half_width == band.population_half_width
    assert band.bootstrap_replicates == MIN_BOOTSTRAP_REPLICATES
    assert band.bootstrap_seed == FAST.bootstrap_seed


def test_the_single_seed_band_names_the_noise_source_it_could_not_see():
    candidate, incumbent = _measured(candidate_amplitude=0.2)

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    band = _warm(decision).band
    assert band is not None
    assert band.basis == BAND_POPULATION_ONLY
    # `None`, never 0.0: no dispersion estimate is a different finding from a
    # dispersion estimate of zero, and only one of them is a claim.
    assert band.seed_half_width is None
    assert decision.seed_regime is SeedRegime.SINGLE_SEED
    assert "not on a different training seed" in decision.uncertainty_basis


def test_three_seeds_get_a_band_too_and_it_carries_the_seed_term():
    """The band is not a single-seed feature; the policy is a pause, not a repeal.

    Two three-seed candidates with the *same* mean warm recall: one whose seeds
    agree exactly and one whose seeds disagree. The second is the model the
    three-seed regime exists to catch, and the seed term is what catches it.
    """
    agreeing, incumbent = _measured(seed_recalls=(SASREC_WARM,) * 3)
    disagreeing, _ = _measured(seed_recalls=(SASREC_WARM - 0.12, SASREC_WARM, SASREC_WARM + 0.12))

    tight = _warm(_gate(agreeing, incumbent, tolerance=TOLERANCE))
    loose = _warm(_gate(disagreeing, incumbent, tolerance=TOLERANCE))

    assert tight.relative_change == pytest.approx(loose.relative_change)
    assert tight.band is not None and loose.band is not None
    assert tight.band.basis == BAND_SEED_AND_POPULATION
    assert loose.band.basis == BAND_SEED_AND_POPULATION
    assert tight.band.seed_half_width == 0.0
    assert loose.band.seed_half_width is not None and loose.band.seed_half_width > 0
    assert loose.band.half_width > tight.band.half_width
    assert tight.passed is True
    assert loose.passed is False


def test_the_multi_seed_band_combines_its_two_terms_in_quadrature():
    recalls = (SASREC_WARM - 0.02, SASREC_WARM, SASREC_WARM + 0.02)
    candidate, incumbent = _measured(seed_recalls=recalls, candidate_amplitude=0.2)

    band = _warm(_gate(candidate, incumbent, tolerance=TOLERANCE)).band

    assert band is not None
    assert band.seed_half_width is not None
    # t(0.95, 2) = 2.920 on a mean of three runs, relative to the incumbent.
    expected_seed = 2.920 * statistics.stdev(recalls) / math.sqrt(3) / ITEMITEM_WARM
    assert band.seed_half_width == pytest.approx(expected_seed, rel=1e-9)
    assert band.half_width == pytest.approx(
        math.hypot(band.seed_half_width, band.population_half_width), rel=1e-12
    )


@pytest.mark.parametrize("side", ["candidate", "incumbent"])
def test_a_missing_per_user_vector_is_incomplete_and_never_a_bare_comparison(side: str):
    """Fail closed: without the vectors there is no band, and so no verdict.

    The candidate here would sail through the old point-estimate comparison at
    +16.25%, which is exactly why the absence has to surface as missing evidence
    rather than as a pass.
    """
    candidate, incumbent = _measured()
    if side == "candidate":
        candidate = replace(
            candidate, runs=tuple(replace(run, per_user_recall={}) for run in candidate.runs)
        )
    else:
        incumbent = replace(incumbent, runs=(replace(incumbent.runs[0], per_user_recall={}),))

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert decision.clauses == ()
    assert any("does not fall back" in reason for reason in decision.reasons)


def test_an_empty_warm_slice_in_an_otherwise_populated_vector_is_still_missing():
    candidate, incumbent = _measured()
    candidate = replace(
        candidate,
        runs=tuple(
            replace(run, per_user_recall={"cold": {1: 0.5}, WARM_SLICE: {}})
            for run in candidate.runs
        ),
    )

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.INCOMPLETE
    assert any("no warm per-user recall vector" in reason for reason in decision.reasons)


def test_vectors_scoring_different_users_cannot_be_paired():
    candidate, incumbent = _measured()
    shifted = {
        user + 10_000: value
        for user, value in candidate.runs[0].per_user_recall[WARM_SLICE].items()
    }
    candidate = replace(
        candidate, runs=(replace(candidate.runs[0], per_user_recall={WARM_SLICE: shifted}),)
    )

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert any("must be paired user by user" in reason for reason in decision.reasons)


def test_a_vector_that_does_not_reproduce_its_own_run_is_not_comparable():
    candidate, incumbent = _measured()
    borrowed = dict(incumbent.runs[0].per_user_recall[WARM_SLICE])
    candidate = replace(
        candidate, runs=(replace(candidate.runs[0], per_user_recall={WARM_SLICE: borrowed}),)
    )

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert any("does not belong to this run" in reason for reason in decision.reasons)


def test_an_out_of_range_or_non_integer_vector_entry_is_not_comparable():
    candidate, incumbent = _measured()
    vector = dict(candidate.runs[0].per_user_recall[WARM_SLICE])
    vector[1] = 1.5
    candidate = replace(
        candidate, runs=(replace(candidate.runs[0], per_user_recall={WARM_SLICE: vector}),)
    )

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.NOT_COMPARABLE
    assert any("invalid recall for user" in reason for reason in decision.reasons)


def test_the_band_is_reproducible_run_to_run():
    """A gate whose verdict moved between two identical invocations is not a gate."""
    candidate, incumbent = _measured(candidate_amplitude=0.3)

    first = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))
    second = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert first == second


def test_the_guardrail_clauses_are_exactly_what_they_were():
    """Regression guard: the band changed the positive claim and nothing else.

    Run with a warm band wide enough to refuse the verdict, so that if the
    guardrails had picked up an uncertainty allowance of their own it would show
    here rather than hide behind a pass.
    """
    candidate, incumbent = _measured(ITEMITEM_WARM * 1.031, candidate_amplitude=0.4)

    decision = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,))

    assert decision.status is RetrievalGateStatus.REFUSE
    cold = next(clause for clause in decision.clauses if clause.name == "cold")
    overall = next(clause for clause in decision.clauses if clause.name == "overall")
    for clause, incumbent_value, candidate_value in (
        (cold, 0.530, 0.525),
        (overall, 0.435, 0.430),
    ):
        assert clause.band is None
        assert clause.incumbent == pytest.approx(incumbent_value)
        assert clause.candidate == pytest.approx(candidate_value)
        assert clause.required_change == pytest.approx(-0.02)
        assert clause.relative_change == pytest.approx(
            (candidate_value - incumbent_value) / incumbent_value
        )
        assert clause.passed is True
        assert "minimum allowed -2.00%" in clause.detail
        assert "lower bound" not in clause.detail


def test_the_guardrail_boundary_stays_inclusive_on_both_sides():
    at_limit, incumbent = _measured()
    at_limit = replace(
        at_limit,
        runs=(
            _rescored(
                at_limit.runs[0],
                _result(SASREC_WARM, 0.530 * 0.98, 0.435 * 0.98, n_warm=WARM_USERS),
            ),
        ),
    )
    beyond = replace(
        at_limit,
        runs=(
            _rescored(
                at_limit.runs[0],
                _result(SASREC_WARM, 0.530 * 0.979, 0.435 * 0.98, n_warm=WARM_USERS),
            ),
        ),
    )

    assert (
        _gate(at_limit, incumbent, tolerance=TOLERANCE, required_seeds=(42,)).status
        is RetrievalGateStatus.PROMOTE
    )
    refusal = _gate(beyond, incumbent, tolerance=TOLERANCE, required_seeds=(42,))
    assert refusal.status is RetrievalGateStatus.REFUSE
    assert any(clause.name == "cold" and not clause.passed for clause in refusal.clauses)


def test_the_decision_payload_carries_the_band_under_the_warm_clause_alone():
    candidate, incumbent = _measured(candidate_amplitude=0.2)

    payload = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,)).to_dict()

    clauses = payload["clauses"]
    assert isinstance(clauses, tuple)
    warm_payload, cold_payload, overall_payload = clauses
    assert set(warm_payload["band"]) == {
        "lower_bound",
        "half_width",
        "seed_half_width",
        "population_half_width",
        "n_users",
        "bootstrap_replicates",
        "bootstrap_seed",
        "basis",
    }
    assert warm_payload["band"]["basis"] == BAND_POPULATION_ONLY
    assert cold_payload["band"] is None
    assert overall_payload["band"] is None


def test_the_summary_shows_the_bound_the_verdict_was_taken_on():
    candidate, incumbent = _measured(candidate_amplitude=0.2)

    summary = _gate(candidate, incumbent, tolerance=TOLERANCE, required_seeds=(42,)).summary()

    assert "one-sided 95% lower bound" in summary
    assert BAND_POPULATION_ONLY in summary
    assert "required at least +3.00%" in summary


def test_band_options_refuse_a_replicate_count_too_small_to_estimate_anything():
    with pytest.raises(ValueError, match="at least"):
        BandOptions(bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES - 1)
    with pytest.raises(ValueError, match="must be an integer"):
        BandOptions(bootstrap_seed=1.5)  # type: ignore[arg-type]


def test_the_run_artifact_the_trainers_write_loads_straight_into_the_gate():
    """The vectors the gate needs are the ones `evaluate()` already publishes."""
    result = _result(0.5, 0.4, 0.47, n_warm=2, n_cold=2)
    result.per_user_recall = {
        "warm": {11: 0.6, 12: 0.4},
        "cold": {21: 0.5, 22: 0.3},
        "overall": {11: 0.6, 12: 0.4, 21: 0.5, 22: 0.3},
    }
    document = json.dumps(
        per_user_recall_document(
            result,
            run_id="sasrec-42",
            model_type="sasrec",
            seed=42,
            configuration_id="sasrec-full-25m-v1",
        )
    )

    vectors = per_user_recall_from_artifact(document)

    assert vectors[WARM_SLICE] == {11: 0.6, 12: 0.4}
    assert set(vectors) == {"warm", "cold", "overall"}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{", "not valid JSON"),
        ("[]", "must be one JSON object"),
        ('{"run_id": "x"}', "missing its 'per_user_recall' object"),
        ('{"per_user_recall": {"warm": [1, 2]}}', "must be an object"),
        ('{"per_user_recall": {"warm": {"nope": 0.5}}}', "non-integer user id"),
        ('{"per_user_recall": {"warm": {"1": "0.5"}}}', "non-numeric recall"),
    ],
)
def test_a_malformed_per_user_recall_artifact_is_refused_not_guessed_at(
    document: str, message: str
):
    with pytest.raises(RetrievalRunNotUsableError, match=message):
        per_user_recall_from_artifact(document)


def test_the_mlflow_reader_passes_supplied_vectors_through():
    vectors = {WARM_SLICE: {1: 0.5, 2: 0.3}}

    run = retrieval_run_from_mlflow(_MlflowRun(), per_user_recall=vectors)
    without = retrieval_run_from_mlflow(_MlflowRun())

    assert run.per_user_recall == vectors
    # No vectors is a run the gate reports as incomplete, not one it waves
    # through — the reader itself stays a pure envelope read.
    assert without.per_user_recall == {}
