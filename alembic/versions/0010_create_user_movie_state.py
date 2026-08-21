"""create durable user movie state and append-only feedback events

Revision ID: 0010
Revises: 0009

The imported ``ratings`` table remains an immutable source-data boundary.  This
migration creates the live product projection, backfills its latest rating per
tenant/user/movie, and records that provenance without updating or deleting a
single legacy rating row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_movie_state",
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rating", sa.Numeric(2, 1), nullable=True),
        sa.Column("rating_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watchlisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_version", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating BETWEEN 0.5 AND 5.0 AND mod(rating * 2, 1) = 0)",
            name="ck_user_movie_state_rating",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (watched_at IS NOT NULL AND rating_updated_at IS NOT NULL)",
            name="ck_user_movie_state_rating_implies_watched",
        ),
        sa.CheckConstraint(
            "NOT (watchlisted_at IS NOT NULL AND dismissed_at IS NOT NULL)",
            name="ck_user_movie_state_watchlist_not_dismissed",
        ),
        sa.CheckConstraint("state_version >= 0", name="ck_user_movie_state_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.movieId"]),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", "movie_id"),
    )
    op.create_index(
        "idx_user_movie_state_tenant_user_updated",
        "user_movie_state",
        ["tenant_id", "user_id", sa.text("updated_at DESC"), "movie_id"],
    )
    op.create_index(
        "idx_user_movie_state_rated",
        "user_movie_state",
        ["tenant_id", "user_id", sa.text("rating_updated_at DESC"), "movie_id"],
        postgresql_where=sa.text("rating IS NOT NULL"),
    )
    op.create_index(
        "idx_user_movie_state_history",
        "user_movie_state",
        ["tenant_id", "user_id", sa.text("watched_at DESC"), "movie_id"],
        postgresql_where=sa.text("watched_at IS NOT NULL"),
    )
    op.create_index(
        "idx_user_movie_state_watchlist",
        "user_movie_state",
        ["tenant_id", "user_id", sa.text("watchlisted_at DESC"), "movie_id"],
        postgresql_where=sa.text("watchlisted_at IS NOT NULL"),
    )
    op.create_index(
        "idx_user_movie_state_dismissed",
        "user_movie_state",
        ["tenant_id", "user_id", "movie_id"],
        postgresql_where=sa.text("dismissed_at IS NOT NULL"),
    )

    op.create_table(
        "user_feedback_events",
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_version", sa.BigInteger, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "action IN ('rating_imported', 'watched_set', 'history_removed', "
            "'rating_set', 'rating_deleted', 'watchlist_set', 'watchlist_deleted', "
            "'dismissal_set', 'dismissal_deleted')",
            name="ck_user_feedback_events_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('backfilled', 'changed', 'no_change')",
            name="ck_user_feedback_events_outcome",
        ),
        sa.CheckConstraint("state_version >= 0", name="ck_user_feedback_events_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.movieId"]),
        sa.PrimaryKeyConstraint("tenant_id", "event_id"),
    )
    op.create_index(
        "idx_user_feedback_events_tenant_user_created",
        "user_feedback_events",
        ["tenant_id", "user_id", sa.text("created_at DESC"), sa.text("event_id DESC")],
    )
    op.create_index(
        "idx_user_feedback_events_tenant_movie_created",
        "user_feedback_events",
        ["tenant_id", "movie_id", sa.text("created_at DESC")],
    )

    # Latest source rating wins deterministically.  The raw table is read only:
    # duplicate imports and their original timestamps remain available for
    # reproducible offline training and source-data audits.
    op.execute("""
        INSERT INTO user_movie_state (
            tenant_id, user_id, movie_id, watched_at, rating,
            rating_updated_at, state_version, updated_at
        )
        SELECT DISTINCT ON (tenant_id, "userId", "movieId")
            tenant_id,
            "userId",
            "movieId",
            to_timestamp(timestamp),
            rating,
            to_timestamp(timestamp),
            1,
            to_timestamp(timestamp)
        FROM ratings
        ORDER BY tenant_id, "userId", "movieId", timestamp DESC, rating DESC
        ON CONFLICT (tenant_id, user_id, movie_id) DO NOTHING;
    """)
    op.execute("""
        INSERT INTO user_feedback_events (
            tenant_id, event_id, actor_user_id, user_id, movie_id, action,
            old_value, new_value, state_version, outcome, created_at
        )
        SELECT
            tenant_id,
            md5('0010|' || tenant_id || '|' || user_id || '|' || movie_id)::uuid,
            'migration:0010',
            user_id,
            movie_id,
            'rating_imported',
            NULL,
            jsonb_build_object(
                'rating', rating,
                'watched_at', watched_at,
                'rating_updated_at', rating_updated_at
            ),
            state_version,
            'backfilled',
            updated_at
        FROM user_movie_state
        WHERE rating IS NOT NULL
        ON CONFLICT (tenant_id, event_id) DO NOTHING;
    """)

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_movie_state TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_movie_state TO admin_user;")
    # Feedback events are append-only for both runtime roles.  The migration
    # owner remains responsible for retention or legal erasure workflows.
    op.execute("GRANT SELECT, INSERT ON user_feedback_events TO app_user, admin_user;")
    for table in ("user_movie_state", "user_feedback_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                FOR ALL
                TO PUBLIC
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            """)


def downgrade() -> None:
    for table in ("user_feedback_events", "user_movie_state"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE SELECT, INSERT ON user_feedback_events FROM app_user, admin_user;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON user_movie_state FROM admin_user;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON user_movie_state FROM app_user;")
    op.drop_index(
        "idx_user_feedback_events_tenant_movie_created",
        table_name="user_feedback_events",
    )
    op.drop_index(
        "idx_user_feedback_events_tenant_user_created",
        table_name="user_feedback_events",
    )
    op.drop_table("user_feedback_events")
    op.drop_index("idx_user_movie_state_dismissed", table_name="user_movie_state")
    op.drop_index("idx_user_movie_state_watchlist", table_name="user_movie_state")
    op.drop_index("idx_user_movie_state_history", table_name="user_movie_state")
    op.drop_index("idx_user_movie_state_rated", table_name="user_movie_state")
    op.drop_index("idx_user_movie_state_tenant_user_updated", table_name="user_movie_state")
    op.drop_table("user_movie_state")
