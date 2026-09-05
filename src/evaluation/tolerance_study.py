"""Measure the retrieval gate's cold and overall non-regression tolerances.

`RetrievalTolerance` has no defaults on purpose, so `make gate-retrieval`
cannot return a verdict until somebody measures them.  This module is the
instrument that does the measuring.  It is deliberately *not* a source of
numbers: it consumes evidence from runs the gate does not read, applies one
written rule, and either proposes two fractions or refuses and says why.

The rule, and the reasoning behind every constant below, is
`docs/model-planning/contracts/retrieval-tolerance-measurement.md`.  The short
version: the tolerance must cover the noise floor of the statistic the gate
reads, which has two independent components — the training seed (the candidate
is stochastic; the item-item incumbent is not) and the finite evaluation
population (a paired, user-level bootstrap).  Variances add, so the two
one-sided half-widths combine in quadrature, and the result is rounded up,
floored, and capped.

**One-run studies.**  The 2026-09-05 standing policy — one run per
configuration until the ladder reaches the transformer rungs — leaves no
across-seed dispersion to estimate, so the bootstrap term carries the whole
tolerance.  That is admitted, not silently accommodated: a one-run study must
declare `single_run_justification`, the seed fields come back `None` rather
than `0.0`, and the report says `seed_regime=single_seed`.  What is lost is
real and worth repeating wherever this is read: a user-level bootstrap measures
sampling noise over the evaluated population, not training stochasticity, so a
model whose seeds genuinely disagree produces no wider tolerance than one whose
seeds agree, and nothing downstream can tell the two apart.

Fail-closed is the whole design.  Insufficient or incomparable evidence
produces a status and a reason, never a number, and
`ToleranceStudyReport.as_tolerance()` raises rather than handing an
unestablished value to the gate.

**Where the arithmetic lives.**  The bootstrap resampler, the Student-t table
and the vector-reconstruction tolerances are imported from
`src/evaluation/retrieval_gate.py`, which uses the same rule to put an
uncertainty band on the warm claim.  The rule in the document is one rule; two
implementations of it would be one rule and a near-miss.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any, Final

import numpy as np

from .manifest import ProtocolManifest, ProtocolManifestError, validate_metric_value
from .protocol import K_CANDIDATES, EvalResult, UserMetrics
from .retrieval_gate import (
    _MEAN_RECONSTRUCTION_ABS_TOL,
    _MEAN_RECONSTRUCTION_REL_TOL,
    DEFAULT_BOOTSTRAP_REPLICATES,
    DEFAULT_BOOTSTRAP_SEED,
    GATE_METRIC,
    GATE_STAGE,
    ITEMITEM_MODEL_TYPE,
    MIN_BOOTSTRAP_REPLICATES,
    MIN_WARM_RELATIVE_GAIN,
    ONE_SIDED_Z_95,
    REQUIRED_SEEDS,
    RetrievalTolerance,
    SeedRegime,
    _bootstrap_replicate_means,
    _paired_differences,
    _t_quantile,
)

STUDY_SCHEMA_VERSION: Final = 1

# The two slices the retrieval gate applies a tolerance to. Warm carries a
# positive claim (+3% relative), not a non-regression clause, so it is reported
# when the evidence has it and never gates anything.
GATING_SLICES: Final = ("cold", "overall")
DIAGNOSTIC_SLICES: Final = ("warm",)
_ALL_SLICES: Final = ("warm", "cold", "overall")

# Three seeds is the floor rather than the target for a study that measures
# dispersion at all. A standard deviation over three samples is a coarse
# estimate, which is exactly what the Student-t multiplier below is for.
MIN_STUDY_SEEDS: Final = 3

# The one admissible study size below that floor, and only with a declared
# justification. A one-run study is not a cheaper dispersion estimate — it is a
# different measurement, covering the evaluation population and nothing else.
# Two runs remain inadmissible: they are neither, and a standard deviation over
# two samples carries a t multiplier of 6.314 that would buy a tolerance far
# wider than the evidence supports.
SINGLE_RUN_STUDY_SIZE: Final = 1

# The bootstrap defaults, the Student-t table, the resampler and the vector
# reconstruction tolerances now live in `retrieval_gate` and are imported above:
# the gate measures the warm claim's uncertainty band with the same instrument
# this module measures the guardrails' tolerances with, and two copies of that
# arithmetic would eventually stop being the same arithmetic. The names are
# re-exported from here unchanged, because this is the module the measurement
# protocol document points at.

# Below this, a tolerance is finer than the resolution these numbers are
# published and reproduced at, and a tolerance of exactly zero would fail on
# floating-point dust.
TOLERANCE_FLOOR: Final = 0.005

# A guardrail that permits a larger relative loss on a supporting slice than
# the relative gain the gate demands on its primary slice cannot tell
# "improved" from "traded cold away for warm". Exceeding it is a statement
# about the measurement, not the model, so the study refuses rather than
# clamping — a clamped value would not cover the noise it claims to.
TOLERANCE_CAP: Final = MIN_WARM_RELATIVE_GAIN

# A tenth of a percentage point: coarse enough to be a number a human types
# into `make gate-retrieval`, fine enough that the rounding is not itself a
# policy decision.
TOLERANCE_ROUNDING_STEP: Final = 0.001

# How far apart two half-widths must be before one is called dominant. Under
# quadrature the smaller of two terms in this band contributes under ~12% of
# the total, so calling either one "the" source would misdirect effort.
_DOMINANCE_RATIO: Final = 1.25

_EXIT_PROPOSED: Final = 0
_EXIT_DECLINED: Final = 1
_EXIT_UNDECIDED: Final = 2

DERIVATION_SAME_CONFIGURATION: Final = "same-configuration-disjoint-seeds"
DERIVATION_SURROGATE: Final = "surrogate-configuration"


class ToleranceStudyStatus(StrEnum):
    PROPOSED = "proposed"
    TOO_NOISY = "too_noisy"
    DEGENERATE = "degenerate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"


class ToleranceEvidenceError(ValueError):
    """The evidence document is malformed and could not be loaded at all.

    Distinct from a refusal: a refusal is a verdict about well-formed evidence,
    whereas this is the loader declining to guess what a document meant.
    """


class ToleranceNotEstablishedError(RuntimeError):
    """A tolerance was asked for that the study did not establish."""


@dataclass(frozen=True)
class StudyRun:
    """One evaluated run, plus the per-user vectors behind its slice means.

    ``per_user_recall`` maps a slice name to ``{user_id: recall}``. It is what
    makes the population term measurable, and it is validated against
    ``result`` rather than trusted: a vector whose mean does not reproduce the
    published slice recall belongs to some other run.
    """

    run_id: str
    model_type: str
    seed: int | None
    configuration_id: str
    protocol: ProtocolManifest
    result: EvalResult
    per_user_recall: Mapping[str, Mapping[int, float]]


@dataclass(frozen=True)
class ToleranceStudyInputs:
    """Everything one study needs, with its provenance declared up front.

    ``single_run_justification`` is required exactly when ``study_runs`` holds
    one run, and forbidden otherwise.  It is the recorded reason a tolerance is
    allowed to rest on the population term alone — normally the standing
    one-run-per-configuration policy — and it exists so the weaker regime is
    something the evidence document states rather than something a reader has
    to infer from an array length.
    """

    model_type: str
    gate_configuration_id: str
    incumbent: StudyRun
    study_runs: tuple[StudyRun, ...]
    surrogate_delta: str | None = None
    zero_seed_variance_justification: str | None = None
    single_run_justification: str | None = None


@dataclass(frozen=True)
class StudyOptions:
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED
    tolerance_floor: float = TOLERANCE_FLOOR
    tolerance_cap: float = TOLERANCE_CAP
    rounding_step: float = TOLERANCE_ROUNDING_STEP

    def __post_init__(self) -> None:
        if type(self.bootstrap_replicates) is not int:
            raise ValueError("bootstrap_replicates must be an integer")
        if self.bootstrap_replicates < MIN_BOOTSTRAP_REPLICATES:
            raise ValueError(
                f"bootstrap_replicates must be at least {MIN_BOOTSTRAP_REPLICATES}, "
                f"got {self.bootstrap_replicates}"
            )
        if type(self.bootstrap_seed) is not int:
            raise ValueError("bootstrap_seed must be an integer")
        for name in ("tolerance_floor", "tolerance_cap", "rounding_step"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric, got {value!r}")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if not 0 <= self.tolerance_floor <= 1:
            raise ValueError("tolerance_floor must be a fraction in [0, 1]")
        if not 0 < self.tolerance_cap <= 1:
            raise ValueError("tolerance_cap must be a fraction in (0, 1]")
        if self.tolerance_floor > self.tolerance_cap:
            raise ValueError("tolerance_floor must not exceed tolerance_cap")
        if self.rounding_step <= 0:
            raise ValueError("rounding_step must be positive")


@dataclass(frozen=True)
class SliceNoise:
    """The measured noise floor of one slice, and what the rule makes of it.

    The three seed-dispersion fields are ``None`` — never ``0.0`` — when the
    study carries a single run.  A zero would say "measured, and the seed
    changes nothing", which is the `degenerate` finding and a materially
    different claim from "not measured at all".
    """

    slice_name: str
    gating: bool
    n_users: int
    incumbent_recall: float
    seed_recalls: tuple[float, ...]
    seed_mean: float
    seed_sd: float | None
    seed_relative_range: float | None
    seed_half_width: float | None
    bootstrap_run_ids: tuple[str, ...]
    bootstrap_standard_error: float
    bootstrap_standard_error_range: tuple[float, float]
    bootstrap_half_width: float
    bootstrap_percentile_half_width: float
    combined_half_width: float
    dominant_component: str
    proposed_tolerance: float | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ToleranceStudyReport:
    status: ToleranceStudyStatus
    model_type: str
    derivation: str
    seed_regime: SeedRegime
    single_run_justification: str | None
    surrogate_delta: str | None
    protocol_hash: str | None
    incumbent_run_id: str
    incumbent_model_type: str
    study_run_ids: tuple[str, ...]
    study_seeds: tuple[int, ...]
    bootstrap_replicates: int
    bootstrap_seed: int
    tolerance_floor: float
    tolerance_cap: float
    slices: tuple[SliceNoise, ...]
    reasons: tuple[str, ...]

    @property
    def established(self) -> bool:
        return self.status is ToleranceStudyStatus.PROPOSED

    def tolerance_for(self, slice_name: str) -> float | None:
        for measured in self.slices:
            if measured.slice_name == slice_name:
                return measured.proposed_tolerance
        return None

    def as_tolerance(self) -> RetrievalTolerance:
        """Hand the gate its two fractions, or refuse.

        The refusal is the point: this is the one boundary where an
        unestablished number could leak into a promotion decision, so it raises
        instead of returning something plausible.
        """
        if not self.established:
            raise ToleranceNotEstablishedError(
                f"study status is {self.status.value!r}; no tolerance was established "
                f"({'; '.join(self.reasons) or 'no reason recorded'})"
            )
        cold = self.tolerance_for("cold")
        overall = self.tolerance_for("overall")
        if cold is None or overall is None:
            raise ToleranceNotEstablishedError(
                "study is marked proposed but a gating slice has no tolerance"
            )
        return RetrievalTolerance(cold=cold, overall=overall)

    def summary(self) -> str:
        headline = self.status.value.upper().replace("_", " ")
        lines = [
            f"{headline} — recall@{K_CANDIDATES} tolerance study for {self.model_type}",
            f"  derivation: {self.derivation}",
            f"  seed regime: {self.seed_regime.value} ({len(self.study_run_ids)} study run(s))",
        ]
        if self.single_run_justification:
            lines.append(f"  single-run justification: {self.single_run_justification}")
        if self.surrogate_delta:
            lines.append(f"  surrogate delta: {self.surrogate_delta}")
        for measured in self.slices:
            scope = "gating" if measured.gating else "diagnostic"
            value = (
                "not established"
                if measured.proposed_tolerance is None
                else f"{measured.proposed_tolerance:.1%}"
            )
            unmeasured_seed = measured.seed_half_width is None
            seed_term = "not measured" if unmeasured_seed else f"{measured.seed_half_width:.2%}"
            source = (
                "[no seed term measured]"
                if unmeasured_seed
                else f"[{measured.dominant_component}-dominated]"
            )
            lines.append(
                f"  {measured.slice_name} ({scope}, n={measured.n_users}): {value} "
                f"— seed {seed_term}, "
                f"population {measured.bootstrap_half_width:.2%}, "
                f"combined {measured.combined_half_width:.2%} {source}"
            )
            lines.extend(f"    note: {note}" for note in measured.notes)
        lines.extend(f"  reason: {reason}" for reason in self.reasons)
        if self.established:
            lines.append(
                "  publish this report before running the gate's seeds; "
                f"the tolerances are denominated in incumbent {self.incumbent_run_id!r}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["seed_regime"] = self.seed_regime.value
        payload["established"] = self.established
        return payload


def measure_retrieval_tolerance(
    inputs: ToleranceStudyInputs,
    *,
    options: StudyOptions | None = None,
) -> ToleranceStudyReport:
    """Derive the gate's cold and overall tolerances from a noise study.

    Two admissible study sizes: `m >= MIN_STUDY_SEEDS`, which measures both
    noise terms and combines them in quadrature, and exactly one run, which
    measures the population term alone and must say why it is allowed to.  The
    report names which of the two produced it.
    """
    resolved = options or StudyOptions()
    run_ids = tuple(run.run_id for run in inputs.study_runs)
    seeds = tuple(run.seed for run in inputs.study_runs if type(run.seed) is int)
    derivation = (
        DERIVATION_SAME_CONFIGURATION
        if _study_configuration(inputs) == inputs.gate_configuration_id
        else DERIVATION_SURROGATE
    )

    incomplete = _structural_reasons(inputs, derivation)
    if incomplete:
        return _report(
            ToleranceStudyStatus.INSUFFICIENT_EVIDENCE,
            inputs,
            derivation,
            resolved,
            run_ids,
            seeds,
            reasons=incomplete,
        )

    invalid = _comparability_reasons(inputs)
    if invalid:
        return _report(
            ToleranceStudyStatus.NOT_COMPARABLE,
            inputs,
            derivation,
            resolved,
            run_ids,
            seeds,
            reasons=invalid,
        )

    protocol_hash = inputs.incumbent.protocol.semantic_hash
    measured: list[SliceNoise] = []
    for slice_name in _ALL_SLICES:
        if slice_name not in GATING_SLICES and not _diagnostic_available(inputs, slice_name):
            continue
        measured.append(_measure_slice(inputs, slice_name, resolved))

    # `seed_sd is not None` matters: a one-run study did not measure a spread of
    # zero, it measured no spread, and that is admitted through
    # `single_run_justification` rather than through this clause.
    degenerate = [
        f"{m.slice_name} slice has zero seed spread and no justification; "
        "a stochastic model whose seed changes nothing is a seed that is not wired through"
        for m in measured
        if m.gating
        and m.seed_sd is not None
        and m.seed_sd == 0.0
        and not inputs.zero_seed_variance_justification
    ]
    too_noisy = [
        f"{m.slice_name} combined half-width {m.combined_half_width:.2%} exceeds the "
        f"{resolved.tolerance_cap:.2%} cap; reduce the noise rather than widen the guardrail"
        for m in measured
        if m.gating and m.combined_half_width > resolved.tolerance_cap
    ]

    if degenerate or too_noisy:
        status = ToleranceStudyStatus.DEGENERATE if degenerate else ToleranceStudyStatus.TOO_NOISY
        withheld = tuple(
            m if not m.gating else replace(m, proposed_tolerance=None) for m in measured
        )
        return _report(
            status,
            inputs,
            derivation,
            resolved,
            run_ids,
            seeds,
            reasons=tuple(degenerate) + tuple(too_noisy),
            protocol_hash=protocol_hash,
            slices=withheld,
        )

    return _report(
        ToleranceStudyStatus.PROPOSED,
        inputs,
        derivation,
        resolved,
        run_ids,
        seeds,
        reasons=(),
        protocol_hash=protocol_hash,
        slices=tuple(measured),
    )


def _study_seed_regime(inputs: ToleranceStudyInputs) -> SeedRegime:
    """Which regime this study measured, read off the run count.

    Shares the gate's vocabulary on purpose: a `single_seed` study is the only
    kind that can legitimately supply a `single_seed` verdict's tolerances, and
    reusing the word is what makes that pairing checkable by a reader.
    """
    return (
        SeedRegime.SINGLE_SEED
        if len(inputs.study_runs) == SINGLE_RUN_STUDY_SIZE
        else SeedRegime.MULTI_SEED
    )


def _study_configuration(inputs: ToleranceStudyInputs) -> str | None:
    """The one configuration the study runs share, or None when they disagree."""
    configurations = {
        run.configuration_id for run in inputs.study_runs if isinstance(run.configuration_id, str)
    }
    if len(configurations) != 1:
        return None
    return next(iter(configurations))


def _structural_reasons(inputs: ToleranceStudyInputs, derivation: str) -> tuple[str, ...]:
    reasons: list[str] = []
    if not _is_normalized(inputs.model_type):
        reasons.append("model_type must be a normalized non-empty string")
    if not _is_normalized(inputs.gate_configuration_id):
        reasons.append("gate_configuration_id must be a normalized non-empty string")

    incumbent = inputs.incumbent
    if incumbent.model_type != ITEMITEM_MODEL_TYPE:
        reasons.append(
            f"incumbent model must be {ITEMITEM_MODEL_TYPE!r}, got {incumbent.model_type!r}"
        )
    if incumbent.seed is not None:
        reasons.append("the item-item incumbent is deterministic and must not carry a seed")
    if inputs.model_type == incumbent.model_type:
        reasons.append("study model and incumbent model identities must differ")

    run_count = len(inputs.study_runs)
    if run_count == SINGLE_RUN_STUDY_SIZE:
        if not _is_normalized(inputs.single_run_justification or ""):
            reasons.append(
                "a one-run study has no across-seed dispersion to estimate, so it must declare "
                "single_run_justification — the recorded reason this tolerance may rest on the "
                "population term alone"
            )
    elif run_count < MIN_STUDY_SEEDS:
        reasons.append(
            f"a dispersion estimate needs at least {MIN_STUDY_SEEDS} seeded runs, "
            f"got {run_count}"
        )
    elif inputs.single_run_justification:
        reasons.append(
            f"single_run_justification was supplied but the study carries {run_count} runs; "
            "one of the two declarations is wrong"
        )
    seeds = [run.seed for run in inputs.study_runs]
    if any(type(seed) is not int for seed in seeds):
        reasons.append("every study run must carry an integer training seed")
    else:
        integer_seeds = [seed for seed in seeds if type(seed) is int]
        duplicates = sorted({seed for seed in integer_seeds if integer_seeds.count(seed) > 1})
        if duplicates:
            reasons.append(f"study seeds must be distinct; repeated {duplicates}")

    configuration = _study_configuration(inputs)
    if configuration is None:
        reasons.append("study runs must share exactly one configuration_id")
    elif not _is_normalized(configuration):
        reasons.append("configuration_id must be a normalized non-empty string")

    # The one place the "do not tune the tolerance to the candidate result" rule
    # is machine-checked rather than trusted to prose: a study of the very
    # configuration the gate will judge may not reuse the gate's own seeds.
    if derivation == DERIVATION_SAME_CONFIGURATION:
        reused = sorted(seed for seed in seeds if type(seed) is int and seed in REQUIRED_SEEDS)
        if reused:
            reasons.append(
                f"study reuses the gate's own seeds {reused} at the gate configuration; "
                "derive the tolerance from seeds the gate does not read"
            )
        if inputs.surrogate_delta:
            reasons.append(
                "surrogate_delta was supplied but the study configuration matches the gate's; "
                "one of the two declarations is wrong"
            )
    elif not _is_normalized(inputs.surrogate_delta or ""):
        reasons.append(
            "a surrogate study must declare surrogate_delta — what the transfer assumption "
            "is being asked to span"
        )

    for run in inputs.study_runs:
        if run.model_type != inputs.model_type:
            reasons.append(
                f"study run {run.run_id!r} model type {run.model_type!r} does not match the "
                f"declared study model {inputs.model_type!r}"
            )
    for run in (inputs.incumbent, *inputs.study_runs):
        if not _is_normalized(run.run_id):
            reasons.append(f"run id {run.run_id!r} must be a normalized non-empty string")
    ids = [run.run_id for run in (inputs.incumbent, *inputs.study_runs)]
    repeated_ids = sorted({run_id for run_id in ids if ids.count(run_id) > 1})
    if repeated_ids:
        reasons.append(f"run ids must be distinct; repeated {repeated_ids}")

    for slice_name in GATING_SLICES:
        if not _vector_for(inputs.incumbent, slice_name):
            reasons.append(
                f"incumbent is missing per-user recall for the {slice_name} slice; "
                "the population term cannot be paired without it"
            )
        if not any(_vector_for(run, slice_name) for run in inputs.study_runs):
            reasons.append(f"no study run carries per-user recall for the {slice_name} slice")
    return tuple(reasons)


def _comparability_reasons(inputs: ToleranceStudyInputs) -> tuple[str, ...]:
    reasons: list[str] = []
    all_runs = (inputs.incumbent, *inputs.study_runs)
    reference = inputs.incumbent.protocol
    for run in inputs.study_runs:
        mismatches = reference.mismatches(run.protocol)
        if mismatches:
            reasons.append(
                f"run {run.run_id!r} asks a different evaluation question; fields differ: "
                + ", ".join(sorted(mismatches))
            )

    for run in all_runs:
        if run.protocol.stage != GATE_STAGE:
            reasons.append(f"run {run.run_id!r} has stage {run.protocol.stage!r}, not retrieval")
        if run.protocol.primary_metric != GATE_METRIC:
            reasons.append(
                f"run {run.run_id!r} has primary metric {run.protocol.primary_metric!r}, "
                "not recall"
            )
        if run.protocol.k != K_CANDIDATES or run.result.k != K_CANDIDATES:
            reasons.append(
                f"run {run.run_id!r} must be scored at k={K_CANDIDATES}; "
                f"protocol={run.protocol.k}, result={run.result.k}"
            )
        for slice_name in _ALL_SLICES:
            try:
                validate_metric_value(
                    f"run {run.run_id!r} {slice_name} recall", _slice_recall(run.result, slice_name)
                )
            except ProtocolManifestError as exc:
                reasons.append(str(exc))
        for name in ("n_warm_users", "n_cold_users"):
            value = getattr(run.result, name)
            if type(value) is not int or value <= 0:
                reasons.append(f"run {run.run_id!r} {name} must be a positive integer")

    for name in ("n_warm_users", "n_cold_users"):
        counts = {getattr(run.result, name) for run in all_runs}
        if len(counts) > 1:
            reasons.append(
                f"runs disagree about {name} ({sorted(str(c) for c in counts)}); "
                "they were not scored on the same population"
            )
    if reasons:
        # Every check below indexes into per-user vectors using counts and slice
        # values these checks have just shown to be untrustworthy.
        return tuple(reasons)

    for run in all_runs:
        for slice_name in _ALL_SLICES:
            vector = _vector_for(run, slice_name)
            if vector is None:
                continue
            reasons.extend(_vector_reasons(run, slice_name, vector))
    if reasons:
        return tuple(reasons)

    for slice_name in _ALL_SLICES:
        incumbent_vector = _vector_for(inputs.incumbent, slice_name)
        if incumbent_vector is None:
            continue
        for run in inputs.study_runs:
            vector = _vector_for(run, slice_name)
            if vector is None:
                continue
            if set(vector) != set(incumbent_vector):
                reasons.append(
                    f"run {run.run_id!r} and the incumbent scored different users on the "
                    f"{slice_name} slice; the bootstrap must be paired"
                )
    for slice_name in GATING_SLICES:
        incumbent_recall = _slice_recall(inputs.incumbent.result, slice_name)
        if incumbent_recall <= 0:
            reasons.append(
                f"incumbent {slice_name} recall@{K_CANDIDATES} is zero; a relative tolerance "
                "has no denominator"
            )
    return tuple(reasons)


def _vector_reasons(run: StudyRun, slice_name: str, vector: Mapping[int, float]) -> tuple[str, ...]:
    reasons: list[str] = []
    expected_users = _slice_user_count(run.result, slice_name)
    if len(vector) != expected_users:
        reasons.append(
            f"run {run.run_id!r} {slice_name} vector holds {len(vector)} users but the run "
            f"reports {expected_users}"
        )
    for user_id, value in vector.items():
        if type(user_id) is not int:
            reasons.append(f"run {run.run_id!r} {slice_name} vector has a non-integer user id")
            break
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            reasons.append(
                f"run {run.run_id!r} {slice_name} vector has an invalid recall for user "
                f"{user_id}: {value!r}"
            )
            break
    if reasons or not vector:
        return tuple(reasons)

    reconstructed = statistics.fmean(float(value) for value in vector.values())
    published = _slice_recall(run.result, slice_name)
    if not math.isclose(
        reconstructed,
        published,
        rel_tol=_MEAN_RECONSTRUCTION_REL_TOL,
        abs_tol=_MEAN_RECONSTRUCTION_ABS_TOL,
    ):
        reasons.append(
            f"run {run.run_id!r} {slice_name} vector averages {reconstructed:.6f} but the run "
            f"published {published:.6f}; the vector does not belong to this run"
        )
    return tuple(reasons)


def _measure_slice(
    inputs: ToleranceStudyInputs, slice_name: str, options: StudyOptions
) -> SliceNoise:
    incumbent_recall = _slice_recall(inputs.incumbent.result, slice_name)
    seed_recalls = tuple(_slice_recall(run.result, slice_name) for run in inputs.study_runs)
    seed_mean = statistics.fmean(seed_recalls)

    # A single run leaves these three genuinely unmeasured. `None` rather than
    # zero, so that every consumer — the quadrature below, the degenerate check,
    # the summary line — has to handle "we do not know" explicitly instead of
    # inheriting the arithmetic of "we know it is nothing".
    seed_sd: float | None = None
    seed_range: float | None = None
    seed_half_width: float | None = None
    if len(seed_recalls) > 1:
        seed_sd = statistics.stdev(seed_recalls)
        seed_range = (max(seed_recalls) - min(seed_recalls)) / seed_mean if seed_mean > 0 else 0.0
        seed_half_width = (
            _t_quantile(len(seed_recalls) - 1) * seed_sd / math.sqrt(len(seed_recalls))
        ) / incumbent_recall

    # Every run's bootstrap draws from the same seed on purpose. The resample
    # indices are then identical across runs, so a spread in the per-run
    # standard errors reflects a real difference in the paired differences
    # rather than Monte-Carlo wobble — which is what makes the spread worth
    # reporting as a note below.
    incumbent_vector = _vector_for(inputs.incumbent, slice_name)
    bootstrap_ids: list[str] = []
    standard_errors: list[float] = []
    percentile_half_widths: list[float] = []
    if incumbent_vector is not None:
        for run in inputs.study_runs:
            vector = _vector_for(run, slice_name)
            if vector is None:
                continue
            differences = _paired_differences(vector, incumbent_vector)
            replicates = _bootstrap_replicate_means(
                differences, options.bootstrap_replicates, options.bootstrap_seed
            )
            bootstrap_ids.append(run.run_id)
            standard_errors.append(float(np.std(replicates, ddof=1)))
            point = float(np.mean(differences))
            percentile_half_widths.append(abs(point - float(np.percentile(replicates, 5.0))))

    standard_error = statistics.fmean(standard_errors) if standard_errors else 0.0
    bootstrap_half_width = ONE_SIDED_Z_95 * standard_error / incumbent_recall
    percentile_half_width = (
        statistics.fmean(percentile_half_widths) / incumbent_recall
        if percentile_half_widths
        else 0.0
    )
    combined = (
        bootstrap_half_width
        if seed_half_width is None
        else math.hypot(seed_half_width, bootstrap_half_width)
    )

    notes: list[str] = []
    if seed_half_width is None:
        notes.append(
            "seed term not measured: one study run. This tolerance covers evaluation-population "
            "sampling only and cannot distinguish a real regression from training-seed variation"
        )
    elif seed_sd == 0.0:
        justification = inputs.zero_seed_variance_justification
        notes.append(
            f"zero seed spread across {len(seed_recalls)} runs"
            + (f"; declared: {justification}" if justification else "")
        )
    if not standard_errors:
        notes.append("no per-user vectors supplied; the population term is not measured")
    elif len(standard_errors) > 1:
        spread = max(standard_errors) - min(standard_errors)
        if spread > 0:
            notes.append(
                f"bootstrap standard error varies {min(standard_errors):.6f}–"
                f"{max(standard_errors):.6f} across {len(standard_errors)} study runs"
            )
    if bootstrap_half_width > 0:
        deviation = abs(percentile_half_width - bootstrap_half_width) / bootstrap_half_width
        if deviation > 0.25:
            notes.append(
                f"percentile and normal half-widths differ by {deviation:.0%}; the bootstrap "
                "distribution may not be well approximated by its standard deviation"
            )

    tolerance = max(options.tolerance_floor, _round_up(combined, options.rounding_step))
    return SliceNoise(
        slice_name=slice_name,
        gating=slice_name in GATING_SLICES,
        n_users=_slice_user_count(inputs.incumbent.result, slice_name),
        incumbent_recall=incumbent_recall,
        seed_recalls=seed_recalls,
        seed_mean=seed_mean,
        seed_sd=seed_sd,
        seed_relative_range=seed_range,
        seed_half_width=seed_half_width,
        bootstrap_run_ids=tuple(bootstrap_ids),
        bootstrap_standard_error=standard_error,
        bootstrap_standard_error_range=(
            (min(standard_errors), max(standard_errors)) if standard_errors else (0.0, 0.0)
        ),
        bootstrap_half_width=bootstrap_half_width,
        bootstrap_percentile_half_width=percentile_half_width,
        combined_half_width=combined,
        dominant_component=_dominant(seed_half_width, bootstrap_half_width),
        proposed_tolerance=tolerance if slice_name in GATING_SLICES else None,
        notes=tuple(notes),
    )


def _dominant(seed_half_width: float | None, bootstrap_half_width: float) -> str:
    if seed_half_width is None:
        # Deliberately not "population": that word would claim a seed term was
        # measured and lost the comparison, when none was measured at all.
        return "population-only"
    if bootstrap_half_width == 0.0 and seed_half_width == 0.0:
        return "none"
    if seed_half_width >= _DOMINANCE_RATIO * bootstrap_half_width:
        return "seed"
    if bootstrap_half_width >= _DOMINANCE_RATIO * seed_half_width:
        return "population"
    return "balanced"


def _round_up(value: float, step: float) -> float:
    # The epsilon keeps a value that is already an exact multiple of the step
    # from being pushed up a whole step by float representation.
    steps = math.ceil(value / step - 1e-9)
    return round(max(steps, 0) * step, 12)


def _slice_recall(result: EvalResult, slice_name: str) -> float:
    metrics: UserMetrics = getattr(result, slice_name)
    return metrics.recall


def _slice_user_count(result: EvalResult, slice_name: str) -> int:
    if slice_name == "warm":
        return result.n_warm_users
    if slice_name == "cold":
        return result.n_cold_users
    return result.n_warm_users + result.n_cold_users


def _vector_for(run: StudyRun, slice_name: str) -> Mapping[int, float] | None:
    vector = run.per_user_recall.get(slice_name)
    return vector if vector else None


def _diagnostic_available(inputs: ToleranceStudyInputs, slice_name: str) -> bool:
    """Whether a non-gating slice can be reported at all.

    A diagnostic slice is worth reporting only when both terms are measurable
    on it — a seed-only half-width would understate the noise and read as a
    tolerance the gate could have used.
    """
    if _slice_recall(inputs.incumbent.result, slice_name) <= 0:
        return False
    if _vector_for(inputs.incumbent, slice_name) is None:
        return False
    return any(_vector_for(run, slice_name) is not None for run in inputs.study_runs)


def _is_normalized(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _report(
    status: ToleranceStudyStatus,
    inputs: ToleranceStudyInputs,
    derivation: str,
    options: StudyOptions,
    run_ids: Sequence[str],
    seeds: Sequence[int],
    *,
    reasons: Sequence[str],
    protocol_hash: str | None = None,
    slices: Sequence[SliceNoise] = (),
) -> ToleranceStudyReport:
    return ToleranceStudyReport(
        status=status,
        model_type=inputs.model_type,
        derivation=derivation,
        seed_regime=_study_seed_regime(inputs),
        single_run_justification=inputs.single_run_justification,
        surrogate_delta=inputs.surrogate_delta,
        protocol_hash=protocol_hash,
        incumbent_run_id=inputs.incumbent.run_id,
        incumbent_model_type=inputs.incumbent.model_type,
        study_run_ids=tuple(run_ids),
        study_seeds=tuple(seeds),
        bootstrap_replicates=options.bootstrap_replicates,
        bootstrap_seed=options.bootstrap_seed,
        tolerance_floor=options.tolerance_floor,
        tolerance_cap=options.tolerance_cap,
        slices=tuple(slices),
        reasons=tuple(reasons),
    )


def study_inputs_from_json(document: str) -> ToleranceStudyInputs:
    """Load one evidence document, refusing to guess at anything it omits."""
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise ToleranceEvidenceError(f"evidence is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ToleranceEvidenceError("evidence must be one JSON object")
    version = payload.get("schema_version")
    if version != STUDY_SCHEMA_VERSION:
        raise ToleranceEvidenceError(
            f"unsupported evidence schema_version {version!r}; expected {STUDY_SCHEMA_VERSION}"
        )
    runs = payload.get("study_runs")
    if not isinstance(runs, list) or not runs:
        raise ToleranceEvidenceError("evidence must carry a non-empty study_runs array")
    return ToleranceStudyInputs(
        model_type=_require_str(payload, "model_type"),
        gate_configuration_id=_require_str(payload, "gate_configuration_id"),
        incumbent=_run_from_payload(_require_object(payload, "incumbent"), "incumbent"),
        study_runs=tuple(
            _run_from_payload(_as_object(run, f"study_runs[{index}]"), f"study_runs[{index}]")
            for index, run in enumerate(runs)
        ),
        surrogate_delta=_optional_str(payload, "surrogate_delta"),
        zero_seed_variance_justification=_optional_str(payload, "zero_seed_variance_justification"),
        single_run_justification=_optional_str(payload, "single_run_justification"),
    )


def _run_from_payload(payload: Mapping[str, Any], where: str) -> StudyRun:
    metrics = _require_object(payload, "metrics", where)
    try:
        protocol = ProtocolManifest.from_dict(_require_object(payload, "protocol", where))
    except ProtocolManifestError as exc:
        raise ToleranceEvidenceError(f"{where}: invalid protocol: {exc}") from exc
    result = EvalResult(
        warm=UserMetrics(recall=_require_float(metrics, "warm_recall", where), ndcg=0.0),
        cold=UserMetrics(recall=_require_float(metrics, "cold_recall", where), ndcg=0.0),
        overall=UserMetrics(recall=_require_float(metrics, "overall_recall", where), ndcg=0.0),
        n_warm_users=_require_int(metrics, "n_warm_users", where),
        n_cold_users=_require_int(metrics, "n_cold_users", where),
        k=protocol.k,
    )
    seed = payload.get("seed")
    if seed is not None and type(seed) is not int:
        raise ToleranceEvidenceError(f"{where}: seed must be an integer or null")
    vectors_payload = payload.get("per_user_recall", {})
    if not isinstance(vectors_payload, dict):
        raise ToleranceEvidenceError(f"{where}: per_user_recall must be an object")
    vectors: dict[str, Mapping[int, float]] = {}
    for slice_name, vector in vectors_payload.items():
        if slice_name not in _ALL_SLICES:
            raise ToleranceEvidenceError(
                f"{where}: per_user_recall has unknown slice {slice_name!r}"
            )
        vectors[slice_name] = _vector_from_payload(vector, f"{where}.per_user_recall.{slice_name}")
    return StudyRun(
        run_id=_require_str(payload, "run_id", where),
        model_type=_require_str(payload, "model_type", where),
        seed=seed,
        configuration_id=_require_str(payload, "configuration_id", where),
        protocol=protocol,
        result=result,
        per_user_recall=vectors,
    )


def _vector_from_payload(payload: object, where: str) -> Mapping[int, float]:
    if not isinstance(payload, dict):
        raise ToleranceEvidenceError(f"{where} must be an object of user id to recall")
    vector: dict[int, float] = {}
    for key, value in payload.items():
        try:
            user_id = int(key)
        except (TypeError, ValueError) as exc:
            raise ToleranceEvidenceError(f"{where} has a non-integer user id {key!r}") from exc
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ToleranceEvidenceError(f"{where} has a non-numeric recall for user {user_id}")
        vector[user_id] = float(value)
    return vector


def _as_object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ToleranceEvidenceError(f"{where} must be an object")
    return value


def _require_object(
    payload: Mapping[str, Any], key: str, where: str = "evidence"
) -> Mapping[str, Any]:
    if key not in payload:
        raise ToleranceEvidenceError(f"{where} is missing {key!r}")
    return _as_object(payload[key], f"{where}.{key}")


def _require_str(payload: Mapping[str, Any], key: str, where: str = "evidence") -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ToleranceEvidenceError(f"{where}.{key} must be a string")
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToleranceEvidenceError(f"evidence.{key} must be a string or null")
    return value


def _require_float(payload: Mapping[str, Any], key: str, where: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ToleranceEvidenceError(f"{where}.metrics.{key} must be numeric")
    return float(value)


def _require_int(payload: Mapping[str, Any], key: str, where: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise ToleranceEvidenceError(f"{where}.metrics.{key} must be an integer")
    return value


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.tolerance_study",
        description=(
            "Derive the retrieval gate's cold and overall non-regression tolerances from a "
            "seed-and-bootstrap noise study, or from the bootstrap alone when the evidence "
            "declares a one-run study. Refuses rather than guessing."
        ),
    )
    parser.add_argument("--evidence", required=True, metavar="PATH")
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from pathlib import Path

    try:
        inputs = study_inputs_from_json(Path(args.evidence).read_text(encoding="utf-8"))
        options = StudyOptions(
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
        )
    except (ToleranceEvidenceError, OSError, ValueError) as exc:
        print(f"INSUFFICIENT EVIDENCE — {exc}")
        return _EXIT_UNDECIDED

    report = measure_retrieval_tolerance(inputs, options=options)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True) if args.json else report.summary())
    if report.status is ToleranceStudyStatus.PROPOSED:
        return _EXIT_PROPOSED
    if report.status in (ToleranceStudyStatus.TOO_NOISY, ToleranceStudyStatus.DEGENERATE):
        return _EXIT_DECLINED
    return _EXIT_UNDECIDED


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
