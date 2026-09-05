"""Migration 0018 creates the normalised TMDB catalog tables.

The single-linear-head assertion lives once, in ``test_audit_migration.py``;
this file checks what is specific to these tables — that they are *not* under
row-level security (they are catalog data, not tenant data), that the six
as-of-pull columns carry a comment saying so in the database itself, and that
the migration and the SQLAlchemy definitions have not drifted apart.
"""

from __future__ import annotations

from pathlib import Path

from src.data.tmdb_schema import NOT_POINT_IN_TIME_SAFE_COLUMNS, tmdb_metadata

_MIGRATION = Path("alembic/versions/0018_tmdb_catalog.py")


def test_the_migration_follows_the_request_audit_log() -> None:
    source = _MIGRATION.read_text()

    assert 'revision: str = "0018_tmdb_catalog"' in source
    assert 'down_revision: str | None = "0017_request_audits"' in source


def test_the_migration_creates_the_same_tables_the_loader_writes() -> None:
    source = _MIGRATION.read_text()

    for table in tmdb_metadata.tables:
        assert f'op.create_table(\n        "{table}"' in source, f"{table} is not in 0018"
        assert f'op.drop_table("{table}")' in source, f"{table} is never dropped by 0018"


def test_no_table_here_is_under_row_level_security() -> None:
    """Catalog data is shared by design, the way 0011's metadata table is.

    Asserted rather than assumed because the reflex in this repo is the other
    way round — every tenant-scoped table since 0004 forces RLS — and a reader
    skimming 0018 should be able to see that the omission was a decision.
    """
    source = _MIGRATION.read_text()

    assert "ROW LEVEL SECURITY" not in source.upper()
    assert "CREATE POLICY" not in source.upper()
    # No column named tenant_id anywhere — the docstring may discuss the word,
    # but a `sa.Column("tenant_id", ...)` would mean these tables were meant to
    # be tenant-scoped and simply forgot their policy.
    assert 'sa.Column("tenant_id"' not in source
    # And it says why, in the docstring rather than only in a review comment.
    assert "No row-level security" in source


def test_every_as_of_pull_column_is_commented_in_the_database() -> None:
    source = _MIGRATION.read_text()

    assert "not point-in-time safe" in source
    for column in NOT_POINT_IN_TIME_SAFE_COLUMNS:
        assert f'sa.Column("{column}"' in source, f"{column} is missing from tmdb_movies"
    assert "COMMENT ON COLUMN tmdb_movies." in source


def test_the_grants_match_the_two_roles_the_project_uses() -> None:
    source = _MIGRATION.read_text()

    assert "GRANT SELECT ON {table} TO app_user;" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO admin_user;" in source


def test_the_movie_id_is_the_key_because_tmdb_ids_repeat() -> None:
    """34 TMDB ids are claimed by two MovieLens movies; the key has to allow it."""
    source = _MIGRATION.read_text()

    assert 'sa.PrimaryKeyConstraint("movie_id", name="pk_tmdb_movies")' in source
    assert 'op.create_index("idx_tmdb_movies_tmdb_id", "tmdb_movies", ["tmdb_id"])' in source
    assert "sa.UniqueConstraint" not in source
