from __future__ import annotations

import pytest
import torch

from src.models.candidates.hard_negatives import select_hard_negatives


def test_selects_highest_scoring_valid_unique_items_with_stable_ties() -> None:
    ids, valid = select_hard_negatives(
        candidate_ids=torch.tensor([9, 2, 5, 2, 7, 0]),
        candidate_scores=torch.tensor(
            [
                [0.9, 0.8, 0.8, 0.7, 0.6, 10.0],
                [0.1, 0.5, 0.4, 0.9, 0.8, 10.0],
            ]
        ),
        positive_ids=torch.tensor([9, 5]),
        history_ids=torch.tensor([[7, 0], [2, 0]]),
        k=3,
    )

    # Row 0 blocks positive 9, history 7, and padding 0. Score-tied ids 2 and
    # 5 are ordered by smaller id, and duplicate 2 appears only once.
    assert ids[0].tolist() == [2, 5, 0]
    assert valid[0].tolist() == [True, True, False]
    # Row 1 blocks positive 5 and history 2, leaving 7 then 9.
    assert ids[1].tolist() == [7, 9, 0]
    assert valid[1].tolist() == [True, True, False]


def test_same_inputs_are_byte_deterministic() -> None:
    def select() -> tuple[torch.Tensor, torch.Tensor]:
        return select_hard_negatives(
            candidate_ids=torch.tensor([4, 3, 2, 1]),
            candidate_scores=torch.tensor([[0.1, 0.3, 0.3, 0.2]]),
            positive_ids=torch.tensor([99]),
            history_ids=torch.tensor([[0, 8]]),
            k=3,
        )

    first_ids, first_mask = select()
    second_ids, second_mask = select()
    assert torch.equal(first_ids, second_ids)
    assert torch.equal(first_mask, second_mask)


def test_zero_k_and_short_pool_are_padded_safely() -> None:
    zero, zero_mask = select_hard_negatives(
        candidate_ids=torch.tensor([1]),
        candidate_scores=torch.tensor([[0.5]]),
        positive_ids=torch.tensor([1]),
        history_ids=torch.tensor([[0]]),
        k=0,
    )
    assert zero.shape == zero_mask.shape == (1, 0)

    padded, mask = select_hard_negatives(
        candidate_ids=torch.tensor([1]),
        candidate_scores=torch.tensor([[0.5]]),
        positive_ids=torch.tensor([1]),
        history_ids=torch.tensor([[0]]),
        k=2,
    )
    assert padded.tolist() == [[0, 0]]
    assert mask.tolist() == [[False, False]]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"k": -1}, "non-negative"),
        ({"candidate_ids": torch.tensor([[1]])}, "candidate_ids"),
        ({"candidate_scores": torch.tensor([0.5])}, "candidate_scores"),
        ({"positive_ids": torch.tensor([[1]])}, "positive_ids"),
        ({"history_ids": torch.tensor([1])}, "history_ids"),
    ],
)
def test_invalid_shapes_fail_loudly(override: dict[str, object], message: str) -> None:
    kwargs: dict[str, object] = {
        "candidate_ids": torch.tensor([1]),
        "candidate_scores": torch.tensor([[0.5]]),
        "positive_ids": torch.tensor([2]),
        "history_ids": torch.tensor([[0]]),
        "k": 1,
    }
    kwargs.update(override)
    with pytest.raises(ValueError, match=message):
        select_hard_negatives(**kwargs)  # type: ignore[arg-type]
