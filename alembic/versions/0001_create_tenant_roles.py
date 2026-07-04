"""create tenant roles (app_user, admin_user, migrator)

Revision ID: 0001
Revises:
Create Date: 2026-07-04

The three DB roles ADR 0008 pins:

  * ``app_user`` — role FastAPI connects as. RLS applies. NOT BYPASSRLS.
  * ``admin_user`` — role Prefect DAGs and offline scripts use.
    BYPASSRLS so cross-tenant materialization / analytics works.
  * ``migrator`` — role Alembic runs as when applying schema changes.
    BYPASSRLS during migration (so RLS-enable statements don't cage the
    migration itself).

Roles are created idempotently — safe to re-run against a DB that
already has them. Grants are minimal and expanded per-table in later
migrations. Passwords are trivial dev values here; the compose stack is
not for production use as-is (see ADR 0007's dev-bypass discussion).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user WITH LOGIN PASSWORD 'app_user';
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_user') THEN
                CREATE ROLE admin_user WITH LOGIN PASSWORD 'admin_user' BYPASSRLS;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
                CREATE ROLE migrator WITH LOGIN PASSWORD 'migrator' BYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    # Grant connect + usage on the movielens database and public schema
    # to all three roles. Table-level grants are added per-table in the
    # migrations that create/alter those tables.
    op.execute("GRANT CONNECT ON DATABASE movielens TO app_user, admin_user, migrator;")
    op.execute("GRANT USAGE ON SCHEMA public TO app_user, admin_user, migrator;")


def downgrade() -> None:
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user, admin_user, migrator;")
    op.execute("REVOKE CONNECT ON DATABASE movielens FROM app_user, admin_user, migrator;")
    op.execute("DROP ROLE IF EXISTS app_user;")
    op.execute("DROP ROLE IF EXISTS admin_user;")
    op.execute("DROP ROLE IF EXISTS migrator;")
