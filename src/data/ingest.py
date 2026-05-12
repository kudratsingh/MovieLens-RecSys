"""
Ingestion script for MovieLens 25M.

Loads the raw CSV files into Postgres once. After this runs,
Postgres is the source of truth — don't read from the CSVs downstream.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from src.data.schema import create_tables

logger = logging.getLogger(__name__)


def ingest_movielens(raw_dir: Path, engine: Engine) -> None:
    """Read the four CSVs and bulk-insert into Postgres.

    This is a one-shot script meant to run against a fresh database.
    Tables are created if they don't exist. Running it twice will insert
    duplicates — truncate the tables first if you need to re-ingest.
    """
    create_tables(engine)

    files = {
        "ratings": raw_dir / "ratings.csv",
        "movies": raw_dir / "movies.csv",
        "tags": raw_dir / "tags.csv",
        "links": raw_dir / "links.csv",
    }

    for table, path in files.items():
        if not path.exists():
            raise FileNotFoundError(f"Expected {path} — run the download step first.")
        logger.info("Ingesting %s ...", table)
        df = pd.read_csv(path)
        df.to_sql(table, engine, if_exists="append", index=False, method="multi", chunksize=10_000)
        logger.info("  inserted %d rows into %s", len(df), table)

    _create_indices(engine)
    logger.info("Ingestion complete.")


def _create_indices(engine: Engine) -> None:
    """Indices that matter for the queries this system actually runs."""
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(userId)",
        "CREATE INDEX IF NOT EXISTS idx_ratings_movie ON ratings(movieId)",
        "CREATE INDEX IF NOT EXISTS idx_ratings_ts ON ratings(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_tags_user ON tags(userId)",
        "CREATE INDEX IF NOT EXISTS idx_tags_movie ON tags(movieId)",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
