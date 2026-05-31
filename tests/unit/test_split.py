import pandas as pd
import pytest

from src.data.split import (
    HOLDOUT_DAYS,
    TRAIN_FRACTION,
    temporal_split,
)

_DAY = 24 * 3600


def _frame(timestamps: list[int]) -> pd.DataFrame:
    """Minimal ratings-shaped frame so tests exercise the same column path production does."""
    return pd.DataFrame(
        {
            "userId": list(range(len(timestamps))),
            "movieId": list(range(len(timestamps))),
            "rating": [4.0] * len(timestamps),
            "timestamp": timestamps,
        }
    )


def test_cutoff_is_value_present_in_input() -> None:
    # 10 rows at t = 0, 100, …, 900. method="lower" 80th-percentile picks index 7 → 700.
    timestamps = [i * 100 for i in range(10)]
    result = temporal_split(_frame(timestamps))
    assert result.cutoff == 700
    # Never an interpolated value:
    assert result.cutoff in timestamps


def test_train_is_strictly_before_cutoff() -> None:
    timestamps = [i * 100 for i in range(10)]
    result = temporal_split(_frame(timestamps))
    assert (result.train["timestamp"] < result.cutoff).all()


def test_boundary_row_lands_in_holdout_not_train() -> None:
    # ADR's `<` boundary: a row at exactly t == cutoff is NOT a training example.
    # This is the leak-prevention guarantee — verify it explicitly.
    timestamps = [i * 100 for i in range(10)]
    result = temporal_split(_frame(timestamps))
    on_boundary = result.holdout[result.holdout["timestamp"] == result.cutoff]
    assert len(on_boundary) == 1
    assert (result.train["timestamp"] == result.cutoff).sum() == 0


def test_holdout_window_is_28_days() -> None:
    base = 1_000_000_000
    timestamps = [
        base + 0 * _DAY,
        base + 10 * _DAY,
        base + 27 * _DAY + 86_399,  # just inside the 28-day window
        base + 28 * _DAY,  # exactly at holdout_end → goes to test
        base + 100 * _DAY,
    ]
    result = temporal_split(_frame(timestamps))
    assert result.holdout_end - result.cutoff == HOLDOUT_DAYS * _DAY
    assert (result.holdout["timestamp"] < result.holdout_end).all()
    assert (result.test["timestamp"] >= result.holdout_end).all()


def test_partition_is_disjoint_and_covers_input() -> None:
    timestamps = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    result = temporal_split(_frame(timestamps))
    assert len(result.train) + len(result.holdout) + len(result.test) == len(timestamps)


def test_unsorted_input_produces_same_split_as_sorted() -> None:
    # The function must not depend on input ordering — a query whose ORDER BY
    # changes shouldn't silently reshape the train/holdout/test partition.
    sorted_ts = [i * 100 for i in range(10)]
    shuffled_ts = [500, 0, 900, 200, 700, 100, 400, 800, 300, 600]
    a = temporal_split(_frame(sorted_ts))
    b = temporal_split(_frame(shuffled_ts))
    assert a.cutoff == b.cutoff
    assert len(a.train) == len(b.train)
    assert len(a.holdout) == len(b.holdout)
    assert len(a.test) == len(b.test)


def test_empty_input_returns_empty_slices() -> None:
    result = temporal_split(_frame([]))
    assert result.train.empty
    assert result.holdout.empty
    assert result.test.empty
    assert result.cutoff == 0
    assert result.holdout_end == 0


def test_missing_timestamp_column_raises() -> None:
    df = pd.DataFrame({"userId": [1], "movieId": [2], "rating": [4.0]})
    with pytest.raises(KeyError, match="timestamp"):
        temporal_split(df)


def test_constants_match_adr_0001() -> None:
    # These are the contract with ADR 0001. Changing them silently is a
    # protocol change in disguise — the test forces a deliberate update
    # (and ideally an ADR amendment) when it ever moves.
    assert TRAIN_FRACTION == 0.8
    assert HOLDOUT_DAYS == 28
