"""Early stopping on a probe carved out of train, never out of the holdout (ADR 0020).

Three things are pinned here: the stopping rule itself (3 to 5 passes, stop on
less than 0.5% relative improvement), the probe (a seeded 1% of users each give up
exactly their last training example, which then appears nowhere in training), and
isolation — the code path that decides when to stop cannot see a holdout row.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd
import pytest

import src.models.candidates.sasrec as sasrec
import src.training.sasrec as sasrec_training
from src.data.split import temporal_split
from src.models.candidates.sasrec import SASRecConfig, SASRecModel
from src.models.candidates.sasrec_early_stopping import (
    StoppingProbe,
    carve_stopping_probe,
    should_stop,
)
from src.models.candidates.sequence_data import build_strict_prefix_example_store
from src.training.sasrec import encoder_weights_sha256

# --- the rule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recalls", "stop"),
    [
        ([0.30], False),
        ([0.30, 0.30], False),  # fewer than 3 passes never stops
        ([0.30, 0.33, 0.331], True),  # +0.3% < 0.5%
        ([0.30, 0.33, 0.34], False),  # +3.0%
        ([0.30, 0.33, 0.33 * 1.0051], False),  # just over 0.5% continues
        ([0.30, 0.33, 0.33 * 1.0049], True),
        ([0.30, 0.29, 0.295], True),  # measured against the best so far, not the last
        ([0.30, 0.33, 0.34, 0.35, 0.36], False),  # the cap is the epoch count, not the rule
        ([0.0, 0.0, 0.0], True),
        ([0.0, 0.0, 0.1], False),
    ],
)
def test_the_stopping_rule(recalls: list[float], stop: bool) -> None:
    assert should_stop(recalls, min_epochs=3, min_relative_improvement=0.005) is stop


def test_the_first_pass_never_stops_whatever_the_minimum() -> None:
    assert should_stop([0.5], min_epochs=1, min_relative_improvement=0.005) is False
    assert should_stop([0.5, 0.5], min_epochs=1, min_relative_improvement=0.005) is True


# --- the probe ----------------------------------------------------------------------


def _train(users: int = 400, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, users + 1):
        length = int(rng.integers(1, 15))
        watched = rng.choice(np.arange(10, 90), size=length, replace=False)
        # Small clocks make ties, including tied final timestamp groups.
        clock = np.sort(rng.integers(0, max(1, length // 2), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    return frame.sample(frac=1.0, random_state=seed)  # shuffled, with a non-default index


def _examples(frame: pd.DataFrame, item_to_index: dict[int, int]) -> list[tuple[int, int, int]]:
    """(user, target, prefix end) for every strict-prefix example, in builder order."""
    ordered = frame.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    store = build_strict_prefix_example_store(frame, item_to_index=item_to_index, max_length=50)
    users = ordered["userId"].to_numpy()[store.prefix_ends]
    return [
        (int(u), int(t), int(n)) for u, t, n in zip(users, store.positives, store.prefix_lengths)
    ]


def test_the_probe_is_a_seeded_one_percent_of_users_with_an_example() -> None:
    train = _train()
    eligible = {
        int(user) for user, group in train.groupby("userId") if group["timestamp"].nunique() >= 2
    }
    _remaining, probe = carve_stopping_probe(train, fraction=0.01, seed=42)
    _again, same = carve_stopping_probe(train, fraction=0.01, seed=42)
    _other, different = carve_stopping_probe(train, fraction=0.01, seed=7)

    assert len(probe) == round(0.01 * len(eligible)) >= 1
    assert set(probe.user_ids) <= eligible
    assert probe == same
    assert probe.user_ids != different.user_ids
    _ten, ten_percent = carve_stopping_probe(train, fraction=0.1, seed=42)
    assert len(ten_percent) == round(0.1 * len(eligible))


def test_each_probe_user_gives_up_exactly_its_last_example_and_nothing_else() -> None:
    train = _train()
    remaining, probe = carve_stopping_probe(train, fraction=0.1, seed=42)
    items = sorted(int(item) for item in train["movieId"].unique())
    item_to_index = {item: index + 1 for index, item in enumerate(items)}
    before = _examples(train, item_to_index)
    after = _examples(remaining, item_to_index)

    # One example per probe user leaves; the dropped-target count is the probe size.
    assert len(train) - len(remaining) == len(probe) == len(before) - len(after)
    for user, target, timestamp, history in zip(
        probe.user_ids, probe.targets, probe.timestamps, probe.histories
    ):
        rows = train[train["userId"] == user].sort_values(["timestamp", "movieId"])
        last = rows.iloc[-1]
        assert (int(last["movieId"]), int(last["timestamp"])) == (target, timestamp)
        earlier = rows[rows["timestamp"] < timestamp]
        assert history == tuple(int(movie) for movie in earlier["movieId"])
        assert history  # a probe user always has a strict prefix
        # The probe target is not a training example any more: leaving it in
        # would be leakage.
        dense = item_to_index[target]
        assert (user, dense, len(history)) in before
        assert (user, dense, len(history)) not in after
    # Every other example is untouched, in the same order.
    dropped = {
        (user, item_to_index[target], len(history))
        for user, target, history in zip(probe.user_ids, probe.targets, probe.histories)
    }
    assert after == [example for example in before if example not in dropped]
    # The remaining frame is ``train`` minus the probe rows, in ``train``'s order.
    assert remaining.index.isin(train.index).all()
    assert list(remaining.index) == [label for label in train.index if label in remaining.index]


def test_a_user_with_one_timestamp_is_never_in_the_probe() -> None:
    train = pd.DataFrame(
        [(1, 10, 5), (1, 11, 5), (1, 12, 5), (2, 10, 1), (2, 13, 2)],
        columns=["userId", "movieId", "timestamp"],
    )
    remaining, probe = carve_stopping_probe(train, fraction=0.5, seed=0)
    assert probe.user_ids == (2,)
    assert probe.targets == (13,) and probe.histories == ((10,),)
    assert len(remaining) == 4


def test_the_probe_rows_all_come_from_the_frame_it_is_given() -> None:
    train = _train()
    _remaining, probe = carve_stopping_probe(train, fraction=0.05, seed=1)
    rows = set(train[["userId", "movieId", "timestamp"]].itertuples(index=False, name=None))
    for user, target, timestamp, history in zip(
        probe.user_ids, probe.targets, probe.timestamps, probe.histories
    ):
        assert (user, target, timestamp) in rows
        for movie in history:
            assert any(r[0] == user and r[1] == movie and r[2] < timestamp for r in rows)


# --- the loop --------------------------------------------------------------------


def _config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 10,
        "hidden_dim": 16,
        "num_blocks": 1,
        "num_heads": 2,
        "feedforward_dim": 32,
        "dropout": 0.2,
        "negative_count": 8,
        "loss": "sampled-softmax",
        "batch_size": 64,
        "epochs": 5,
        "faiss_exact": True,
        "seed": 42,
        "early_stopping": True,
        "early_stopping_probe_fraction": 0.05,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _scripted(monkeypatch: pytest.MonkeyPatch, recalls: list[float]) -> None:
    values: Iterator[float] = iter(recalls)
    monkeypatch.setattr(SASRecModel, "_probe_recall", lambda self, probe: next(values))


@pytest.mark.parametrize(
    ("recalls", "passes", "stopped_early"),
    [
        ([0.10, 0.20, 0.30, 0.40, 0.50], 5, False),
        ([0.10, 0.20, 0.2005], 3, True),
        ([0.10, 0.20, 0.30, 0.3001], 4, True),
        ([0.10, 0.20, 0.30, 0.40, 0.4001], 5, False),  # the rule fires on the last pass allowed
    ],
)
def test_training_runs_three_to_five_passes_and_stops_on_the_probe(
    recalls: list[float], passes: int, stopped_early: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted(monkeypatch, recalls)
    epochs: list[int] = []
    model = SASRecModel(config=_config(), cold_start_threshold=None).fit(
        _train(120), on_epoch=lambda epoch, _loss: epochs.append(epoch), retrieval_backend="torch"
    )
    report = model.training_report
    assert report is not None

    assert epochs == list(range(1, passes + 1))
    assert report.epochs_completed == passes
    assert report.probe_recalls == recalls[:passes]
    assert report.stopped_early is stopped_early


def test_without_early_stopping_there_is_no_probe_and_every_pass_runs() -> None:
    model = SASRecModel(config=_config(early_stopping=False, epochs=2), cold_start_threshold=None)
    model.fit(_train(120), retrieval_backend="torch")
    report = model.training_report
    assert report is not None
    assert (report.epochs_completed, report.probe_users, report.probe_recalls) == (2, 0, [])


def test_the_probe_scores_through_the_shared_recall_and_leaves_history_out() -> None:
    model = SASRecModel(config=_config(early_stopping=False, epochs=1), cold_start_threshold=None)
    train = _train(120)
    model.fit(train, retrieval_backend="torch")
    user = int(train["userId"].iloc[0])
    history = tuple(int(m) for m in train[train["userId"] == user]["movieId"])
    unseen = next(int(m) for m in train["movieId"].unique() if int(m) not in history)
    seen_probe = StoppingProbe(
        user_ids=(user,), histories=(history,), targets=(history[0],), timestamps=(0,)
    )
    unseen_probe = StoppingProbe(
        user_ids=(user,), histories=(history,), targets=(unseen,), timestamps=(0,)
    )

    # A movie in the user's own history is excluded, so it can never be found;
    # with fewer than 500 movies every other movie is retrieved.
    assert model._probe_recall(seen_probe) == 0.0
    assert model._probe_recall(unseen_probe) == 1.0


# --- isolation from the holdout ----------------------------------------------------


def test_the_stopping_decision_is_taken_before_the_holdout_callback_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    original = SASRecModel._probe_recall

    def probe_first(self: SASRecModel, probe: StoppingProbe) -> float:
        order.append("probe")
        return original(self, probe)

    monkeypatch.setattr(SASRecModel, "_probe_recall", probe_first)
    model = SASRecModel(config=_config(epochs=3), cold_start_threshold=None).fit(
        _train(120), on_epoch=lambda _e, _l: order.append("holdout"), retrieval_backend="torch"
    )
    assert model.training_report is not None
    assert order == ["probe", "holdout"] * model.training_report.epochs_completed


def test_two_different_holdouts_cannot_change_when_or_how_training_stops() -> None:
    """Same train, two unrelated 'holdouts' read by the epoch callback, one of which
    shouts to stop: the stop pass, the probe recalls and the weights are identical."""
    train = _train(200)
    outcomes = []
    for holdout_seed, verdict in ((1, None), (2, "stop")):
        holdout = _train(50, seed=holdout_seed)

        def on_epoch(_epoch: int, _loss: float, frame: pd.DataFrame = holdout) -> Any:
            frame.groupby("userId").size()  # read it
            return verdict

        model = SASRecModel(config=_config(), cold_start_threshold=None).fit(
            train, on_epoch=on_epoch, retrieval_backend="torch"
        )
        report = model.training_report
        assert report is not None and model._encoder is not None
        outcomes.append(
            (
                report.epochs_completed,
                report.probe_recalls,
                encoder_weights_sha256(model._encoder.state_dict()),
            )
        )
    assert outcomes[0] == outcomes[1]


def test_the_probe_is_carved_from_the_fit_frame_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[pd.DataFrame] = []
    original = sasrec.carve_stopping_probe

    def spy(frame: pd.DataFrame, **kwargs: Any) -> tuple[pd.DataFrame, StoppingProbe]:
        seen.append(frame)
        return original(frame, **kwargs)

    monkeypatch.setattr(sasrec, "carve_stopping_probe", spy)
    train = _train(120)
    SASRecModel(config=_config(epochs=3), cold_start_threshold=None).fit(
        train, retrieval_backend="torch"
    )
    assert len(seen) == 1 and seen[0] is train


def test_a_run_carves_its_probe_strictly_before_the_holdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """End to end through ``run_once``: the frame handed to ``fit`` stops before the
    cutoff, and no probe row is a holdout row."""
    ratings = pd.DataFrame(
        [
            (user, 2000 + (user * 5 + step) % 70, 4.0, step * 3600 + user)
            for user in range(1, 61)
            for step in range(15)
        ],
        columns=["userId", "movieId", "rating", "timestamp"],
    )
    fitted: list[pd.DataFrame] = []
    probes: list[StoppingProbe] = []
    original_fit = SASRecModel.fit
    original_carve = sasrec.carve_stopping_probe

    def fit_spy(self: SASRecModel, train: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        fitted.append(train)
        return original_fit(self, train, *args, **kwargs)

    def carve_spy(frame: pd.DataFrame, **kwargs: Any) -> tuple[pd.DataFrame, StoppingProbe]:
        remaining, probe = original_carve(frame, **kwargs)
        probes.append(probe)
        return remaining, probe

    monkeypatch.setattr(SASRecModel, "fit", fit_spy)
    monkeypatch.setattr(sasrec, "carve_stopping_probe", carve_spy)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    previous = sasrec_training.mlflow.get_tracking_uri()
    try:
        sasrec_training.mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        sasrec_training.run_once(
            ratings,
            _config(epochs=3, early_stopping_probe_fraction=0.1),
            sample_fraction=0.5,
            artifact_root=tmp_path / "artifacts",
        )
    finally:
        sasrec_training.mlflow.set_tracking_uri(previous)

    split = temporal_split(ratings)
    holdout_rows = set(
        split.holdout[["userId", "movieId", "timestamp"]].itertuples(index=False, name=None)
    )
    assert len(fitted) == 1 and len(probes) == 1
    assert fitted[0]["timestamp"].max() < split.cutoff
    probe = probes[0]
    assert len(probe) >= 1
    for user, target, timestamp in zip(probe.user_ids, probe.targets, probe.timestamps):
        assert timestamp < split.cutoff
        assert (user, target, timestamp) not in holdout_rows
