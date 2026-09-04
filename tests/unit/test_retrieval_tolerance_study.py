"""Truth table for the retrieval tolerance study.

The fixtures are built so the two noise components are known in advance: every
per-user vector is a fixed alternating wobble of amplitude ``AMPLITUDE`` around
an exact mean, so the paired bootstrap's standard error is ``AMPLITUDE / sqrt(n)``
analytically and the seed term can be recomputed by hand from the slice means.
That is what lets these tests assert the written rule rather than whatever the
implementation happens to produce.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from src.evaluation.manifest import PROTOCOL_SCHEMA_VERSION, ProtocolManifest
from src.evaluation.protocol import EvalResult, UserMetrics
from src.evaluation.retrieval_gate import ITEMITEM_MODEL_TYPE, REQUIRED_SEEDS
from src.evaluation.tolerance_study import (
    DERIVATION_SAME_CONFIGURATION,
    DERIVATION_SURROGATE,
    MIN_BOOTSTRAP_REPLICATES,
    ONE_SIDED_Z_95,
    STUDY_SCHEMA_VERSION,
    TOLERANCE_FLOOR,
    StudyOptions,
    StudyRun,
    ToleranceEvidenceError,
    ToleranceNotEstablishedError,
    ToleranceStudyInputs,
    ToleranceStudyReport,
    ToleranceStudyStatus,
    main,
    measure_retrieval_tolerance,
    study_inputs_from_json,
)

WARM_USERS = tuple(range(1, 41))
COLD_USERS = tuple(range(1_001, 1_101))
AMPLITUDE = 0.01

STUDY_MODEL = "sasrec"
GATE_CONFIG = "sasrec-full-25m-v1"
STUDY_SEEDS = (101, 202, 303)

# One-sided 95% Student-t values for the seed means these tests build.
T_DF2 = 2.920
T_DF4 = 2.132

FAST = StudyOptions(bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES)


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
        "positive_history_filter": "strict-prior-equal-timestamp-excluded-v1",
        "seen_item_filter": "watched-strictly-prior-excluded-v1",
        "dismissal_filter": "dismissals-absent-from-dataset-v1",
        "target_filter": "target-retained-never-negative-v1",
        "candidate_filter": "unfiltered-retrieval-then-point-in-time-exclusions-v1",
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


def _vector(users: Sequence[int], mean: float, amplitude: float) -> dict[int, float]:
    """An exact-mean per-user vector with a known dispersion.

    The alternating wobble keeps the mean exactly on ``mean`` for an even user
    count, so the harness's "does this vector reproduce the published slice
    recall?" check passes for the right reason rather than by luck.
    """
    assert len(users) % 2 == 0, "the alternating wobble needs an even user count"
    return {
        user: mean + (amplitude if index % 2 == 0 else -amplitude)
        for index, user in enumerate(users)
    }


def _run(
    run_id: str,
    seed: int | None,
    *,
    cold_mean: float,
    warm_mean: float = 0.30,
    amplitude: float = AMPLITUDE,
    model_type: str = STUDY_MODEL,
    configuration_id: str = GATE_CONFIG,
    protocol: ProtocolManifest | None = None,
    warm_users: Sequence[int] = WARM_USERS,
    cold_users: Sequence[int] = COLD_USERS,
    k: int = 500,
) -> StudyRun:
    warm = _vector(warm_users, warm_mean, amplitude)
    cold = _vector(cold_users, cold_mean, amplitude)
    overall = {**warm, **cold}
    result = EvalResult(
        warm=UserMetrics(recall=statistics.fmean(warm.values()), ndcg=0.0),
        cold=UserMetrics(recall=statistics.fmean(cold.values()), ndcg=0.0),
        overall=UserMetrics(recall=statistics.fmean(overall.values()), ndcg=0.0),
        n_warm_users=len(warm),
        n_cold_users=len(cold),
        k=k,
    )
    return StudyRun(
        run_id=run_id,
        model_type=model_type,
        seed=seed,
        configuration_id=configuration_id,
        protocol=protocol or _protocol(),
        result=result,
        per_user_recall={"warm": warm, "cold": cold, "overall": overall},
    )


def _incumbent(**changes: object) -> StudyRun:
    defaults: dict[str, object] = {
        "cold_mean": 0.50,
        "warm_mean": 0.30,
        "amplitude": 0.0,
        "model_type": ITEMITEM_MODEL_TYPE,
        "configuration_id": "itemitem-cosine-topn200-v1",
    }
    defaults.update(changes)
    return _run("itemitem-run", None, **defaults)  # type: ignore[arg-type]


# Cold slice means chosen so the seed sd is exactly 0.006 on a 0.50 incumbent.
COLD_MEANS = (0.500, 0.506, 0.494)
WARM_MEANS = (0.300, 0.301, 0.299)


def _study_runs(
    *,
    seeds: Sequence[int] = STUDY_SEEDS,
    configuration_id: str = GATE_CONFIG,
    cold_means: Sequence[float] = COLD_MEANS,
    warm_means: Sequence[float] = WARM_MEANS,
) -> tuple[StudyRun, ...]:
    return tuple(
        _run(
            f"{STUDY_MODEL}-{seed}",
            seed,
            cold_mean=cold_means[index % len(cold_means)],
            warm_mean=warm_means[index % len(warm_means)],
            configuration_id=configuration_id,
        )
        for index, seed in enumerate(seeds)
    )


def _inputs(**changes: object) -> ToleranceStudyInputs:
    defaults: dict[str, object] = {
        "model_type": STUDY_MODEL,
        "gate_configuration_id": GATE_CONFIG,
        "incumbent": _incumbent(),
        "study_runs": _study_runs(),
    }
    defaults.update(changes)
    return ToleranceStudyInputs(**defaults)  # type: ignore[arg-type]


def _measured(report: ToleranceStudyReport, slice_name: str):  # type: ignore[no-untyped-def]
    return next(m for m in report.slices if m.slice_name == slice_name)


# --- happy path -------------------------------------------------------------


def test_complete_evidence_proposes_both_gating_tolerances():
    report = measure_retrieval_tolerance(_inputs(), options=FAST)

    assert report.status is ToleranceStudyStatus.PROPOSED
    assert report.established is True
    assert report.derivation == DERIVATION_SAME_CONFIGURATION
    assert report.study_seeds == STUDY_SEEDS
    assert report.protocol_hash == _protocol().semantic_hash
    assert report.incumbent_run_id == "itemitem-run"
    assert report.reasons == ()

    tolerance = report.as_tolerance()
    assert 0 < tolerance.cold <= 1
    assert 0 < tolerance.overall <= 1
    assert tolerance.cold == report.tolerance_for("cold")
    assert tolerance.overall == report.tolerance_for("overall")
    assert "publish this report" in report.summary()


def test_seed_half_width_follows_the_written_rule():
    report = measure_retrieval_tolerance(_inputs(), options=FAST)
    cold = _measured(report, "cold")

    expected_sd = statistics.stdev(COLD_MEANS)
    assert cold.seed_sd == pytest.approx(expected_sd)
    assert cold.seed_half_width == pytest.approx(
        T_DF2 * expected_sd / math.sqrt(3) / 0.50, rel=1e-9
    )
    # The gate reads a mean, so more seeds must tighten the term even when the
    # spread is identical.
    assert cold.seed_relative_range == pytest.approx(
        (max(COLD_MEANS) - min(COLD_MEANS)) / statistics.fmean(COLD_MEANS)
    )


def test_more_seeds_tighten_the_seed_term_through_both_t_and_sqrt_m():
    five = _study_runs(
        seeds=(101, 202, 303, 404, 505),
        cold_means=(0.500, 0.506, 0.494, 0.500, 0.506),
        warm_means=(0.300, 0.301, 0.299, 0.300, 0.301),
    )
    report = measure_retrieval_tolerance(_inputs(study_runs=five), options=FAST)
    cold = _measured(report, "cold")

    expected_sd = statistics.stdev((0.500, 0.506, 0.494, 0.500, 0.506))
    assert cold.seed_half_width == pytest.approx(
        T_DF4 * expected_sd / math.sqrt(5) / 0.50, rel=1e-9
    )


def test_population_term_is_a_paired_bootstrap_of_the_per_user_difference():
    report = measure_retrieval_tolerance(_inputs(), options=FAST)
    cold = _measured(report, "cold")

    # The fixture's wobble makes sd(d_u) = AMPLITUDE exactly, so the paired
    # standard error is AMPLITUDE / sqrt(n) up to Monte-Carlo error.
    analytic_se = AMPLITUDE / math.sqrt(len(COLD_USERS))
    assert cold.bootstrap_standard_error == pytest.approx(analytic_se, rel=0.15)
    assert cold.bootstrap_half_width == pytest.approx(
        ONE_SIDED_Z_95 * cold.bootstrap_standard_error / 0.50, rel=1e-9
    )
    assert cold.bootstrap_run_ids == tuple(f"{STUDY_MODEL}-{seed}" for seed in STUDY_SEEDS)


def test_combined_half_width_is_the_quadrature_sum_rounded_up():
    report = measure_retrieval_tolerance(_inputs(), options=FAST)

    for measured in report.slices:
        assert measured.combined_half_width == pytest.approx(
            math.hypot(measured.seed_half_width, measured.bootstrap_half_width), rel=1e-12
        )
        if measured.proposed_tolerance is None:
            continue
        assert measured.proposed_tolerance >= measured.combined_half_width
        assert measured.proposed_tolerance >= TOLERANCE_FLOOR
        steps = measured.proposed_tolerance / 0.001
        assert steps == pytest.approx(round(steps), abs=1e-6)


def test_this_fixture_is_seed_dominated_and_says_so():
    cold = _measured(measure_retrieval_tolerance(_inputs(), options=FAST), "cold")
    assert cold.seed_half_width > cold.bootstrap_half_width
    assert cold.dominant_component == "seed"


def test_report_is_deterministic():
    first = measure_retrieval_tolerance(_inputs(), options=FAST)
    second = measure_retrieval_tolerance(_inputs(), options=FAST)
    assert first.to_dict() == second.to_dict()


def test_warm_is_reported_as_diagnostic_and_never_gates():
    report = measure_retrieval_tolerance(_inputs(), options=FAST)
    warm = _measured(report, "warm")

    assert warm.gating is False
    assert warm.proposed_tolerance is None
    assert report.tolerance_for("warm") is None
    assert "diagnostic" in report.summary()


def test_warm_is_omitted_entirely_when_its_vectors_are_missing():
    stripped = tuple(
        replace(
            run,
            per_user_recall={"cold": run.per_user_recall["cold"], **_overall_only(run)},
        )
        for run in _study_runs()
    )
    incumbent = _incumbent()
    incumbent = replace(
        incumbent,
        per_user_recall={
            "cold": incumbent.per_user_recall["cold"],
            **_overall_only(incumbent),
        },
    )
    report = measure_retrieval_tolerance(
        _inputs(incumbent=incumbent, study_runs=stripped), options=FAST
    )

    assert report.status is ToleranceStudyStatus.PROPOSED
    assert {m.slice_name for m in report.slices} == {"cold", "overall"}


def _overall_only(run: StudyRun) -> Mapping[str, Mapping[int, float]]:
    return {"overall": run.per_user_recall["overall"]}


# --- insufficient evidence --------------------------------------------------


def test_two_seeds_cannot_support_a_dispersion_estimate():
    report = measure_retrieval_tolerance(
        _inputs(study_runs=_study_runs(seeds=(101, 202))), options=FAST
    )
    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("at least 3 seeded runs" in reason for reason in report.reasons)
    assert report.slices == ()


def test_repeated_seeds_are_not_three_measurements():
    runs = _study_runs(seeds=(101, 101, 303))
    runs = tuple(replace(run, run_id=f"run-{index}") for index, run in enumerate(runs))
    report = measure_retrieval_tolerance(_inputs(study_runs=runs), options=FAST)

    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("distinct" in reason for reason in report.reasons)


def test_reusing_the_gates_own_seeds_at_the_gate_configuration_is_refused():
    report = measure_retrieval_tolerance(
        _inputs(study_runs=_study_runs(seeds=REQUIRED_SEEDS)), options=FAST
    )
    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("seeds the gate does not read" in reason for reason in report.reasons)


def test_a_surrogate_study_may_use_any_seed_but_must_declare_its_delta():
    surrogate = _study_runs(seeds=REQUIRED_SEEDS, configuration_id="sasrec-2epoch-pilot")

    undeclared = measure_retrieval_tolerance(_inputs(study_runs=surrogate), options=FAST)
    assert undeclared.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("surrogate_delta" in reason for reason in undeclared.reasons)

    declared = measure_retrieval_tolerance(
        _inputs(study_runs=surrogate, surrogate_delta="2 epochs instead of 8; all else identical"),
        options=FAST,
    )
    assert declared.status is ToleranceStudyStatus.PROPOSED
    assert declared.derivation == DERIVATION_SURROGATE
    assert "surrogate delta" in declared.summary()


def test_a_surrogate_delta_on_a_same_configuration_study_is_contradictory():
    report = measure_retrieval_tolerance(_inputs(surrogate_delta="fewer epochs"), options=FAST)
    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("one of the two declarations is wrong" in reason for reason in report.reasons)


def test_missing_per_user_vectors_block_the_population_term():
    runs = tuple(replace(run, per_user_recall={}) for run in _study_runs())
    report = measure_retrieval_tolerance(_inputs(study_runs=runs), options=FAST)

    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("cold slice" in reason for reason in report.reasons)
    assert any("overall slice" in reason for reason in report.reasons)


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"seed": 42}, "must not carry a seed"),
        ({"model_type": "twotower"}, "incumbent model must be"),
    ],
)
def test_the_incumbent_must_be_the_deterministic_item_item_run(
    change: dict[str, object], fragment: str
):
    report = measure_retrieval_tolerance(
        _inputs(incumbent=replace(_incumbent(), **change)), options=FAST  # type: ignore[arg-type]
    )
    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any(fragment in reason for reason in report.reasons)


def test_study_runs_must_agree_on_one_configuration():
    runs = _study_runs()
    runs = (*runs[:2], replace(runs[2], configuration_id="something-else"))
    report = measure_retrieval_tolerance(_inputs(study_runs=runs), options=FAST)

    assert report.status is ToleranceStudyStatus.INSUFFICIENT_EVIDENCE
    assert any("exactly one configuration_id" in reason for reason in report.reasons)


# --- not comparable ---------------------------------------------------------


def test_a_different_evaluation_question_is_not_comparable():
    runs = _study_runs()
    runs = (
        *runs[:2],
        replace(runs[2], protocol=_protocol(raw_data_revision="md5:something-else.dir")),
    )
    report = measure_retrieval_tolerance(_inputs(study_runs=runs), options=FAST)

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("raw_data_revision" in reason for reason in report.reasons)


def test_a_ranking_protocol_cannot_produce_a_retrieval_tolerance():
    ranking = _protocol(stage="ranking", primary_metric="ndcg")
    runs = tuple(replace(run, protocol=ranking) for run in _study_runs())
    report = measure_retrieval_tolerance(
        _inputs(incumbent=replace(_incumbent(), protocol=ranking), study_runs=runs), options=FAST
    )

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("not retrieval" in reason for reason in report.reasons)


def test_a_different_k_is_not_comparable():
    at_ten = _protocol(k=10)
    runs = tuple(
        replace(run, protocol=at_ten, result=replace(run.result, k=10)) for run in _study_runs()
    )
    incumbent = _incumbent()
    incumbent = replace(incumbent, protocol=at_ten, result=replace(incumbent.result, k=10))
    report = measure_retrieval_tolerance(
        _inputs(incumbent=incumbent, study_runs=runs), options=FAST
    )

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("must be scored at k=500" in reason for reason in report.reasons)


def test_disagreeing_slice_populations_are_not_comparable():
    runs = _study_runs()
    runs = (
        *runs[:2],
        replace(runs[2], result=replace(runs[2].result, n_cold_users=99)),
    )
    report = measure_retrieval_tolerance(_inputs(study_runs=runs), options=FAST)

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("n_cold_users" in reason for reason in report.reasons)


def test_a_vector_that_does_not_reproduce_its_published_mean_is_refused():
    runs = _study_runs()
    broken = replace(
        runs[0],
        result=replace(
            runs[0].result, cold=UserMetrics(recall=runs[0].result.cold.recall + 0.05, ndcg=0.0)
        ),
    )
    report = measure_retrieval_tolerance(_inputs(study_runs=(broken, *runs[1:])), options=FAST)

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("does not belong to this run" in reason for reason in report.reasons)


def test_unpaired_user_sets_are_refused_rather_than_bootstrapped():
    shifted = _run(
        f"{STUDY_MODEL}-101",
        101,
        cold_mean=COLD_MEANS[0],
        warm_mean=WARM_MEANS[0],
        cold_users=tuple(user + 5_000 for user in COLD_USERS),
    )
    report = measure_retrieval_tolerance(
        _inputs(study_runs=(shifted, *_study_runs()[1:])), options=FAST
    )

    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("must be paired" in reason for reason in report.reasons)


def test_a_zero_incumbent_slice_has_no_denominator():
    report = measure_retrieval_tolerance(_inputs(incumbent=_incumbent(cold_mean=0.0)), options=FAST)
    assert report.status is ToleranceStudyStatus.NOT_COMPARABLE
    assert any("no denominator" in reason for reason in report.reasons)


# --- degenerate and capped --------------------------------------------------


def test_zero_seed_spread_without_a_justification_is_degenerate():
    flat = _study_runs(cold_means=(0.50, 0.50, 0.50), warm_means=(0.30, 0.30, 0.30))
    report = measure_retrieval_tolerance(_inputs(study_runs=flat), options=FAST)

    assert report.status is ToleranceStudyStatus.DEGENERATE
    assert any("not wired through" in reason for reason in report.reasons)
    assert all(m.proposed_tolerance is None for m in report.slices if m.gating)


def test_a_justified_zero_seed_spread_falls_back_to_the_population_term():
    flat = _study_runs(cold_means=(0.50, 0.50, 0.50), warm_means=(0.30, 0.30, 0.30))
    report = measure_retrieval_tolerance(
        _inputs(
            study_runs=flat,
            zero_seed_variance_justification=(
                "every cold user routes to the deterministic popularity fallback"
            ),
        ),
        options=FAST,
    )
    cold = _measured(report, "cold")

    assert report.status is ToleranceStudyStatus.PROPOSED
    assert cold.seed_half_width == 0.0
    assert cold.combined_half_width == pytest.approx(cold.bootstrap_half_width)
    assert cold.dominant_component == "population"
    assert any("zero seed spread" in note for note in cold.notes)


def test_the_cap_boundary_is_inclusive_and_exceeding_it_withholds_every_number():
    baseline = measure_retrieval_tolerance(_inputs(), options=FAST)
    widest = max(m.combined_half_width for m in baseline.slices if m.gating)

    at_cap = measure_retrieval_tolerance(
        _inputs(),
        options=StudyOptions(bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES, tolerance_cap=widest),
    )
    assert at_cap.status is ToleranceStudyStatus.PROPOSED

    over_cap = measure_retrieval_tolerance(
        _inputs(),
        options=StudyOptions(
            bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES, tolerance_cap=widest * 0.999
        ),
    )
    assert over_cap.status is ToleranceStudyStatus.TOO_NOISY
    assert all(m.proposed_tolerance is None for m in over_cap.slices if m.gating)
    assert any("reduce the noise" in reason for reason in over_cap.reasons)


def test_noise_below_the_floor_lands_on_the_floor():
    quiet_runs = tuple(
        _run(
            f"{STUDY_MODEL}-{seed}",
            seed,
            cold_mean=cold,
            warm_mean=warm,
            amplitude=1e-6,
        )
        for seed, cold, warm in zip(
            STUDY_SEEDS,
            (0.500_000, 0.500_001, 0.499_999),
            (0.300_000, 0.300_001, 0.299_999),
            strict=True,
        )
    )
    report = measure_retrieval_tolerance(
        _inputs(incumbent=_incumbent(amplitude=0.0), study_runs=quiet_runs), options=FAST
    )

    assert report.status is ToleranceStudyStatus.PROPOSED
    assert report.as_tolerance().cold == TOLERANCE_FLOOR
    assert report.as_tolerance().overall == TOLERANCE_FLOOR


# --- refusing to hand over a number -----------------------------------------


@pytest.mark.parametrize(
    "inputs",
    [
        _inputs(study_runs=_study_runs(seeds=(101, 202))),
        _inputs(study_runs=_study_runs(cold_means=(0.50, 0.50, 0.50))),
    ],
)
def test_as_tolerance_refuses_unless_the_study_established_one(inputs: ToleranceStudyInputs):
    report = measure_retrieval_tolerance(inputs, options=FAST)
    assert not report.established
    with pytest.raises(ToleranceNotEstablishedError):
        report.as_tolerance()


@pytest.mark.parametrize(
    "change",
    [
        {"bootstrap_replicates": MIN_BOOTSTRAP_REPLICATES - 1},
        {"bootstrap_replicates": 1.5},
        {"tolerance_cap": 0.0},
        {"tolerance_floor": 0.5, "tolerance_cap": 0.03},
        {"rounding_step": 0.0},
        {"tolerance_cap": math.inf},
    ],
)
def test_study_options_reject_settings_that_would_quietly_weaken_the_rule(
    change: dict[str, object],
):
    with pytest.raises(ValueError):
        StudyOptions(**change)  # type: ignore[arg-type]


# --- evidence document ------------------------------------------------------


def _document(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "model_type": STUDY_MODEL,
        "gate_configuration_id": GATE_CONFIG,
        "incumbent": _run_payload(_incumbent()),
        "study_runs": [_run_payload(run) for run in _study_runs()],
    }
    payload.update(changes)
    return payload


def _run_payload(run: StudyRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "model_type": run.model_type,
        "seed": run.seed,
        "configuration_id": run.configuration_id,
        "protocol": run.protocol.to_dict(),
        "metrics": {
            "warm_recall": run.result.warm.recall,
            "cold_recall": run.result.cold.recall,
            "overall_recall": run.result.overall.recall,
            "n_warm_users": run.result.n_warm_users,
            "n_cold_users": run.result.n_cold_users,
        },
        "per_user_recall": {
            name: {str(user): value for user, value in vector.items()}
            for name, vector in run.per_user_recall.items()
        },
    }


def test_evidence_document_round_trips_into_the_same_report():
    loaded = study_inputs_from_json(json.dumps(_document()))
    assert (
        measure_retrieval_tolerance(loaded, options=FAST).to_dict()
        == measure_retrieval_tolerance(_inputs(), options=FAST).to_dict()
    )


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "one JSON object"),
        (json.dumps(_document(schema_version=2)), "schema_version"),
        (json.dumps(_document(study_runs=[])), "non-empty study_runs"),
        (json.dumps(_document(model_type=7)), "model_type must be a string"),
    ],
)
def test_the_loader_refuses_to_guess(document: str, fragment: str):
    with pytest.raises(ToleranceEvidenceError, match=fragment):
        study_inputs_from_json(document)


def test_the_loader_rejects_an_unknown_slice_and_a_non_integer_user_id():
    with_unknown = _document()
    incumbent = dict(with_unknown["incumbent"])  # type: ignore[arg-type]
    incumbent["per_user_recall"] = {"lukewarm": {"1": 0.5}}
    with_unknown["incumbent"] = incumbent
    with pytest.raises(ToleranceEvidenceError, match="unknown slice"):
        study_inputs_from_json(json.dumps(with_unknown))

    with_bad_key = _document()
    incumbent = dict(with_bad_key["incumbent"])  # type: ignore[arg-type]
    incumbent["per_user_recall"] = {"cold": {"user-7": 0.5}}
    with_bad_key["incumbent"] = incumbent
    with pytest.raises(ToleranceEvidenceError, match="non-integer user id"):
        study_inputs_from_json(json.dumps(with_bad_key))


# --- CLI --------------------------------------------------------------------


def test_cli_exits_zero_on_a_proposal_and_two_when_it_cannot_read_evidence(tmp_path, capsys):
    evidence = tmp_path / "study.json"
    evidence.write_text(json.dumps(_document()), encoding="utf-8")

    assert (
        main(
            [
                "--evidence",
                str(evidence),
                "--bootstrap-replicates",
                str(MIN_BOOTSTRAP_REPLICATES),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "proposed"
    assert payload["established"] is True

    assert main(["--evidence", str(tmp_path / "absent.json")]) == 2
    assert "INSUFFICIENT EVIDENCE" in capsys.readouterr().out


def test_cli_exits_one_when_it_measured_and_declined(tmp_path, capsys):
    document = _document(
        study_runs=[
            _run_payload(run)
            for run in _study_runs(cold_means=(0.50, 0.50, 0.50), warm_means=(0.30, 0.30, 0.30))
        ]
    )
    evidence = tmp_path / "degenerate.json"
    evidence.write_text(json.dumps(document), encoding="utf-8")

    assert (
        main(
            [
                "--evidence",
                str(evidence),
                "--bootstrap-replicates",
                str(MIN_BOOTSTRAP_REPLICATES),
            ]
        )
        == 1
    )
    assert "DEGENERATE" in capsys.readouterr().out
