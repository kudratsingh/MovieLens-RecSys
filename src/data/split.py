"""
Temporal train / holdout / test split per ADR 0001, and the rolling-origin
backtest windows that development evidence is drawn from.

The split is computed from the data — never materialized as separate tables.
Training and evaluation code both call ``temporal_split`` so the membership
of every row in train vs. holdout vs. test is defined in exactly one place,
which removes the class of bug where a later re-materialization drifts from
an earlier one. Cutoff and window parameters live as named module-level
constants tied to the ADR.

``rolling_origin_windows`` adds the second half of that story. One fixed
holdout has now carried repeated model decisions, and every decision taken
against it fits it a little further by selection — the risk register calls this
R-02. Rolling-origin backtests are the answer: several windows, each a separate
"train on the past, score the next 28 days" simulation, so a verdict has to hold
up more than once. The windows are tiled backwards from the sealed-test
boundary, which makes the newest of them ADR 0001's holdout exactly rather than
something that merely resembles it; see
``docs/model-planning/contracts/rolling-origin-backtests.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Per ADR 0001: cutoff T is the timestamp by which 80% of interactions have
# happened; holdout is the next 28 days; everything after is reserved test.
TRAIN_FRACTION = 0.8
HOLDOUT_DAYS = 28
_HOLDOUT_SECONDS = HOLDOUT_DAYS * 24 * 3600

TIMESTAMP_COL = "timestamp"

# A backtest window is exactly as long as ADR 0001's holdout, by definition
# rather than by configuration. The windows are laid backwards from the sealed
# boundary, so equal length is precisely what makes window 0 *be* the fixed
# holdout — give the windows their own length and the continuity claim, and the
# ability to read a recorded number as a window result, both evaporate.
BACKTEST_WINDOW_DAYS = HOLDOUT_DAYS
_BACKTEST_WINDOW_SECONDS = BACKTEST_WINDOW_DAYS * 24 * 3600

# Three is a floor, not a default anyone should feel free to lower. Two windows
# can still be one lucky pair, and a suite that can be reduced to a single
# window is the thing this module exists to stop being.
MIN_BACKTEST_WINDOWS = 3
DEFAULT_BACKTEST_WINDOWS = 3

# Every window id carries this prefix, and it moves if the tiling rule ever
# changes. The id is what ``ProtocolManifest.backtest_window_id`` holds, so a
# run made under an older rule then differs semantically from one made under the
# new rule and the gate refuses to pool them — which is the correct outcome and
# not something anyone has to remember to do.
BACKTEST_WINDOW_SCHEMA = "rolling-origin-v1"


@dataclass
class TemporalSplit:
    """Result of a single temporal split, with both the slices and the cutoffs.

    The cutoffs are returned alongside the frames so downstream code (e.g.
    the eval harness building cold-start counts) can re-derive boundaries
    without re-running the quantile computation.
    """

    train: pd.DataFrame
    holdout: pd.DataFrame
    test: pd.DataFrame
    cutoff: int  # Unix epoch seconds. timestamp < cutoff → train.
    holdout_end: int  # cutoff + HOLDOUT_DAYS · 86400. timestamp ≥ holdout_end → test.


def temporal_split(ratings: pd.DataFrame) -> TemporalSplit:
    """Split a ratings DataFrame on time per ADR 0001.

    The cutoff T is the timestamp of the 80th-percentile interaction selected
    via ``method="lower"`` — i.e. an actual value from the input, never an
    interpolated fractional epoch second that no row could have. Train is
    ``t < T``; holdout is ``[T, T+28d)``; test is everything from ``T+28d`` on.

    Ties at the boundary land in the *later* slice. A row at exactly
    ``t == cutoff`` goes to holdout, not train — matches the ADR's strict
    inequality and means a model can never be trained on its own holdout
    even if many rows share a second.

    Empty input returns three empty frames and ``cutoff == 0`` so callers can
    use the same code path regardless of whether their query produced rows.
    """
    if ratings.empty:
        empty = ratings.iloc[0:0]
        return TemporalSplit(
            train=empty,
            holdout=empty,
            test=empty,
            cutoff=0,
            holdout_end=0,
        )

    _require_timestamp_column(ratings)
    cutoff = _cutoff(ratings)
    holdout_end = cutoff + _HOLDOUT_SECONDS

    is_train = ratings[TIMESTAMP_COL] < cutoff
    is_test = ratings[TIMESTAMP_COL] >= holdout_end
    is_holdout = ~is_train & ~is_test

    return TemporalSplit(
        train=ratings.loc[is_train].reset_index(drop=True),
        holdout=ratings.loc[is_holdout].reset_index(drop=True),
        test=ratings.loc[is_test].reset_index(drop=True),
        cutoff=cutoff,
        holdout_end=holdout_end,
    )


def _require_timestamp_column(ratings: pd.DataFrame) -> None:
    if TIMESTAMP_COL not in ratings.columns:
        raise KeyError(f"Expected '{TIMESTAMP_COL}' column in ratings DataFrame")


def _cutoff(ratings: pd.DataFrame) -> int:
    """ADR 0001's cutoff T, computed in the one place both split flavours read."""
    timestamps = ratings[TIMESTAMP_COL].to_numpy()
    return int(np.quantile(timestamps, TRAIN_FRACTION, method="lower"))


def sealed_test_boundary(ratings: pd.DataFrame) -> int:
    """The timestamp at which ADR 0001's reserved test partition begins.

    Equal to ``temporal_split(ratings).holdout_end`` — the same cutoff helper
    computes both — but without materializing three copies of the frame, which
    matters when the only thing a caller wants from 25M rows is one integer.

    Returns 0 on empty input, matching ``temporal_split``'s empty-frame
    contract so callers can't get a different answer depending on which of the
    two they happened to ask.
    """
    if ratings.empty:
        return 0
    _require_timestamp_column(ratings)
    return _cutoff(ratings) + _HOLDOUT_SECONDS


class BacktestWindowError(ValueError):
    """A backtest window is malformed, or cannot be derived from this data.

    Raised rather than returning a degenerate window: a window with an empty
    holdout scores 0.0, and a 0.0 that came from having nothing to score is
    indistinguishable in a report from a 0.0 the model earned.
    """


@dataclass(frozen=True)
class BacktestWindow:
    """One rolling-origin backtest window: what it may train on, what it scores.

    ``index`` counts backwards from the anchor, so window 0 is the most recent
    window (ADR 0001's own holdout) and window 2 is two windows older. Counting
    backwards rather than forwards is what keeps ids stable: asking for a fourth
    window appends ``w3`` and renames nothing, so a window id means the same
    interval regardless of how many windows the caller wanted.

    ``train_cutoff`` and ``holdout_start`` are equal today and are still two
    fields, because they are two different claims — the last instant a model may
    learn from, and the first instant it is scored on. An embargo between them
    would be a change of value, not a change of shape.
    """

    index: int
    train_cutoff: int  # timestamp < train_cutoff → this window's training data.
    holdout_start: int  # timestamp ≥ holdout_start → no longer trainable here.
    holdout_end: int  # timestamp ≥ holdout_end → outside this window entirely.
    test_boundary: int  # ADR 0001's sealed boundary. Nothing here may reach it.

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise BacktestWindowError(
                f"window index must be a non-negative integer, got {self.index!r}"
            )
        for name in ("train_cutoff", "holdout_start", "holdout_end", "test_boundary"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise BacktestWindowError(
                    f"{name} must be a non-negative integer timestamp, got {value!r}"
                )
        if self.train_cutoff > self.holdout_start:
            raise BacktestWindowError(
                f"window w{self.index} would train on its own holdout: "
                f"train_cutoff={self.train_cutoff} is after holdout_start={self.holdout_start}"
            )
        if self.holdout_start >= self.holdout_end:
            raise BacktestWindowError(
                f"window w{self.index} has an empty holdout interval "
                f"[{self.holdout_start}, {self.holdout_end})"
            )
        if self.holdout_end > self.test_boundary:
            raise BacktestWindowError(
                f"window w{self.index} ends at {self.holdout_end}, at or beyond the sealed "
                f"test boundary {self.test_boundary}; development evidence never reads the "
                "test partition"
            )

    @property
    def window_id(self) -> str:
        """The identity ``ProtocolManifest.backtest_window_id`` is meant to hold.

        The interval is in the id even though the manifest also carries
        ``holdout_start``/``holdout_end`` separately. The redundancy is the
        point: an id that is only an index silently means a different interval
        the day the anchor moves, and two runs would then look comparable
        because their window ids matched.
        """
        return f"{BACKTEST_WINDOW_SCHEMA}:w{self.index}:{self.holdout_start}-{self.holdout_end}"

    @property
    def is_fixed_holdout(self) -> bool:
        """Whether this is ADR 0001's holdout, which window 0 is by construction."""
        return self.index == 0


@dataclass
class BacktestSplit:
    """Train and holdout for one window — and deliberately nothing else.

    There is no ``test`` attribute and no way to get one from here. The sealed
    partition should not be one attribute access away from an evaluation, and a
    reviewer should be able to see that from the type rather than by reading
    every call site.
    """

    window: BacktestWindow
    train: pd.DataFrame
    holdout: pd.DataFrame


def rolling_origin_windows(
    ratings: pd.DataFrame,
    n_windows: int = DEFAULT_BACKTEST_WINDOWS,
) -> tuple[BacktestWindow, ...]:
    """Derive the development backtest windows, newest first.

    Window ``i`` holds out ``[B − (i+1)·28d, B − i·28d)`` where ``B`` is the
    sealed-test boundary, and trains on everything strictly before its own
    holdout. Window 0 therefore reproduces ADR 0001's split exactly — same
    cutoff, same holdout, same rows — which is what lets an already-recorded
    fixed-holdout number be read as a window result instead of as a separate
    number sitting next to the windows.

    Args:
        ratings: the full pre-split ratings frame. Only ``timestamp`` is read.
        n_windows: how many windows to derive, at least ``MIN_BACKTEST_WINDOWS``.

    Raises:
        BacktestWindowError: fewer than three windows were asked for, the frame
            is empty, or the data does not reach back far enough for every
            window to have both training rows and holdout rows.
    """
    if type(n_windows) is not int or n_windows < MIN_BACKTEST_WINDOWS:
        raise BacktestWindowError(
            f"a backtest needs at least {MIN_BACKTEST_WINDOWS} windows, got {n_windows!r}; "
            "fewer is the single-window verdict this is meant to replace"
        )
    if ratings.empty:
        raise BacktestWindowError("cannot derive backtest windows from an empty ratings frame")

    boundary = sealed_test_boundary(ratings)
    windows = tuple(_window_at(boundary, index) for index in range(n_windows))
    assert_no_window_leakage(windows)
    _require_populated(ratings, windows)
    return windows


def fixed_holdout_window(ratings: pd.DataFrame) -> BacktestWindow:
    """ADR 0001's split expressed as window 0, so a fixed-holdout run can be stamped.

    A run against the plain ``temporal_split`` has always had a window identity;
    it just had no way to say so. This is that name, and it is the same object
    ``rolling_origin_windows`` returns first — so if the two ever disagreed, the
    tiling rule would be broken rather than the label.
    """
    if ratings.empty:
        raise BacktestWindowError("cannot derive a backtest window from an empty ratings frame")
    window = _window_at(sealed_test_boundary(ratings), 0)
    _require_populated(ratings, (window,))
    return window


def apply_backtest_window(ratings: pd.DataFrame, window: BacktestWindow) -> BacktestSplit:
    """Slice ``ratings`` into one window's train and holdout.

    Boundary behaviour is ADR 0001's, unchanged: ``t < train_cutoff`` trains,
    ``[holdout_start, holdout_end)`` is scored, and a row landing exactly on a
    boundary belongs to the later slice. Equal timestamps therefore cannot put a
    model's own target into its training data no matter how many rows share a
    second — the single most load-bearing detail in this file.

    Rows at or after ``holdout_end`` are dropped rather than returned; for
    window 0 that set is exactly the sealed test partition.
    """
    _require_timestamp_column(ratings)
    timestamps = ratings[TIMESTAMP_COL]
    is_train = timestamps < window.train_cutoff
    is_holdout = (timestamps >= window.holdout_start) & (timestamps < window.holdout_end)
    return BacktestSplit(
        window=window,
        train=ratings.loc[is_train].reset_index(drop=True),
        holdout=ratings.loc[is_holdout].reset_index(drop=True),
    )


def assert_no_window_leakage(windows: Sequence[BacktestWindow]) -> None:
    """Enforce what "no overlap" means for a set of windows.

    Four claims, all mechanical:

    1. **No window trains on its own future.** ``train_cutoff <= holdout_start``
       for every window, checked when the window is constructed so it holds for
       hand-built windows too.
    2. **No two windows share a holdout row.** Holdout intervals are pairwise
       disjoint, so a single interaction is never scored twice and the mean
       across windows is a mean over distinct evidence.
    3. **No window reads the sealed partition**, and every window in a set was
       derived against the same boundary. A set that mixes boundaries was built
       from two different datasets and its aggregate means nothing.
    4. **Window ids and indices are unique**, because two windows answering to
       one id would be pooled as repeated measurements of one thing.

    What this deliberately does *not* forbid is a newer window training on an
    older window's holdout. Under an expanding origin that is unavoidable, and
    it is not leakage — it is the simulation: retrain on everything that has
    happened, then serve the next 28 days. Forbidding it would mean each window
    trained on a different slice of history, which turns a temporal comparison
    into a training-set-size comparison and stops window 0 from reproducing
    ADR 0001. The contract document argues this at length; the cost, which is
    real, is that window results are correlated and the aggregate interval has
    to account for it.
    """
    if not windows:
        raise BacktestWindowError("no windows to validate")

    boundaries = {window.test_boundary for window in windows}
    if len(boundaries) > 1:
        raise BacktestWindowError(
            f"windows disagree about the sealed test boundary {sorted(boundaries)}; "
            "they were not derived from one dataset and must not be aggregated"
        )

    indices = [window.index for window in windows]
    duplicate_indices = sorted({index for index in indices if indices.count(index) > 1})
    if duplicate_indices:
        raise BacktestWindowError(f"duplicate window indices {duplicate_indices}")

    ids = [window.window_id for window in windows]
    duplicate_ids = sorted({window_id for window_id in ids if ids.count(window_id) > 1})
    if duplicate_ids:
        raise BacktestWindowError(f"duplicate window ids {duplicate_ids}")

    ordered = sorted(windows, key=lambda window: window.holdout_start)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.holdout_end > later.holdout_start:
            raise BacktestWindowError(
                f"windows w{earlier.index} [{earlier.holdout_start}, {earlier.holdout_end}) and "
                f"w{later.index} [{later.holdout_start}, {later.holdout_end}) share holdout rows"
            )


def _window_at(test_boundary: int, index: int) -> BacktestWindow:
    """Tile one window backwards from the sealed boundary."""
    holdout_end = test_boundary - index * _BACKTEST_WINDOW_SECONDS
    holdout_start = holdout_end - _BACKTEST_WINDOW_SECONDS
    if holdout_start < 0:
        raise BacktestWindowError(
            f"window w{index} would start before the epoch; the dataset does not span "
            f"{index + 1} windows of {BACKTEST_WINDOW_DAYS} days"
        )
    return BacktestWindow(
        index=index,
        train_cutoff=holdout_start,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        test_boundary=test_boundary,
    )


def _require_populated(ratings: pd.DataFrame, windows: Sequence[BacktestWindow]) -> None:
    """Refuse windows the data cannot actually support.

    Asking for more windows than the dataset spans is the obvious way to get a
    suite of confident-looking zeros, so it fails here rather than in a report.
    """
    _require_timestamp_column(ratings)
    timestamps = ratings[TIMESTAMP_COL].to_numpy()
    for window in windows:
        n_train = int((timestamps < window.train_cutoff).sum())
        n_holdout = int(
            ((timestamps >= window.holdout_start) & (timestamps < window.holdout_end)).sum()
        )
        if n_train == 0 or n_holdout == 0:
            raise BacktestWindowError(
                f"window w{window.index} [{window.holdout_start}, {window.holdout_end}) has "
                f"{n_train} training rows and {n_holdout} holdout rows; the data does not reach "
                "far enough back to support this many windows"
            )
