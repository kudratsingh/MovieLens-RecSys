from __future__ import annotations

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from src.data.demo_setup import prepare_demo_database


def test_prepare_demo_database_creates_base_tables_before_migrations() -> None:
    engine = create_engine("sqlite://")
    observed_tables: set[str] = set()
    observed_revision = ""

    def upgrade(config: Config, revision: str) -> None:
        nonlocal observed_tables, observed_revision
        observed_tables = set(inspect(engine).get_table_names())
        observed_revision = revision
        assert config.config_file_name == "alembic.ini"

    prepare_demo_database(engine, upgrade=upgrade)

    assert observed_tables == {"links", "movies", "ratings", "tags"}
    assert observed_revision == "head"
    engine.dispose()
