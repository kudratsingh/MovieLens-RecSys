import math

import pandas as pd
import pytest

from src.data.split import (
    BACKTEST_WINDOW_DAYS,
    BACKTEST_WINDOW_SCHEMA,
    DEFAULT_BACKTEST_WINDOWS,
    HOLDOUT_DAYS,
    MIN_BACKTEST_WINDOWS,
    TRAIN_FRACTION,
    BacktestWindow,
    BacktestWindowError,
    apply_backtest_window,
    assert_no_window_leakage,
    fixed_holdout_window,
    rolling_origin_windows,
    sealed_test_boundary,
    temporal_split,
)

_DAY = 24 * 3600
_BASE = 1_000_000_000


def _frame(timestamps: list[int]) -> pd.DataFrame:
    """Minimal ratings-shaped frame, same column path production takes."""
    return pd.DataFrame(
        {
            "userId": [i % 7 for i in range(len(timestamps))],
            "movieId": list(range(len(timestamps))),
            "rating": [4.0] * len(timestamps),
            "timestamp": timestamps,
        }
    )


def _daily_frame(n_days: int) -> pd.DataFrame:
    """One interaction per day, so window arithmetic is easy to reason about."""
    return _frame([_BASE + day * _DAY for day in range(n_days)])


# --- The fixed split must keep behaving exactly as it does today --------------


@pytest.mark.parametrize(
    "timestamps",
    [
        [i * 100 for i in range(10)],
        [500, 0, 900, 200, 700, 100, 400, 800, 300, 600],
        [_BASE + day * _DAY for day in range(400)],
        [_BASE] * 20 + [_BASE + 40 * _DAY] * 5,
        [7],
    ],
)
def test_temporal_split_matches_an_independent_reading_of_adr_0001(timestamps: list[int]) -> None:
    # Deliberately not a call into src/: this recomputes the ADR's rule from
    # scratch (80th-percentile *value*, ties to the later slice) so the refactor
    # that introduced the window helpers cannot pass by agreeing with itself.
    ordered = sorted(timestamps)
    expected_cutoff = ordered[math.floor(TRAIN_FRACTION * (len(ordered) - 1))]
    expected_end = expected_cutoff + HOLDOUT_DAYS * _DAY

    result = temporal_split(_frame(timestamps))

    assert result.cutoff == expected_cutoff
    assert result.holdout_end == expected_end
    assert list(result.train["timestamp"]) == [t for t in timestamps if t < expected_cutoff]
    assert list(result.holdout["timestamp"]) == [
        t for t in timestamps if expected_cutoff <= t < expected_end
    ]
    assert list(result.test["timestamp"]) == [t for t in timestamps if t >= expected_end]


def test_sealed_test_boundary_agrees_with_temporal_split() -> None:
    for frame in (_daily_frame(400), _frame([1, 2, 3]), _frame([])):
        assert sealed_test_boundary(frame) == temporal_split(frame).holdout_end


def test_sealed_test_boundary_requires_a_timestamp_column() -> None:
    with pytest.raises(KeyError, match="timestamp"):
        sealed_test_boundary(pd.DataFrame({"userId": [1], "movieId": [2]}))


# --- Window 0 is ADR 0001's holdout, by construction --------------------------


def test_window_zero_reproduces_the_fixed_split_row_for_row() -> None:
    ratings = _daily_frame(400)
    split = temporal_split(ratings)
    window = rolling_origin_windows(ratings)[0]

    assert window.is_fixed_holdout
    assert window.train_cutoff == split.cutoff
    assert window.holdout_start == split.cutoff
    assert window.holdout_end == split.holdout_end

    applied = apply_backtest_window(ratings, window)
    pd.testing.assert_frame_equal(applied.train, split.train)
    pd.testing.assert_frame_equal(applied.holdout, split.holdout)


def test_window_zero_reproduces_the_fixed_split_when_the_cutoff_is_a_tied_timestamp() -> None:
    # The continuity claim is only worth having if it survives the boundary case
    # it is most likely to break on: many rows sharing the cutoff second.
    ratings = _frame([_BASE + day * _DAY for day in range(300)] + [_BASE + 239 * _DAY] * 60)
    split = temporal_split(ratings)
    window = rolling_origin_windows(ratings)[0]

    assert (ratings["timestamp"] == split.cutoff).sum() == 61, "fixture must tie on the cutoff"
    assert split.cutoff == window.holdout_start
    applied = apply_backtest_window(ratings, window)
    pd.testing.assert_frame_equal(applied.train, split.train)
    pd.testing.assert_frame_equal(applied.holdout, split.holdout)


def test_fixed_holdout_window_is_window_zero() -> None:
    ratings = _daily_frame(400)
    assert fixed_holdout_window(ratings) == rolling_origin_windows(ratings)[0]


# --- Window layout ------------------------------------------------------------


def test_windows_tile_backwards_contiguously_from_the_sealed_boundary() -> None:
    ratings = _daily_frame(400)
    boundary = sealed_test_boundary(ratings)
    windows = rolling_origin_windows(ratings, 4)

    assert [window.index for window in windows] == [0, 1, 2, 3]
    assert windows[0].holdout_end == boundary
    for window in windows:
        assert window.holdout_end - window.holdout_start == BACKTEST_WINDOW_DAYS * _DAY
        assert window.train_cutoff == window.holdout_start
        assert window.test_boundary == boundary
    for newer, older in zip(windows[:-1], windows[1:], strict=True):
        assert older.holdout_end == newer.holdout_start


def test_window_ids_are_stable_when_more_windows_are_requested() -> None:
    # Indices count back from the anchor precisely so that widening the suite
    # cannot silently repoint an id at a different month.
    ratings = _daily_frame(400)
    three = rolling_origin_windows(ratings, 3)
    four = rolling_origin_windows(ratings, 4)
    assert [w.window_id for w in three] == [w.window_id for w in four[:3]]


def test_window_id_carries_schema_index_and_interval() -> None:
    window = BacktestWindow(
        index=2, train_cutoff=200, holdout_start=200, holdout_end=300, test_boundary=500
    )
    assert window.window_id == f"{BACKTEST_WINDOW_SCHEMA}:w2:200-300"


def test_windows_do_not_depend_on_row_order() -> None:
    ratings = _daily_frame(400)
    shuffled = ratings.sample(frac=1.0, random_state=0).reset_index(drop=True)
    assert rolling_origin_windows(ratings) == rolling_origin_windows(shuffled)


# --- Boundary behaviour -------------------------------------------------------


def test_equal_timestamps_at_a_boundary_land_in_the_later_slice() -> None:
    # Same rule as temporal_split: `t < train_cutoff` trains, `t == holdout_start`
    # is scored. A shared second can never put a target into training data.
    window = BacktestWindow(
        index=1, train_cutoff=200, holdout_start=200, holdout_end=300, test_boundary=400
    )
    result = apply_backtest_window(_frame([199, 200, 200, 200, 299, 300, 300, 399]), window)

    assert list(result.train["timestamp"]) == [199]
    assert list(result.holdout["timestamp"]) == [200, 200, 200, 299]


def test_rows_at_or_after_holdout_end_are_not_returned() -> None:
    window = BacktestWindow(
        index=1, train_cutoff=200, holdout_start=200, holdout_end=300, test_boundary=400
    )
    result = apply_backtest_window(_frame([100, 250, 300, 350, 400, 999]), window)

    returned = list(result.train["timestamp"]) + list(result.holdout["timestamp"])
    assert returned == [100, 250]


def test_every_window_trains_strictly_before_its_own_holdout() -> None:
    ratings = _daily_frame(400)
    for window in rolling_origin_windows(ratings, 4):
        result = apply_backtest_window(ratings, window)
        assert (result.train["timestamp"] < window.holdout_start).all()
        assert (result.holdout["timestamp"] >= window.holdout_start).all()
        assert (result.holdout["timestamp"] < window.holdout_end).all()


# --- The sealed test partition ------------------------------------------------


def test_no_window_returns_a_row_from_the_sealed_partition() -> None:
    ratings = _daily_frame(400)
    boundary = sealed_test_boundary(ratings)
    assert (ratings["timestamp"] >= boundary).any(), "fixture must contain sealed rows to be a test"

    for window in rolling_origin_windows(ratings, 4):
        result = apply_backtest_window(ratings, window)
        assert (result.train["timestamp"] < boundary).all()
        assert (result.holdout["timestamp"] < boundary).all()


def test_a_window_split_has_no_test_attribute() -> None:
    # Structural, not conventional: there is no attribute to reach for, so the
    # sealed partition cannot be evaluated against by typo.
    ratings = _daily_frame(400)
    result = apply_backtest_window(ratings, fixed_holdout_window(ratings))
    assert not hasattr(result, "test")


def test_a_window_reaching_past_the_sealed_boundary_is_refused() -> None:
    with pytest.raises(BacktestWindowError, match="sealed test boundary"):
        BacktestWindow(
            index=0, train_cutoff=100, holdout_start=100, holdout_end=500, test_boundary=400
        )


def test_a_window_training_on_its_own_holdout_is_refused() -> None:
    with pytest.raises(BacktestWindowError, match="train on its own holdout"):
        BacktestWindow(
            index=0, train_cutoff=250, holdout_start=200, holdout_end=300, test_boundary=400
        )


def test_an_empty_holdout_interval_is_refused() -> None:
    with pytest.raises(BacktestWindowError, match="empty holdout interval"):
        BacktestWindow(
            index=0, train_cutoff=200, holdout_start=200, holdout_end=200, test_boundary=400
        )


# --- The no-overlap contract, enforced ----------------------------------------


def test_derived_windows_satisfy_the_no_overlap_contract() -> None:
    assert_no_window_leakage(rolling_origin_windows(_daily_frame(400), 4))


def test_overlapping_holdouts_are_refused() -> None:
    windows = [
        BacktestWindow(
            index=0, train_cutoff=200, holdout_start=200, holdout_end=300, test_boundary=400
        ),
        BacktestWindow(
            index=1, train_cutoff=150, holdout_start=150, holdout_end=250, test_boundary=400
        ),
    ]
    with pytest.raises(BacktestWindowError, match="share holdout rows"):
        assert_no_window_leakage(windows)


def test_windows_from_different_datasets_are_refused() -> None:
    windows = [
        BacktestWindow(
            index=0, train_cutoff=200, holdout_start=200, holdout_end=300, test_boundary=300
        ),
        BacktestWindow(
            index=1, train_cutoff=100, holdout_start=100, holdout_end=200, test_boundary=400
        ),
    ]
    with pytest.raises(BacktestWindowError, match="disagree about the sealed test boundary"):
        assert_no_window_leakage(windows)


def test_duplicate_window_identities_are_refused() -> None:
    window = BacktestWindow(
        index=1, train_cutoff=100, holdout_start=100, holdout_end=200, test_boundary=400
    )
    with pytest.raises(BacktestWindowError, match="duplicate window"):
        assert_no_window_leakage([window, window])


def test_an_empty_window_set_is_refused() -> None:
    with pytest.raises(BacktestWindowError, match="no windows"):
        assert_no_window_leakage([])


def test_a_newer_window_may_train_on_an_older_windows_holdout() -> None:
    # Not leakage, and the contract says so explicitly: this is the simulation
    # of retraining on everything that has happened. Pinned as a test so nobody
    # "fixes" it later without reading why.
    ratings = _daily_frame(400)
    windows = rolling_origin_windows(ratings, 3)
    newest_train = apply_backtest_window(ratings, windows[0]).train
    oldest_holdout = apply_backtest_window(ratings, windows[2]).holdout
    assert set(oldest_holdout["timestamp"]) <= set(newest_train["timestamp"])


# --- Refusals rather than degenerate windows ----------------------------------


def test_fewer_than_three_windows_is_refused() -> None:
    with pytest.raises(BacktestWindowError, match="at least 3 windows"):
        rolling_origin_windows(_daily_frame(400), 2)


def test_more_windows_than_the_data_supports_is_refused() -> None:
    # 60 days cannot carry three 28-day windows plus training data before them.
    with pytest.raises(BacktestWindowError, match="does not reach far enough back"):
        rolling_origin_windows(_daily_frame(60), 3)


def test_empty_frames_are_refused() -> None:
    with pytest.raises(BacktestWindowError, match="empty ratings frame"):
        rolling_origin_windows(_frame([]))
    with pytest.raises(BacktestWindowError, match="empty ratings frame"):
        fixed_holdout_window(_frame([]))


def test_constants_match_the_backtest_contract() -> None:
    # The window length is ADR 0001's holdout length by definition — decouple
    # them and window 0 stops being the fixed holdout.
    assert BACKTEST_WINDOW_DAYS == HOLDOUT_DAYS
    assert MIN_BACKTEST_WINDOWS == 3
    assert DEFAULT_BACKTEST_WINDOWS == MIN_BACKTEST_WINDOWS
    assert BACKTEST_WINDOW_SCHEMA == "rolling-origin-v1"
