"""Strict recall@500 promotion gate for candidate retrieval models.

The existing :mod:`src.evaluation.gate` is intentionally ranking-only: it
reads NDCG and ADR 0001's ranker tolerances.  This module is a separate API so
a retrieval result can never be promoted by accidentally passing through the
ranking gate.

**Seed policy.**  How many training runs a verdict rests on is an argument, not
a constant: the caller passes ``required_seeds`` and the gate holds the run set
to exactly that.  The default stays the three-seed set, so nobody obtains a
one-run verdict without asking for one.  The 2026-09-05 standing policy — one
run per configuration until the ladder reaches the transformer rungs — is
exercised by passing a single seed, and the decision then records
``seed_regime=single_seed`` and says in ``uncertainty_basis`` what a single run
cannot tell anyone.  Nothing else about the gate changes with the seed count.

**Uncertainty on the positive claim.**  The warm clause used to be a bare
comparison of two numbers: candidate mean over incumbent, at least +3%.  That
was defensible while the warm figure was a mean over three seeds and stopped
being defensible when it became a single draw, so every verdict now carries a
one-sided 95% band on the warm gain and the clause passes on the band's *lower*
bound rather than on the point estimate.  The claim it makes is therefore "we
are confident the gain is at least 3%" rather than "the number came out above
3%", which is the only form of the claim a single run can support honestly.

The band is built from the same arithmetic the guardrail tolerances are —
`docs/model-planning/contracts/retrieval-tolerance-measurement.md`'s
``H = sqrt(A² + B²)`` — with the paired user-level bootstrap supplying ``B`` and
the across-seed dispersion supplying ``A`` whenever there is more than one seed
to estimate it from.  It applies in both regimes: three seeds do not make a
finite holdout infinite, and a band that switched itself off when the ladder
returns to multi-seed runs would be the same silent weakening this module exists
to refuse.  The band needs per-user warm recall vectors on both sides; without
them the gate returns ``incomplete`` rather than falling back to the comparison
it replaced.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from .aggregate import MismatchedResultsError, mean_eval_result
from .manifest import (
    MLFLOW_PROTOCOL_HASH_PARAM,
    MLFLOW_PROTOCOL_SCHEMA_PARAM,
    MLFLOW_PROTOCOL_TAG,
    ProtocolManifest,
    ProtocolManifestError,
    validate_metric_value,
)
from .protocol import K_CANDIDATES, PER_USER_RECALL_ARTIFACT, EvalResult, UserMetrics

REQUIRED_SEEDS: Final = (42, 7, 13)
MIN_WARM_RELATIVE_GAIN: Final = 0.03
GATE_STAGE: Final = "retrieval"
GATE_METRIC: Final = "recall"

# The one slice carrying a positive claim, and therefore the only one whose
# per-user vectors the gate requires. The guardrails keep consuming the measured
# tolerances; asking for their vectors too would raise the evidence bar without
# changing a single verdict.
WARM_SLICE: Final = "warm"

MLFLOW_DETERMINISTIC_PARAM: Final = "model_deterministic"
MLFLOW_SEED_PARAM: Final = "train_seed"
MLFLOW_MODEL_TYPE_TAG: Final = "model_type"
ITEMITEM_MODEL_TYPE: Final = "itemitem_cosine"

_EXIT_PROMOTE: Final = 0
_EXIT_REFUSE: Final = 1
_EXIT_UNDECIDED: Final = 2


# --- shared uncertainty kernel ----------------------------------------------
#
# The gate's warm band and `tolerance_study`'s guardrail tolerances are the same
# measurement pointed at different clauses, so they are the same code. It lives
# here rather than in the study because the dependency already runs that way —
# the study imports the gate's vocabulary, not the other way round — and because
# a gate that computed its own bootstrap would eventually drift from the one the
# tolerances it consumes were measured with.

ONE_SIDED_Z_95: Final = 1.6448536269514722

# One-sided 95% Student-t critical values indexed by degrees of freedom. A seed
# term is a mean over m runs, so df = m - 1; beyond df 30 the difference from the
# normal quantile is smaller than anything else in these calculations.
_T_95_ONE_SIDED: Final = (
    math.nan,
    6.314,
    2.920,
    2.353,
    2.132,
    2.015,
    1.943,
    1.895,
    1.860,
    1.833,
    1.812,
    1.796,
    1.782,
    1.771,
    1.761,
    1.753,
    1.746,
    1.740,
    1.734,
    1.729,
    1.725,
    1.721,
    1.717,
    1.714,
    1.711,
    1.708,
    1.706,
    1.703,
    1.701,
    1.699,
    1.697,
)

# Enough replicates that the bootstrap's own Monte-Carlo error is far below the
# quantity it estimates. Chunked when drawn (see `_bootstrap_replicate_means`).
DEFAULT_BOOTSTRAP_REPLICATES: Final = 10_000
MIN_BOOTSTRAP_REPLICATES: Final = 1_000

# Fixed on the day the measurement protocol was written. It is a resampling seed
# and has nothing to do with the training seeds a gate reads — naming it after
# the date keeps the two from being confused.
DEFAULT_BOOTSTRAP_SEED: Final = 20_260_904

# Replicates are drawn in blocks so a 10,000 x n index matrix never has to exist
# at once. The block size is a constant rather than a parameter because it
# participates in the random stream: change it and the same seed produces
# different (equally valid) replicates.
_BOOTSTRAP_BLOCK: Final = 512

# Slack allowed when checking that a per-user vector reproduces the slice mean
# the run published. Wide enough for float accumulation over thousands of users,
# far too narrow for a vector that belongs to a different run.
_MEAN_RECONSTRUCTION_REL_TOL: Final = 1e-6
_MEAN_RECONSTRUCTION_ABS_TOL: Final = 1e-9


def _t_quantile(degrees_of_freedom: int) -> float:
    if degrees_of_freedom < 1:
        raise ValueError("a dispersion estimate needs at least two observations")
    if degrees_of_freedom >= len(_T_95_ONE_SIDED):
        return ONE_SIDED_Z_95
    return _T_95_ONE_SIDED[degrees_of_freedom]


def _paired_differences(
    candidate: Mapping[int, float], incumbent: Mapping[int, float]
) -> npt.NDArray[np.float64]:
    users = sorted(candidate)
    return np.array([candidate[user] - incumbent[user] for user in users], dtype=np.float64)


def _bootstrap_replicate_means(
    differences: npt.NDArray[np.float64], replicates: int, seed: int
) -> npt.NDArray[np.float64]:
    """Resample users with replacement; return the mean difference per replicate.

    Drawn in blocks so the index matrix stays small. The block size is fixed
    (`_BOOTSTRAP_BLOCK`) because it is part of the random stream: the same seed
    reproduces the same replicates only for the same block size.
    """
    rng = np.random.default_rng(seed)
    n = int(differences.size)
    means = np.empty(replicates, dtype=np.float64)
    filled = 0
    while filled < replicates:
        block = min(_BOOTSTRAP_BLOCK, replicates - filled)
        indices = rng.integers(0, n, size=(block, n))
        means[filled : filled + block] = np.mean(differences[indices], axis=1)
        filled += block
    return means


class RetrievalGateStatus(StrEnum):
    PROMOTE = "promote"
    REFUSE = "refuse"
    NOT_COMPARABLE = "not_comparable"
    INCOMPLETE = "incomplete"


class SeedRegime(StrEnum):
    """How many training runs a verdict rests on, and therefore what it can see.

    This is not a knob of its own — it is read off ``required_seeds`` so it can
    never disagree with the seed set the verdict was actually issued under.  It
    exists so an operator reading the JSON can tell a one-run decision from a
    three-run one without counting run ids.
    """

    SINGLE_SEED = "single_seed"
    MULTI_SEED = "multi_seed"


# Spelled out rather than left implicit because this is the whole cost of the
# 2026-09-05 policy, and a verdict that does not state it invites being read as
# the stronger thing it looks like.
_SINGLE_SEED_BASIS: Final = (
    "one training run: the supplied tolerances can only have covered evaluation-population "
    "sampling (a paired user-level bootstrap). Training stochasticity is unmeasured, so a "
    "model whose seeds genuinely disagree is not caught by this verdict, and the warm claim "
    "is a single draw rather than a mean. Its uncertainty band is measured on that same "
    "population term alone and inherits the same blind spot: it says how far the gain would "
    "move on a different sample of users, not on a different training seed."
)
_MULTI_SEED_BASIS: Final = (
    "{count} training runs: the supplied tolerances cover across-seed training dispersion and "
    "evaluation-population sampling combined in quadrature, and every clause reads a mean "
    "over the seed set. The warm claim's uncertainty band combines the same two terms the "
    "same way."
)


def _seed_regime(required_seeds: Sequence[int]) -> SeedRegime:
    return SeedRegime.SINGLE_SEED if len(required_seeds) == 1 else SeedRegime.MULTI_SEED


def _uncertainty_basis(required_seeds: Sequence[int]) -> str:
    if len(required_seeds) == 1:
        return _SINGLE_SEED_BASIS
    return _MULTI_SEED_BASIS.format(count=len(required_seeds))


@dataclass(frozen=True)
class RetrievalTolerance:
    """Measured relative regression limits for required supporting slices.

    There is deliberately no module-level default.  The plan requires these
    tolerances to be measured for retrieval rather than copied from the NDCG
    gate.  A caller that does not supply both values cannot obtain a verdict.
    """

    cold: float
    overall: float

    def __post_init__(self) -> None:
        for name in ("cold", "overall"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} tolerance must be numeric, got {value!r}")
            if not math.isfinite(float(value)) or not 0 <= value <= 1:
                raise ValueError(
                    f"{name} tolerance must be a finite fraction in [0, 1], got {value!r}"
                )


@dataclass(frozen=True)
class BandOptions:
    """Resampling knobs for the warm band, fixed so a verdict is reproducible.

    Both defaults are shared with `tolerance_study`, which matters more than it
    looks: the guardrail tolerances a decision consumes and the band it computes
    for itself are then drawn from the same replicate stream on the same users,
    so the two half-widths in one JSON payload are comparable quantities rather
    than two estimates of loosely related things.
    """

    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED

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


@dataclass(frozen=True)
class UncertaintyBand:
    """The one-sided 95% interval a positive clause is decided on.

    Every field is relative to the incumbent's slice recall, in the same units
    as `RecallClause.relative_change` and `MIN_WARM_RELATIVE_GAIN`, so the three
    can be read against each other without conversion.

    ``seed_half_width`` is ``None`` — never ``0.0`` — under a single seed. A zero
    would claim the seed term was measured and found to be nothing, which is a
    different and much stronger statement than "there was one run".
    """

    lower_bound: float
    half_width: float
    seed_half_width: float | None
    population_half_width: float
    n_users: int
    bootstrap_replicates: int
    bootstrap_seed: int
    basis: str


# Named rather than spelled inline because these two strings are what a reader
# checks a `promote` against, and "the band saw one noise source" versus "the
# band saw both" is the whole difference between the two regimes' verdicts.
BAND_POPULATION_ONLY: Final = "population-only"
BAND_SEED_AND_POPULATION: Final = "seed-and-population"


@dataclass(frozen=True)
class RetrievalRun:
    run_id: str
    model_type: str
    seed: int | None
    deterministic: bool
    protocol: ProtocolManifest
    result: EvalResult
    # ``{slice_name: {user_id: recall}}``, as `evaluation.protocol.evaluate`
    # publishes it. Only the warm slice is read, and only to build the band the
    # positive clause is decided on. It defaults to empty so an existing caller
    # still constructs, but a run that arrives without it makes the decision
    # `incomplete` — the absence has to surface as missing evidence, not as a
    # gate that quietly went back to comparing two bare numbers.
    per_user_recall: Mapping[str, Mapping[int, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalRunSet:
    model_id: str
    deterministic: bool
    runs: tuple[RetrievalRun, ...]


@dataclass(frozen=True)
class RecallClause:
    name: str
    incumbent: float
    candidate: float
    relative_change: float | None
    required_change: float
    passed: bool
    detail: str
    # Present on a positive clause and absent on a non-regression one: the
    # guardrails already consume a measured tolerance that *is* a noise
    # allowance, and giving them a second one would double-count it.
    band: UncertaintyBand | None = None


@dataclass(frozen=True)
class RetrievalGateDecision:
    status: RetrievalGateStatus
    stage: str
    metric: str
    k: int
    protocol_hash: str | None
    required_seeds: tuple[int, ...]
    candidate_run_ids: tuple[str, ...]
    incumbent_run_ids: tuple[str, ...]
    clauses: tuple[RecallClause, ...]
    reasons: tuple[str, ...]
    # Derived, never passed: a decision that could be constructed with a regime
    # disagreeing with its own seed set would be exactly the silent weakening
    # this field exists to prevent.
    seed_regime: SeedRegime = field(init=False)
    uncertainty_basis: str = field(init=False)
    serving_eligible: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "seed_regime", _seed_regime(self.required_seeds))
        object.__setattr__(self, "uncertainty_basis", _uncertainty_basis(self.required_seeds))

    @property
    def promote(self) -> bool:
        """Whether the retrieval-stage quality gate passed."""
        return self.status is RetrievalGateStatus.PROMOTE

    def summary(self) -> str:
        headline = self.status.value.upper().replace("_", " ")
        lines = [
            f"{headline} — {self.metric}@{self.k} ({self.stage})",
            f"  seed regime: {self.seed_regime.value} {list(self.required_seeds)}",
            f"  uncertainty: {self.uncertainty_basis}",
        ]
        lines.extend(f"  {clause.name}: {clause.detail}" for clause in self.clauses)
        lines.extend(f"  reason: {reason}" for reason in self.reasons)
        if self.promote and not self.serving_eligible:
            lines.append("  serving: blocked pending the paired LightGBM NDCG@10 guardrail")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["seed_regime"] = self.seed_regime.value
        payload["promote"] = self.promote
        return payload


class RetrievalRunNotUsableError(RuntimeError):
    """An MLflow run is missing required protocol, seed, or metric evidence."""


def retrieval_promotion_decision(
    candidate: RetrievalRunSet,
    incumbent: RetrievalRunSet,
    *,
    tolerance: RetrievalTolerance,
    min_warm_relative_gain: float = MIN_WARM_RELATIVE_GAIN,
    required_seeds: tuple[int, ...] = REQUIRED_SEEDS,
    band_options: BandOptions | None = None,
) -> RetrievalGateDecision:
    """Apply the owner-approved retrieval gate without permissive fallbacks.

    ``required_seeds`` is the caller's stated seed policy and the candidate run
    set must match it exactly — no missing, extra, or repeated seeds.  Passing a
    single seed is how the 2026-09-05 one-run-per-configuration policy reaches a
    verdict; it is deliberately not the default, because a weaker evidence base
    should be something a caller asks for rather than something it inherits.
    The decision records which regime produced it either way.

    The warm clause is decided on the lower bound of a one-sided 95% band, not
    on the point estimate, so a `promote` claims the gain is *at least* the
    threshold rather than that one measurement of it landed above.  Building
    that band needs a warm per-user recall vector on every run; a run set that
    does not carry them is `incomplete`.

    Nothing else about the gate varies with the seed count.  The guardrail
    clauses, the protocol and population checks, the deterministic-incumbent
    requirement and ``serving_eligible=False`` are identical for one seed and
    for three.
    """
    resolved_band_options = band_options or BandOptions()
    if (
        not isinstance(min_warm_relative_gain, (int, float))
        or isinstance(min_warm_relative_gain, bool)
        or not math.isfinite(float(min_warm_relative_gain))
        or min_warm_relative_gain < 0
    ):
        raise ValueError("min_warm_relative_gain must be a finite non-negative fraction")
    if not required_seeds:
        raise ValueError("required_seeds must be a non-empty tuple of unique integers")
    if any(type(seed) is not int for seed in required_seeds):
        raise ValueError("required_seeds must contain only integers")
    if len(set(required_seeds)) != len(required_seeds):
        raise ValueError("required_seeds must be a non-empty tuple of unique integers")

    candidate_ids = tuple(
        run.run_id if isinstance(run.run_id, str) else repr(run.run_id) for run in candidate.runs
    )
    incumbent_ids = tuple(
        run.run_id if isinstance(run.run_id, str) else repr(run.run_id) for run in incumbent.runs
    )
    policy_errors: list[str] = []
    if candidate.deterministic:
        policy_errors.append(
            "candidate must be stochastic and provide the complete approved seed set"
        )
    if not incumbent.deterministic:
        policy_errors.append("incumbent item-item baseline must be deterministic")
    if incumbent.model_id != ITEMITEM_MODEL_TYPE:
        policy_errors.append(
            f"incumbent model must be {ITEMITEM_MODEL_TYPE!r}, got {incumbent.model_id!r}"
        )
    if candidate.model_id == incumbent.model_id:
        policy_errors.append("candidate and incumbent model identities must differ")
    overlapping_ids = sorted(set(candidate_ids) & set(incumbent_ids))
    if overlapping_ids:
        policy_errors.append(f"candidate and incumbent contain the same run ids {overlapping_ids}")

    incomplete = _run_set_completeness(candidate, required_seeds, side="candidate")
    incomplete.extend(_run_set_completeness(incumbent, required_seeds, side="incumbent"))
    incomplete.extend(policy_errors)
    if incomplete:
        return _decision(
            RetrievalGateStatus.INCOMPLETE,
            candidate_ids,
            incumbent_ids,
            reasons=incomplete,
            required_seeds=required_seeds,
        )

    invalid: list[str] = []
    all_runs = (*candidate.runs, *incumbent.runs)
    for run in all_runs:
        invalid.extend(_validate_run(run))
    if invalid:
        return _decision(
            RetrievalGateStatus.NOT_COMPARABLE,
            candidate_ids,
            incumbent_ids,
            reasons=invalid,
            required_seeds=required_seeds,
        )

    protocol_hashes = {run.protocol.semantic_hash for run in all_runs}
    if len(protocol_hashes) != 1:
        reference = all_runs[0].protocol
        mismatched_fields = sorted(
            {name for run in all_runs[1:] for name in reference.mismatches(run.protocol)}
        )
        return _decision(
            RetrievalGateStatus.NOT_COMPARABLE,
            candidate_ids,
            incumbent_ids,
            reasons=("semantic protocol mismatch in fields: " + ", ".join(mismatched_fields),),
            required_seeds=required_seeds,
        )
    protocol_hash = next(iter(protocol_hashes))

    try:
        candidate_mean = mean_eval_result([run.result for run in candidate.runs])
        incumbent_mean = mean_eval_result([run.result for run in incumbent.runs])
    except MismatchedResultsError as exc:
        return _decision(
            RetrievalGateStatus.NOT_COMPARABLE,
            candidate_ids,
            incumbent_ids,
            protocol_hash=protocol_hash,
            reasons=(str(exc),),
            required_seeds=required_seeds,
        )
    population_mismatches = []
    for name in ("n_warm_users", "n_cold_users"):
        if getattr(candidate_mean, name) != getattr(incumbent_mean, name):
            population_mismatches.append(
                f"slice population mismatch for {name}: "
                f"candidate={getattr(candidate_mean, name)}, "
                f"incumbent={getattr(incumbent_mean, name)}"
            )
    if population_mismatches:
        return _decision(
            RetrievalGateStatus.NOT_COMPARABLE,
            candidate_ids,
            incumbent_ids,
            protocol_hash=protocol_hash,
            reasons=population_mismatches,
            required_seeds=required_seeds,
        )

    band_terms, unpaired = _warm_band_terms(
        candidate, incumbent, incumbent_mean.warm.recall, resolved_band_options
    )
    if unpaired:
        return _decision(
            RetrievalGateStatus.NOT_COMPARABLE,
            candidate_ids,
            incumbent_ids,
            protocol_hash=protocol_hash,
            reasons=unpaired,
            required_seeds=required_seeds,
        )

    clauses = (
        _positive_clause(
            WARM_SLICE,
            incumbent_mean.warm.recall,
            candidate_mean.warm.recall,
            min_warm_relative_gain,
            band_terms,
        ),
        _non_regression_clause(
            "cold",
            incumbent_mean.cold.recall,
            candidate_mean.cold.recall,
            tolerance.cold,
        ),
        _non_regression_clause(
            "overall",
            incumbent_mean.overall.recall,
            candidate_mean.overall.recall,
            tolerance.overall,
        ),
    )
    failed = tuple(clause.detail for clause in clauses if not clause.passed)
    status = RetrievalGateStatus.PROMOTE if not failed else RetrievalGateStatus.REFUSE
    reasons = (
        ("retrieval passed; serving remains blocked until paired LightGBM NDCG@10 passes",)
        if status is RetrievalGateStatus.PROMOTE
        else failed
    )
    return _decision(
        status,
        candidate_ids,
        incumbent_ids,
        protocol_hash=protocol_hash,
        clauses=clauses,
        reasons=reasons,
        required_seeds=required_seeds,
    )


def _run_set_completeness(
    run_set: RetrievalRunSet, required_seeds: tuple[int, ...], *, side: str
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(run_set.model_id, str) or not run_set.model_id.strip():
        reasons.append(f"{side} model_id is empty")
    elif run_set.model_id != run_set.model_id.strip():
        reasons.append(f"{side} model_id must not have surrounding whitespace")
    if type(run_set.deterministic) is not bool:
        reasons.append(f"{side} deterministic flag must be a boolean")
    if run_set.deterministic:
        if len(run_set.runs) != 1:
            reasons.append(
                f"deterministic {side} requires exactly one run, got {len(run_set.runs)}"
            )
    else:
        invalid_seeds = [run.seed for run in run_set.runs if type(run.seed) is not int]
        seeds = [run.seed for run in run_set.runs if type(run.seed) is int]
        if invalid_seeds:
            reasons.append(
                f"{side} has non-integer or missing seeds "
                f"{[repr(seed) for seed in invalid_seeds]}"
            )
        missing = [seed for seed in required_seeds if seed not in seeds]
        unexpected = [seed for seed in seeds if seed not in required_seeds]
        duplicates = sorted({seed for seed in seeds if seeds.count(seed) > 1}, key=str)
        if missing:
            reasons.append(f"{side} is missing required seeds {missing}")
        if unexpected:
            reasons.append(f"{side} has unexpected seeds {unexpected}")
        if duplicates:
            reasons.append(f"{side} has duplicate seeds {duplicates}")
    run_ids = [run.run_id for run in run_set.runs if isinstance(run.run_id, str)]
    duplicate_ids = sorted({run_id for run_id in run_ids if run_ids.count(run_id) > 1})
    if duplicate_ids:
        reasons.append(f"{side} has duplicate run ids {duplicate_ids}")
    for run in run_set.runs:
        if run.model_type != run_set.model_id:
            reasons.append(
                f"{side} run {run.run_id!r} model type {run.model_type!r} "
                f"does not match run-set model {run_set.model_id!r}"
            )
        if type(run.deterministic) is not bool:
            reasons.append(f"{side} run {run.run_id!r} deterministic flag must be a boolean")
        if run.deterministic != run_set.deterministic:
            reasons.append(
                f"{side} run {run.run_id!r} deterministic flag disagrees with its run set"
            )
        if run_set.deterministic and run.seed is not None:
            reasons.append(f"deterministic {side} run {run.run_id!r} must not claim a seed")
        if not run_set.deterministic and run.seed is None:
            reasons.append(f"stochastic {side} run {run.run_id!r} is missing its seed")
        if run.seed is not None and type(run.seed) is not int:
            reasons.append(f"{side} run {run.run_id!r} seed must be an integer or null")
        if not _warm_vector(run):
            # Deliberately worded as a refusal rather than a note. The failure
            # this sentence is guarding against is a future reader deciding the
            # vectors are optional decoration and reinstating the bare
            # comparison, which is the same class of mistake as a tolerance
            # quietly defaulting to something permissive.
            reasons.append(
                f"{side} run {run.run_id!r} carries no warm per-user recall vector, so the "
                "+3% claim cannot be given an uncertainty band; the gate does not fall back "
                "to a bare comparison of two means"
            )
    return reasons


def _validate_run(run: RetrievalRun) -> list[str]:
    reasons: list[str] = []
    if not isinstance(run.run_id, str) or not run.run_id.strip():
        reasons.append("run id is empty")
    elif run.run_id != run.run_id.strip():
        reasons.append(f"run id {run.run_id!r} has surrounding whitespace")
    if (
        not isinstance(run.model_type, str)
        or not run.model_type.strip()
        or run.model_type != run.model_type.strip()
    ):
        reasons.append(f"run {run.run_id!r} model type must be a normalized non-empty string")
    if run.protocol.stage != GATE_STAGE:
        reasons.append(
            f"run {run.run_id!r} has stage {run.protocol.stage!r}; retrieval is required"
        )
    if run.protocol.primary_metric != GATE_METRIC:
        reasons.append(
            f"run {run.run_id!r} has primary metric {run.protocol.primary_metric!r}; "
            "recall is required"
        )
    if run.protocol.k != K_CANDIDATES or run.result.k != K_CANDIDATES:
        reasons.append(
            f"run {run.run_id!r} must use k={K_CANDIDATES}; "
            f"protocol={run.protocol.k}, result={run.result.k}"
        )
    if run.protocol.k != run.result.k:
        reasons.append(
            f"run {run.run_id!r} protocol/result K mismatch: " f"{run.protocol.k}/{run.result.k}"
        )
    for name, value in (
        ("warm recall", run.result.warm.recall),
        ("cold recall", run.result.cold.recall),
        ("overall recall", run.result.overall.recall),
    ):
        try:
            validate_metric_value(f"run {run.run_id!r} {name}", value)
        except ProtocolManifestError as exc:
            reasons.append(str(exc))
    for name in ("n_warm_users", "n_cold_users"):
        value = getattr(run.result, name)
        if type(value) is not int or value < 0:
            reasons.append(f"run {run.run_id!r} {name} must be a non-negative integer")
    if run.result.n_warm_users == 0:
        reasons.append(f"run {run.run_id!r} has no warm users for the primary gate")
    if run.result.n_cold_users == 0:
        reasons.append(f"run {run.run_id!r} has no cold users for the required guardrail")
    reasons.extend(_warm_vector_reasons(run))
    return reasons


def _warm_vector(run: RetrievalRun) -> Mapping[int, float] | None:
    if not isinstance(run.per_user_recall, Mapping):
        return None
    vector = run.per_user_recall.get(WARM_SLICE)
    return vector if vector else None


def _warm_vector_reasons(run: RetrievalRun) -> list[str]:
    """Check that the warm vector describes *this* run before resampling it.

    Same three checks the tolerance study makes for the same reason: a vector of
    the right shape from the wrong run would produce a band that is arithmetic
    about nothing, and it would do so silently.
    """
    vector = _warm_vector(run)
    if vector is None:
        # Absence is `incomplete` evidence, reported by `_run_set_completeness`.
        # Repeating it here would turn a missing artifact into a comparability
        # failure, which reads as a claim about the runs rather than about what
        # was supplied.
        return []
    reasons: list[str] = []
    if len(vector) != run.result.n_warm_users:
        reasons.append(
            f"run {run.run_id!r} warm vector holds {len(vector)} users but the run reports "
            f"{run.result.n_warm_users}"
        )
    for user_id, value in vector.items():
        if type(user_id) is not int:
            reasons.append(f"run {run.run_id!r} warm vector has a non-integer user id")
            break
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            reasons.append(
                f"run {run.run_id!r} warm vector has an invalid recall for user "
                f"{user_id}: {value!r}"
            )
            break
    if reasons:
        return reasons
    reconstructed = statistics.fmean(float(value) for value in vector.values())
    if not math.isclose(
        reconstructed,
        run.result.warm.recall,
        rel_tol=_MEAN_RECONSTRUCTION_REL_TOL,
        abs_tol=_MEAN_RECONSTRUCTION_ABS_TOL,
    ):
        reasons.append(
            f"run {run.run_id!r} warm vector averages {reconstructed:.6f} but the run published "
            f"{run.result.warm.recall:.6f}; the vector does not belong to this run"
        )
    return reasons


def _at_least(value: float, bound: float) -> bool:
    return value >= bound or math.isclose(value, bound, rel_tol=1e-9, abs_tol=1e-12)


@dataclass(frozen=True)
class _BandTerms:
    """The measured half-widths, before a point estimate is subtracted from them.

    Kept separate from `UncertaintyBand` so exactly one expression in this module
    computes the relative change a clause reports. The band's lower bound is that
    same number minus these half-widths, and a second copy of the subtraction
    would be a second chance to disagree with the clause it belongs to.
    """

    seed_half_width: float | None
    population_half_width: float
    n_users: int
    bootstrap_replicates: int
    bootstrap_seed: int

    @property
    def half_width(self) -> float:
        # Quadrature, per the composition argument in
        # docs/model-planning/contracts/retrieval-tolerance-measurement.md: the
        # holdout is pinned, so the across-seed spread is estimated at a fixed
        # user set and the bootstrap at a fixed model. The two are independent
        # and their variances add.
        if self.seed_half_width is None:
            return self.population_half_width
        return math.hypot(self.seed_half_width, self.population_half_width)

    @property
    def basis(self) -> str:
        return BAND_POPULATION_ONLY if self.seed_half_width is None else BAND_SEED_AND_POPULATION


def _warm_band_terms(
    candidate: RetrievalRunSet,
    incumbent: RetrievalRunSet,
    incumbent_warm_recall: float,
    options: BandOptions,
) -> tuple[_BandTerms | None, list[str]]:
    """Measure how far the warm gain would move under resampling and re-seeding.

    The population term is a *paired* bootstrap over per-user differences, which
    is the only honest way to do it here: easy users are easy for both models, so
    the two per-user vectors are strongly correlated and bootstrapping the two
    means separately would inflate the term substantially — and an inflated band
    is the permissive direction on a guardrail but the *restrictive* one on a
    positive claim, so getting it wrong refuses good models.

    Returns ``(terms, reasons)``; a non-empty ``reasons`` means the two sides
    cannot be paired at all and the comparison is not available.
    """
    incumbent_vector = _warm_vector(incumbent.runs[0])
    candidate_vectors = [_warm_vector(run) for run in candidate.runs]
    if incumbent_vector is None or any(vector is None for vector in candidate_vectors):
        # Unreachable from `retrieval_promotion_decision`, which returns
        # `incomplete` first. Kept so a direct caller cannot get a band-free pass.
        return None, []

    users = set(incumbent_vector)
    reasons = [
        f"candidate run {run.run_id!r} and the incumbent scored different warm users; "
        "the uncertainty band must be paired user by user"
        for run, vector in zip(candidate.runs, candidate_vectors, strict=True)
        if vector is not None and set(vector) != users
    ]
    if reasons:
        return None, reasons
    if incumbent_warm_recall <= 0:
        # The clause refuses an undefined relative claim before it looks at a
        # band, and every half-width here is denominated in this number.
        return None, []

    # One vector per user averaged across seeds, so the difference the bootstrap
    # resamples has exactly the mean the clause reports: mean over users of the
    # mean over seeds is the seed mean of the slice means.
    seed_mean_recall = {
        user: statistics.fmean(vector[user] for vector in candidate_vectors if vector is not None)
        for user in users
    }
    differences = _paired_differences(seed_mean_recall, incumbent_vector)
    replicates = _bootstrap_replicate_means(
        differences, options.bootstrap_replicates, options.bootstrap_seed
    )
    population_half_width = (
        ONE_SIDED_Z_95 * float(np.std(replicates, ddof=1)) / incumbent_warm_recall
    )

    # `None`, not `0.0`, under one seed: there is no dispersion estimate, which
    # is a different finding from a dispersion estimate of zero.
    seed_half_width: float | None = None
    warm_recalls = [run.result.warm.recall for run in candidate.runs]
    if len(warm_recalls) > 1:
        seed_half_width = (
            _t_quantile(len(warm_recalls) - 1)
            * statistics.stdev(warm_recalls)
            / math.sqrt(len(warm_recalls))
        ) / incumbent_warm_recall

    return (
        _BandTerms(
            seed_half_width=seed_half_width,
            population_half_width=population_half_width,
            n_users=len(users),
            bootstrap_replicates=options.bootstrap_replicates,
            bootstrap_seed=options.bootstrap_seed,
        ),
        [],
    )


def _positive_clause(
    name: str,
    incumbent: float,
    candidate: float,
    required: float,
    terms: _BandTerms | None,
) -> RecallClause:
    if incumbent <= 0:
        return RecallClause(
            name=name,
            incumbent=incumbent,
            candidate=candidate,
            relative_change=None,
            required_change=required,
            passed=False,
            detail=f"{name} incumbent recall@500 is zero; relative improvement is undefined",
        )
    change = (candidate - incumbent) / incumbent
    if terms is None:
        return RecallClause(
            name=name,
            incumbent=incumbent,
            candidate=candidate,
            relative_change=change,
            required_change=required,
            passed=False,
            detail=(
                f"{name} recall@500 changed {change:+.2%} ({incumbent:.6f} → {candidate:.6f}), "
                "but no uncertainty band could be measured; a positive claim is not decided on "
                "a point estimate alone"
            ),
        )
    band = UncertaintyBand(
        lower_bound=change - terms.half_width,
        half_width=terms.half_width,
        seed_half_width=terms.seed_half_width,
        population_half_width=terms.population_half_width,
        n_users=terms.n_users,
        bootstrap_replicates=terms.bootstrap_replicates,
        bootstrap_seed=terms.bootstrap_seed,
        basis=terms.basis,
    )
    # The lower bound, not the point estimate. "The measurement came out above
    # the bar" and "we are confident the truth is above the bar" are different
    # claims, and only the second survives being read off a single run.
    passed = _at_least(band.lower_bound, required)
    return RecallClause(
        name=name,
        incumbent=incumbent,
        candidate=candidate,
        relative_change=change,
        required_change=required,
        passed=passed,
        detail=(
            f"{name} recall@500 changed {change:+.2%} ({incumbent:.6f} → {candidate:.6f}); "
            f"one-sided 95% lower bound {band.lower_bound:+.2%} "
            f"(half-width {band.half_width:.2%}, {band.basis}, n={band.n_users}); "
            f"required at least +{required:.2%}"
        ),
        band=band,
    )


def _non_regression_clause(
    name: str, incumbent: float, candidate: float, tolerance: float
) -> RecallClause:
    if incumbent <= 0:
        return RecallClause(
            name=name,
            incumbent=incumbent,
            candidate=candidate,
            relative_change=None,
            required_change=-tolerance,
            passed=False,
            detail=f"{name} incumbent recall@500 is zero; non-regression is undefined",
        )
    change = (candidate - incumbent) / incumbent
    passed = _at_least(change, -tolerance)
    return RecallClause(
        name=name,
        incumbent=incumbent,
        candidate=candidate,
        relative_change=change,
        required_change=-tolerance,
        passed=passed,
        detail=(
            f"{name} recall@500 changed {change:+.2%} ({incumbent:.6f} → {candidate:.6f}); "
            f"minimum allowed {-tolerance:+.2%}"
        ),
    )


def _decision(
    status: RetrievalGateStatus,
    candidate_run_ids: Sequence[str],
    incumbent_run_ids: Sequence[str],
    *,
    protocol_hash: str | None = None,
    clauses: Sequence[RecallClause] = (),
    reasons: Sequence[str] = (),
    required_seeds: tuple[int, ...] = REQUIRED_SEEDS,
) -> RetrievalGateDecision:
    return RetrievalGateDecision(
        status=status,
        stage=GATE_STAGE,
        metric=GATE_METRIC,
        k=K_CANDIDATES,
        protocol_hash=protocol_hash,
        required_seeds=required_seeds,
        candidate_run_ids=tuple(candidate_run_ids),
        incumbent_run_ids=tuple(incumbent_run_ids),
        clauses=tuple(clauses),
        reasons=tuple(reasons),
    )


def per_user_recall_from_artifact(document: str) -> Mapping[str, Mapping[int, float]]:
    """Parse one `per_user_recall.json` artifact into the gate's vector shape.

    The artifact is what `evaluation.protocol.per_user_recall_document` writes,
    and it is a whole run object rather than a bare vector map — the gate reads
    only the vectors out of it and leaves the rest to the checks it already
    makes against MLflow's own envelope.

    JSON has no integer keys, so user ids arrive as strings and are parsed back
    to the dataset's own ids here. They are never positions: pairing two runs by
    position would silently compare different people.

    Raises:
        RetrievalRunNotUsableError: the document is not a per-user recall
            artifact, or holds a vector this gate cannot resample.
    """
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise RetrievalRunNotUsableError(
            f"per-user recall artifact is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise RetrievalRunNotUsableError("per-user recall artifact must be one JSON object")
    vectors_payload = payload.get("per_user_recall")
    if not isinstance(vectors_payload, dict):
        raise RetrievalRunNotUsableError(
            "per-user recall artifact is missing its 'per_user_recall' object"
        )
    vectors: dict[str, Mapping[int, float]] = {}
    for slice_name, vector in vectors_payload.items():
        if not isinstance(vector, dict):
            raise RetrievalRunNotUsableError(
                f"per-user recall artifact slice {slice_name!r} must be an object of "
                "user id to recall"
            )
        parsed: dict[int, float] = {}
        for key, value in vector.items():
            try:
                user_id = int(key)
            except (TypeError, ValueError) as exc:
                raise RetrievalRunNotUsableError(
                    f"per-user recall artifact slice {slice_name!r} has a non-integer user "
                    f"id {key!r}"
                ) from exc
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise RetrievalRunNotUsableError(
                    f"per-user recall artifact slice {slice_name!r} has a non-numeric recall "
                    f"for user {user_id}"
                )
            parsed[user_id] = float(value)
        vectors[str(slice_name)] = parsed
    return vectors


def retrieval_run_from_mlflow(
    run: Any, *, per_user_recall: Mapping[str, Mapping[int, float]] | None = None
) -> RetrievalRun:
    """Read a strict retrieval envelope from one MLflow run.

    Legacy runs without the canonical protocol are intentionally unusable.
    They may be documented as historical evidence, but cannot silently enter
    an executable promotion decision.

    ``per_user_recall`` is supplied by the caller rather than read here because
    the vectors live in a run *artifact* and this function is deliberately given
    only the run object — keeping it free of a tracking client is what makes it
    testable without one. Omitting them produces a run the gate will report as
    ``incomplete``, never one it waves through.
    """
    run_id = str(run.info.run_id)
    status = getattr(run.info, "status", "FINISHED")
    if status != "FINISHED":
        raise RetrievalRunNotUsableError(
            f"run {run_id} has status {status!r}; only FINISHED runs are gateable"
        )
    params = run.data.params
    tags = run.data.tags
    metrics = run.data.metrics
    model_type = tags.get(MLFLOW_MODEL_TYPE_TAG)
    if not isinstance(model_type, str) or not model_type.strip():
        raise RetrievalRunNotUsableError(
            f"run {run_id} is missing the {MLFLOW_MODEL_TYPE_TAG!r} model identity tag"
        )
    if model_type != model_type.strip():
        raise RetrievalRunNotUsableError(
            f"run {run_id} has a non-normalized model identity {model_type!r}"
        )
    protocol_json = tags.get(MLFLOW_PROTOCOL_TAG)
    if protocol_json is None:
        raise RetrievalRunNotUsableError(
            f"run {run_id} is missing the {MLFLOW_PROTOCOL_TAG!r} protocol tag"
        )
    try:
        protocol = ProtocolManifest.from_json(protocol_json)
    except ProtocolManifestError as exc:
        raise RetrievalRunNotUsableError(f"run {run_id} has an invalid protocol: {exc}") from exc
    recorded_hash = params.get(MLFLOW_PROTOCOL_HASH_PARAM)
    if recorded_hash != protocol.semantic_hash:
        raise RetrievalRunNotUsableError(
            f"run {run_id} protocol hash mismatch: recorded={recorded_hash!r}, "
            f"calculated={protocol.semantic_hash!r}"
        )
    recorded_schema = params.get(MLFLOW_PROTOCOL_SCHEMA_PARAM)
    if recorded_schema != str(protocol.schema_version):
        raise RetrievalRunNotUsableError(
            f"run {run_id} protocol schema mismatch: recorded={recorded_schema!r}, "
            f"payload={protocol.schema_version!r}"
        )
    deterministic_raw = params.get(MLFLOW_DETERMINISTIC_PARAM)
    if deterministic_raw not in ("true", "false"):
        raise RetrievalRunNotUsableError(
            f"run {run_id} must set {MLFLOW_DETERMINISTIC_PARAM} to true or false"
        )
    deterministic = deterministic_raw == "true"
    seed_raw = params.get(MLFLOW_SEED_PARAM)
    if deterministic:
        if seed_raw is not None:
            raise RetrievalRunNotUsableError(
                f"deterministic run {run_id} must not set {MLFLOW_SEED_PARAM}"
            )
        seed = None
    else:
        if seed_raw is None:
            raise RetrievalRunNotUsableError(
                f"stochastic run {run_id} is missing {MLFLOW_SEED_PARAM}"
            )
        try:
            seed = int(seed_raw)
        except ValueError as exc:
            raise RetrievalRunNotUsableError(
                f"run {run_id} has a non-integer seed {seed_raw!r}"
            ) from exc
    required_metrics = (
        "warm_recall_at_k_candidates",
        "cold_recall_at_k_candidates",
        "overall_recall_at_k_candidates",
        "n_warm_users",
        "n_cold_users",
    )
    missing = [name for name in required_metrics if name not in metrics]
    if missing:
        raise RetrievalRunNotUsableError(
            f"run {run_id} is missing required metrics: {', '.join(missing)}"
        )
    for name in required_metrics[:3]:
        try:
            validate_metric_value(f"run {run_id} {name}", metrics[name])
        except ProtocolManifestError as exc:
            raise RetrievalRunNotUsableError(str(exc)) from exc

    def read_count(name: str) -> int:
        value = metrics[name]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
            or not float(value).is_integer()
        ):
            raise RetrievalRunNotUsableError(
                f"run {run_id} {name} must be a finite non-negative integer, got {value!r}"
            )
        return int(value)

    result = EvalResult(
        warm=UserMetrics(recall=float(metrics[required_metrics[0]]), ndcg=0.0),
        cold=UserMetrics(recall=float(metrics[required_metrics[1]]), ndcg=0.0),
        overall=UserMetrics(recall=float(metrics[required_metrics[2]]), ndcg=0.0),
        n_warm_users=read_count(required_metrics[3]),
        n_cold_users=read_count(required_metrics[4]),
        k=protocol.k,
    )
    return RetrievalRun(
        run_id=run_id,
        model_type=model_type,
        seed=seed,
        deterministic=deterministic,
        protocol=protocol,
        result=result,
        per_user_recall=dict(per_user_recall or {}),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.retrieval_gate",
        description=(
            "Strict retrieval gate: the one-sided 95% lower bound on the warm recall@500 gain "
            "over the stated seed set must clear 3%, with measured cold and overall "
            "non-regression tolerances."
        ),
    )
    parser.add_argument("--candidate", required=True, nargs="+", metavar="RUN_ID")
    parser.add_argument("--incumbent", required=True, nargs="+", metavar="RUN_ID")
    parser.add_argument("--cold-tolerance", required=True, type=float)
    parser.add_argument("--overall-tolerance", required=True, type=float)
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        metavar="SEED",
        default=list(REQUIRED_SEEDS),
        help=(
            "the exact training seeds the candidate run set must contain "
            f"(default: {' '.join(str(seed) for seed in REQUIRED_SEEDS)}). Pass one seed to "
            "gate a single run under the one-run-per-configuration policy; the decision then "
            "reports seed_regime=single_seed and the tolerances must have been measured "
            "without a seed term."
        ),
    )
    parser.add_argument(
        "--per-user-recall",
        action="append",
        default=[],
        metavar="RUN_ID=PATH",
        help=(
            "read one run's per-user recall vectors from a local "
            f"{PER_USER_RECALL_ARTIFACT} instead of its MLflow artifacts. Repeatable, and an "
            "escape hatch rather than the normal path: the vectors are logged with the run, "
            "and a gate reading them from somewhere else is a gate nobody can reproduce."
        ),
    )
    parser.add_argument("--tracking-uri", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    required_seeds = tuple(args.seeds)

    import mlflow

    from src.config import Settings

    mlflow.set_tracking_uri(args.tracking_uri or Settings().mlflow_tracking_uri)
    client = mlflow.tracking.MlflowClient()

    def load_vectors(run_id: str) -> Mapping[str, Mapping[int, float]]:
        from pathlib import Path

        for override in args.per_user_recall:
            named, separator, path = override.partition("=")
            if not separator:
                raise ValueError(f"--per-user-recall expects RUN_ID=PATH, got {override!r}")
            if named == run_id:
                return per_user_recall_from_artifact(Path(path).read_text(encoding="utf-8"))
        local = mlflow.artifacts.download_artifacts(
            run_id=run_id, artifact_path=PER_USER_RECALL_ARTIFACT
        )
        return per_user_recall_from_artifact(Path(local).read_text(encoding="utf-8"))

    try:
        candidate_runs = tuple(
            retrieval_run_from_mlflow(client.get_run(run_id), per_user_recall=load_vectors(run_id))
            for run_id in args.candidate
        )
        incumbent_runs = tuple(
            retrieval_run_from_mlflow(client.get_run(run_id), per_user_recall=load_vectors(run_id))
            for run_id in args.incumbent
        )
        decision = retrieval_promotion_decision(
            RetrievalRunSet(
                model_id=candidate_runs[0].model_type,
                deterministic=candidate_runs[0].deterministic,
                runs=candidate_runs,
            ),
            RetrievalRunSet(
                model_id=incumbent_runs[0].model_type,
                deterministic=incumbent_runs[0].deterministic,
                runs=incumbent_runs,
            ),
            tolerance=RetrievalTolerance(
                cold=args.cold_tolerance,
                overall=args.overall_tolerance,
            ),
            required_seeds=required_seeds,
        )
    except (
        RetrievalRunNotUsableError,
        ValueError,
        OSError,
        mlflow.exceptions.MlflowException,
    ) as exc:
        decision = _decision(
            RetrievalGateStatus.INCOMPLETE,
            args.candidate,
            args.incumbent,
            reasons=(str(exc),),
            # Report the policy that was asked for, not the default, so a refusal
            # to even load the runs still says which regime was being attempted.
            required_seeds=required_seeds,
        )

    print(
        json.dumps(decision.to_dict(), indent=2, sort_keys=True)
        if args.json
        else decision.summary()
    )
    if decision.status is RetrievalGateStatus.PROMOTE:
        return _EXIT_PROMOTE
    if decision.status is RetrievalGateStatus.REFUSE:
        return _EXIT_REFUSE
    return _EXIT_UNDECIDED


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
