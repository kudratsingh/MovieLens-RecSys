"""WO-3: the three repairs of the all-positions trainer, each tested on its mechanism.

The all-positions trainer (PR #183) is 9.8x faster than the trainer of record and
scores lower. WO-3 tests three causes one at a time, each behind a setting that
defaults off:

* **short history** -- ``window_stride``: windows overlap, and a window scores
  only targets with at least ``max_sequence_length - window_stride`` movies
  behind them (or all the user has);
* **narrow batches** -- ``windows_per_step``: a step's targets come from about
  that many windows instead of a dozen whole ones;
* **too few passes** -- ``epochs``, which already existed.

With every setting off the recorded loop must be unchanged, and the objective
of record (``strict-prefix-final-position-v1``) must not move at all; both are
pinned here against a copy of the loop as it was, and, when the data is
present, against the first real pilot batches the code before WO-3 produced.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import torch

import src.models.candidates.sasrec as sasrec_module
import src.training.sasrec_sweep as sweep_module
from src.models.candidates.sasrec import (
    ALL_POSITION_TRAINING_OBJECTIVE,
    ALL_POSITION_TRAINING_OBJECTIVES,
    LEGACY_TRAINING_OBJECTIVE,
    OVERLAP_TRAINING_OBJECTIVE,
    OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE,
    WIDE_BATCH_TRAINING_OBJECTIVE,
    SASRecConfig,
    SASRecEncoder,
    SASRecModel,
    _wide_batches,
    _window_batches,
    all_position_objective_for,
    gbce_beta,
    sample_position_negatives,
    sampled_gbce_loss,
)
from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, export_sasrec, load_sasrec
from src.models.candidates.sequence_data import (
    AllPositionTrainingData,
    build_all_position_training_data,
    build_overlapping_all_position_training_data,
    build_strict_prefix_examples,
)
from src.training.sasrec import _configuration_id
from src.training.sasrec_sweep import parse_grid

EXPERIMENTS = Path("docs/experiments/sasrec")
PILOT_PROTOCOL = "sha256:faf2828d08a0b0ecf23993fcfaf037134359017e20c7601b53da7e2ebecc22bc"


def _config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 6,
        "hidden_dim": 8,
        "num_blocks": 2,
        "num_heads": 2,
        "feedforward_dim": 16,
        "dropout": 0.2,
        "negative_count": 3,
        "loss": "bce",
        "batch_size": 16,
        "epochs": 2,
        "faiss_exact": True,
        "seed": 42,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _objective_config(objective: str, **changes: object) -> SASRecConfig:
    """``_config`` with the WO-3 settings the named objective requires."""
    settings = {
        ALL_POSITION_TRAINING_OBJECTIVE: {},
        OVERLAP_TRAINING_OBJECTIVE: {"window_stride": 3},
        WIDE_BATCH_TRAINING_OBJECTIVE: {"windows_per_step": 8},
        OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE: {"window_stride": 3, "windows_per_step": 8},
    }[objective]
    return _config(training_objective=objective, **{**settings, **changes})


def _train(seed: int = 5) -> pd.DataFrame:
    """The WO-1 test frame: 30 users, histories of 2-24, many equal timestamps."""
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, 31):
        length = int(rng.integers(2, 25))
        watched = rng.choice(np.arange(100, 160), size=length, replace=False)
        clock = np.sort(rng.integers(0, max(2, length - 3), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    return frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def _histories_with_ties(seed: int = 3) -> tuple[pd.DataFrame, dict[int, int]]:
    """Short and long histories, equal-timestamp groups, and one oversized group.

    User 99 watches 5 movies at one timestamp, then 25 at the next (longer than
    any window used below), then 10 one at a time: the shapes a window boundary
    can fall inside.
    """
    rng = np.random.default_rng(seed)
    movies = np.arange(1_000, 1_400)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, 21):
        length = int(rng.choice([1, 2, 5, 9, 23, 60, 130]))
        watched = rng.choice(movies, size=length, replace=False)
        clock = np.sort(rng.integers(0, max(2, length * 2 // 3), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    burst = rng.choice(movies, size=40, replace=False)
    rows.extend(
        (99, int(item), 0 if index < 5 else 1 if index < 30 else index)
        for index, item in enumerate(burst)
    )
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    frame = frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return frame, {int(movie): index + 1 for index, movie in enumerate(movies)}


def _scored(data: AllPositionTrainingData) -> list[tuple[list[int], int, int]]:
    """Every scored target as ``(visible prefix, target, movies behind its position)``."""
    scored: list[tuple[list[int], int, int]] = []
    for window in range(len(data.sequences)):
        row = [int(item) for item in data.sequences[window]]
        padding = row.index(next(item for item in row if item)) if any(row) else len(row)
        start = int(data.prediction_offsets[window])
        end = int(data.prediction_offsets[window + 1])
        for prediction in range(start, end):
            position = int(data.prediction_positions[prediction])
            scored.append(
                (row[padding : position + 1], int(data.positives[prediction]), position - padding)
            )
    return scored


def _full_prefixes(frame: pd.DataFrame, vocabulary: dict[int, int]) -> list[tuple[list[int], int]]:
    """The copied-prefix objective's examples with nothing truncated."""
    longest = int(frame.groupby("userId").size().max())
    histories, targets = build_strict_prefix_examples(
        frame, item_to_index=vocabulary, max_length=longest
    )
    return [
        ([int(item) for item in history if item], int(target))
        for history, target in zip(histories, targets)
    ]


# --- short history: overlapping windows ----------------------------------------


@pytest.mark.parametrize(("max_length", "stride"), [(8, 4), (8, 3), (8, 1), (8, 7), (50, 25)])
def test_overlap_scores_every_target_once_with_the_context_it_promises(
    max_length: int, stride: int
) -> None:
    frame, vocabulary = _histories_with_ties()
    expected = _full_prefixes(frame, vocabulary)

    data = build_overlapping_all_position_training_data(
        frame, item_to_index=vocabulary, max_length=max_length, window_stride=stride
    )
    scored = _scored(data)

    # The same targets as the copied-prefix objective, in its order, each once.
    assert [target for _prefix, target, _behind in scored] == [t for _p, t in expected]
    omitted = []
    for (visible, _target, behind), (full, _t) in zip(scored, expected):
        # What a scored position sees is the latest part of its strict prefix:
        # nothing at or after the target's timestamp, at most one window long.
        assert 0 < len(visible) <= max_length
        assert visible == full[-len(visible) :]
        assert behind == len(visible) - 1
        # The mask: at least max_length - stride movies behind every scored
        # position, unless the user has fewer than that before it.
        assert behind >= min(len(full) - 1, max_length - stride)
        omitted.append(len(full) - len(visible))
    assert data.stats.n_targets == len(expected)
    assert data.stats.n_truncated_sequences == sum(1 for count in omitted if count)
    assert data.stats.n_truncated_interactions == sum(omitted)
    assert data.sequences.shape == (data.stats.n_sequences, max_length)


def test_back_to_back_windows_leave_long_histories_one_movie_of_context() -> None:
    """The cause WO-3 tests first: the recorded builder restarts context at every window."""
    frame, vocabulary = _histories_with_ties()
    expected = _full_prefixes(frame, vocabulary)
    data = build_all_position_training_data(frame, item_to_index=vocabulary, max_length=8)

    starved = [
        (len(visible), len(full))
        for (visible, _target, _behind), (full, _t) in zip(_scored(data), expected)
        if len(full) >= 8 and len(visible) == 1
    ]
    assert starved, "back-to-back windows should leave some long-history targets one movie"
    overlap = build_overlapping_all_position_training_data(
        frame, item_to_index=vocabulary, max_length=8, window_stride=4
    )
    assert (
        min(
            len(visible)
            for (visible, _target, _behind), (full, _t) in zip(_scored(overlap), expected)
            if len(full) >= 8
        )
        >= 5
    )


def test_overlap_on_one_long_history_slides_by_the_stride() -> None:
    """L = 50, stride 25: windows of the latest 50 movies, ending every 25."""
    frame = pd.DataFrame(
        [(1, item, item) for item in range(1, 121)], columns=["userId", "movieId", "timestamp"]
    )
    identity = {item: item for item in range(1, 121)}

    data = build_overlapping_all_position_training_data(
        frame, item_to_index=identity, max_length=50, window_stride=25
    )

    first_items = [int(row[row.nonzero()[0, 0]]) for row in data.sequences]
    last_items = [int(row[-1]) for row in data.sequences]
    assert first_items == [1, 1, 26, 51, 70]
    assert last_items == [25, 50, 75, 100, 119]
    assert data.prediction_offsets.tolist() == [0, 25, 50, 75, 100, 119]
    assert data.positives.tolist() == list(range(2, 121))
    positions = data.prediction_positions.tolist()
    # The first window is padded by 25 and scores everything: the user has no more.
    assert positions[:25] == list(range(25, 50))
    # Every later window scores only positions with 25 or more movies behind them.
    assert positions[25:100] == list(range(25, 50)) * 3
    assert positions[100:] == list(range(31, 50))

    recorded = build_all_position_training_data(frame, item_to_index=identity, max_length=50)
    assert recorded.prediction_positions.tolist()[49:52] == [49, 0, 1]


def test_overlap_rejects_a_stride_that_does_not_overlap() -> None:
    frame, vocabulary = _histories_with_ties()
    for stride in (0, 8, 9):
        with pytest.raises(ValueError, match="window_stride"):
            build_overlapping_all_position_training_data(
                frame, item_to_index=vocabulary, max_length=8, window_stride=stride
            )
    empty = build_overlapping_all_position_training_data(
        pd.DataFrame(columns=["userId", "movieId", "timestamp"]),
        item_to_index={},
        max_length=8,
        window_stride=4,
    )
    assert empty.stats.n_targets == 0 and empty.sequences.shape == (0, 8)


# --- narrow batches: the wide sampler ---------------------------------------------


def _long_singletons(
    users: int = 40, length: int = 41, max_length: int = 10
) -> AllPositionTrainingData:
    frame = pd.DataFrame(
        [(user, user * 1_000 + item, item) for user in range(users) for item in range(length)],
        columns=["userId", "movieId", "timestamp"],
    )
    vocabulary = {int(movie): index + 1 for index, movie in enumerate(sorted(frame["movieId"]))}
    return build_all_position_training_data(frame, item_to_index=vocabulary, max_length=max_length)


def _window_of_target(data: AllPositionTrainingData) -> torch.Tensor:
    counts = data.prediction_offsets[1:] - data.prediction_offsets[:-1]
    return torch.repeat_interleave(torch.arange(len(counts)), counts)


@pytest.mark.parametrize(("windows_per_step", "at_least"), [(16, 13), (32, 26), (5, 3)])
def test_wide_batches_score_every_target_once_from_many_windows(
    windows_per_step: int, at_least: int
) -> None:
    data = _long_singletons(users=200)
    n_targets = int(data.prediction_offsets[-1])
    per_visit = -(-32 // windows_per_step)
    window_of = _window_of_target(data)

    torch.manual_seed(0)
    steps = list(_wide_batches(data, batch_size=32, windows_per_step=windows_per_step))

    every = torch.cat([targets for _windows, targets, _local in steps])
    assert sorted(every.tolist()) == list(range(n_targets))
    for windows, targets, local in steps:
        assert len(targets) <= 32
        # Each row is one visit to a window, holding at most ``per_visit`` of its
        # targets, and every target is read from the window it belongs to.
        assert torch.equal(windows[local], window_of[targets])
        assert int(torch.bincount(local).max()) <= per_visit
    for windows, targets, _local in steps[:-1]:
        # Greedy packing fills a step to within one visit of the batch size.
        assert len(targets) > 32 - per_visit
        assert len(windows) >= -(-(32 - per_visit + 1) // per_visit)

    # The recorded packing reads three whole windows of ten targets per step;
    # the wide sampler reads about ``windows_per_step`` distinct windows.
    torch.manual_seed(0)
    packed = list(_window_batches(data, torch.randperm(len(data.sequences)), max_predictions=32))
    assert np.mean([len(rows) for rows in packed]) == pytest.approx(3, abs=0.05)
    distinct = [len(set(windows.tolist())) for windows, _targets, _local in steps]
    assert np.mean(distinct) >= at_least


def test_wide_batches_line_up_with_the_flat_target_arrays() -> None:
    data = _long_singletons(users=7, length=23, max_length=6)
    window_of = _window_of_target(data)
    torch.manual_seed(3)
    for windows, targets, local in _wide_batches(data, batch_size=12, windows_per_step=6):
        sequences, rows, positions, positives = data.batch_visits(windows, targets, local)
        assert torch.equal(sequences[rows], data.sequences[window_of[targets]])
        assert torch.equal(positions, data.prediction_positions[targets].long())
        assert torch.equal(positives, data.positives[targets].long())
        # The target is the movie after the one at its position, in its own window.
        for row, position, positive in zip(rows, positions, positives):
            assert int(sequences[row, position]) + 1 == int(positive)


def test_wide_batches_are_drawn_from_the_seeded_generator() -> None:
    data = _long_singletons(users=12)

    def draw(seed: int) -> list[list[int]]:
        torch.manual_seed(seed)
        return [
            targets.tolist()
            for _w, targets, _l in _wide_batches(data, batch_size=32, windows_per_step=16)
        ]

    assert draw(1) == draw(1)
    assert draw(1) != draw(2)


# --- the repairs inside fit, and the pass count ------------------------------------


@pytest.mark.parametrize("objective", ALL_POSITION_TRAINING_OBJECTIVES)
def test_each_repair_trains_every_target_once_per_pass(objective: str) -> None:
    config = _objective_config(objective, epochs=3)
    train = _train()
    epochs_seen: list[int] = []

    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        train, on_epoch=lambda epoch, _loss: epochs_seen.append(epoch), retrieval_backend="torch"
    )

    assert epochs_seen == [1, 2, 3]
    assert model._training_objective == objective
    stats = model._all_position_batch_stats
    assert stats is not None and model._training_stats is not None
    n_examples = len(_full_prefixes(train, model._item_to_index))
    assert model._training_stats.n_targets == n_examples
    assert stats["targets_per_step"] * stats["steps_per_epoch"] == pytest.approx(n_examples)
    assert stats["targets_per_step"] <= config.batch_size


def test_wide_steps_read_more_windows_and_overlap_encodes_more_of_them() -> None:
    train = _train()

    def fitted(objective: str) -> SASRecModel:
        return SASRecModel(
            config=_objective_config(objective, epochs=1), cold_start_threshold=None
        ).fit(train, retrieval_backend="torch")

    recorded, overlap, wide = (
        fitted(objective)
        for objective in (
            ALL_POSITION_TRAINING_OBJECTIVE,
            OVERLAP_TRAINING_OBJECTIVE,
            WIDE_BATCH_TRAINING_OBJECTIVE,
        )
    )
    assert recorded._training_stats and overlap._training_stats
    assert overlap._training_stats.n_sequences > recorded._training_stats.n_sequences
    assert recorded._all_position_batch_stats and wide._all_position_batch_stats
    assert (
        wide._all_position_batch_stats["encoded_windows_per_step"]
        > 1.5 * recorded._all_position_batch_stats["encoded_windows_per_step"]
    )


@pytest.mark.parametrize("objective", ALL_POSITION_TRAINING_OBJECTIVES)
def test_the_kth_pass_of_a_longer_run_is_the_k_pass_run(objective: str) -> None:
    """Why the 8-pass pilot also reports 4 passes: nothing in pass k depends on the total.

    ``on_epoch`` does what ``run_once``'s does (index rebuild and retrieval in
    eval mode), so this also shows the per-pass evaluation draws nothing from
    the generator the next pass trains with.
    """
    train = _train()
    users = sorted(int(user) for user in train["userId"].unique())

    def run(epochs: int) -> tuple[dict[int, dict[str, torch.Tensor]], list[float]]:
        model = SASRecModel(config=_objective_config(objective, epochs=epochs))
        snapshots: dict[int, dict[str, torch.Tensor]] = {}
        losses: list[float] = []

        def on_epoch(epoch: int, loss: float) -> None:
            losses.append(loss)
            model.build_exact_tensor_index()
            model.recommend_for_users(users, 20)
            assert model._encoder is not None
            snapshots[epoch] = {
                name: value.clone() for name, value in model._encoder.state_dict().items()
            }

        model.fit(train, on_epoch=on_epoch, retrieval_backend="torch")
        return snapshots, losses

    short, short_losses = run(2)
    long, long_losses = run(4)

    assert sorted(long) == [1, 2, 3, 4]
    assert long_losses[:2] == short_losses
    for name, value in short[2].items():
        assert torch.equal(long[2][name], value), name
    assert any(not torch.equal(long[4][name], value) for name, value in short[2].items())


# --- with every setting off, nothing moved -------------------------------------------


def _all_positions_of_record(
    train: pd.DataFrame, config: SASRecConfig
) -> tuple[dict[str, torch.Tensor], list[float]]:
    """``SASRecModel.fit`` before WO-3 (PR #183's loop), statement for statement.

    The objective-of-record counterpart is ``_trainer_of_record`` in
    ``test_sasrec_trainer_of_record.py``. Index build omitted.
    """
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    items = sorted(int(item) for item in train["movieId"].unique())
    item_to_index = {item: index + 1 for index, item in enumerate(items)}
    training_data = build_all_position_training_data(
        train, item_to_index=item_to_index, max_length=config.max_sequence_length
    )
    encoder = SASRecEncoder(len(items) + 2, config)
    optimizer = torch.optim.Adam(encoder.parameters(), lr=config.learning_rate)
    rng = np.random.default_rng(config.seed)
    beta = (
        1.0
        if config.loss == "bce"
        else gbce_beta(
            negative_count=config.negative_count,
            catalog_size=len(items),
            calibration_t=config.calibration_t,
        )
    )
    epoch_losses: list[float] = []
    for _epoch in range(config.epochs):
        encoder.train()
        permutation = torch.randperm(len(training_data.sequences))
        epoch_loss = 0.0
        predictions_seen = 0
        for rows in _window_batches(training_data, permutation, max_predictions=config.batch_size):
            sequences, prediction_windows, prediction_positions, positive_batch = (
                training_data.batch(rows)
            )
            negative_batch = sample_position_negatives(
                sequences,
                prediction_windows,
                prediction_positions,
                positive_batch,
                n_items=len(items),
                count=config.negative_count,
                rng=rng,
            )
            encoded_positions = encoder.encode_positions(sequences.long())
            user_vectors = encoded_positions[prediction_windows, prediction_positions]
            positive_logits = (
                user_vectors * encoder.item_vectors(positive_batch, normalize=False)
            ).sum(dim=1)
            negative_logits = torch.einsum(
                "bd,bkd->bk",
                user_vectors,
                encoder.item_vectors(negative_batch, normalize=False),
            )
            loss = sampled_gbce_loss(positive_logits, negative_logits, beta=beta)
            optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            with torch.no_grad():
                encoder.item_embedding.weight[0].zero_()
            batch_predictions = len(positive_batch)
            epoch_loss += float(loss.item()) * batch_predictions
            predictions_seen += batch_predictions
        epoch_losses.append(epoch_loss / max(1, predictions_seen))
    return encoder.state_dict(), epoch_losses


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"loss": "gbce", "calibration_t": 0.5, "seed": 7},
        {"max_sequence_length": 50, "seed": 13},
    ],
)
def test_the_recorded_all_positions_loop_is_unchanged_bit_for_bit(
    changes: dict[str, object],
) -> None:
    config = _config(training_objective=ALL_POSITION_TRAINING_OBJECTIVE, **changes)
    train = _train()
    expected_state, expected_losses = _all_positions_of_record(train, config)

    losses: list[float] = []
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        train, on_epoch=lambda _epoch, loss: losses.append(loss), retrieval_backend="torch"
    )

    assert model._encoder is not None
    state = model._encoder.state_dict()
    for name, value in expected_state.items():
        assert torch.equal(state[name], value), name
    assert losses == expected_losses


# Configuration ids of the committed cells as the code before WO-3 computed them
# (c07b4e0). A new field must not move an id already recorded in MLflow.
RECORDED_CONFIGURATION_IDS = {
    "full.json": [
        "sasrec-sha256:c57774687d8151b9e605084a6eacd3eb5bc4fb32be1af00f9abd08b2af568efa",
    ],
    "pilot-6pct.json": [
        "sasrec-sha256:0ddbfffec0457d2f6b7f2b5e39ab79aa8e32e1e233076cef6af199d2d3d3c21d",
        "sasrec-sha256:fecbdb788646de90b7be61f1abb11f660f4ed77d033206a489f9bcc054dda5a7",
    ],
    "all-positions-pilot-6pct.json": [
        "sasrec-sha256:15ae1c7876b231ac6701f415fce0b3752b5459be08b586f0f05b46496a9b2db2"
    ],
    "all-positions-full.json": [
        "sasrec-sha256:bd14a76386477e077ab2dd632672544945de77d6830a8c4bb09d7f12dff0c6d9"
    ],
    "wo1-restored-loop-6pct-s42.json": [
        "sasrec-sha256:0ddbfffec0457d2f6b7f2b5e39ab79aa8e32e1e233076cef6af199d2d3d3c21d"
    ],
}


@pytest.mark.parametrize("name", sorted(RECORDED_CONFIGURATION_IDS))
def test_recorded_cells_keep_their_configuration_ids(name: str) -> None:
    fraction, cells = parse_grid(json.loads((EXPERIMENTS / name).read_text()))
    ids = [_configuration_id(config, sample_fraction=fraction) for _label, config in cells]
    assert ids == RECORDED_CONFIGURATION_IDS[name]


def test_each_wo3_setting_enters_the_configuration_id() -> None:
    recorded = _configuration_id(_config(training_objective=ALL_POSITION_TRAINING_OBJECTIVE))
    overlap = _configuration_id(_objective_config(OVERLAP_TRAINING_OBJECTIVE))
    wider_overlap = _configuration_id(
        _objective_config(OVERLAP_TRAINING_OBJECTIVE, window_stride=2)
    )
    wide = _configuration_id(_objective_config(WIDE_BATCH_TRAINING_OBJECTIVE))
    assert len({recorded, overlap, wider_overlap, wide}) == 4


# Data-gated: the first three steps of the seed-42 pilot cell on O-25's 6%
# partition, as the code before WO-3 (c07b4e0) took them on this machine (an M3
# Pro, OMP_NUM_THREADS=1; the losses are float32 and need not match elsewhere).
# Set SASREC_WO3_UNCHANGED_CHECK=1 and TWOTOWER_INPUT_DIR to run it. The example
# digest is the one WO-1's record gives for the 1,186,847 x 50 copied-prefix
# table: SHA-256 over the int32 histories, then the int32 targets.
REAL_PILOT_PINS: dict[str, Any] = {
    "n_examples": 1_186_847,
    "examples_sha256": "62cc7e7af8ba3a48677c5bab417bb403cc46624e706ec87e301426568af4ef78",
    "strict_prefix": {
        "batches_sha256": "fd748a8518c56fdad057ca4b05d54baf3c1d4a2325f66123b5081a0307380470",
        "step_losses": [0.799338698387146, 0.7917272448539734, 0.7706018686294556],
    },
    "all_positions": {
        "batches_sha256": "34406263cd5b142040bea947e1a0980c981b70844216dd194e572962cd1a1924",
        "step_losses": [0.8027490377426147, 0.8081477284431458, 0.8050366640090942],
    },
}


class _StopTrainingError(Exception):
    pass


def _first_steps(
    config: SASRecConfig, train: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, n: int = 3
) -> dict[str, Any]:
    """Digest the first ``n`` steps' inputs and negatives, and their losses."""
    digest = hashlib.sha256()
    losses: list[float] = []
    calls = 0

    def record(*tensors: torch.Tensor) -> None:
        for tensor in tensors:
            array = np.ascontiguousarray(tensor.numpy())
            digest.update(str(array.dtype).encode())
            digest.update(repr(array.shape).encode())
            digest.update(array.tobytes())

    original_strict = sasrec_module.sample_negatives
    original_position = sasrec_module.sample_position_negatives
    original_loss = sasrec_module.sampled_gbce_loss

    def strict(histories: torch.Tensor, positives: torch.Tensor, **kwargs: Any) -> torch.Tensor:
        nonlocal calls
        if calls == n:
            raise _StopTrainingError
        calls += 1
        out = original_strict(histories, positives, **kwargs)
        record(histories, positives, out)
        return out

    def position(*arrays: torch.Tensor, **kwargs: Any) -> torch.Tensor:
        nonlocal calls
        if calls == n:
            raise _StopTrainingError
        calls += 1
        out = original_position(*arrays, **kwargs)
        record(*arrays, out)
        return out

    def loss(positive: torch.Tensor, negative: torch.Tensor, *, beta: float) -> torch.Tensor:
        value = original_loss(positive, negative, beta=beta)
        losses.append(float(value.item()))
        return value

    with monkeypatch.context() as patch:
        patch.setattr(sasrec_module, "sample_negatives", strict)
        patch.setattr(sasrec_module, "sample_position_negatives", position)
        patch.setattr(sasrec_module, "sampled_gbce_loss", loss)
        with pytest.raises(_StopTrainingError):
            SASRecModel(config=config, cold_start_threshold=None).fit(
                train, retrieval_backend="torch"
            )
    return {"batches_sha256": digest.hexdigest(), "step_losses": losses}


def test_wo1_and_pr183_first_real_pilot_steps_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = os.environ.get("TWOTOWER_INPUT_DIR", "").strip()
    if not input_dir or os.environ.get("SASREC_WO3_UNCHANGED_CHECK", "").strip() != "1":
        pytest.skip("SASREC_WO3_UNCHANGED_CHECK=1 and TWOTOWER_INPUT_DIR are not both set")

    from src.config import Settings
    from src.data.split import temporal_cutoff, temporal_split
    from src.models.candidates.sequence_data import build_strict_prefix_example_store
    from src.training.candidate_data import load_inputs, subsample_users
    from src.training.sasrec import SUBSAMPLE_SEED

    full, _movies = load_inputs(Settings(), input_dir=Path(input_dir))
    split = temporal_split(
        subsample_users(full, 0.06, SUBSAMPLE_SEED), cutoff=temporal_cutoff(full)
    )
    assert split.holdout_end == 1469256597
    train = split.train
    items = sorted(int(item) for item in train["movieId"].unique())
    store = build_strict_prefix_example_store(
        train, item_to_index={item: index + 1 for index, item in enumerate(items)}, max_length=50
    )
    histories, positives = store.batch(torch.arange(len(store)))
    examples = hashlib.sha256(np.ascontiguousarray(histories.numpy().astype(np.int32)).tobytes())
    examples.update(np.ascontiguousarray(positives.numpy().astype(np.int32)).tobytes())
    assert len(store) == REAL_PILOT_PINS["n_examples"]
    assert examples.hexdigest() == REAL_PILOT_PINS["examples_sha256"]
    del histories, positives

    cell = {"loss": "bce", "negative_count": 32, "epochs": 2, "faiss_exact": True, "seed": 42}
    assert _first_steps(SASRecConfig(**cell), train, monkeypatch) == (  # type: ignore[arg-type]
        REAL_PILOT_PINS["strict_prefix"]
    )
    all_positions = SASRecConfig(
        **cell, training_objective=ALL_POSITION_TRAINING_OBJECTIVE  # type: ignore[arg-type]
    )
    assert _first_steps(all_positions, train, monkeypatch) == REAL_PILOT_PINS["all_positions"]


# --- configuration -------------------------------------------------------------------


def test_the_settings_default_off_and_are_read_from_the_environment() -> None:
    config = SASRecConfig()
    assert (config.window_stride, config.windows_per_step) == (0, 0)
    assert config.training_objective == LEGACY_TRAINING_OBJECTIVE
    from_env = SASRecConfig.from_env(
        {
            "SASREC_TRAINING_OBJECTIVE": OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE,
            "SASREC_WINDOW_STRIDE": "25",
            "SASREC_WINDOWS_PER_STEP": "512",
        }
    )
    assert (from_env.window_stride, from_env.windows_per_step) == (25, 512)
    from_env.validate()
    assert from_env.min_window_context == 25
    assert _config(training_objective=ALL_POSITION_TRAINING_OBJECTIVE).min_window_context == 0


def test_each_combination_of_settings_has_exactly_one_name() -> None:
    names = {
        all_position_objective_for(window_stride=stride, windows_per_step=width)
        for stride in (0, 25)
        for width in (0, 512)
    }
    assert names == set(ALL_POSITION_TRAINING_OBJECTIVES)
    assert all_position_objective_for(window_stride=0, windows_per_step=0) == (
        ALL_POSITION_TRAINING_OBJECTIVE
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        # A repaired model must never carry the ablation's name, nor the reverse.
        ({"training_objective": ALL_POSITION_TRAINING_OBJECTIVE, "window_stride": 3}, "match"),
        ({"training_objective": ALL_POSITION_TRAINING_OBJECTIVE, "windows_per_step": 4}, "match"),
        ({"training_objective": OVERLAP_TRAINING_OBJECTIVE}, "match"),
        ({"training_objective": WIDE_BATCH_TRAINING_OBJECTIVE, "window_stride": 3}, "match"),
        # The objective of record trains one window per example; the settings
        # would be silently ignored there, so they are refused instead.
        ({"window_stride": 3}, "all-positions objectives only"),
        ({"windows_per_step": 4}, "all-positions objectives only"),
        (
            {"training_objective": OVERLAP_TRAINING_OBJECTIVE, "window_stride": 6},
            "below max_sequence_length",
        ),
        ({"training_objective": OVERLAP_TRAINING_OBJECTIVE, "window_stride": -1}, "0 \\(off\\)"),
        (
            {"training_objective": WIDE_BATCH_TRAINING_OBJECTIVE, "windows_per_step": 17},
            "cannot exceed batch_size",
        ),
    ],
)
def test_inconsistent_settings_are_refused_before_any_data_loads(
    changes: dict[str, object], message: str
) -> None:
    config = _config(**changes)
    with pytest.raises(ValueError, match=message):
        config.validate()
    with pytest.raises(ValueError, match="cell 0 is invalid"):
        parse_grid({"cells": [{"label": "bad", **config.as_params()}]})


def test_a_repaired_model_exports_and_reloads_under_its_own_name(tmp_path: Path) -> None:
    model = SASRecModel(
        config=_objective_config(OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE, epochs=1),
        cold_start_threshold=None,
    ).fit(_train(), retrieval_backend="faiss")
    manifest = export_sasrec(model, tmp_path / "run")

    loaded = load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

    assert manifest.training_objective == OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE
    assert loaded._training_objective == OVERLAP_WIDE_BATCH_TRAINING_OBJECTIVE
    assert (loaded.config.window_stride, loaded.config.windows_per_step) == (3, 8)
    history = sorted(model._item_to_index)[:5]
    assert loaded.recommend_from_history(history, 10) == model.recommend_from_history(history, 10)


# --- the committed cells files -------------------------------------------------------


def _cells(name: str) -> tuple[dict[str, Any], float, list[tuple[str, SASRecConfig]]]:
    spec = json.loads((EXPERIMENTS / name).read_text())
    fraction, cells = parse_grid(spec)
    return spec, fraction, cells


def test_the_clean_baseline_pilot_is_the_recorded_ablation_cell() -> None:
    _spec, _fraction, recorded = _cells("all-positions-pilot-6pct.json")
    spec, fraction, cells = _cells("wo3-allpos-baseline-6pct-s42.json")

    assert fraction == 0.06
    assert spec["expected_protocol_hash"] == PILOT_PROTOCOL
    assert [config for _label, config in cells] == [recorded[0][1]]
    assert "hold" not in spec


@pytest.mark.parametrize(
    ("name", "changed"),
    [
        (
            "wo3-overlap-6pct-s42.json",
            {"window_stride": 25, "training_objective": OVERLAP_TRAINING_OBJECTIVE},
        ),
        (
            "wo3-wide-6pct-s42.json",
            {"windows_per_step": 512, "training_objective": WIDE_BATCH_TRAINING_OBJECTIVE},
        ),
        ("wo3-passes-6pct-s42.json", {"epochs": 8}),
    ],
)
def test_each_single_cause_pilot_changes_one_thing_from_the_baseline(
    name: str, changed: dict[str, object]
) -> None:
    _base_spec, _base_fraction, baseline = _cells("wo3-allpos-baseline-6pct-s42.json")
    spec, fraction, cells = _cells(name)

    assert fraction == 0.06
    assert spec["expected_protocol_hash"] == PILOT_PROTOCOL
    assert "hold" not in spec
    assert len(cells) == 1
    assert cells[0][1] == dataclasses.replace(baseline[0][1], **changed)  # type: ignore[arg-type]


def test_the_cell_0b_pilot_is_wo4s_strict_prefix_cell_on_the_all_positions_trainer() -> None:
    """Revised plan (b): the pass check made at the WO-5 loss, objective the only change."""
    _wo4_spec, wo4_fraction, wo4 = _cells("wo4-cell0b-pair-cpu-6pct.json")
    spec, fraction, cells = _cells("wo3-allpos-cell0b-6pct-s42.json")

    assert fraction == wo4_fraction == 0.06
    assert spec["expected_protocol_hash"] == PILOT_PROTOCOL
    assert "hold" not in spec and spec["projection"]["kill_wall_minutes"] > 0
    assert wo4[0][1].training_objective == LEGACY_TRAINING_OBJECTIVE
    assert cells[0][1] == dataclasses.replace(
        wo4[0][1], training_objective=ALL_POSITION_TRAINING_OBJECTIVE
    )


def test_the_seed_repeats_and_the_full_run_are_held_until_a_variant_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, fraction, cells = _cells("wo3-repaired-6pct-seeds.json")
    assert spec["hold"] and fraction == 0.06
    assert spec["expected_protocol_hash"] == PILOT_PROTOCOL
    assert [config.seed for _label, config in cells] == [7, 13, 21]
    assert len({dataclasses.replace(config, seed=42) for _label, config in cells}) == 1
    assert cells[0][1].training_objective in ALL_POSITION_TRAINING_OBJECTIVES
    assert cells[0][1].training_objective != ALL_POSITION_TRAINING_OBJECTIVE

    full_spec, full_fraction, full_cells = _cells("wo3-repaired-full.json")
    assert full_spec["hold"] and full_fraction == 1.0
    assert [dataclasses.replace(config, seed=7) for _label, config in full_cells] == [cells[0][1]]

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a held cells file must not load data")

    monkeypatch.setattr(sweep_module, "load_inputs", refuse)
    for name in ("wo3-repaired-6pct-seeds.json", "wo3-repaired-full.json"):
        assert sweep_module.main([str(EXPERIMENTS / name)]) == 3
