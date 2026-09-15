"""Produce the published popularity fill order for a SASRec bundle (W21).

``export_sasrec`` writes this file for every artifact exported from now on, but
the encoder a served bundle is built from was exported before the role existed
and its directory is immutable. This entrypoint fills that gap: it rebuilds the
exact training frame a run fitted on and writes the ordering beside that run's
encoder, create-only, without touching anything already there.

**Same semantics as the evaluation, or it is worthless.** The ordering is derived
from the frame the model was fitted on — ``temporal_split`` at ADR 0001's cutoff,
with the ADR 0011 cold-start cohort's history attached exactly as
``src/training/sasrec.py`` attaches it — and read under the same cold-start
threshold the protocol routes on. Serving a fill built from a different frame
than the one that earned the model its numbers would mean the fallback route
answers with an ordering nobody measured.

The row count and cutoff are asserted rather than logged and hoped for. A frame
that is the wrong size is the one failure mode that produces a plausible-looking
artifact: the ratings table is shared with the demo tenant's rows, and a moved
cutoff moves every count in the file.

Usage::

    OMP_NUM_THREADS=1 python -m src.training.export_popularity_order \\
        --output-dir artifacts/sasrec/<run-id> \\
        --expect-rows 25000095 --expect-cutoff 1466837397
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import pandas as pd

from src.config import Settings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD
from src.models.candidates.popularity import PopularityModel
from src.models.popularity_artifact import (
    POPULARITY_ARTIFACT_FILENAME,
    popularity_order_from_counts,
    write_popularity_order,
)
from src.training.twotower import INPUT_DIR_ENV_VAR, load_inputs
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)


def build_order(
    ratings: pd.DataFrame,
    *,
    expect_rows: int | None = None,
    expect_cutoff: int | None = None,
) -> tuple[int, ...]:
    """The fill order for a bundle fitted on this ratings frame."""
    logger.info("Ratings rows: %s", f"{len(ratings):,}")
    if expect_rows is not None and len(ratings) != expect_rows:
        raise ValueError(
            f"expected {expect_rows:,} ratings rows, read {len(ratings):,}. The frame this "
            "artifact must reproduce is the one the run fitted on; a different one silently "
            "changes every count in the ordering."
        )
    split = temporal_split(ratings)
    logger.info("Split cutoff: %d (holdout ends %d)", split.cutoff, split.holdout_end)
    if expect_cutoff is not None and split.cutoff != expect_cutoff:
        raise ValueError(f"expected split cutoff {expect_cutoff}, computed {split.cutoff}")
    train_frame, cohort = synth_cold.prepare(split, logger=logger)
    if cohort is None:
        # Not fatal — a checkout without the parquet is a supported state — but
        # the resulting bytes differ from a cohort-attached build, so it must be
        # visible in the run log rather than discovered by a checksum mismatch.
        logger.warning(
            "No ADR 0011 cohort on this machine; the ordering is built from split.train alone "
            "and will not match a cohort-attached build byte for byte."
        )
    model = PopularityModel().fit(train_frame)
    order = popularity_order_from_counts(model.counts)
    logger.info(
        "Popularity ordering: %s movies over %s training rows, cold-start threshold %s",
        f"{len(order):,}",
        f"{len(train_frame):,}",
        COLD_START_THRESHOLD,
    )
    return order


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="the artifact directory to write the ordering into, beside the encoder",
    )
    parser.add_argument("--expect-rows", type=int, default=None)
    parser.add_argument("--expect-cutoff", type=int, default=None)
    args = parser.parse_args()

    settings = Settings()
    input_dir_raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
    ratings, _movies = load_inputs(
        settings, input_dir=Path(input_dir_raw) if input_dir_raw else None
    )
    order = build_order(ratings, expect_rows=args.expect_rows, expect_cutoff=args.expect_cutoff)
    path = args.output_dir / POPULARITY_ARTIFACT_FILENAME
    digest = write_popularity_order(path, order)
    logger.info("Wrote %s sha256=%s", path, digest)
    print(f"{POPULARITY_ARTIFACT_FILENAME} sha256={digest} n_movies={len(order)}")


if __name__ == "__main__":
    main()
