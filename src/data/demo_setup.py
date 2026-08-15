"""Prepare the database schema required by the self-contained demo stack."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, create_engine

from alembic import command
from src.config import Settings
from src.data.schema import create_tables

logger = logging.getLogger(__name__)


def prepare_demo_database(
    engine: Engine,
    *,
    config_path: Path = Path("alembic.ini"),
    upgrade: Callable[[Config, str], None] = command.upgrade,
) -> None:
    """Create the ingest-owned base tables, then apply every migration."""
    create_tables(engine)
    upgrade(Config(str(config_path)), "head")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    engine = create_engine(settings.database_url, future=True)
    try:
        prepare_demo_database(engine)
    finally:
        engine.dispose()
    logger.info("Demo database schema is ready.")


if __name__ == "__main__":
    main()
