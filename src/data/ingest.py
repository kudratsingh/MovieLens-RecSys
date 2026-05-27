"""
Ingestion script for MovieLens 25M.

Loads the raw CSV files into Postgres once. After this runs,
Postgres is the source of truth — don't read from the CSVs downstream.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from src.config import Settings
from src.data.schema import create_tables, metadata

logger = logging.getLogger(__name__)


def ingest_movielens(raw_dir: Path, engine: Engine, *, reset: bool = False) -> None:
    """Read the four CSVs and bulk-insert into Postgres.

    With ``reset=True`` the target tables are dropped and recreated from the
    current schema definition before loading. That makes re-runs safe after a
    schema change (otherwise ``create_all`` is a no-op against the stale
    in-DB schema and inserts fail). Without ``reset``, rows are appended —
    re-running will produce duplicates.
    """
    if reset:
        metadata.drop_all(engine)
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
    """Indices that matter for the queries this system actually runs.

    The camelCase column names come from the MovieLens CSV headers; Postgres
    folds unquoted identifiers to lower-case, so we must quote them explicitly
    here to match what SQLAlchemy created.
    """
    statements = [
        'CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings("userId")',
        'CREATE INDEX IF NOT EXISTS idx_ratings_movie ON ratings("movieId")',
        "CREATE INDEX IF NOT EXISTS idx_ratings_ts ON ratings(timestamp)",
        'CREATE INDEX IF NOT EXISTS idx_tags_user ON tags("userId")',
        'CREATE INDEX IF NOT EXISTS idx_tags_movie ON tags("movieId")',
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest MovieLens 25M CSVs into Postgres.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "DROP and recreate ratings/movies/tags/links before inserting. "
            "Use on re-runs and after any schema change. Destructive: wipes "
            "all existing data in those four tables."
        ),
    )
    args = parser.parse_args()

    settings = Settings()
    engine = create_engine(settings.database_url)
    raw_dir = settings.raw_data_dir / "ml-25m"
    ingest_movielens(raw_dir, engine, reset=args.reset)


if __name__ == "__main__":
    main()
