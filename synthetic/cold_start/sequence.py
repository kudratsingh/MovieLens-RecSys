"""Generate and load the sequence-valid ADR 0011 cohort v2.

V1 measures routing by history size, but every event for one synthetic user has
the same timestamp. That is intentionally retained as immutable evidence. V2
adds a separate, paired slice whose histories are real MovieLens transitions:
for each sampled anchor, h0/h1/h3/h10 share the same next-item target and use
successively longer suffixes of the same strictly ordered prefix.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from synthetic.cold_start.config import (
    BUCKET_ID_STRIDE,
    HISTORY_BUCKETS,
    HISTORY_ROLE,
    SYNTH_COLD_RATING,
    SYNTH_COLD_SEED,
    SYNTH_COLD_TENANT_ID,
    TARGET_ROLE,
    USERS_PER_BUCKET,
)
from synthetic.cold_start.generator import (
    CohortGenerationError,
    build_provenance,
    read_movielens_data_version,
    write_cohort,
)
from synthetic.cold_start.load import SyntheticColdCohort, load_cohort

logger = logging.getLogger(__name__)

SEQUENCE_COHORT_VERSION = "v2"
SEQUENCE_GENERATOR_VERSION = "2"
SEQUENCE_COHORT_PARQUET_PATH = (
    Path("data") / "synthetic" / "cold_start" / SEQUENCE_COHORT_VERSION / "users.parquet"
)
SEQUENCE_USER_ID_BASE = 970_000_000
SEQUENCE_METRIC_PREFIX = "synth_sequence_cold"


def sequence_user_ids_for_bucket(history_size: int, *, count: int) -> list[int]:
    """Return v2 ids in a namespace disjoint from the immutable v1 cohort."""
    start = SEQUENCE_USER_ID_BASE + history_size * BUCKET_ID_STRIDE
    return list(range(start, start + count))


def _eligible_anchors(train_ratings: pd.DataFrame, max_history: int) -> list[pd.DataFrame]:
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(train_ratings.columns)
    if missing:
        raise CohortGenerationError(f"training ratings are missing columns: {sorted(missing)}")

    # Equal-time events cannot define an order. Keep the lowest movie id as the
    # deterministic representative of that instant, then take each user's last
    # transition with enough strictly earlier instants behind it.
    ordered = train_ratings.sort_values(
        ["userId", "timestamp", "movieId"], kind="stable"
    ).drop_duplicates(["userId", "timestamp"], keep="first")
    anchors: list[pd.DataFrame] = []
    for _user_id, events in ordered.groupby("userId", sort=True):
        if len(events) < max_history + 1:
            continue
        window = events.tail(max_history + 1).reset_index(drop=True)
        target = int(window.iloc[-1]["movieId"])
        if target in set(window.iloc[:-1]["movieId"].astype(int)):
            # Serving excludes the full prefix. A repeated target would make
            # recall impossible for exclusion reasons rather than sequence quality.
            continue
        anchors.append(window)
    return anchors


def generate_sequence_cohort(
    train_ratings: pd.DataFrame,
    cutoff: int,
    *,
    seed: int = SYNTH_COLD_SEED,
    buckets: Sequence[int] = HISTORY_BUCKETS,
    users_per_bucket: int = USERS_PER_BUCKET,
) -> pd.DataFrame:
    """Build paired, transition-aligned histories with strictly increasing time."""
    if train_ratings.empty:
        raise CohortGenerationError("cannot build a sequence cohort from an empty train slice")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(train_ratings.columns)
    if missing:
        raise CohortGenerationError(f"training ratings are missing columns: {sorted(missing)}")
    if bool((train_ratings["timestamp"] >= cutoff).any()):
        raise CohortGenerationError("sequence cohort input contains events at or after its cutoff")
    if not buckets or min(buckets) < 0 or len(set(buckets)) != len(buckets):
        raise CohortGenerationError(
            f"history buckets must be distinct non-negative values: {list(buckets)}"
        )
    if users_per_bucket > BUCKET_ID_STRIDE:
        raise CohortGenerationError(
            f"{users_per_bucket} users per bucket exceeds the {BUCKET_ID_STRIDE} id stride"
        )

    max_history = max(buckets)
    anchors = _eligible_anchors(train_ratings, max_history)
    if len(anchors) < users_per_bucket:
        raise CohortGenerationError(
            f"only {len(anchors)} eligible strict transitions; need {users_per_bucket}"
        )

    # Same PCG64 discipline as v1, consuming one documented uniform per
    # candidate. Stable ordering resolves the measure-zero equal-key case.
    rng = np.random.Generator(np.random.PCG64(seed))
    keys = rng.random(len(anchors))
    selected = np.argsort(keys, kind="stable")[:users_per_bucket]

    rows: list[dict[str, object]] = []
    for history_size in buckets:
        user_ids = sequence_user_ids_for_bucket(history_size, count=users_per_bucket)
        for synthetic_user_id, anchor_index in zip(user_ids, selected, strict=True):
            anchor = anchors[int(anchor_index)]
            target = anchor.iloc[-1]
            history = anchor.iloc[-(history_size + 1) : -1] if history_size else anchor.iloc[0:0]
            for event in history.itertuples(index=False):
                rows.append(
                    {
                        "userId": synthetic_user_id,
                        "movieId": int(event.movieId),
                        "rating": SYNTH_COLD_RATING,
                        "timestamp": int(event.timestamp),
                        "history_size": history_size,
                        "role": HISTORY_ROLE,
                        "tenant_id": SYNTH_COLD_TENANT_ID,
                        "synthetic": True,
                    }
                )
            rows.append(
                {
                    "userId": synthetic_user_id,
                    "movieId": int(target["movieId"]),
                    "rating": SYNTH_COLD_RATING,
                    "timestamp": int(target["timestamp"]),
                    "history_size": history_size,
                    "role": TARGET_ROLE,
                    "tenant_id": SYNTH_COLD_TENANT_ID,
                    "synthetic": True,
                }
            )

    frame = pd.DataFrame(rows)
    return frame.astype(
        {
            "userId": "int64",
            "movieId": "int64",
            "rating": "float64",
            "timestamp": "int64",
            "history_size": "int32",
            "role": "object",
            "tenant_id": "object",
            "synthetic": "bool",
        }
    )


def load_sequence_cohort(
    path: Path = SEQUENCE_COHORT_PARQUET_PATH,
    *,
    expected_data_version: str | None = None,
) -> SyntheticColdCohort:
    """Load v2 and reject any non-sequential or wrongly versioned payload."""
    table = pq.read_table(path)
    frame = table.to_pandas()
    for user_id, events in frame.groupby("userId", sort=False):
        timestamps = events["timestamp"].astype(int).to_numpy()
        if len(timestamps) > 1 and not bool(np.all(np.diff(timestamps) > 0)):
            raise CohortGenerationError(
                f"{path}: user {int(user_id)} does not have strictly increasing timestamps"
            )
        if events.iloc[-1]["role"] != TARGET_ROLE:
            raise CohortGenerationError(f"{path}: user {int(user_id)} does not end in a target")

    cohort = load_cohort(path, expected_data_version=expected_data_version)
    if cohort.provenance.generator_version != SEQUENCE_GENERATOR_VERSION:
        raise CohortGenerationError(
            f"{path}: generator version {cohort.provenance.generator_version!r} is not "
            f"sequence cohort version {SEQUENCE_GENERATOR_VERSION!r}"
        )
    return cohort


def sequence_metrics(result: object, *, suffix: str) -> dict[str, float]:
    """Return v2's separately named bucket metrics for MLflow."""
    from src.evaluation.protocol import EvalResult

    if not isinstance(result, EvalResult):
        raise TypeError("result must be an EvalResult")
    metrics: dict[str, float] = {}
    for history_size, bucket in sorted(result.synthetic_cold_slices.items()):
        metrics[f"{SEQUENCE_METRIC_PREFIX}_recall_{suffix}_h{history_size}"] = bucket.metrics.recall
        metrics[f"{SEQUENCE_METRIC_PREFIX}_ndcg_{suffix}_h{history_size}"] = bucket.metrics.ndcg
        metrics[f"{SEQUENCE_METRIC_PREFIX}_n_users_h{history_size}"] = float(bucket.n_users)
        if bucket.n_fallback_served is not None:
            metrics[f"{SEQUENCE_METRIC_PREFIX}_fallback_served_h{history_size}"] = float(
                bucket.n_fallback_served
            )
    return metrics


def sequence_params(cohort: SyntheticColdCohort) -> dict[str, str | int]:
    """Return v2 provenance without colliding with immutable v1 run keys."""
    provenance = cohort.provenance
    return {
        f"{SEQUENCE_METRIC_PREFIX}_seed": provenance.seed,
        f"{SEQUENCE_METRIC_PREFIX}_generator_version": provenance.generator_version,
        f"{SEQUENCE_METRIC_PREFIX}_data_version": provenance.data_version,
        f"{SEQUENCE_METRIC_PREFIX}_fingerprint": provenance.fingerprint,
        f"{SEQUENCE_METRIC_PREFIX}_cutoff": provenance.split_cutoff,
        f"{SEQUENCE_METRIC_PREFIX}_users_per_bucket": provenance.users_per_bucket,
        f"n_{SEQUENCE_METRIC_PREFIX}_users": cohort.n_users,
        f"n_{SEQUENCE_METRIC_PREFIX}_history_rows": len(cohort.history),
    }


def _load_train_split(ratings_csv: Path | None) -> tuple[pd.DataFrame, int]:
    from synthetic.cold_start.generator import _load_train_split as load_train_split

    return load_train_split(ratings_csv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=SEQUENCE_COHORT_PARQUET_PATH)
    parser.add_argument("--ratings-csv", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=SYNTH_COLD_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    data_version = read_movielens_data_version()
    train, cutoff = _load_train_split(args.ratings_csv)
    frame = generate_sequence_cohort(train, cutoff, seed=args.seed)
    provenance = build_provenance(
        frame,
        cutoff=cutoff,
        seed=args.seed,
        buckets=HISTORY_BUCKETS,
        users_per_bucket=USERS_PER_BUCKET,
        data_version=data_version,
        generator_version=SEQUENCE_GENERATOR_VERSION,
    )
    write_cohort(frame, args.out, provenance=provenance)
    logger.info(
        "Wrote sequence cohort %s: %d rows, %d users, fingerprint=%s",
        args.out,
        len(frame),
        frame["userId"].nunique(),
        provenance.fingerprint,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
