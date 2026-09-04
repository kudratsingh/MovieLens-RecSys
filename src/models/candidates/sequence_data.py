"""Point-in-time sequence examples shared by learned candidate models."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
import torch


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
    for _user_id, group in ordered.groupby("userId", sort=False):
        prefix: list[int] = []
        for _timestamp, simultaneous in group.groupby("timestamp", sort=False):
            targets = [item_to_index[int(item)] for item in simultaneous["movieId"]]
            if prefix:
                window = np.asarray(prefix[-max_length:], dtype=np.int32)
                for target in targets:
                    histories[row, max_length - len(window) :] = window
                    positives[row] = target
                    row += 1
            prefix.extend(targets)
    return torch.from_numpy(histories), torch.from_numpy(positives)
