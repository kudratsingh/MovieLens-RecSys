from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from synthetic.cold_start.config import HISTORY_ROLE, TARGET_ROLE
from synthetic.cold_start.generator import (
    CohortGenerationError,
    build_provenance,
    write_cohort,
)
from synthetic.cold_start.sequence import (
    SEQUENCE_GENERATOR_VERSION,
    generate_sequence_cohort,
    load_sequence_cohort,
)


def _train(n_users: int = 6) -> pd.DataFrame:
    return pd.DataFrame(
        [
            (user_id, user_id * 100 + position, 1_000 + position * 10)
            for user_id in range(1, n_users + 1)
            for position in range(12)
        ],
        columns=["userId", "movieId", "timestamp"],
    )


def test_v2_is_paired_transition_aligned_and_strictly_ordered() -> None:
    cohort = generate_sequence_cohort(
        _train(),
        cutoff=2_000,
        seed=42,
        buckets=(0, 1, 3, 10),
        users_per_bucket=3,
    )

    targets = cohort.loc[cohort["role"] == TARGET_ROLE]
    for offset in range(3):
        paired = targets.groupby("history_size", sort=True).nth(offset)
        assert paired["movieId"].nunique() == 1

    for _user_id, rows in cohort.groupby("userId", sort=False):
        assert rows.iloc[-1]["role"] == TARGET_ROLE
        assert rows.iloc[:-1]["role"].eq(HISTORY_ROLE).all()
        assert rows["timestamp"].is_monotonic_increasing
        assert rows["timestamp"].is_unique
        assert rows.iloc[-1]["movieId"] not in set(rows.iloc[:-1]["movieId"])


def test_v2_generation_is_independent_of_input_row_order() -> None:
    train = _train()
    first = generate_sequence_cohort(train, cutoff=2_000, users_per_bucket=3)
    second = generate_sequence_cohort(
        train.iloc[::-1].reset_index(drop=True), cutoff=2_000, users_per_bucket=3
    )
    pd.testing.assert_frame_equal(first, second)


def test_v2_refuses_too_few_strict_transitions_and_post_cutoff_rows() -> None:
    with pytest.raises(CohortGenerationError, match="eligible strict transitions"):
        generate_sequence_cohort(_train(1), cutoff=2_000, users_per_bucket=2)
    with pytest.raises(CohortGenerationError, match="at or after its cutoff"):
        generate_sequence_cohort(_train(), cutoff=1_100, users_per_bucket=2)


def test_v2_round_trip_checks_version_and_sequence(tmp_path: Path) -> None:
    frame = generate_sequence_cohort(_train(), cutoff=2_000, users_per_bucket=2)
    provenance = build_provenance(
        frame,
        cutoff=2_000,
        seed=42,
        buckets=(0, 1, 3, 10),
        users_per_bucket=2,
        data_version="fixture-md5",
        generator_version=SEQUENCE_GENERATOR_VERSION,
    )
    path = tmp_path / "users.parquet"
    write_cohort(frame, path, provenance=provenance)

    loaded = load_sequence_cohort(path, expected_data_version="fixture-md5")
    assert loaded.provenance.generator_version == "2"
    assert loaded.n_users == 8
    assert len(loaded.history) == 28
