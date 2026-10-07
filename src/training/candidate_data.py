"""Lightweight data seams shared by candidate-model training entrypoints."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import TemporalSplit, sealed_test_boundary, temporal_cutoff, temporal_split

logger = logging.getLogger(__name__)

PHASE_2_EXPERIMENT = "phase-2-candidates"
INPUT_DIR_ENV_VAR = "TWOTOWER_INPUT_DIR"
SAMPLE_FRACTION_ENV_VAR = "TWOTOWER_USER_SAMPLE_FRACTION"


def subsample_users(
    ratings: pd.DataFrame,
    fraction: float,
    seed: int,
) -> pd.DataFrame:
    """Keep every interaction of a deterministic random subset of users."""
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"{SAMPLE_FRACTION_ENV_VAR} must be in (0, 1], got {fraction}")
    if fraction == 1.0:
        return ratings

    user_ids = np.sort(ratings["userId"].unique())
    n_keep = max(1, int(round(len(user_ids) * fraction)))
    rng = np.random.default_rng(seed)
    keep = rng.choice(user_ids, size=n_keep, replace=False)
    return ratings[ratings["userId"].isin(set(keep.tolist()))].reset_index(drop=True)


class SealedPartitionError(RuntimeError):
    """A run would fit on or score a rating at or after the sealed-test boundary."""


def sample_and_split(
    ratings: pd.DataFrame, *, sample_fraction: float, sample_seed: int
) -> tuple[pd.DataFrame, TemporalSplit]:
    """Subsample users, then split at the *full* frame's boundaries (O-25).

    A subsample that computed its own 80th-percentile cutoff put the 6% pilot's
    whole holdout inside the sealed window. Every trainer that can subsample
    cuts here instead, so its train and holdout end where the full split's do.
    On the full frame (``sample_fraction == 1.0``) this is ``temporal_split``,
    exactly as before. ``sealed_partition_params`` still checks the rows.
    """
    if sample_fraction == 1.0:
        return ratings, temporal_split(ratings)
    sample = subsample_users(ratings, sample_fraction, sample_seed)
    return sample, temporal_split(sample, cutoff=temporal_cutoff(ratings))


def sealed_partition_params(
    full_ratings: pd.DataFrame,
    split: TemporalSplit,
    fitted_frame: pd.DataFrame,
) -> dict[str, int]:
    """Refuse a run that would touch the sealed partition; return what it did touch.

    The boundary is derived from the full frame, before any user subsample, so a
    pilot is held to the same sealed window as a full run. Before O-25 a
    subsample computed its own 80th-percentile cutoff, and the 6% pilot's landed
    past the full split's ``holdout_end``. ``sample_and_split`` (and SASRec's
    ``run_once``) now cut a subsample at the full frame's boundaries; this check
    stays as the backstop, on the rows themselves. The returned timestamps fill
    the experiment record's partition declaration.
    """
    boundary = sealed_test_boundary(full_ratings)
    latest_fit = int(fitted_frame["timestamp"].max()) if not fitted_frame.empty else 0
    latest_scored = int(split.holdout["timestamp"].max()) if not split.holdout.empty else 0
    if latest_fit >= boundary or latest_scored >= boundary:
        raise SealedPartitionError(
            f"run would read the sealed partition: boundary {boundary}, latest fitted "
            f"timestamp {latest_fit}, latest scored timestamp {latest_scored}"
        )
    return {
        "sealed_boundary_timestamp": boundary,
        "holdout_end_timestamp": split.holdout_end,
        "latest_fit_timestamp": latest_fit,
        "latest_scored_timestamp": latest_scored,
    }


def load_inputs(
    settings: Settings, input_dir: Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the pinned CSV snapshot or the default-tenant database frames."""
    if input_dir is not None:
        logger.info("Loading ratings and movie metadata from CSV files in %s ...", input_dir)
        ratings = pd.read_csv(
            input_dir / "ratings.csv",
            usecols=["userId", "movieId", "rating", "timestamp"],
        )
        movies = pd.read_csv(
            input_dir / "movies.csv",
            usecols=["movieId", "title", "genres"],
        )
        logger.info("Loaded %s ratings and %s movies", f"{len(ratings):,}", f"{len(movies):,}")
        return ratings, movies

    logger.info("Loading ratings and movie metadata from Postgres ...")
    engine = create_engine(settings.database_url)
    try:
        ratings = load_ratings(engine)
        movies = pd.read_sql('SELECT "movieId", title, genres FROM movies', engine)
    finally:
        engine.dispose()
    logger.info("Loaded %s ratings and %s movies", f"{len(ratings):,}", f"{len(movies):,}")
    return ratings, movies
