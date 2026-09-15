"""Lightweight data seams shared by candidate-model training entrypoints."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from src.config import Settings
from src.data.load import load_ratings

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
