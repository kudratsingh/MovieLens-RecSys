"""Point-in-time sequence examples shared by learned candidate models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import torch


@dataclass(frozen=True)
class SequenceExampleStats:
    n_sequences: int
    n_targets: int
    n_truncated_sequences: int
    n_truncated_interactions: int


@dataclass(frozen=True)
class AllPositionTrainingData:
    """CSR-shaped windows for one causal pass and many next-item targets.

    ``prediction_offsets`` partitions the flat position/positive arrays by
    window. This keeps storage linear in interactions instead of materializing
    one ``max_length`` prefix row per target.
    """

    sequences: torch.Tensor
    prediction_offsets: torch.Tensor
    prediction_positions: torch.Tensor
    positives: torch.Tensor
    stats: SequenceExampleStats

    def batch(
        self, window_indices: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return windows and their flat local prediction coordinates."""
        selected = window_indices.tolist()
        local_windows: list[int] = []
        positions: list[torch.Tensor] = []
        positives: list[torch.Tensor] = []
        for local_window, global_window in enumerate(selected):
            start = int(self.prediction_offsets[global_window])
            end = int(self.prediction_offsets[global_window + 1])
            count = end - start
            local_windows.extend([local_window] * count)
            positions.append(self.prediction_positions[start:end])
            positives.append(self.positives[start:end])
        return (
            self.sequences[window_indices],
            torch.tensor(local_windows, dtype=torch.long),
            torch.cat(positions).long(),
            torch.cat(positives).long(),
        )

    def batch_visits(
        self,
        window_indices: torch.Tensor,
        target_indices: torch.Tensor,
        local_windows: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return chosen targets of chosen windows, in ``batch``'s layout (WO-3).

        ``batch`` scores every target of every window it is given. This scores
        only ``target_indices`` (flat indices into ``positives``), each read from
        row ``local_windows[i]`` of the returned sequences, which are
        ``window_indices``. A window may appear in several batches of one epoch,
        each time with a different share of its targets.
        """
        return (
            self.sequences[window_indices],
            local_windows.long(),
            self.prediction_positions[target_indices].long(),
            self.positives[target_indices].long(),
        )


@dataclass(frozen=True)
class StrictPrefixExamples:
    """The objective of record's examples, stored once and windowed per batch.

    ``build_strict_prefix_examples_with_stats`` copies a left-padded
    ``max_length`` window for every target: 19,739,546 x 50 int32 values, about
    3.95 GB, on the full training split. This keeps every user's chronological
    item sequence once in ``items`` and describes each example by where its
    strict prefix ends, how long that prefix is, and its target. The window is
    cut from ``items`` only when a batch is assembled (ADR 0020, step P1), so
    resident memory is linear in interactions plus examples and no longer
    scales with ``max_length``.

    The examples, their order, and their statistics are exactly the copied
    builder's. That is what lets the restored loop draw the same permutation,
    the same negatives, and the same weights from the same seed.
    """

    items: np.ndarray[Any, np.dtype[np.int32]]
    prefix_ends: np.ndarray[Any, np.dtype[np.int64]]
    prefix_lengths: np.ndarray[Any, np.dtype[np.int32]]
    positives: np.ndarray[Any, np.dtype[np.int32]]
    max_length: int
    stats: SequenceExampleStats

    def __len__(self) -> int:
        return len(self.positives)

    @property
    def nbytes(self) -> int:
        """Resident size of the stored arrays, logged as the data path's footprint."""
        return int(
            self.items.nbytes
            + self.prefix_ends.nbytes
            + self.prefix_lengths.nbytes
            + self.positives.nbytes
        )

    def batch(self, rows: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Return ``(len(rows), max_length)`` left-padded histories and their targets.

        Each history is the latest ``max_length`` items of the example's strict
        prefix, zero-padded on the left exactly as the copied builder pads it.
        The only two-dimensional array ever built is this batch.
        """
        index = rows.numpy()
        ends = self.prefix_ends[index]
        lengths = np.minimum(self.prefix_lengths[index].astype(np.int64), self.max_length)
        offsets = np.arange(-self.max_length, 0, dtype=np.int64)
        source = ends[:, None] + offsets[None, :]
        inside = offsets[None, :] >= -lengths[:, None]
        histories = np.where(inside, self.items[np.maximum(source, 0)], 0)
        return (
            torch.from_numpy(histories.astype(np.int64)),
            torch.from_numpy(self.positives[index].astype(np.int64)),
        )


def build_user_history(
    train: pd.DataFrame,
    item_to_index: Mapping[int, int],
) -> dict[int, list[int]]:
    """Return each user's deterministic chronological dense-item history."""
    ordered = train.sort_values(["userId", "timestamp"], kind="stable")
    ordered_dense = ordered["movieId"].map(item_to_index).astype("int64")
    grouped = ordered.assign(_dense=ordered_dense).groupby("userId")["_dense"].apply(list)
    return {int(user_id): [int(item) for item in history] for user_id, history in grouped.items()}


def build_strict_prefix_examples(
    interactions: pd.DataFrame,
    *,
    item_to_index: Mapping[int, int],
    max_length: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Build left-padded ``(strictly earlier prefix, target)`` tensors.

    Items sharing a timestamp receive the same prefix. They become visible to
    later timestamp groups but never to one another, even though movie id is a
    deterministic ordering key inside a timestamp group.
    """
    histories, positives, _stats = build_strict_prefix_examples_with_stats(
        interactions,
        item_to_index=item_to_index,
        max_length=max_length,
    )
    return histories, positives


def build_strict_prefix_examples_with_stats(
    interactions: pd.DataFrame,
    *,
    item_to_index: Mapping[int, int],
    max_length: int,
) -> tuple[torch.Tensor, torch.Tensor, SequenceExampleStats]:
    """Build strict-prefix tensors and report sequence truncation explicitly."""
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(interactions.columns)
    if missing:
        raise ValueError(f"interactions is missing required columns: {sorted(missing)}")

    ordered = interactions.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    total = 0
    for _user_id, group in ordered.groupby("userId", sort=False):
        first_timestamp = group["timestamp"].min()
        total += int((group["timestamp"] > first_timestamp).sum())

    histories = np.zeros((total, max_length), dtype=np.int32)
    positives = np.empty(total, dtype=np.int32)
    row = 0
    n_truncated_sequences = 0
    n_truncated_interactions = 0
    for _user_id, group in ordered.groupby("userId", sort=False):
        prefix: list[int] = []
        for _timestamp, simultaneous in group.groupby("timestamp", sort=False):
            targets = [item_to_index[int(item)] for item in simultaneous["movieId"]]
            if prefix:
                truncated = max(0, len(prefix) - max_length)
                window = np.asarray(prefix[-max_length:], dtype=np.int32)
                for target in targets:
                    histories[row, max_length - len(window) :] = window
                    positives[row] = target
                    if truncated:
                        n_truncated_sequences += 1
                        n_truncated_interactions += truncated
                    row += 1
            prefix.extend(targets)
    stats = SequenceExampleStats(
        n_sequences=row,
        n_targets=row,
        n_truncated_sequences=n_truncated_sequences,
        n_truncated_interactions=n_truncated_interactions,
    )
    return torch.from_numpy(histories), torch.from_numpy(positives), stats


def build_strict_prefix_example_store(
    interactions: pd.DataFrame,
    *,
    item_to_index: Mapping[int, int],
    max_length: int,
) -> StrictPrefixExamples:
    """Index the strict-prefix examples without materializing their windows.

    Same ordering contract as ``build_strict_prefix_examples_with_stats``:
    rows sorted by ``(userId, timestamp, movieId)``; every interaction outside
    its user's first timestamp group is one example whose prefix is everything
    in strictly earlier timestamp groups. Items sharing a timestamp share a
    prefix and never see one another.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(interactions.columns)
    if missing:
        raise ValueError(f"interactions is missing required columns: {sorted(missing)}")

    ordered = interactions.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    dense = ordered["movieId"].map(dict(item_to_index))
    if dense.isna().any():
        unknown = ordered.loc[dense.isna(), "movieId"].iloc[0]
        raise KeyError(int(unknown))
    items = dense.to_numpy(dtype=np.int64).astype(np.int32)
    users = ordered["userId"].to_numpy()
    timestamps = ordered["timestamp"].to_numpy()
    n_rows = len(ordered)
    row_numbers = np.arange(n_rows, dtype=np.int64)

    starts_user = np.ones(n_rows, dtype=bool)
    starts_user[1:] = users[1:] != users[:-1]
    starts_group = starts_user.copy()
    starts_group[1:] |= timestamps[1:] != timestamps[:-1]
    # Carry each run's first row forward: for every row, the index where its
    # user's sequence starts and where its own timestamp group starts.
    user_start = np.maximum.accumulate(np.where(starts_user, row_numbers, 0))
    group_start = np.maximum.accumulate(np.where(starts_group, row_numbers, 0))

    is_example = group_start > user_start
    prefix_ends = group_start[is_example]
    prefix_lengths = (prefix_ends - user_start[is_example]).astype(np.int32)
    positives = items[is_example]
    omitted = np.maximum(prefix_lengths.astype(np.int64) - max_length, 0)
    n_examples = len(positives)
    return StrictPrefixExamples(
        items=items,
        prefix_ends=prefix_ends,
        prefix_lengths=prefix_lengths,
        positives=positives,
        max_length=max_length,
        stats=SequenceExampleStats(
            n_sequences=n_examples,
            n_targets=n_examples,
            n_truncated_sequences=int((omitted > 0).sum()),
            n_truncated_interactions=int(omitted.sum()),
        ),
    )


def build_all_position_training_data(
    interactions: pd.DataFrame,
    *,
    item_to_index: Mapping[int, int],
    max_length: int,
) -> AllPositionTrainingData:
    """Build canonical SASRec windows that supervise every causal position.

    Each timestamp group is an atomic event: every item in the following
    group is predicted from the representation at the final item of the
    current group. Items sharing a timestamp therefore never enter one
    another's prefix. Long histories are split into bounded windows; the first
    prediction after a boundary sees only the new window's causal context.

    The returned CSR layout stores each input interaction once per window and
    each target once, rather than copying a length-``max_length`` prefix for
    every target as the legacy builder does.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(interactions.columns)
    if missing:
        raise ValueError(f"interactions is missing required columns: {sorted(missing)}")

    ordered = interactions.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    n_windows = 0
    n_targets = 0
    for _user_id, group in ordered.groupby("userId", sort=False):
        sizes = group.groupby("timestamp", sort=False).size().tolist()
        if len(sizes) < 2:
            continue
        n_targets += sum(int(size) for size in sizes[1:])
        n_windows += _count_all_position_windows(sizes[:-1], max_length=max_length)

    sequences = np.zeros((n_windows, max_length), dtype=np.int32)
    prediction_offsets = np.zeros(n_windows + 1, dtype=np.int64)
    prediction_positions = np.empty(n_targets, dtype=np.int16)
    positives = np.empty(n_targets, dtype=np.int32)
    window_row = 0
    prediction_row = 0
    n_truncated_targets = 0
    n_truncated_interactions = 0

    for _user_id, group in ordered.groupby("userId", sort=False):
        timestamp_groups = [
            np.asarray(
                [item_to_index[int(item)] for item in simultaneous["movieId"]],
                dtype=np.int32,
            )
            for _timestamp, simultaneous in group.groupby("timestamp", sort=False)
        ]
        if len(timestamp_groups) < 2:
            continue

        window_tokens: list[np.ndarray[Any, np.dtype[np.int32]]] = []
        assignments: list[tuple[int, np.ndarray[Any, np.dtype[np.int32]], int]] = []
        window_length = 0
        full_prefix_length = 0

        def flush() -> None:
            nonlocal prediction_row, window_row, window_tokens, assignments, window_length
            if not assignments:
                return
            padding = max_length - window_length
            sequences[window_row, padding:] = np.concatenate(window_tokens)
            for position, targets, omitted in assignments:
                end = prediction_row + len(targets)
                prediction_positions[prediction_row:end] = padding + position
                positives[prediction_row:end] = targets
                prediction_row = end
            prediction_offsets[window_row + 1] = prediction_row
            window_row += 1
            window_tokens = []
            assignments = []
            window_length = 0

        for source, targets in zip(timestamp_groups[:-1], timestamp_groups[1:]):
            source_tail = source[-max_length:]
            if window_length and window_length + len(source_tail) > max_length:
                flush()
            if len(source_tail) == max_length and window_length:
                flush()

            window_tokens.append(source_tail)
            window_length += len(source_tail)
            full_prefix_length += len(source)
            omitted = full_prefix_length - window_length
            if omitted:
                n_truncated_targets += len(targets)
                n_truncated_interactions += omitted * len(targets)
            assignments.append((window_length - 1, targets, omitted))

            # A full window cannot accept another timestamp group. Flushing
            # here also handles a single oversized group after retaining its
            # most recent ``max_length`` items.
            if window_length == max_length:
                flush()
        flush()

    if window_row != n_windows or prediction_row != n_targets:
        raise RuntimeError("all-position sequence preallocation count drifted")
    return AllPositionTrainingData(
        sequences=torch.from_numpy(sequences),
        prediction_offsets=torch.from_numpy(prediction_offsets),
        prediction_positions=torch.from_numpy(prediction_positions),
        positives=torch.from_numpy(positives),
        stats=SequenceExampleStats(
            n_sequences=n_windows,
            n_targets=n_targets,
            n_truncated_sequences=n_truncated_targets,
            n_truncated_interactions=n_truncated_interactions,
        ),
    )


def build_overlapping_all_position_training_data(
    interactions: pd.DataFrame,
    *,
    item_to_index: Mapping[int, int],
    max_length: int,
    window_stride: int,
) -> AllPositionTrainingData:
    """All-position windows that overlap, so no scored target starts from nothing (WO-3).

    ``build_all_position_training_data`` cuts a long history into windows back
    to back, so the first prediction in each window sees one movie even when the
    user has hundreds behind it. Here the timestamp groups are packed into
    *chunks* of at most ``window_stride`` movies by the same greedy rule, and each
    chunk becomes one window holding the latest ``max_length`` movies that end
    where the chunk ends. A window scores only its own chunk's predictions. So:

    * every target is scored exactly once, the same targets the copied-prefix
      objective trains on;
    * a scored position has at least ``max_length - window_stride`` movies behind
      it in its window, or every movie the user has, whichever is fewer. The
      positions before that are context only, never scored;
    * consecutive windows overlap by about ``max_length - window_stride`` movies,
      so a history is encoded about ``max_length / window_stride`` times as often.

    Timestamp groups stay atomic exactly as in the back-to-back builder: a target
    is predicted from the final movie of the group before it, and movies sharing
    a timestamp never enter one another's visible prefix. A window may begin
    inside a group, which is still causal because every movie in it is strictly
    earlier than the target. ``window_stride`` must be below ``max_length``; the
    back-to-back builder is the recorded behaviour and stays as it was.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if not 0 < window_stride < max_length:
        raise ValueError("window_stride must be positive and below max_length")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(interactions.columns)
    if missing:
        raise ValueError(f"interactions is missing required columns: {sorted(missing)}")

    ordered = interactions.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    dense = ordered["movieId"].map(dict(item_to_index))
    if dense.isna().any():
        unknown = ordered.loc[dense.isna(), "movieId"].iloc[0]
        raise KeyError(int(unknown))
    items = dense.to_numpy(dtype=np.int64).astype(np.int32)
    users = ordered["userId"].to_numpy()
    all_timestamps = ordered["timestamp"].to_numpy()
    user_bounds = np.r_[np.flatnonzero(np.r_[True, users[1:] != users[:-1]]), len(items)]
    windows: list[np.ndarray[Any, np.dtype[np.int32]]] = []
    window_positions: list[np.ndarray[Any, np.dtype[np.int16]]] = []
    window_positives: list[np.ndarray[Any, np.dtype[np.int32]]] = []
    n_truncated_targets = 0
    n_truncated_interactions = 0

    for user_start, user_end in zip(user_bounds[:-1], user_bounds[1:]):
        sequence = items[user_start:user_end]
        timestamps = all_timestamps[user_start:user_end]
        starts = np.flatnonzero(np.r_[True, timestamps[1:] != timestamps[:-1]])
        bounds = np.r_[starts, len(sequence)]
        if len(starts) < 2:
            continue

        # (end of the source group, start and end of its target group)
        assignments: list[tuple[int, int, int]] = []
        chunk_length = 0

        def flush() -> None:
            nonlocal assignments, chunk_length, n_truncated_targets, n_truncated_interactions
            if not assignments:
                return
            chunk_end = assignments[-1][0]
            window_start = max(0, chunk_end - max_length)
            tokens = sequence[window_start:chunk_end]
            padding = max_length - len(tokens)
            window = np.zeros(max_length, dtype=np.int32)
            window[padding:] = tokens
            sizes = [target_end - target_start for _end, target_start, target_end in assignments]
            window_positions.append(
                np.repeat(
                    np.asarray(
                        [
                            padding + source_end - 1 - window_start
                            for source_end, _s, _e in assignments
                        ],
                        dtype=np.int16,
                    ),
                    sizes,
                )
            )
            window_positives.append(
                np.concatenate([sequence[start:end] for _source_end, start, end in assignments])
            )
            if window_start:
                n_truncated_targets += sum(sizes)
                n_truncated_interactions += window_start * sum(sizes)
            windows.append(window)
            assignments = []
            chunk_length = 0

        for index in range(len(starts) - 1):
            source_start, source_end = int(bounds[index]), int(bounds[index + 1])
            retained = min(source_end - source_start, window_stride)
            if chunk_length and chunk_length + retained > window_stride:
                flush()
            if retained == window_stride and chunk_length:
                flush()
            assignments.append((source_end, source_end, int(bounds[index + 2])))
            chunk_length += retained
            if chunk_length == window_stride:
                flush()
        flush()

    offsets = np.zeros(len(windows) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum([len(targets) for targets in window_positives], dtype=np.int64)
    positions = (
        np.concatenate(window_positions) if window_positions else np.zeros(0, dtype=np.int16)
    )
    positives = (
        np.concatenate(window_positives) if window_positives else np.zeros(0, dtype=np.int32)
    )
    return AllPositionTrainingData(
        sequences=torch.from_numpy(
            np.stack(windows) if windows else np.zeros((0, max_length), dtype=np.int32)
        ),
        prediction_offsets=torch.from_numpy(offsets),
        prediction_positions=torch.from_numpy(positions.astype(np.int16)),
        positives=torch.from_numpy(positives.astype(np.int32)),
        stats=SequenceExampleStats(
            n_sequences=len(windows),
            n_targets=len(positives),
            n_truncated_sequences=n_truncated_targets,
            n_truncated_interactions=n_truncated_interactions,
        ),
    )


def _count_all_position_windows(source_sizes: list[int], *, max_length: int) -> int:
    windows = 0
    current_length = 0
    for source_size in source_sizes:
        retained = min(int(source_size), max_length)
        if current_length and current_length + retained > max_length:
            windows += 1
            current_length = 0
        current_length += retained
        if current_length == max_length:
            windows += 1
            current_length = 0
    return windows + int(current_length > 0)
