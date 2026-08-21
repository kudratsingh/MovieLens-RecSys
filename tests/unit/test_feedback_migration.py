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
