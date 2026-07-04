"""add tenant_id column to ratings and tags

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-04

ADR 0008 pins ``tenant_id TEXT NOT NULL`` on every tenant-scoped
table. In the current schema (see src/data/schema.py) that's
``ratings`` and ``tags`` — both hold per-user interactions. ``movies``
and ``links`` are shared catalog: a movie is a movie regardless of
tenant, so they stay in public without a tenant_id column and without
RLS. The tenant registry (``public.tenants``) itself is cross-tenant
by definition and also has no RLS.

The migration path:
  1. Add ``tenant_id`` column nullable (safe on a live table).
  2. Backfill every existing row to ``'default'`` (single UPDATE).
  3. Add the foreign key to ``public.tenants(id)``.
  4. Set NOT NULL.
  5. Add a composite index on ``(tenant_id, userId)`` — the shape RLS
     policies scan by, and the shape most application queries will
     filter by.

RLS is enabled in the next migration (0004). This one just gets the
column into place.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_SCOPED_TABLES = ("ratings", "tags")


def upgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.add_column(
            table,
            sa.Column("tenant_id", sa.Text, nullable=True),
        )
        op.execute(f"UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL;")
        op.create_foreign_key(
            f"fk_{table}_tenant_id",
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            source_schema=None,
            referent_schema="public",
        )
        op.alter_column(table, "tenant_id", nullable=False)
        op.create_index(
            f"idx_{table}_tenant_user",
            table,
            ["tenant_id", "userId"],
        )

    # app_user / admin_user need SELECT on the scoped tables to serve
    # reads; admin_user needs INSERT / UPDATE / DELETE for materialization.
    op.execute("GRANT SELECT ON ratings, tags TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ratings, tags TO admin_user;")

    # Catalog tables — no tenant_id, no RLS. Both roles read.
    op.execute("GRANT SELECT ON movies, links TO app_user, admin_user;")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON movies, links FROM app_user, admin_user;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON ratings, tags FROM admin_user;")
    op.execute("REVOKE SELECT ON ratings, tags FROM app_user;")
    for table in _TENANT_SCOPED_TABLES:
        op.drop_index(f"idx_{table}_tenant_user", table_name=table)
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
