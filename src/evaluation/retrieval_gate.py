"""Strict recall@500 promotion gate for candidate retrieval models.

The existing :mod:`src.evaluation.gate` is intentionally ranking-only: it
reads NDCG and ADR 0001's ranker tolerances.  This module is a separate API so
a retrieval result can never be promoted by accidentally passing through the
ranking gate.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Final

from .aggregate import MismatchedResultsError, mean_eval_result
from .manifest import (
    MLFLOW_PROTOCOL_HASH_PARAM,
    MLFLOW_PROTOCOL_SCHEMA_PARAM,
    MLFLOW_PROTOCOL_TAG,
    ProtocolManifest,
    ProtocolManifestError,
    validate_metric_value,
)
from .protocol import K_CANDIDATES, EvalResult, UserMetrics

REQUIRED_SEEDS: Final = (42, 7, 13)
MIN_WARM_RELATIVE_GAIN: Final = 0.03
GATE_STAGE: Final = "retrieval"
GATE_METRIC: Final = "recall"

MLFLOW_DETERMINISTIC_PARAM: Final = "model_deterministic"
MLFLOW_SEED_PARAM: Final = "train_seed"
MLFLOW_MODEL_TYPE_TAG: Final = "model_type"
ITEMITEM_MODEL_TYPE: Final = "itemitem_cosine"

_EXIT_PROMOTE: Final = 0
_EXIT_REFUSE: Final = 1
_EXIT_UNDECIDED: Final = 2


class RetrievalGateStatus(StrEnum):
    PROMOTE = "promote"
    REFUSE = "refuse"
    NOT_COMPARABLE = "not_comparable"
    INCOMPLETE = "incomplete"


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
class RetrievalRun:
    run_id: str
    model_type: str
    seed: int | None
    deterministic: bool
    protocol: ProtocolManifest
    result: EvalResult


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
    serving_eligible: bool = field(default=False, init=False)

    @property
    def promote(self) -> bool:
        """Whether the retrieval-stage quality gate passed."""
        return self.status is RetrievalGateStatus.PROMOTE

    def summary(self) -> str:
        headline = self.status.value.upper().replace("_", " ")
        lines = [f"{headline} — {self.metric}@{self.k} ({self.stage})"]
        lines.extend(f"  {clause.name}: {clause.detail}" for clause in self.clauses)
        lines.extend(f"  reason: {reason}" for reason in self.reasons)
        if self.promote and not self.serving_eligible:
            lines.append("  serving: blocked pending the paired LightGBM NDCG@10 guardrail")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
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
) -> RetrievalGateDecision:
    """Apply the owner-approved retrieval gate without permissive fallbacks."""
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

    clauses = (
        _positive_clause(
            "warm",
            incumbent_mean.warm.recall,
            candidate_mean.warm.recall,
            min_warm_relative_gain,
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
    return reasons


def _at_least(value: float, bound: float) -> bool:
    return value >= bound or math.isclose(value, bound, rel_tol=1e-9, abs_tol=1e-12)


def _positive_clause(
    name: str, incumbent: float, candidate: float, required: float
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
    passed = _at_least(change, required)
    return RecallClause(
        name=name,
        incumbent=incumbent,
        candidate=candidate,
        relative_change=change,
        required_change=required,
        passed=passed,
        detail=(
            f"{name} recall@500 changed {change:+.2%} ({incumbent:.6f} → {candidate:.6f}); "
            f"required at least +{required:.2%}"
        ),
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


def retrieval_run_from_mlflow(run: Any) -> RetrievalRun:
    """Read a strict retrieval envelope from one MLflow run.

    Legacy runs without the canonical protocol are intentionally unusable.
    They may be documented as historical evidence, but cannot silently enter
    an executable promotion decision.
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
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.retrieval_gate",
        description=(
            "Strict retrieval gate: three-seed mean warm recall@500 must improve by 3%, "
            "with measured cold and overall non-regression tolerances."
        ),
    )
    parser.add_argument("--candidate", required=True, nargs="+", metavar="RUN_ID")
    parser.add_argument("--incumbent", required=True, nargs="+", metavar="RUN_ID")
    parser.add_argument("--cold-tolerance", required=True, type=float)
    parser.add_argument("--overall-tolerance", required=True, type=float)
    parser.add_argument("--tracking-uri", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    import mlflow

    from src.config import Settings

    mlflow.set_tracking_uri(args.tracking_uri or Settings().mlflow_tracking_uri)
    client = mlflow.tracking.MlflowClient()

    try:
        candidate_runs = tuple(
            retrieval_run_from_mlflow(client.get_run(run_id)) for run_id in args.candidate
        )
        incumbent_runs = tuple(
            retrieval_run_from_mlflow(client.get_run(run_id)) for run_id in args.incumbent
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
        )
    except (RetrievalRunNotUsableError, ValueError, mlflow.exceptions.MlflowException) as exc:
        decision = _decision(
            RetrievalGateStatus.INCOMPLETE,
            args.candidate,
            args.incumbent,
            reasons=(str(exc),),
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
