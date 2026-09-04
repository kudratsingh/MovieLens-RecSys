"""
Rolling-origin backtest aggregation.

Deliberately a separate module from ``aggregate.py``. That one is reachable from
``src/evaluation/__init__.py``, which ``src.serving.app`` imports transitively through
``src.serving.orchestration`` — so anything it pulls in lands in the slim API image,
which ADR 0008 keeps free of numpy, pandas, LightGBM, Feast, FAISS and torch. The
bootstrap below needs numpy, and the window ids come from ``src.data.split``, which
needs pandas. Putting this beside ``mean_eval_result`` therefore puts both into an
image built without them, which is what ``tests/unit/test_serving_image_imports.py``
and ``tests/unit/test_release_bootstrap.py`` exist to catch. They caught it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np

from src.data.split import MIN_BACKTEST_WINDOWS

# --- Rolling-origin backtest reporting (M0-07) --------------------------------
#
# `mean_eval_result` above averages away *seed* noise: several runs of one model
# on one holdout. What follows averages away *window* noise: one model across
# several rolling-origin windows (`src/data/split.py`). They are different
# questions and stay separate functions — a seed mean says "this is the model",
# a window summary says "and it was not one lucky month".
#
# The reporting contract is mean, dispersion, worst window, and a user-level
# bootstrap interval. Design notes live in
# `docs/model-planning/contracts/rolling-origin-backtests.md`.

# Deliberately not one of the training seeds (42, 7, 13): a report that says
# "seed 42" should never have two possible meanings.
BOOTSTRAP_SEED: Final = 20260904
BOOTSTRAP_REPLICATES: Final = 2000
BOOTSTRAP_CONFIDENCE: Final = 0.95
BOOTSTRAP_METHOD: Final = "percentile"


class BacktestAggregationError(ValueError):
    """The per-window inputs do not describe one comparable backtest.

    Sibling of `MismatchedResultsError` and raised in the same spirit, but kept
    separate because it fails on a different kind of input: window-keyed
    per-user values rather than finished `EvalResult`s. A caller doing both
    wants to know which layer refused.
    """


@dataclass(frozen=True)
class BootstrapInterval:
    """A user-clustered bootstrap interval for an across-window quantity.

    `point` is the observed statistic; `low`/`high` are percentiles of the
    replicate distribution and are *not* recentred on it, so a skewed
    distribution gives an interval that is not symmetric about `point`. That is
    the percentile method behaving correctly, not a bug to be tidied away.
    """

    point: float
    low: float
    high: float
    confidence: float
    replicates: int
    seed: int
    n_users: int
    method: str = BOOTSTRAP_METHOD


@dataclass(frozen=True)
class WindowSummary:
    window_id: str
    value: float
    n_users: int


@dataclass(frozen=True)
class BacktestSummary:
    """What a rolling backtest reports: the mean, the spread, the worst case.

    `relative_range` is `None` rather than infinite when the mean is zero — a
    ratio to nothing is not a dispersion measurement and should not be printed
    as one.
    """

    windows: tuple[WindowSummary, ...]
    mean: float
    stdev: float
    minimum: float
    maximum: float
    relative_range: float | None
    worst_window_id: str
    interval: BootstrapInterval


def paired_user_deltas(
    candidate: Mapping[str, Mapping[int, float]],
    incumbent: Mapping[str, Mapping[int, float]],
) -> dict[str, dict[int, float]]:
    """Per-window, per-user candidate-minus-incumbent differences.

    Pairing is the whole point. Users differ from each other far more than two
    models differ on one user, so an unpaired comparison spends most of its
    interval width on between-user variance that both models share. Requiring
    identical populations per window is what makes the subtraction legal — the
    same rule `retrieval_gate` applies to slice populations, one level down.

    Raises:
        BacktestAggregationError: the two sides cover different windows, or a
            window's two sides scored different users.
    """
    if set(candidate) != set(incumbent):
        only_candidate = sorted(set(candidate) - set(incumbent))
        only_incumbent = sorted(set(incumbent) - set(candidate))
        raise BacktestAggregationError(
            "candidate and incumbent cover different backtest windows "
            f"(candidate-only: {only_candidate}, incumbent-only: {only_incumbent})"
        )

    deltas: dict[str, dict[int, float]] = {}
    for window_id in sorted(candidate):
        candidate_users = candidate[window_id]
        incumbent_users = incumbent[window_id]
        if set(candidate_users) != set(incumbent_users):
            raise BacktestAggregationError(
                f"window {window_id!r} scored different populations: "
                f"{len(candidate_users)} candidate users vs {len(incumbent_users)} incumbent "
                "users; an unpaired difference here would not be a per-user delta"
            )
        deltas[window_id] = {
            user: float(candidate_users[user]) - float(incumbent_users[user])
            for user in sorted(candidate_users)
        }
    return deltas


def backtest_summary(
    per_user_by_window: Mapping[str, Mapping[int, float]],
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
    confidence: float = BOOTSTRAP_CONFIDENCE,
) -> BacktestSummary:
    """Summarize one quantity across rolling-origin windows.

    Args:
        per_user_by_window: `{window_id: {user_id: value}}`. The value is
            whatever the caller is reporting — one model's per-user recall, or
            the paired delta from `paired_user_deltas`. This function does not
            need to know which, and deliberately does not ask: the arithmetic is
            identical and inventing two near-identical functions would invite
            them to drift.
        seed: fixed by default. A bootstrap whose interval moves between two
            readings of the same numbers is not evidence.
        replicates: resamples drawn.
        confidence: two-sided coverage, e.g. 0.95 for a 2.5%/97.5% interval.

    Returns:
        Mean across windows (unweighted — each window is one observation of
        "would this have held that month?", and weighting by user count would
        let one dense window outvote the others), the sample standard deviation
        and range across windows, the worst window, and the bootstrap interval.

    Raises:
        BacktestAggregationError: fewer than `MIN_BACKTEST_WINDOWS` windows, an
            empty window, a non-finite value, or a resampled population too thin
            to give every window users.
    """
    _validate_windows(per_user_by_window)
    _validate_bootstrap_arguments(seed, replicates, confidence)

    window_ids = tuple(sorted(per_user_by_window))
    summaries = tuple(
        WindowSummary(
            window_id=window_id,
            value=statistics.fmean(per_user_by_window[window_id].values()),
            n_users=len(per_user_by_window[window_id]),
        )
        for window_id in window_ids
    )
    values = [summary.value for summary in summaries]
    mean = statistics.fmean(values)
    minimum = min(values)
    maximum = max(values)
    worst = min(summaries, key=lambda summary: (summary.value, summary.window_id))

    return BacktestSummary(
        windows=summaries,
        mean=mean,
        stdev=statistics.stdev(values),
        minimum=minimum,
        maximum=maximum,
        relative_range=None if mean == 0 else (maximum - minimum) / abs(mean),
        worst_window_id=worst.window_id,
        interval=_user_cluster_bootstrap(
            per_user_by_window,
            window_ids,
            point=mean,
            seed=seed,
            replicates=replicates,
            confidence=confidence,
        ),
    )


def _validate_windows(per_user_by_window: Mapping[str, Mapping[int, float]]) -> None:
    if len(per_user_by_window) < MIN_BACKTEST_WINDOWS:
        raise BacktestAggregationError(
            f"a backtest summary needs at least {MIN_BACKTEST_WINDOWS} windows, got "
            f"{len(per_user_by_window)}; fewer is the single-window verdict rolling backtests "
            "exist to replace"
        )
    for window_id, users in per_user_by_window.items():
        if not isinstance(window_id, str) or not window_id.strip():
            raise BacktestAggregationError(
                f"window id must be a non-empty string, got {window_id!r}"
            )
        if not users:
            raise BacktestAggregationError(
                f"window {window_id!r} has no scored users; an empty window is unavailable, "
                "not zero"
            )
        for user, value in users.items():
            if type(user) is not int:
                raise BacktestAggregationError(
                    f"window {window_id!r} has a non-integer user id {user!r}"
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BacktestAggregationError(
                    f"window {window_id!r} user {user} has a non-numeric value {value!r}"
                )
            if not math.isfinite(float(value)):
                raise BacktestAggregationError(
                    f"window {window_id!r} user {user} has a non-finite value {value!r}"
                )


def _validate_bootstrap_arguments(seed: int, replicates: int, confidence: float) -> None:
    if type(seed) is not int:
        raise BacktestAggregationError(f"bootstrap seed must be an integer, got {seed!r}")
    if type(replicates) is not int or replicates < 1:
        raise BacktestAggregationError(
            f"bootstrap replicates must be a positive integer, got {replicates!r}"
        )
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 < float(confidence) < 1.0
    ):
        raise BacktestAggregationError(
            f"bootstrap confidence must be a fraction strictly between 0 and 1, got {confidence!r}"
        )


def _user_cluster_bootstrap(
    per_user_by_window: Mapping[str, Mapping[int, float]],
    window_ids: tuple[str, ...],
    *,
    point: float,
    seed: int,
    replicates: int,
    confidence: float,
) -> BootstrapInterval:
    """Resample *users*, not interactions and not windows, and not per window.

    The user is the sampling unit because the metric of record is an unweighted
    mean over users: the population the interval describes is "another draw of
    MovieLens users", which is the variation a promotion decision is exposed to.
    Resampling interactions would treat one user's items as independent
    observations when they share a single taste, and would report an interval
    narrower than the evidence supports.

    A user is drawn as a *cluster* — once, for all windows at the same time —
    because the windows share users and are therefore correlated. Resampling
    independently inside each window would pretend the windows are independent
    replications and understate the aggregate's width, which is the specific
    error that would make a rolling backtest look more conclusive than a single
    holdout rather than less.
    """
    pool = tuple(sorted({user for users in per_user_by_window.values() for user in users}))
    n_users = len(pool)
    index_of = {user: position for position, user in enumerate(pool)}

    present = np.zeros((len(window_ids), n_users), dtype=np.float64)
    values = np.zeros((len(window_ids), n_users), dtype=np.float64)
    for row, window_id in enumerate(window_ids):
        for user, value in per_user_by_window[window_id].items():
            column = index_of[user]
            present[row, column] = 1.0
            values[row, column] = float(value)

    rng = np.random.default_rng(seed)
    replicate_statistics = np.empty(replicates, dtype=np.float64)
    for replicate in range(replicates):
        draw = rng.integers(0, n_users, size=n_users)
        multiplicity = np.bincount(draw, minlength=n_users).astype(np.float64)
        denominator = present @ multiplicity
        if not bool(np.all(denominator > 0)):
            empty = [window_ids[row] for row in range(len(window_ids)) if denominator[row] == 0]
            raise BacktestAggregationError(
                f"resampled user draw left {empty} with no scored users; the shared user "
                "population is too thin for a clustered bootstrap across these windows"
            )
        replicate_statistics[replicate] = float(np.mean((values @ multiplicity) / denominator))

    tail = (1.0 - confidence) / 2.0
    return BootstrapInterval(
        point=point,
        low=float(np.quantile(replicate_statistics, tail)),
        high=float(np.quantile(replicate_statistics, 1.0 - tail)),
        confidence=confidence,
        replicates=replicates,
        seed=seed,
        n_users=n_users,
    )
