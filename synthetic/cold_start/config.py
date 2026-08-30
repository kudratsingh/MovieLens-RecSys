"""Fixed parameters of the ADR 0011 synthetic cold-start cohort.

Every value here is part of the cohort's identity. Change one and the parquet
at :data:`COHORT_PARQUET_PATH` describes a different cohort, so the numbers it
produces stop being comparable to the ones already sitting in MLflow. That is
why they live in one module instead of as defaults scattered across the
generator, the loader and four trainers: the generator writes them into the
parquet's provenance metadata and the loader asserts them back.
"""

from __future__ import annotations

from pathlib import Path

# ADR 0011 pins the seed. It seeds a numpy PCG64 bit generator directly —
# see generator.py for why the sampling avoids ``Generator.choice``.
SYNTH_COLD_SEED = 42

# History sizes the cohort covers. 0/1/3 sit below ADR 0001's
# COLD_START_THRESHOLD (fallback territory); 10 sits at or above it. The bucket
# above the boundary is deliberately close to it: the failure mode worth
# catching is a routing bug *at* the threshold, and a bucket at 50 would sail
# past one. Since the owner moved the threshold to 10 on 2026-08-30, h10 sits
# exactly *on* the boundary, which is the sharpest version of that test — the
# expected fallback counts are unchanged at 500/500/500/0 because
# `expected_fallback_served` derives them from the constant rather than
# restating them (`synthetic/cold_start/harness.py`).
HISTORY_BUCKETS: tuple[int, ...] = (0, 1, 3, 10)

# 500 users per bucket. At a target rate around 0.15 the standard error is
# ≈0.016, which resolves a 5% recall difference between buckets — ADR 0011
# rationale #4, where 100 is too loose and 1 000 buys a decimal nobody spends.
USERS_PER_BUCKET = 500

COHORT_SIZE = USERS_PER_BUCKET * len(HISTORY_BUCKETS)

# History rows the cohort adds to the training frame: (0+1+3+10) × 500 = 7 000
# against MovieLens's ~20 M train rows, i.e. 0.035%. Small enough that the real
# warm/cold metrics cannot move; the synthetic users are also absent from
# holdout, so they never enter the warm/cold slices at all.
COHORT_HISTORY_ROWS = USERS_PER_BUCKET * sum(HISTORY_BUCKETS)

# Every cohort row is stamped 24 hours before the split cutoff. Uniform
# timestamps hold recency constant across the cohort so history *size* is the
# only thing that varies, which is what makes a per-bucket comparison mean
# anything (ADR 0011 rationale #6).
TIMESTAMP_OFFSET_SECONDS = 86_400

# ADR 0008's isolation boundary. Synthetic cold users are never interleaved
# with real MovieLens users, so no downstream analysis has to remember to
# filter them out — the tenant does it.
SYNTH_COLD_TENANT_ID = "synth_cold"

# Synthetic user ids are carved out of a range nothing else can reach:
# MovieLens 25M numbers its users 1..162 541 and the demo personas sit at
# 900000101..900000104. The bucket is encoded in the id as
# ``base + history_size × 100 000 + index``, so a user id read out of a log or
# an MLflow artifact says which bucket it belongs to without a lookup. The
# stride is two orders of magnitude larger than USERS_PER_BUCKET, so buckets
# cannot run into each other however far apart their history sizes are.
SYNTH_COLD_USER_ID_BASE = 950_000_000
BUCKET_ID_STRIDE = 100_000

# ADR 0002 binarizes every rating into a positive interaction and drops the
# numeric value before it reaches a model, so this number never changes a
# prediction — a synthetic history row counts exactly as much as a real one.
# It still has to be *a* value, because these rows are concatenated onto the
# MovieLens train frame and have to carry its schema. 4.0 is MovieLens's modal
# rating, which also keeps a synthetic row unremarkable in the one place the
# value does surface: the frontend's star display (ADR 0012).
SYNTH_COLD_RATING = 4.0

# Row roles. The loader appends HISTORY_ROLE rows to train and hands
# TARGET_ROLE rows to the eval harness; nothing may append both.
HISTORY_ROLE = "history"
TARGET_ROLE = "target"

# Bumped when the generation *algorithm* changes in a way that produces a
# different cohort from the same seed and dataset. The parquet path's ``v1``
# is the cohort version a run refers to; this is what tells a loader that a
# file bearing that path was written by a generator it does not understand.
GENERATOR_VERSION = "1"

COHORT_VERSION = "v1"
COHORT_PARQUET_PATH = Path("data") / "synthetic" / "cold_start" / COHORT_VERSION / "users.parquet"

# The DVC pointer for MovieLens 25M. Its md5 is the dataset version the cohort
# is generated against; the loader refuses a cohort built against a different
# one, because the target items in a stale parquet may not exist in the model's
# item vocabulary (ADR 0011, Risks).
MOVIELENS_DVC_PATH = Path("data") / "raw" / "ml-25m.dvc"

# Parquet key-value metadata keys carrying the cohort's provenance.
META_SEED = "synth_cold_seed"
META_GENERATOR_VERSION = "synth_cold_generator_version"
META_SPLIT_CUTOFF = "synth_cold_split_cutoff"
META_DATA_VERSION = "synth_cold_data_version"
META_BUCKETS = "synth_cold_buckets"
META_USERS_PER_BUCKET = "synth_cold_users_per_bucket"
META_FINGERPRINT = "synth_cold_fingerprint"


def user_ids_for_bucket(history_size: int, *, count: int = USERS_PER_BUCKET) -> list[int]:
    """The user ids belonging to one history bucket, in generation order."""
    start = SYNTH_COLD_USER_ID_BASE + history_size * BUCKET_ID_STRIDE
    return list(range(start, start + count))
