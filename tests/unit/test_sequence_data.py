"""Point-in-time sequence construction tests shared by neural retrievers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch

from src.models.candidates.sequence_data import (
    AllPositionTrainingData,
    build_all_position_training_data,
    build_strict_prefix_example_store,
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


def _awkward_interactions(seed: int = 3) -> tuple[pd.DataFrame, dict[int, int]]:
    """Small, but with every shape the copied builder has to get right.

    Users with one interaction, one timestamp group, many equal-timestamp
    groups, and histories far longer than the window; sparse movie ids; and
    rows deliberately shuffled so the builder's own sort carries the order.
    """
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, int, int]] = []
    movie_ids = rng.choice(np.arange(1_000, 9_000), size=120, replace=False)
    for user in rng.choice(np.arange(1, 10_000), size=24, replace=False):
        length = int(rng.choice([1, 2, 3, 9, 17, 40, 75]))
        watched = rng.choice(movie_ids, size=length, replace=False)
        # Few distinct timestamps per user so equal-time groups are common.
        clock = np.sort(rng.integers(0, max(2, length // 2), size=length)) * 3_600
        rows.extend((int(user), int(item), int(1_000_000 + t)) for item, t in zip(watched, clock))
    rows.append((77_777, int(movie_ids[0]), 5))  # one interaction: no example
    rows.extend((88_888, int(item), 9) for item in movie_ids[:4])  # one timestamp group: none
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    frame = frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    vocabulary = {int(item): index + 1 for index, item in enumerate(sorted(movie_ids))}
    return frame, vocabulary


@pytest.mark.parametrize("max_length", [1, 3, 8, 50])
def test_example_store_yields_the_copied_builders_examples_in_its_order(max_length: int) -> None:
    """WO-1's equivalence check: same examples, same order, same statistics.

    The restored loop indexes examples by a ``torch.randperm`` over their count,
    so equality has to hold row for row — and again for shuffled batches drawn
    the way the loop draws them.
    """
    interactions, vocabulary = _awkward_interactions()
    copied_histories, copied_positives, copied_stats = build_strict_prefix_examples_with_stats(
        interactions, item_to_index=vocabulary, max_length=max_length
    )

    store = build_strict_prefix_example_store(
        interactions, item_to_index=vocabulary, max_length=max_length
    )

    assert len(store) == len(copied_positives) > 0
    assert store.stats == copied_stats
    histories, positives = store.batch(torch.arange(len(store)))
    assert torch.equal(histories, copied_histories.long())
    assert torch.equal(positives, copied_positives.long())
    assert histories.dtype == positives.dtype == torch.int64

    permutation = torch.randperm(len(store), generator=torch.Generator().manual_seed(11))
    for start in range(0, len(store), 7):
        rows = permutation[start : start + 7]
        batch_histories, batch_positives = store.batch(rows)
        assert torch.equal(batch_histories, copied_histories[rows].long())
        assert torch.equal(batch_positives, copied_positives[rows].long())


def test_example_store_keeps_truncation_statistics_when_windows_are_cut() -> None:
    interactions = pd.DataFrame(
        [(1, item, item) for item in range(1, 6)],
        columns=["userId", "movieId", "timestamp"],
    )
    store = build_strict_prefix_example_store(
        interactions, item_to_index={item: item for item in range(1, 6)}, max_length=2
    )

    histories, positives = store.batch(torch.arange(len(store)))

    assert histories.tolist() == [[0, 1], [1, 2], [2, 3], [3, 4]]
    assert positives.tolist() == [2, 3, 4, 5]
    assert store.stats.n_truncated_sequences == 2
    assert store.stats.n_truncated_interactions == 3


def test_example_store_never_holds_an_examples_by_window_table() -> None:
    """The point of ADR 0020's P1: storage is linear, the window exists per batch only."""
    interactions = pd.DataFrame(
        [(user, item, item) for user in (1, 2, 3) for item in range(1, 401)],
        columns=["userId", "movieId", "timestamp"],
    )
    vocabulary = {item: item for item in range(1, 401)}
    max_length = 200

    store = build_strict_prefix_example_store(
        interactions, item_to_index=vocabulary, max_length=max_length
    )

    stored = (store.items, store.prefix_ends, store.prefix_lengths, store.positives)
    assert all(array.ndim == 1 for array in stored)
    assert len(store.items) == len(interactions)
    assert len(store) == 3 * 399
    copied_table_bytes = len(store) * max_length * np.dtype(np.int32).itemsize
    assert store.nbytes < copied_table_bytes / 10
    histories, _positives = store.batch(torch.arange(16))
    assert histories.shape == (16, max_length)


def test_example_store_handles_empty_input_and_rejects_unknown_items() -> None:
    empty = build_strict_prefix_example_store(
        pd.DataFrame(columns=["userId", "movieId", "timestamp"]),
        item_to_index={},
        max_length=4,
    )
    assert len(empty) == 0
    assert empty.stats.n_targets == 0

    with pytest.raises(KeyError):
        build_strict_prefix_example_store(
            pd.DataFrame([(1, 5, 1), (1, 6, 2)], columns=["userId", "movieId", "timestamp"]),
            item_to_index={5: 1},
            max_length=4,
        )


@pytest.mark.parametrize("max_length", [0, -1])
def test_example_store_rejects_non_positive_max_length(max_length: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        build_strict_prefix_example_store(
            pd.DataFrame(columns=["userId", "movieId", "timestamp"]),
            item_to_index={},
            max_length=max_length,
        )
