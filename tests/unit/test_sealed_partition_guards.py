"""O-25 for every trainer that can subsample: cut at the full split, then check the rows.

WO-1 found that a user subsample computed its own 80th-percentile cutoff, which
put the 6% pilot's whole holdout inside the sealed window, and fixed SASRec's
trainer. The owner's 2026-10-07 ruling extends the fix to every other trainer
that can subsample: item-item, last-item, two-tower and the SASRec ranker. Each
test below hands a trainer a late-heavy subsample — one whose own quantile would
move the holdout past the full split's boundary — and checks, at the trainer's
own guard, that it was cut at the full frame's boundaries and that nothing it
fits or scores reaches the sealed partition.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import pytest

import src.training.candidate_data as candidate_data
import src.training.itemitem as itemitem
import src.training.last_item as last_item
import src.training.sasrec_ranker as sasrec_ranker
import src.training.twotower as twotower
from src.data.split import TemporalSplit, temporal_split
from src.models.candidates.twotower import TwoTowerConfig


class _ReachedGuardError(Exception):
    """Raised by the spy once the guard has run, so no trainer fits anything."""


def _ratings() -> pd.DataFrame:
    rows = [
        (user, 1_000 + (user * 13 + day) % 97, 4.0, day * 86_400 + user)
        for user in range(1, 41)
        for day in range(0, 400, 1 + user % 5)
    ]
    return pd.DataFrame(rows, columns=["userId", "movieId", "rating", "timestamp"])


def _late_heavy(full: pd.DataFrame) -> pd.DataFrame:
    """A sample dominated by late activity: its own quantile crosses the boundary."""
    whole = temporal_split(full)
    late = pd.concat(
        [full[full["userId"] <= 4], full[full["timestamp"] >= whole.cutoff]]
    ).drop_duplicates()
    assert temporal_split(late).holdout_end > whole.holdout_end
    return late.reset_index(drop=True)


def _spy_on_the_guard(monkeypatch: pytest.MonkeyPatch, module: Any) -> dict[str, Any]:
    """Run the trainer's real guard, record what it saw, then stop the trainer."""
    seen: dict[str, Any] = {}

    def spy(full: pd.DataFrame, split: TemporalSplit, fitted: pd.DataFrame) -> dict[str, int]:
        seen.update(
            full=full,
            split=split,
            fitted=fitted,
            partition=candidate_data.sealed_partition_params(full, split, fitted),
        )
        raise _ReachedGuardError

    monkeypatch.setattr(module, "sealed_partition_params", spy)
    return seen


def _feed_a_late_heavy_subsample(monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    full = _ratings()
    late = _late_heavy(full)

    def subsample(ratings: pd.DataFrame, fraction: float, seed: int) -> pd.DataFrame:
        assert ratings is full and fraction == 0.5
        return late

    monkeypatch.setattr(candidate_data, "subsample_users", subsample)
    return full


def _assert_cut_at_the_full_split(seen: dict[str, Any], full: pd.DataFrame) -> None:
    whole = temporal_split(full)
    split: TemporalSplit = seen["split"]
    assert seen["full"] is full
    assert (split.cutoff, split.holdout_end) == (whole.cutoff, whole.holdout_end)
    partition = seen["partition"]
    assert partition["sealed_boundary_timestamp"] == whole.holdout_end
    assert partition["latest_fit_timestamp"] < whole.cutoff
    assert partition["latest_scored_timestamp"] < whole.holdout_end
    assert int(seen["fitted"]["timestamp"].max()) < whole.holdout_end
    assert not split.holdout.empty


def _postgres_returns(monkeypatch: pytest.MonkeyPatch, module: Any, full: pd.DataFrame) -> None:
    class _Engine:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(module, "create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr(module, "load_ratings", lambda _engine: full)


@pytest.mark.parametrize(
    ("module", "entrypoint"),
    [(itemitem, itemitem.main), (last_item, last_item.main)],
    ids=["itemitem", "last_item"],
)
def test_a_postgres_trainer_cuts_its_subsample_at_the_full_split(
    monkeypatch: pytest.MonkeyPatch, module: Any, entrypoint: Callable[[], None]
) -> None:
    full = _feed_a_late_heavy_subsample(monkeypatch)
    _postgres_returns(monkeypatch, module, full)
    monkeypatch.setattr(module, "resolve_sample_fraction", lambda: 0.5)
    seen = _spy_on_the_guard(monkeypatch, module)

    with pytest.raises(_ReachedGuardError):
        entrypoint()

    _assert_cut_at_the_full_split(seen, full)


def test_the_two_tower_cuts_its_subsample_at_the_full_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = _feed_a_late_heavy_subsample(monkeypatch)
    seen = _spy_on_the_guard(monkeypatch, twotower)

    with pytest.raises(_ReachedGuardError):
        twotower.run_once(
            full, pd.DataFrame(), TwoTowerConfig(), sample_fraction=0.5, routing_policy="threshold"
        )

    _assert_cut_at_the_full_split(seen, full)


def test_the_sasrec_ranker_cuts_its_subsample_at_the_full_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = _feed_a_late_heavy_subsample(monkeypatch)
    _postgres_returns(monkeypatch, sasrec_ranker, full)
    monkeypatch.setattr(sasrec_ranker, "_load_movies", lambda _engine: pd.DataFrame())
    monkeypatch.setattr(sasrec_ranker, "resolve_sample_fraction", lambda: 0.5)
    seen = _spy_on_the_guard(monkeypatch, sasrec_ranker)

    with pytest.raises(_ReachedGuardError):
        sasrec_ranker.prepare_shared()

    _assert_cut_at_the_full_split(seen, full)


def test_the_full_frame_is_split_exactly_as_before() -> None:
    full = _ratings()
    ratings, split = candidate_data.sample_and_split(full, sample_fraction=1.0, sample_seed=7)
    whole = temporal_split(full)

    assert ratings is full
    assert (split.cutoff, split.holdout_end) == (whole.cutoff, whole.holdout_end)
    assert split.train.equals(whole.train) and split.holdout.equals(whole.holdout)


def test_the_guard_still_refuses_rows_that_reach_the_sealed_partition() -> None:
    """The backstop: a split cut at its own quantile is refused on the rows."""
    full = _ratings()
    own_quantile = temporal_split(_late_heavy(full))

    with pytest.raises(candidate_data.SealedPartitionError, match="sealed partition"):
        candidate_data.sealed_partition_params(full, own_quantile, own_quantile.train)
