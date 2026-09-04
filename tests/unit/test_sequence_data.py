"""Point-in-time sequence construction tests shared by neural retrievers."""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.candidates.sequence_data import build_strict_prefix_examples


def test_equal_timestamp_targets_share_strictly_earlier_prefix() -> None:
    interactions = pd.DataFrame(
        [
            (1, 13, 30),
            (1, 11, 20),
            (1, 10, 10),
            (1, 12, 20),
            (2, 20, 40),
            (2, 21, 40),
        ],
        columns=["userId", "movieId", "timestamp"],
    )
    vocab = {item: index + 1 for index, item in enumerate(range(10, 22))}

    histories, targets = build_strict_prefix_examples(
        interactions, item_to_index=vocab, max_length=3
    )

    assert targets.tolist() == [vocab[11], vocab[12], vocab[13]]
    assert histories.tolist() == [
        [0, 0, vocab[10]],
        [0, 0, vocab[10]],
        [vocab[10], vocab[11], vocab[12]],
    ]


def test_prefix_is_truncated_to_latest_items() -> None:
    interactions = pd.DataFrame(
        [(1, item, item) for item in range(1, 6)],
        columns=["userId", "movieId", "timestamp"],
    )
    histories, targets = build_strict_prefix_examples(
        interactions, item_to_index={item: item for item in range(1, 6)}, max_length=2
    )
    assert histories[-1].tolist() == [3, 4]
    assert targets[-1].item() == 5


@pytest.mark.parametrize("max_length", [0, -1])
def test_non_positive_max_length_is_rejected(max_length: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_strict_prefix_examples(
            pd.DataFrame(columns=["userId", "movieId", "timestamp"]),
            item_to_index={},
            max_length=max_length,
        )
