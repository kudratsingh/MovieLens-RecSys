from __future__ import annotations

from pathlib import Path


def test_catalog_migration_is_the_linear_head_after_feedback() -> None:
    source = Path("alembic/versions/0010_create_user_movie_state.py").read_text()
    catalog_source = Path("alembic/versions/0011_catalog_metadata.py").read_text()

    assert 'revision: str = "0010"' in source
    assert 'down_revision: str | None = "0009"' in source
    assert 'revision: str = "0011_catalog_metadata"' in catalog_source
    assert 'down_revision: str | None = "0010"' in catalog_source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "def upgrade() -> None:" in catalog_source
    assert "def downgrade() -> None:" in catalog_source


def test_backfill_reads_but_never_rewrites_raw_ratings() -> None:
    source = Path("alembic/versions/0010_create_user_movie_state.py").read_text()
    normalized = " ".join(source.upper().split())

    assert "FROM RATINGS" in normalized
    assert "UPDATE RATINGS" not in normalized
    assert "DELETE FROM RATINGS" not in normalized
    assert 'DISTINCT ON (TENANT_ID, "USERID", "MOVIEID")' in normalized
    assert "RATING_IMPORTED" in normalized


def test_migration_pins_constraints_rls_least_privilege_and_indexes() -> None:
    source = Path("alembic/versions/0010_create_user_movie_state.py").read_text()

    for required in (
        "ck_user_movie_state_rating",
        "ck_user_movie_state_rating_implies_watched",
        "ck_user_movie_state_watchlist_not_dismissed",
        'PrimaryKeyConstraint("tenant_id", "user_id", "movie_id")',
        "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        "current_setting('app.tenant_id', true)",
        "GRANT SELECT, INSERT ON user_feedback_events",
        "idx_user_movie_state_rated",
        "idx_user_movie_state_history",
        "idx_user_movie_state_watchlist",
        "idx_user_feedback_events_tenant_user_created",
    ):
        assert required in source


def test_details_migration_is_the_linear_head_and_adds_one_nullable_column() -> None:
    """0013 is additive: one nullable JSONB column on the shared snapshot.

    Nullable is the whole compatibility story — a database that has not been
    re-seeded since the enrichment ran serves detail pages exactly as it did
    before, and no existing row has to be rewritten for the migration to apply.
    """
    source = Path("alembic/versions/0013_catalog_details.py").read_text()

    assert 'revision: str = "0013_catalog_details"' in source
    assert 'down_revision: str | None = "0012_audit_serving_inputs"' in source
    assert 'op.add_column(\n        "movie_catalog_metadata",' in source
    assert "postgresql.JSONB(astext_type=sa.Text()), nullable=True" in source
    assert 'op.drop_column("movie_catalog_metadata", "details")' in source
    # The shared snapshot stays read-only for the application role.
    assert "GRANT SELECT ON movie_catalog_metadata TO app_user;" in source
    normalized = " ".join(source.upper().split())
    assert "ENABLE ROW LEVEL SECURITY" not in normalized, "0011 is shared by design"
    assert "UPDATE MOVIE_CATALOG_METADATA" not in normalized
