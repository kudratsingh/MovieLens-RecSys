"""ADR 0001's promotion gate, as code.

The ADR pins the rule as *"a challenger model is only promoted if it beats the
incumbent by ≥ +3% relative NDCG@10 on the holdout"*, and two sections earlier
requires warm and cold to be *"reported separately — aggregating them hides
cold-start failure modes"*. Those sentences produce one number and three, and
until 2026-08-30 the ADR did not say which of them the gate reads.

On the first full-dataset comparison the answer decided the verdict. The
LightGBM ranker against CF/ALS was **+10.57% overall, +15.39% cold and −4.16%
warm** — so the same model is promoted or refused depending on a reading nobody
had chosen. The decomposition is in `docs/promotion-gate-slice-decision.md`:
26.6% of the holdout users carry 78.6% of the aggregate's NDCG mass, so the
aggregate on this dataset is close to a cold-slice gate wearing an aggregate's
name. That is the mirror image of the failure the per-slice reporting rule
exists to prevent — the cold slice cannot be hidden by the warm majority, and
here the warm slice was being hidden by the cold minority.

The owner's decision (2026-08-30) is the memo's option (c), and it is what this
module implements:

    overall NDCG@k must gain at least `min_relative_gain` relative to the
    incumbent, AND neither slice may regress by more than its tolerance.

The two clauses are deliberately asymmetric, because they make different kinds
of claim:

  * **The overall clause is a positive claim** the candidate has to establish.
    If it cannot be computed — an incumbent scoring exactly zero makes a
    relative gain undefined — then it has not been established, and the gate
    refuses.
  * **The slice clauses are negative claims** — "this did not get materially
    worse". If a slice cannot be compared (the incumbent has no users in it, or
    scored zero), then no regression has been demonstrated either, so the
    clause does not block. It is recorded as `comparable=False` rather than
    silently counted as a pass.

The tolerance is measured, not chosen. See `SliceTolerance` below and the
2026-08-30 section of `docs/results.md` for the seed runs it comes from.

Nothing in this module reads MLflow, a database or a file: `promotion_decision`
is a pure function over two `EvalResult`s, so Phase 4's Prefect promotion task
and a unit test call exactly the same code. The MLflow read lives in the CLI at
the bottom, behind a function-local import.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Final

from .aggregate import MismatchedResultsError, mean_eval_result
from .protocol import EvalResult

# ADR 0001's threshold, unchanged by the 2026-08-30 amendment: "3% filters out
# retraining noise while remaining achievable for genuine architectural
# improvements (expected 5–15% gains between major changes)."
MIN_RELATIVE_GAIN: Final = 0.03

# The metric the gate reads. ADR 0001 pins NDCG@10 for the recommender end to
# end; `EvalResult.k` carries which K a given result was scored at, and the
# gate refuses to compare two results scored at different ones.
GATE_METRIC: Final = "ndcg"


class GateInputError(ValueError):
    """The two results cannot be compared at all.

    Distinct from "the candidate failed": a failure is a verdict this module
    is happy to return, whereas this is the gate declining to produce one
    because the inputs do not describe the same question. The only case today
    is a K mismatch — comparing a candidate stage's recall@500-era result with
    a ranker's NDCG@10 one would silently answer a question nobody asked.
    """


@dataclass(frozen=True)
class SliceTolerance:
    """How far each slice may regress, relative, before the gate refuses.

    Positive fractions: ``warm=0.03`` means "warm NDCG@k may fall by at most
    3% relative to the incumbent". A slice that improves is never constrained.

    The defaults below are :data:`MEASURED_SLICE_TOLERANCE` — derived from
    seed-to-seed variation rather than picked, because a tolerance chosen by
    taste is a threshold that cannot be defended in the review where it
    matters.
    """

    warm: float
    cold: float

    def __post_init__(self) -> None:
        for name in ("warm", "cold"):
            value = getattr(self, name)
            if value < 0:
                raise GateInputError(
                    f"{name} tolerance must be a non-negative relative fraction, got {value!r}"
                )

    def for_slice(self, name: str) -> float:
        if name == "warm":
            return self.warm
        if name == "cold":
            return self.cold
        raise GateInputError(f"no tolerance defined for slice {name!r}")


# --- The measured noise floor -----------------------------------------------
#
# Provenance, so these numbers are never mistaken for a preference. Measured
# 2026-08-30 on the machine and dataset `docs/results.md` describes (Apple M3,
# MovieLens 25M at DVC version c3ce6309f6f0ec347a9e0a662c640021.dir), at
# `COLD_START_THRESHOLD = 10` under threshold routing — the defaults since
# ADR 0001's amendment — over a holdout of 1,931 warm and 710 cold users.
# CF/ALS and the LightGBM ranker were each run at three seeds, 42, 7 and 13,
# with nothing else changed, via `TRAIN_SEED` (see `src/training/seeds.py`).
# The popularity baseline and item-item cosine have no stochastic component at
# all and were not re-run: the same inputs produce the same model, so their
# seed-to-seed spread is exactly zero and measuring it would prove nothing.
#
# Relative spread ((max − min) / mean) of NDCG@10 across those three seeds:
#
#     model    slice    min       max       mean      range    sd
#     CF/ALS   warm     0.057572  0.059295  0.058158   2.96%   1.69%
#     CF/ALS   cold     0.483440  0.483440  0.483440   0.00%   0.00%
#     ranker   warm     0.069967  0.071150  0.070495   1.68%   0.85%
#     ranker   cold     0.544948  0.556510  0.549533   2.10%   1.12%
#
# The rule is unchanged from the first derivation: **2× the largest relative
# range observed on that slice, rounded up to the next whole percentage point,
# with a floor of 0.5%.** Two rather than one because the gate compares two
# independently seeded runs, so the difference it reads carries both runs'
# noise and not one run's; rounding up because a tolerance sitting exactly on
# an observed maximum will refuse a model for a wobble the next re-seed would
# have produced; and a 0.5% floor because anything finer is below the
# resolution at which `docs/results.md` publishes these numbers. That gives
# warm 6% and cold 5%.
#
# **These replace the warm 58% / cold 7% measured earlier the same day, and the
# reason they could fall is the point.** That first measurement had the ranker's
# warm NDCG@10 moving 28.68% of its own mean on the seed alone, because
# `RANKER_POSITIVE_LIMIT` was 20,000 against a trailing window holding 154,003
# rows — so the seed was choosing *which* positives the ranker trained on and a
# re-seed was a different training set. The limit now sits above the window's
# size (`src/training/sampling.py`), the three seeds build the identical
# training set (87,794 groups, 1,843,674 rows, to the row), and the warm spread
# falls to 1.68%. **A 58% warm clause could not have refused any regression a
# reviewer would care about; a 6% one can.** The derivation, the three sample
# sizes it is read off, and what the gate then says about the ladder are in
# `docs/results.md`'s 2026-08-30 sample-size section.
#
# One caveat these numbers carry: they describe the pipeline at its *default*
# sample size. A run made with `RANKER_POSITIVE_LIMIT` set below the trailing
# window's size is a noisier pipeline and is not covered — every run logs
# `ranker_positive_limit` and `ranker_positive_limit_binding` so that is
# checkable rather than assumed.
MEASURED_SLICE_TOLERANCE: Final = SliceTolerance(warm=0.06, cold=0.05)

DEFAULT_SLICE_TOLERANCE: Final = MEASURED_SLICE_TOLERANCE


@dataclass(frozen=True)
class OverallVerdict:
    """The aggregate clause: did the candidate clear the required gain?"""

    metric: str
    incumbent: float
    candidate: float
    #: ``None`` when the incumbent scored zero and a relative gain is undefined.
    relative_change: float | None
    required_gain: float
    passed: bool
    detail: str


@dataclass(frozen=True)
class SliceVerdict:
    """One slice's non-regression clause."""

    name: str
    incumbent: float
    candidate: float
    incumbent_users: int
    candidate_users: int
    #: ``None`` when the slice could not be compared — see ``comparable``.
    relative_change: float | None
    tolerance: float
    #: False when the incumbent has no users in this slice, or scored zero in
    #: it. Such a slice cannot block promotion, and says so rather than
    #: appearing as a pass it did not earn.
    comparable: bool
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateDecision:
    """The structured verdict: promote or not, and exactly which clause said so."""

    promote: bool
    k: int
    metric: str
    overall: OverallVerdict
    slices: tuple[SliceVerdict, ...]

    @property
    def failures(self) -> tuple[str, ...]:
        """Every clause that refused, each naming its own margin.

        Empty when ``promote`` is True — so "why was this refused?" is
        answerable from the decision object alone, without re-deriving
        anything.
        """
        reasons = [] if self.overall.passed else [self.overall.detail]
        reasons.extend(s.detail for s in self.slices if not s.passed)
        return tuple(reasons)

    def summary(self) -> str:
        """A few lines a human or a CI log can read without a JSON parser."""
        headline = "PROMOTE" if self.promote else "DO NOT PROMOTE"
        lines = [f"{headline} — {self.metric}@{self.k}", f"  overall: {self.overall.detail}"]
        lines.extend(f"  {s.name}: {s.detail}" for s in self.slices)
        if self.failures:
            lines.append("  refused because:")
            lines.extend(f"    - {reason}" for reason in self.failures)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failures"] = list(self.failures)
        return payload


def promotion_decision(
    candidate: EvalResult,
    incumbent: EvalResult,
    *,
    min_relative_gain: float = MIN_RELATIVE_GAIN,
    slice_tolerance: SliceTolerance = DEFAULT_SLICE_TOLERANCE,
) -> GateDecision:
    """Apply ADR 0001's gate to a candidate against the incumbent champion.

    Args:
        candidate: the challenger's `EvalResult`, from `src/evaluation/`.
        incumbent: the current champion's, scored over the same holdout at the
            same K. A K mismatch raises rather than returning a verdict.
        min_relative_gain: the aggregate clause's threshold, as a relative
            fraction. ADR 0001's +3% is the default.
        slice_tolerance: how far warm and cold may each regress before the gate
            refuses. Defaults to the measured floor, not to zero — see
            :data:`MEASURED_SLICE_TOLERANCE`.

    Returns:
        A `GateDecision` naming every clause's margin, whether it promoted or
        not.

    Raises:
        GateInputError: the two results are not comparable (different K), or
            the thresholds are negative.
    """
    if candidate.k != incumbent.k:
        raise GateInputError(
            f"candidate was scored at k={candidate.k} and the incumbent at k={incumbent.k}; "
            "a gate verdict across two different K values would answer a question nobody "
            "asked (see EvalResult.k)"
        )
    if min_relative_gain < 0:
        raise GateInputError(
            f"min_relative_gain must be a non-negative relative fraction, got "
            f"{min_relative_gain!r}"
        )

    overall = _overall_verdict(candidate, incumbent, min_relative_gain)
    slices = (
        _slice_verdict(
            "warm",
            incumbent_value=incumbent.warm.ndcg,
            candidate_value=candidate.warm.ndcg,
            incumbent_users=incumbent.n_warm_users,
            candidate_users=candidate.n_warm_users,
            tolerance=slice_tolerance.for_slice("warm"),
            k=candidate.k,
        ),
        _slice_verdict(
            "cold",
            incumbent_value=incumbent.cold.ndcg,
            candidate_value=candidate.cold.ndcg,
            incumbent_users=incumbent.n_cold_users,
            candidate_users=candidate.n_cold_users,
            tolerance=slice_tolerance.for_slice("cold"),
            k=candidate.k,
        ),
    )
    return GateDecision(
        promote=overall.passed and all(s.passed for s in slices),
        k=candidate.k,
        metric=GATE_METRIC,
        overall=overall,
        slices=slices,
    )


def _at_least(value: float, bound: float) -> bool:
    """``value >= bound``, with the boundary itself inclusive.

    Both sides are computed quantities, so a bare `>=` decides the exact
    boundary on representation error: a warm slice that fell by exactly 2%
    against a 2% tolerance evaluates to -0.020000000000000018, which is a hair
    below the bound for no reason anybody could defend in a review. The
    thresholds here are derived from a rounded measurement in the first place;
    letting one turn on the seventeenth decimal would be precision the number
    does not have.
    """
    return value >= bound or math.isclose(value, bound, rel_tol=1e-9, abs_tol=1e-12)


def _overall_verdict(
    candidate: EvalResult, incumbent: EvalResult, required_gain: float
) -> OverallVerdict:
    inc = incumbent.overall.ndcg
    cand = candidate.overall.ndcg
    label = f"{GATE_METRIC}@{candidate.k}"
    if inc <= 0.0:
        # A relative gain against zero is undefined, and the aggregate clause
        # is the one the candidate has to *establish*. Refusing is the honest
        # outcome: an incumbent scoring zero on the holdout is a broken or
        # absent champion, not a low bar to clear.
        return OverallVerdict(
            metric=label,
            incumbent=inc,
            candidate=cand,
            relative_change=None,
            required_gain=required_gain,
            passed=False,
            detail=(
                f"overall {label} incumbent is {inc:.6f}, so a relative gain is undefined; "
                "the gate cannot confirm the required improvement"
            ),
        )
    relative = (cand - inc) / inc
    passed = _at_least(relative, required_gain)
    verb = "gained" if relative >= 0 else "lost"
    return OverallVerdict(
        metric=label,
        incumbent=inc,
        candidate=cand,
        relative_change=relative,
        required_gain=required_gain,
        passed=passed,
        detail=(
            f"overall {label} {verb} {abs(relative):.2%} "
            f"({inc:.6f} → {cand:.6f}) against a required +{required_gain:.2%}"
        ),
    )


def _slice_verdict(
    name: str,
    *,
    incumbent_value: float,
    candidate_value: float,
    incumbent_users: int,
    candidate_users: int,
    tolerance: float,
    k: int,
) -> SliceVerdict:
    label = f"{GATE_METRIC}@{k}"
    if incumbent_users == 0 or incumbent_value <= 0.0:
        # Nothing to regress from. `_mean([])` is 0.0, so an empty slice and a
        # slice that genuinely scored zero arrive here identically; both are
        # reported as not comparable rather than as a pass, because a clause
        # that could not be evaluated is not a clause that was satisfied.
        why = (
            "the incumbent has no users in it"
            if incumbent_users == 0
            else f"the incumbent scored {incumbent_value:.6f} in it"
        )
        return SliceVerdict(
            name=name,
            incumbent=incumbent_value,
            candidate=candidate_value,
            incumbent_users=incumbent_users,
            candidate_users=candidate_users,
            relative_change=None,
            tolerance=tolerance,
            comparable=False,
            passed=True,
            detail=(
                f"{name} {label} not comparable — {why}, so no regression can be "
                f"measured; this clause does not block (candidate {candidate_value:.6f}, "
                f"n={candidate_users})"
            ),
        )
    relative = (candidate_value - incumbent_value) / incumbent_value
    passed = _at_least(relative, -tolerance)
    if relative >= 0:
        detail = (
            f"{name} {label} improved {relative:.2%} "
            f"({incumbent_value:.6f} → {candidate_value:.6f}, n={incumbent_users})"
        )
    elif passed:
        detail = (
            f"{name} {label} regressed {abs(relative):.2%} "
            f"({incumbent_value:.6f} → {candidate_value:.6f}, n={incumbent_users}), "
            f"within the {tolerance:.2%} tolerance"
        )
    else:
        detail = (
            f"{name} {label} regressed {abs(relative):.2%} "
            f"({incumbent_value:.6f} → {candidate_value:.6f}, n={incumbent_users}), "
            f"beyond the {tolerance:.2%} tolerance by "
            f"{abs(relative) - tolerance:.2%}"
        )
    return SliceVerdict(
        name=name,
        incumbent=incumbent_value,
        candidate=candidate_value,
        incumbent_users=incumbent_users,
        candidate_users=candidate_users,
        relative_change=relative,
        tolerance=tolerance,
        comparable=True,
        passed=passed,
        detail=detail,
    )


# --- CLI ---------------------------------------------------------------------
#
# The shape Phase 4's Prefect promotion task will call. It reads two MLflow
# runs and prints the verdict; the decision itself is `promotion_decision`
# above, which is where the logic lives and where the tests point.

#: How a trainer says which K it scored at, and what it therefore named its
#: metrics. The two travel together: the recommender-end-to-end trainers log
#: `k` (or `k_final`) beside `*_at_k`, and the candidate-stage trainers log
#: `k_candidates` beside `*_at_k_candidates`. Order matters because the ranker
#: logs *both* `k_final` (10) and `k_candidates` (500) — reading the second
#: would score a top-10 result as though it were a top-500 one, which is
#: exactly the confusion `EvalResult.k` exists to prevent. A pair only matches
#: when the metrics it names are actually present, so the K and the numbers
#: read next to it can never come from different questions.
_K_PARAM_SUFFIXES: Final = (
    ("k_final", "at_k"),
    ("k", "at_k"),
    ("k_candidates", "at_k_candidates"),
)

_EXIT_PROMOTE: Final = 0
_EXIT_REFUSE: Final = 1
_EXIT_UNDECIDED: Final = 2


class RunNotUsableError(RuntimeError):
    """An MLflow run does not carry the metrics or params the gate needs."""


def eval_result_from_mlflow_run(run: Any) -> EvalResult:
    """Rebuild an `EvalResult` from the metrics a trainer logged.

    Every trainer in `src/training/` logs the same six metric names and the two
    slice sizes, so this is a faithful reconstruction rather than a guess — but
    only of the fields the gate reads. `synthetic_cold_slices` is deliberately
    not rebuilt: ADR 0011's buckets are reported per run and are not part of
    the promotion decision.
    """
    from .protocol import UserMetrics

    metrics = run.data.metrics
    params = run.data.params
    for k_param, suffix in _K_PARAM_SUFFIXES:
        if k_param in params and f"overall_ndcg_{suffix}" in metrics:
            break
    else:
        raise RunNotUsableError(
            f"run {run.info.run_id} carries no (K parameter, metric suffix) pair the gate "
            f"recognises — it looked for {', '.join(f'{p}/{s}' for p, s in _K_PARAM_SUFFIXES)}. "
            "An unfinished or killed run has parameters and no metrics, and cannot be gated"
        )
    missing = [
        name
        for name in (
            f"warm_ndcg_{suffix}",
            f"cold_ndcg_{suffix}",
            "n_warm_users",
            "n_cold_users",
        )
        if name not in metrics
    ]
    if missing:
        raise RunNotUsableError(
            f"run {run.info.run_id} is missing {', '.join(missing)} — a partially logged run "
            "cannot be gated"
        )
    return EvalResult(
        warm=UserMetrics(
            recall=metrics.get(f"warm_recall_{suffix}", 0.0), ndcg=metrics[f"warm_ndcg_{suffix}"]
        ),
        cold=UserMetrics(
            recall=metrics.get(f"cold_recall_{suffix}", 0.0), ndcg=metrics[f"cold_ndcg_{suffix}"]
        ),
        overall=UserMetrics(
            recall=metrics.get(f"overall_recall_{suffix}", 0.0),
            ndcg=metrics[f"overall_ndcg_{suffix}"],
        ),
        n_warm_users=int(metrics["n_warm_users"]),
        n_cold_users=int(metrics["n_cold_users"]),
        k=int(float(params[k_param])),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.gate",
        description=(
            "ADR 0001's promotion gate over two MLflow runs: overall NDCG@k must gain at "
            "least the required relative amount, and neither slice may regress beyond its "
            "measured tolerance. Either side accepts several run ids, in which case the "
            "gate reads their mean — the right comparison for a model whose metrics move "
            "with the seed."
        ),
        epilog=(
            f"Exit codes: {_EXIT_PROMOTE} promote, {_EXIT_REFUSE} do not promote, "
            f"{_EXIT_UNDECIDED} could not decide (runs not comparable or not found)."
        ),
    )
    parser.add_argument(
        "--candidate",
        required=True,
        nargs="+",
        metavar="RUN_ID",
        help="MLflow run id(s) of the challenger; several are averaged",
    )
    parser.add_argument(
        "--incumbent",
        required=True,
        nargs="+",
        metavar="RUN_ID",
        help="MLflow run id(s) of the champion; several are averaged",
    )
    parser.add_argument(
        "--min-relative-gain",
        type=float,
        default=MIN_RELATIVE_GAIN,
        help=f"aggregate clause threshold as a fraction (default {MIN_RELATIVE_GAIN})",
    )
    parser.add_argument(
        "--warm-tolerance",
        type=float,
        default=DEFAULT_SLICE_TOLERANCE.warm,
        help=f"warm-slice regression tolerance (default {DEFAULT_SLICE_TOLERANCE.warm})",
    )
    parser.add_argument(
        "--cold-tolerance",
        type=float,
        default=DEFAULT_SLICE_TOLERANCE.cold,
        help=f"cold-slice regression tolerance (default {DEFAULT_SLICE_TOLERANCE.cold})",
    )
    parser.add_argument(
        "--tracking-uri",
        default=None,
        help="MLflow tracking URI (defaults to Settings.mlflow_tracking_uri)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the decision as JSON instead of prose"
    )
    args = parser.parse_args(argv)

    # Imported here rather than at module scope so that importing the gate —
    # which Phase 4's flow and every unit test do — never requires an MLflow
    # client or a reachable tracking server.
    import mlflow

    from src.config import Settings

    mlflow.set_tracking_uri(args.tracking_uri or Settings().mlflow_tracking_uri)
    client = mlflow.tracking.MlflowClient()

    def read(run_ids: list[str]) -> EvalResult:
        return mean_eval_result([eval_result_from_mlflow_run(client.get_run(r)) for r in run_ids])

    try:
        candidate = read(args.candidate)
        incumbent = read(args.incumbent)
        decision = promotion_decision(
            candidate,
            incumbent,
            min_relative_gain=args.min_relative_gain,
            slice_tolerance=SliceTolerance(warm=args.warm_tolerance, cold=args.cold_tolerance),
        )
    except (GateInputError, MismatchedResultsError, RunNotUsableError) as exc:
        print(f"could not decide: {exc}")
        return _EXIT_UNDECIDED

    if args.json:
        payload = decision.to_dict()
        payload["candidate_run_ids"] = args.candidate
        payload["incumbent_run_ids"] = args.incumbent
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"candidate  {_describe(args.candidate)}")
        print(f"incumbent  {_describe(args.incumbent)}")
        print(decision.summary())
    return _EXIT_PROMOTE if decision.promote else _EXIT_REFUSE


def _describe(run_ids: list[str]) -> str:
    """Name the runs behind one side, saying so when it is a mean of several."""
    if len(run_ids) == 1:
        return run_ids[0]
    return f"mean of {len(run_ids)}: " + ", ".join(run_ids)


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
