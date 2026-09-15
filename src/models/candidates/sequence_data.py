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
