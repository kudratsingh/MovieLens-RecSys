"""Generate the ADR 0011 synthetic cold-start cohort.

2 000 synthetic users — 500 at each of the history sizes {0, 1, 3, 10} — whose
histories are drawn popularity-weighted from the training catalog, each with a
single held-out target drawn from the same distribution and excluded from their
history. The output is a DVC-tracked parquet that the trainers append to their
train slice before fitting, so the cohort is *in* the model the way a real
low-history user would be, and the eval harness can then ask what the model did
with each bucket.

Determinism is the whole point — non-negotiable #5 and ADR 0011's Risks both
turn on it — so three things are pinned deliberately:

  * **The RNG.** ``numpy.random.Generator(PCG64(seed))``, and only
    ``Generator.random``. ``Generator.choice(..., replace=False, p=...)`` would
    be the obvious call, but its selection algorithm is an implementation
    detail numpy is free to change; the uniform stream is a documented
    guarantee. Weighted sampling without replacement is built on top of it with
    the Efraimidis–Spirakis key trick, ``key = log(u) / w``, largest keys win.
  * **The item ordering.** Items are ordered by ascending ``movieId`` before
    weights are attached, so the catalog the RNG indexes into is a function of
    the data alone rather than of whatever order a groupby happened to produce.
  * **The consumption pattern.** Every user draws exactly one uniform per
    catalog item, whatever their bucket, so the stream stays aligned even
    though bucket 0 needs a single item and bucket 10 needs eleven.

Run it with ``make synth-cold-cohort``, or directly:

    python -m synthetic.cold_start.generator --out data/synthetic/cold_start/v1/users.parquet

which reads ratings from Postgres the way every ``make train-*`` target does.
``--ratings-csv data/raw/ml-25m/ratings.csv`` reads the DVC-tracked CSV instead,
for a machine that has the dataset on disk but has not loaded it into Postgres.
Both paths run the same ``temporal_split`` over the same columns and produce
the identical cohort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from synthetic.cold_start.config import (
    BUCKET_ID_STRIDE,
    COHORT_PARQUET_PATH,
    GENERATOR_VERSION,
    HISTORY_BUCKETS,
    HISTORY_ROLE,
    META_BUCKETS,
    META_DATA_VERSION,
    META_FINGERPRINT,
    META_GENERATOR_VERSION,
    META_SEED,
    META_SPLIT_CUTOFF,
    META_USERS_PER_BUCKET,
    MOVIELENS_DVC_PATH,
    SYNTH_COLD_RATING,
    SYNTH_COLD_SEED,
    SYNTH_COLD_TENANT_ID,
    TARGET_ROLE,
    TIMESTAMP_OFFSET_SECONDS,
    USERS_PER_BUCKET,
    user_ids_for_bucket,
)

logger = logging.getLogger(__name__)

# The cohort's on-disk schema, declared rather than inferred. An explicit
# schema keeps the written types independent of whatever pandas inferred this
# afternoon, which is one fewer way for a regenerated file to differ.
COHORT_SCHEMA = pa.schema(
    [
        pa.field("userId", pa.int64(), nullable=False),
        pa.field("movieId", pa.int64(), nullable=False),
        pa.field("rating", pa.float64(), nullable=False),
        pa.field("timestamp", pa.int64(), nullable=False),
        pa.field("history_size", pa.int32(), nullable=False),
        pa.field("role", pa.string(), nullable=False),
        pa.field("tenant_id", pa.string(), nullable=False),
        pa.field("synthetic", pa.bool_(), nullable=False),
    ]
)

COHORT_COLUMNS = tuple(COHORT_SCHEMA.names)

# DVC pointer files carry exactly one ``md5:`` key under ``outs``. Matching it
# with a regex rather than a YAML parser is deliberate: this module ships
# inside the API image, and reading three lines of provenance has no business
# dragging a parser in behind it.
_DVC_MD5 = re.compile(r"^\s*-?\s*md5:\s*(\S+)\s*$", re.MULTILINE)


class CohortGenerationError(RuntimeError):
    """The cohort could not be generated from the inputs it was handed."""


@dataclass(frozen=True)
class CohortProvenance:
    """What a cohort parquet says about how it came to exist.

    Written into the parquet's key-value metadata by :func:`write_cohort` and
    asserted back by the loader. ``data_version`` in particular is load-bearing:
    a cohort generated against a different MovieLens release can name target
    items that are not in the model's vocabulary, and the resulting recall would
    be quietly wrong rather than loudly broken.
    """

    seed: int
    generator_version: str
    split_cutoff: int
    data_version: str
    buckets: tuple[int, ...]
    users_per_bucket: int
    fingerprint: str


def read_movielens_data_version(path: Path = MOVIELENS_DVC_PATH) -> str:
    """Return the MovieLens content hash recorded in the committed DVC pointer."""
    if not path.exists():
        raise CohortGenerationError(
            f"cannot resolve the MovieLens data version: {path} is missing. "
            "The DVC pointer is committed to the repository, so a checkout without "
            "it is a checkout the cohort cannot be trusted against."
        )
    matches = _DVC_MD5.findall(path.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise CohortGenerationError(
            f"expected exactly one md5 entry in {path}, found {len(matches)}"
        )
    return str(matches[0])


def item_popularity(train_ratings: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Catalog and interaction counts, ordered by ascending ``movieId``.

    The sort is what makes the catalog a function of the data rather than of
    pandas' grouping order, which is the difference between a cohort that
    regenerates identically and one that mostly does.
    """
    counts = train_ratings.groupby("movieId").size().sort_index()
    items = counts.index.to_numpy(dtype=np.int64)
    weights = counts.to_numpy(dtype=np.float64)
    return items, weights


def _weighted_sample_without_replacement(
    items: np.ndarray,
    weights: np.ndarray,
    size: int,
    rng: np.random.Generator,
) -> list[int]:
    """Draw ``size`` distinct items with probability proportional to ``weights``.

    Efraimidis–Spirakis: give item *i* the key ``log(uᵢ) / wᵢ`` for
    ``uᵢ ~ Uniform(0, 1]`` and take the largest keys. The returned order is the
    order the scheme would have drawn them in, which the caller relies on — the
    first element has exactly the marginal of a single popularity-weighted draw.

    The uniform block is always the full catalog width regardless of ``size``,
    which costs nothing at this scale and keeps the RNG stream aligned across
    buckets that ask for different numbers of items.
    """
    # (0, 1] rather than [0, 1): a uniform of exactly zero would give log(0).
    uniforms = 1.0 - rng.random(items.size)
    keys = np.log(uniforms) / weights
    if size == 0:
        return []
    # A stable sort so that the (measure-zero, but not impossible under float
    # rounding) case of two equal keys resolves by catalog position rather than
    # by whatever the sort implementation felt like.
    order = np.argsort(-keys, kind="stable")[:size]
    return [int(items[index]) for index in order]


def generate_cohort(
    train_ratings: pd.DataFrame,
    cutoff: int,
    *,
    seed: int = SYNTH_COLD_SEED,
    buckets: Sequence[int] = HISTORY_BUCKETS,
    users_per_bucket: int = USERS_PER_BUCKET,
) -> pd.DataFrame:
    """Build the cohort frame. Pure: same inputs, same rows, every time.

    Each user draws ``history_size + 1`` distinct items in one weighted
    sample-without-replacement pass. **The first draw is the target**, and the
    rest are the history. That ordering is not incidental: the first element of
    such a sample is distributed exactly as a single popularity-weighted draw
    no matter how many items follow it, so the target is equally hard to hit in
    every bucket. Taking the *last* element instead would hand bucket 10 a
    systematically less popular target than bucket 0 and quietly confound the
    per-bucket recall comparison the whole cohort exists to make.
    """
    if train_ratings.empty:
        raise CohortGenerationError("cannot build a cohort from an empty train slice")
    if not buckets:
        raise CohortGenerationError("cannot build a cohort with no history buckets")
    if len(set(buckets)) != len(buckets):
        raise CohortGenerationError(f"history buckets must be distinct: {list(buckets)}")
    if users_per_bucket > BUCKET_ID_STRIDE:
        # The bucket is encoded in the user id as base + size × stride, so a
        # bucket wider than the stride would mint ids belonging to the next one.
        raise CohortGenerationError(
            f"{users_per_bucket} users per bucket exceeds the {BUCKET_ID_STRIDE} id stride; "
            "widen BUCKET_ID_STRIDE before widening the cohort"
        )

    items, weights = item_popularity(train_ratings)
    largest_draw = max(buckets) + 1
    if items.size < largest_draw:
        raise CohortGenerationError(
            f"the training catalog has {items.size} items, fewer than the "
            f"{largest_draw} a bucket-{max(buckets)} user must draw"
        )

    rng = np.random.Generator(np.random.PCG64(seed))
    timestamp = cutoff - TIMESTAMP_OFFSET_SECONDS

    user_ids: list[int] = []
    movie_ids: list[int] = []
    history_sizes: list[int] = []
    roles: list[str] = []

    for history_size in buckets:
        for user_id in user_ids_for_bucket(history_size, count=users_per_bucket):
            drawn = _weighted_sample_without_replacement(items, weights, history_size + 1, rng)
            target, history = drawn[0], drawn[1:]
            for movie_id in history:
                user_ids.append(user_id)
                movie_ids.append(movie_id)
                history_sizes.append(history_size)
                roles.append(HISTORY_ROLE)
            user_ids.append(user_id)
            movie_ids.append(target)
            history_sizes.append(history_size)
            roles.append(TARGET_ROLE)

    return pd.DataFrame(
        {
            "userId": pd.array(user_ids, dtype="int64"),
            "movieId": pd.array(movie_ids, dtype="int64"),
            "rating": pd.array([SYNTH_COLD_RATING] * len(user_ids), dtype="float64"),
            # The target row carries the same timestamp as the history rows
            # because the cohort has exactly one anchor in time. It is not a
            # claim that the target happened before the cutoff — the target
            # never enters the training frame at all, since the loader is the
            # only path into training and it filters on ``role``.
            "timestamp": pd.array([timestamp] * len(user_ids), dtype="int64"),
            "history_size": pd.array(history_sizes, dtype="int32"),
            "role": pd.array(roles, dtype="object"),
            "tenant_id": pd.array([SYNTH_COLD_TENANT_ID] * len(user_ids), dtype="object"),
            "synthetic": pd.array([True] * len(user_ids), dtype="bool"),
        }
    )


def cohort_fingerprint(frame: pd.DataFrame) -> str:
    """A content hash of the cohort that does not depend on how it is stored.

    Parquet bytes are the stronger equality check but they move with the
    pyarrow version; this hash moves only with the rows. It is what a test pins
    and what the parquet's metadata carries, so "did the generator change?" and
    "did the writer change?" stay separate questions.
    """
    digest = hashlib.sha256()
    for row in frame.itertuples(index=False):
        digest.update(
            f"{int(row.userId)},{int(row.movieId)},{float(row.rating):.1f},"
            f"{int(row.timestamp)},{int(row.history_size)},{row.role}\n".encode()
        )
    return digest.hexdigest()


def build_provenance(
    frame: pd.DataFrame,
    *,
    cutoff: int,
    seed: int,
    buckets: Sequence[int],
    users_per_bucket: int,
    data_version: str,
) -> CohortProvenance:
    return CohortProvenance(
        seed=seed,
        generator_version=GENERATOR_VERSION,
        split_cutoff=cutoff,
        data_version=data_version,
        buckets=tuple(int(bucket) for bucket in buckets),
        users_per_bucket=users_per_bucket,
        fingerprint=cohort_fingerprint(frame),
    )


def provenance_metadata(provenance: CohortProvenance) -> dict[bytes, bytes]:
    return {
        META_SEED.encode(): str(provenance.seed).encode(),
        META_GENERATOR_VERSION.encode(): provenance.generator_version.encode(),
        META_SPLIT_CUTOFF.encode(): str(provenance.split_cutoff).encode(),
        META_DATA_VERSION.encode(): provenance.data_version.encode(),
        META_BUCKETS.encode(): json.dumps(list(provenance.buckets)).encode(),
        META_USERS_PER_BUCKET.encode(): str(provenance.users_per_bucket).encode(),
        META_FINGERPRINT.encode(): provenance.fingerprint.encode(),
    }


def write_cohort(frame: pd.DataFrame, path: Path, *, provenance: CohortProvenance) -> None:
    """Write the cohort parquet, byte-for-byte reproducibly.

    ``replace_schema_metadata`` drops the pandas-version-dependent blob
    ``Table.from_pandas`` attaches and leaves only the provenance, which is one
    fewer reason for two identical cohorts to produce two different files.
    """
    table = pa.Table.from_pandas(frame, schema=COHORT_SCHEMA, preserve_index=False)
    table = table.replace_schema_metadata(provenance_metadata(provenance))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="snappy", version="2.6")


def _load_train_split(ratings_csv: Path | None) -> tuple[pd.DataFrame, int]:
    """Read ratings and return (train slice, cutoff) per ADR 0001's split."""
    from src.data.split import temporal_split

    if ratings_csv is not None:
        logger.info("Reading ratings from %s ...", ratings_csv)
        ratings = pd.read_csv(ratings_csv)
    else:
        from sqlalchemy import create_engine

        from src.config import Settings
        from src.data.load import load_ratings

        settings = Settings()
        logger.info("Reading ratings from Postgres ...")
        ratings = load_ratings(create_engine(settings.database_url))

    logger.info("Loaded %s ratings", f"{len(ratings):,}")
    split = temporal_split(ratings)
    logger.info(
        "Train=%s Holdout=%s (cutoff=%d)",
        f"{len(split.train):,}",
        f"{len(split.holdout):,}",
        split.cutoff,
    )
    return split.train, split.cutoff


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=COHORT_PARQUET_PATH,
        help="where to write the cohort parquet (default: %(default)s)",
    )
    parser.add_argument(
        "--ratings-csv",
        type=Path,
        default=None,
        help=(
            "read ratings from this CSV instead of Postgres — the DVC-tracked "
            "data/raw/ml-25m/ratings.csv. Produces the identical cohort."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SYNTH_COLD_SEED,
        help="RNG seed (default: %(default)s, the value ADR 0011 pins)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    data_version = read_movielens_data_version()
    train, cutoff = _load_train_split(args.ratings_csv)

    logger.info("Generating cohort (seed=%d, buckets=%s) ...", args.seed, list(HISTORY_BUCKETS))
    frame = generate_cohort(train, cutoff, seed=args.seed)
    provenance = build_provenance(
        frame,
        cutoff=cutoff,
        seed=args.seed,
        buckets=HISTORY_BUCKETS,
        users_per_bucket=USERS_PER_BUCKET,
        data_version=data_version,
    )

    write_cohort(frame, args.out, provenance=provenance)
    logger.info(
        "Wrote %s: %d rows, %d users, %d history rows, fingerprint=%s, data_version=%s",
        args.out,
        len(frame),
        frame["userId"].nunique(),
        int((frame["role"] == HISTORY_ROLE).sum()),
        provenance.fingerprint,
        provenance.data_version,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
