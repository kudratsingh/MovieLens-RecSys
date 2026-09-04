"""Deterministic hard-negative selection for two-tower v2 (ADR 0015)."""

from __future__ import annotations

import torch


def select_hard_negatives(
    *,
    candidate_ids: torch.Tensor,
    candidate_scores: torch.Tensor,
    positive_ids: torch.Tensor,
    history_ids: torch.Tensor,
    k: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Select each row's highest-scoring valid, unique candidate ids.

    Returns ``(ids, valid_mask)`` with shape ``(batch, k)``. Missing slots are
    padding id 0 and false in the mask. Ties are broken by smaller dense item
    id, making mined pools reproducible independently of backend sort stability.
    """
    if k < 0:
        raise ValueError("k must be non-negative")
    if candidate_ids.ndim != 1:
        raise ValueError("candidate_ids must have shape (candidates,)")
    if candidate_scores.ndim != 2:
        raise ValueError("candidate_scores must have shape (batch, candidates)")
    batch_size, candidate_count = candidate_scores.shape
    if candidate_count != candidate_ids.numel():
        raise ValueError("candidate id and score counts differ")
    if positive_ids.shape != (batch_size,):
        raise ValueError("positive_ids must have shape (batch,)")
    if history_ids.ndim != 2 or history_ids.shape[0] != batch_size:
        raise ValueError("history_ids must have shape (batch, history)")

    selected = torch.zeros((batch_size, k), dtype=torch.long)
    valid = torch.zeros((batch_size, k), dtype=torch.bool)
    if k == 0 or candidate_count == 0:
        return selected, valid

    ids = [int(value) for value in candidate_ids.tolist()]
    for row in range(batch_size):
        blocked = {0, int(positive_ids[row])}
        blocked.update(int(value) for value in history_ids[row].tolist())
        ranked = sorted(
            zip(ids, (float(value) for value in candidate_scores[row].tolist())),
            key=lambda pair: (-pair[1], pair[0]),
        )
        seen: set[int] = set()
        output: list[int] = []
        for item_id, _score in ranked:
            if item_id in blocked or item_id in seen:
                continue
            seen.add(item_id)
            output.append(item_id)
            if len(output) == k:
                break
        if output:
            selected[row, : len(output)] = torch.tensor(output, dtype=torch.long)
            valid[row, : len(output)] = True
    return selected, valid
