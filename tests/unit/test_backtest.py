import pytest

from src.evaluation.backtest import (
    BOOTSTRAP_METHOD,
    BacktestAggregationError,
    backtest_summary,
    paired_user_deltas,
)

_REPLICATES = 200


def _windows(
    *values: dict[int, float],
) -> dict[str, dict[int, float]]:
    """Window-keyed per-user values, ids in the shape the split module emits."""
    return {f"rolling-origin-v1:w{index}:{index}00-{index}99": v for index, v in enumerate(values)}


def _spread_population(scale: float, n_users: int = 24) -> dict[str, dict[int, float]]:
    """Three windows over one shared user panel, dispersion controlled by `scale`."""
    return {
        f"rolling-origin-v1:w{window}:{window}00-{window}99": {
            user: scale * ((user % 5) - 2) + 0.1 * window for user in range(n_users)
        }
        for window in range(3)
    }


# --- Mean, dispersion, worst window -------------------------------------------


def test_summary_reports_mean_dispersion_and_worst_window() -> None:
    summary = backtest_summary(
        _windows({1: 0.0, 2: 0.2}, {1: 0.4, 2: 0.6}, {1: 0.3, 2: 0.3}),
        replicates=_REPLICATES,
    )

    assert [window.value for window in summary.windows] == pytest.approx([0.1, 0.5, 0.3])
    assert [window.n_users for window in summary.windows] == [2, 2, 2]
    assert summary.mean == pytest.approx(0.3)
    assert summary.stdev == pytest.approx(0.2)
    assert summary.minimum == pytest.approx(0.1)
    assert summary.maximum == pytest.approx(0.5)
    assert summary.relative_range == pytest.approx(0.4 / 0.3)
    assert summary.worst_window_id == summary.windows[0].window_id


def test_windows_are_weighted_equally_regardless_of_population_size() -> None:
    # A window is one observation of "would this have held that month?". Letting
    # a dense window outvote a sparse one would answer a different question.
    summary = backtest_summary(
        _windows(
            {user: 1.0 for user in range(30)},
            {user: 0.0 for user in range(10)},
            {user: 0.0 for user in range(10, 20)},
        ),
        replicates=_REPLICATES,
    )
    # A user-weighted mean would read 0.6 here; an equally-weighted one reads 1/3.
    assert summary.mean == pytest.approx(1.0 / 3.0)


def test_worst_window_tie_break_is_deterministic() -> None:
    summary = backtest_summary(
        _windows({1: 0.5, 2: 0.5}, {1: 0.1, 2: 0.1}, {1: 0.1, 2: 0.1}),
        replicates=_REPLICATES,
    )
    assert summary.worst_window_id == summary.windows[1].window_id


def test_relative_range_is_unavailable_when_the_mean_is_zero() -> None:
    summary = backtest_summary(
        _windows({1: 0.1}, {1: -0.1}, {1: 0.0}),
        replicates=_REPLICATES,
    )
    assert summary.mean == pytest.approx(0.0)
    assert summary.relative_range is None


# --- Pairing ------------------------------------------------------------------


def test_paired_deltas_subtract_per_user() -> None:
    candidate = _windows({1: 0.5, 2: 0.4}, {1: 0.2, 2: 0.9}, {1: 0.1, 2: 0.1})
    incumbent = _windows({1: 0.3, 2: 0.4}, {1: 0.4, 2: 0.5}, {1: 0.1, 2: 0.0})

    deltas = paired_user_deltas(candidate, incumbent)

    assert deltas == {
        "rolling-origin-v1:w0:000-099": {1: pytest.approx(0.2), 2: pytest.approx(0.0)},
        "rolling-origin-v1:w1:100-199": {1: pytest.approx(-0.2), 2: pytest.approx(0.4)},
        "rolling-origin-v1:w2:200-299": {1: pytest.approx(0.0), 2: pytest.approx(0.1)},
    }


def test_paired_deltas_refuse_different_window_sets() -> None:
    candidate = _windows({1: 0.5}, {1: 0.5}, {1: 0.5})
    incumbent = _windows({1: 0.5}, {1: 0.5})
    with pytest.raises(BacktestAggregationError, match="different backtest windows"):
        paired_user_deltas(candidate, incumbent)


def test_paired_deltas_refuse_different_populations() -> None:
    candidate = _windows({1: 0.5, 2: 0.5}, {1: 0.5}, {1: 0.5})
    incumbent = _windows({1: 0.5}, {1: 0.5}, {1: 0.5})
    with pytest.raises(BacktestAggregationError, match="scored different populations"):
        paired_user_deltas(candidate, incumbent)


# --- Bootstrap ----------------------------------------------------------------


def test_bootstrap_is_reproducible_for_a_fixed_seed() -> None:
    population = _spread_population(0.2)
    first = backtest_summary(population, seed=7, replicates=_REPLICATES).interval
    second = backtest_summary(population, seed=7, replicates=_REPLICATES).interval

    assert (first.low, first.high) == (second.low, second.high)
    assert first.method == BOOTSTRAP_METHOD
    assert first.replicates == _REPLICATES
    assert first.seed == 7
    assert first.n_users == 24


def test_bootstrap_depends_on_the_seed() -> None:
    population = _spread_population(0.2)
    first = backtest_summary(population, seed=7, replicates=_REPLICATES).interval
    second = backtest_summary(population, seed=13, replicates=_REPLICATES).interval
    assert (first.low, first.high) != (second.low, second.high)


def test_bootstrap_ignores_dictionary_ordering() -> None:
    # Insertion order is not evidence. Users and windows are sorted before they
    # index the draw, so a differently-built dict cannot move the interval.
    population = _spread_population(0.2)
    reversed_population = {
        window_id: dict(reversed(list(users.items())))
        for window_id, users in reversed(list(population.items()))
    }

    forward = backtest_summary(population, replicates=_REPLICATES).interval
    backward = backtest_summary(reversed_population, replicates=_REPLICATES).interval

    assert (forward.point, forward.low, forward.high) == (
        backward.point,
        backward.low,
        backward.high,
    )


def test_a_constant_effect_gives_a_zero_width_interval() -> None:
    # Every resample of an all-identical population reproduces the same mean, so
    # a nonzero width here would mean the resampling unit was wrong.
    population = {
        f"rolling-origin-v1:w{window}:{window}00-{window}99": {user: 0.1 for user in range(10)}
        for window in range(3)
    }
    interval = backtest_summary(population, replicates=_REPLICATES).interval

    assert interval.point == pytest.approx(0.1)
    assert interval.low == pytest.approx(0.1)
    assert interval.high == pytest.approx(0.1)


def test_a_more_dispersed_population_gives_a_wider_interval() -> None:
    narrow = backtest_summary(_spread_population(0.05), replicates=_REPLICATES).interval
    wide = backtest_summary(_spread_population(0.50), replicates=_REPLICATES).interval
    assert (wide.high - wide.low) > (narrow.high - narrow.low)


def test_the_interval_covers_a_signal_that_is_really_there() -> None:
    population = _spread_population(0.05)
    summary = backtest_summary(population, replicates=_REPLICATES)
    assert summary.interval.low <= summary.mean <= summary.interval.high


def test_bootstrap_refuses_a_panel_too_thin_to_resample() -> None:
    # A window carried by one user is a window a resample can miss entirely.
    # Silently averaging the windows that survived would quietly change the
    # estimand, so it fails instead.
    population = {
        "rolling-origin-v1:w0:000-099": {user: 0.2 for user in range(30)},
        "rolling-origin-v1:w1:100-199": {user: 0.3 for user in range(30)},
        "rolling-origin-v1:w2:200-299": {999: 0.4},
    }
    with pytest.raises(BacktestAggregationError, match="too thin for a clustered bootstrap"):
        backtest_summary(population, replicates=_REPLICATES)


# --- Refusals -----------------------------------------------------------------


def test_fewer_than_three_windows_is_refused() -> None:
    with pytest.raises(BacktestAggregationError, match="at least 3 windows"):
        backtest_summary(_windows({1: 0.1}, {1: 0.2}), replicates=_REPLICATES)


def test_an_empty_window_is_refused() -> None:
    with pytest.raises(BacktestAggregationError, match="no scored users"):
        backtest_summary(_windows({1: 0.1}, {}, {1: 0.2}), replicates=_REPLICATES)


def test_a_non_finite_value_is_refused() -> None:
    with pytest.raises(BacktestAggregationError, match="non-finite value"):
        backtest_summary(_windows({1: 0.1}, {1: float("nan")}, {1: 0.2}), replicates=_REPLICATES)


def test_a_blank_window_id_is_refused() -> None:
    with pytest.raises(BacktestAggregationError, match="non-empty string"):
        backtest_summary({"a": {1: 0.1}, "b": {1: 0.2}, "  ": {1: 0.3}}, replicates=_REPLICATES)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"replicates": 0}, "positive integer"),
        ({"confidence": 1.0}, "strictly between 0 and 1"),
        ({"confidence": 0.0}, "strictly between 0 and 1"),
        ({"seed": "42"}, "must be an integer"),
    ],
)
def test_bootstrap_arguments_are_validated(kwargs: dict[str, object], message: str) -> None:
    population = _spread_population(0.2)
    call = {"replicates": _REPLICATES, **kwargs}
    with pytest.raises(BacktestAggregationError, match=message):
        backtest_summary(population, **call)  # type: ignore[arg-type]
