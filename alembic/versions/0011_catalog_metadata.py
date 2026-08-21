"""create the persisted movie catalog metadata read model

Revision ID: 0011_catalog_metadata
Revises: 0010

This table is deliberately shared rather than tenant scoped. Movie metadata is
the same for every tenant; tenant-owned rating and product state remain behind
RLS and are overlaid by request-scoped queries.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_catalog_metadata"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "movie_catalog_metadata",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("sort_title", sa.Text, nullable=False),
        sa.Column("release_year", sa.SmallInteger, nullable=True),
        sa.Column("poster_url", sa.Text, nullable=True),
        sa.Column("overview", sa.Text, nullable=True),
        sa.Column("metadata_source", sa.Text, nullable=False),
        sa.Column("source_status", sa.Text, nullable=False),
        sa.Column("visible", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "source_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "release_year IS NULL OR release_year BETWEEN 1878 AND 2100",
            name="ck_catalog_release_year",
        ),
        sa.CheckConstraint(
            "metadata_source IN ('reviewed-fixture', 'tmdb-snapshot', 'movielens')",
            name="ck_catalog_metadata_source",
        ),
        sa.CheckConstraint(
            "source_status IN ('complete', 'partial', 'unavailable')",
            name="ck_catalog_source_status",
        ),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.movieId"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id"),
    )
    op.create_index(
        "idx_catalog_visible_title_id",
        "movie_catalog_metadata",
        ["visible", "sort_title", "movie_id"],
    )
    op.create_index(
        "idx_catalog_visible_year_id",
        "movie_catalog_metadata",
        ["visible", sa.text("release_year DESC"), "movie_id"],
    )
    op.execute("GRANT SELECT ON movie_catalog_metadata TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON movie_catalog_metadata TO admin_user;")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON movie_catalog_metadata FROM admin_user;")
    op.execute("REVOKE SELECT ON movie_catalog_metadata FROM app_user;")
    op.drop_index("idx_catalog_visible_year_id", table_name="movie_catalog_metadata")
    op.drop_index("idx_catalog_visible_title_id", table_name="movie_catalog_metadata")
    op.drop_table("movie_catalog_metadata")
