"""create tenant-scoped offline feature tables

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE SCHEMA feature_store;
        CREATE TABLE feature_store.user_features (
            tenant_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            event_timestamp TIMESTAMPTZ NOT NULL,
            user_interaction_count BIGINT NOT NULL,
            user_days_active DOUBLE PRECISION NOT NULL,
            user_days_since_last_interaction DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (tenant_id, user_id, event_timestamp)
        );
        CREATE TABLE feature_store.item_features (
            tenant_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            event_timestamp TIMESTAMPTZ NOT NULL,
            item_popularity_all_time BIGINT NOT NULL,
            item_popularity_30d BIGINT NOT NULL,
            item_popularity_7d BIGINT NOT NULL,
            item_age_days DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (tenant_id, item_id, event_timestamp)
        );
        CREATE TABLE feature_store.user_item_features (
            tenant_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            event_timestamp TIMESTAMPTZ NOT NULL,
            user_genre_affinity DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (tenant_id, user_id, item_id, event_timestamp)
        );
        GRANT USAGE, CREATE ON SCHEMA feature_store TO admin_user;
        GRANT SELECT, INSERT ON feature_store.user_features TO admin_user;
        GRANT SELECT, INSERT ON feature_store.item_features TO admin_user;
        GRANT SELECT, INSERT ON feature_store.user_item_features TO admin_user;
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA feature_store CASCADE;")
