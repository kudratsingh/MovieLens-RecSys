"""Point-in-time sequence examples shared by learned candidate models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch


@dataclass(frozen=True)
class SequenceExampleStats:
    n_sequences: int
    n_targets: int
    n_truncated_sequences: int
    n_truncated_interactions: int


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
