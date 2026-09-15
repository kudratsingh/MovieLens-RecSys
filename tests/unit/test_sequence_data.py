"""Point-in-time sequence construction tests shared by neural retrievers."""

from __future__ import annotations

import pandas as pd
import pytest

from src.models.candidates.sequence_data import (
    AllPositionTrainingData,
    build_all_position_training_data,
    build_strict_prefix_examples,
    build_strict_prefix_examples_with_stats,
)


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


def test_sequence_stats_count_omitted_prefix_interactions() -> None:
    interactions = pd.DataFrame(
        [(1, item, item) for item in range(1, 6)],
        columns=["userId", "movieId", "timestamp"],
    )
    _histories, _targets, stats = build_strict_prefix_examples_with_stats(
        interactions,
        item_to_index={item: item for item in range(1, 6)},
        max_length=2,
    )
    assert stats.n_sequences == 4
    assert stats.n_targets == 4
    assert stats.n_truncated_sequences == 2
    assert stats.n_truncated_interactions == 3


def _expand_all_position_prefixes(
    data: AllPositionTrainingData,
) -> list[tuple[list[int], int]]:
    expanded: list[tuple[list[int], int]] = []
    for window in range(len(data.sequences)):
        start = int(data.prediction_offsets[window])
        end = int(data.prediction_offsets[window + 1])
        for prediction in range(start, end):
            position = int(data.prediction_positions[prediction])
            prefix = [int(item) for item in data.sequences[window, : position + 1] if item]
            expanded.append((prefix, int(data.positives[prediction])))
    return expanded


def test_all_position_window_expands_to_legacy_examples_on_small_corpus() -> None:
    """The computational shape changes, not the eligible point-in-time pairs."""
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
    legacy_histories, legacy_targets = build_strict_prefix_examples(
        interactions, item_to_index=vocab, max_length=3
    )

    all_positions = build_all_position_training_data(
        interactions, item_to_index=vocab, max_length=3
    )

    legacy = [
        ([int(item) for item in history if item], int(target))
        for history, target in zip(legacy_histories, legacy_targets)
    ]
    assert _expand_all_position_prefixes(all_positions) == legacy
    assert all_positions.sequences.tolist() == [[vocab[10], vocab[11], vocab[12]]]
    assert all_positions.stats.n_sequences == 1
    assert all_positions.stats.n_targets == 3


def test_all_position_windows_slice_long_histories_without_losing_targets() -> None:
    interactions = pd.DataFrame(
        [(1, item, item) for item in range(1, 8)],
        columns=["userId", "movieId", "timestamp"],
    )

    data = build_all_position_training_data(
        interactions,
        item_to_index={item: item for item in range(1, 8)},
        max_length=3,
    )

    assert data.sequences.tolist() == [[1, 2, 3], [4, 5, 6]]
    assert data.positives.tolist() == [2, 3, 4, 5, 6, 7]
    assert data.prediction_positions.tolist() == [0, 1, 2, 0, 1, 2]
    assert data.prediction_offsets.tolist() == [0, 3, 6]
    assert data.stats.n_sequences == 2
    assert data.stats.n_targets == 6
    assert data.stats.n_truncated_sequences == 3
    assert data.stats.n_truncated_interactions == 9


def test_all_position_storage_is_bounded_at_length_200() -> None:
    interactions = pd.DataFrame(
        [(1, item, item) for item in range(1, 452)],
        columns=["userId", "movieId", "timestamp"],
    )

    data = build_all_position_training_data(
        interactions,
        item_to_index={item: item for item in range(1, 452)},
        max_length=200,
    )

    assert data.sequences.shape == (3, 200)
    assert data.positives.numel() == 450
    assert data.sequences.numel() <= 3 * 200


@pytest.mark.parametrize("max_length", [0, -1])
def test_non_positive_max_length_is_rejected(max_length: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_strict_prefix_examples(
            pd.DataFrame(columns=["userId", "movieId", "timestamp"]),
            item_to_index={},
            max_length=max_length,
        )
