"""
Temporal train / holdout / test split per ADR 0001.

The split is computed from the data — never materialized as separate tables.
Training and evaluation code both call ``temporal_split`` so the membership
of every row in train vs. holdout vs. test is defined in exactly one place,
which removes the class of bug where a later re-materialization drifts from
an earlier one. Cutoff and window parameters live as named module-level
constants tied to the ADR.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Per ADR 0001: cutoff T is the timestamp by which 80% of interactions have
# happened; holdout is the next 28 days; everything after is reserved test.
TRAIN_FRACTION = 0.8
HOLDOUT_DAYS = 28
_HOLDOUT_SECONDS = HOLDOUT_DAYS * 24 * 3600

TIMESTAMP_COL = "timestamp"


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

    if TIMESTAMP_COL not in ratings.columns:
        raise KeyError(f"Expected '{TIMESTAMP_COL}' column in ratings DataFrame")

    timestamps = ratings[TIMESTAMP_COL].to_numpy()
    cutoff = int(np.quantile(timestamps, TRAIN_FRACTION, method="lower"))
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
