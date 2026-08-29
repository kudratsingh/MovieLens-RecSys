"""Load the ADR 0011 cohort into the two shapes training actually needs.

A trainer wants exactly two things out of the parquet: the history rows to
concatenate onto its train slice before ``fit``, and the per-bucket
``{user_id: {target}}`` mapping the eval harness consumes. Handing those out
separately is the mechanism that keeps the target held out — there is no call
here that returns both in one frame, so no caller can accidentally train on the
item it is about to be scored on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from synthetic.cold_start.config import (
    COHORT_PARQUET_PATH,
    HISTORY_ROLE,
    META_BUCKETS,
    META_DATA_VERSION,
    META_FINGERPRINT,
    META_GENERATOR_VERSION,
    META_SEED,
    META_SPLIT_CUTOFF,
    META_USERS_PER_BUCKET,
    TARGET_ROLE,
)
from synthetic.cold_start.generator import (
    COHORT_COLUMNS,
    CohortProvenance,
    read_movielens_data_version,
)

logger = logging.getLogger(__name__)

# The MovieLens-shaped columns a trainer concatenates onto its train slice.
TRAIN_COLUMNS = ("userId", "movieId", "rating", "timestamp")


class CohortLoadError(RuntimeError):
    """The cohort parquet is present but cannot be trusted."""


class CohortDataVersionMismatchError(CohortLoadError):
    """The cohort was generated against a different MovieLens release.

    ADR 0011's Risks section calls this out by name: if the item catalog has
    moved under the cohort, its target items may not exist in the model's
    vocabulary and the recall it reports is quietly wrong. Quietly wrong is the
    one outcome a coverage harness may not produce, so this is fatal rather
    than a warning.
    """


@dataclass(frozen=True)
class SyntheticColdCohort:
    """A loaded cohort, split into what trains and what scores."""

    history: pd.DataFrame
    """Train-shaped rows (``userId``, ``movieId``, ``rating``, ``timestamp``)."""

    targets_by_bucket: dict[int, dict[int, set[int]]]
    """``{history_size: {user_id: {target_movie_id}}}`` — one target per user."""

    user_ids: tuple[int, ...]
    """Every cohort user, in generation order, for a batch recommend call."""

    provenance: CohortProvenance

    @property
    def buckets(self) -> tuple[int, ...]:
        return tuple(sorted(self.targets_by_bucket))

    @property
    def n_users(self) -> int:
        return len(self.user_ids)


def _read_provenance(path: Path, metadata: dict[bytes, bytes] | None) -> CohortProvenance:
    raw = metadata or {}

    def value(key: str) -> str:
        encoded = raw.get(key.encode())
        if encoded is None:
            raise CohortLoadError(
                f"{path} carries no {key!r} provenance. A cohort without provenance "
                "cannot be checked against the dataset it was generated from."
            )
        return encoded.decode()

    return CohortProvenance(
        seed=int(value(META_SEED)),
        generator_version=value(META_GENERATOR_VERSION),
        split_cutoff=int(value(META_SPLIT_CUTOFF)),
        data_version=value(META_DATA_VERSION),
        buckets=tuple(int(bucket) for bucket in json.loads(value(META_BUCKETS))),
        users_per_bucket=int(value(META_USERS_PER_BUCKET)),
        fingerprint=value(META_FINGERPRINT),
    )


def load_cohort(
    path: Path = COHORT_PARQUET_PATH,
    *,
    expected_data_version: str | None = None,
) -> SyntheticColdCohort:
    """Read the cohort parquet, asserting its provenance before returning it."""
    table = pq.read_table(path)
    provenance = _read_provenance(path, table.schema.metadata)

    expected = expected_data_version or read_movielens_data_version()
    if provenance.data_version != expected:
        raise CohortDataVersionMismatchError(
            f"{path} was generated against MovieLens {provenance.data_version} but this "
            f"checkout has {expected}. Regenerate it with `make synth-cold-cohort`; a "
            "cohort whose targets predate the current catalog reports recall for items "
            "the model may never have seen."
        )

    frame = table.to_pandas()
    missing = [column for column in COHORT_COLUMNS if column not in frame.columns]
    if missing:
        raise CohortLoadError(f"{path} is missing cohort columns: {missing}")

    history = frame.loc[frame["role"] == HISTORY_ROLE, list(TRAIN_COLUMNS)].reset_index(drop=True)
    targets_frame = frame.loc[frame["role"] == TARGET_ROLE]

    targets_by_bucket: dict[int, dict[int, set[int]]] = {}
    for row in targets_frame.itertuples(index=False):
        targets_by_bucket.setdefault(int(row.history_size), {})[int(row.userId)] = {
            int(row.movieId)
        }

    # Generation order, which is bucket order then index order — the order the
    # ids were minted in, so a recommend call over them is reproducible too.
    user_ids = tuple(int(user_id) for user_id in frame["userId"].drop_duplicates())

    cohort = SyntheticColdCohort(
        history=history,
        targets_by_bucket=targets_by_bucket,
        user_ids=user_ids,
        provenance=provenance,
    )
    _assert_invariants(cohort, path)
    return cohort


def _assert_invariants(cohort: SyntheticColdCohort, path: Path) -> None:
    """Re-check on load what the generator guarantees on write.

    The loader is the only path into training, which makes it the right place
    to catch a hand-edited or half-regenerated parquet before a model is fitted
    on it rather than after the metrics look strange.
    """
    n_targets = sum(len(users) for users in cohort.targets_by_bucket.values())
    if n_targets != len(cohort.user_ids):
        raise CohortLoadError(
            f"{path}: {len(cohort.user_ids)} users but {n_targets} targets — "
            "every cohort user must hold out exactly one item"
        )

    history_pairs = set(
        zip(
            cohort.history["userId"].astype(int),
            cohort.history["movieId"].astype(int),
            strict=True,
        )
    )
    for users in cohort.targets_by_bucket.values():
        for user_id, targets in users.items():
            for target in targets:
                if (user_id, target) in history_pairs:
                    raise CohortLoadError(
                        f"{path}: user {user_id}'s target {target} also appears in their "
                        "history — the cohort would be scored on an item it trained on"
                    )


def load_cohort_if_present(
    path: Path = COHORT_PARQUET_PATH,
    *,
    expected_data_version: str | None = None,
) -> SyntheticColdCohort | None:
    """Load the cohort, or return ``None`` if the parquet is not on this machine.

    Absence is the ordinary case on a clean checkout — the parquet is DVC-tracked
    and regenerable, not committed — so a trainer that cannot find it logs and
    carries on without ADR 0011's coverage metrics. Every *other* problem still
    raises: a cohort that exists but disagrees with the dataset is a defect, not
    a missing optional extra.
    """
    if not path.exists():
        logger.info(
            "No synthetic cold-start cohort at %s — skipping ADR 0011 coverage metrics. "
            "Generate it with `make synth-cold-cohort`.",
            path,
        )
        return None
    return load_cohort(path, expected_data_version=expected_data_version)
