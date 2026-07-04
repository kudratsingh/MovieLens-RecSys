"""enable row-level security on ratings and tags

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-04

The ADR 0008 RLS policy: every SELECT/INSERT/UPDATE/DELETE on a
tenant-scoped table is filtered by
``tenant_id = current_setting('app.tenant_id', true)``.

Notes on the shape:

  * ``FORCE ROW LEVEL SECURITY`` — the default is that table owners are
    exempt from RLS. Force flips that so even the owner (``migrator``)
    is subject to the policy unless they've been granted BYPASSRLS
    (which ``admin_user`` and ``migrator`` have). This is belt-and-
    suspenders — we don't want a future migration silently gaining
    owner-privileged access to cross-tenant data.
  * ``current_setting(..., true)`` — the ``true`` second arg makes it
    return ``NULL`` when the setting isn't set, rather than erroring.
    Combined with ``tenant_id = NULL`` evaluating to ``NULL`` (not
    ``TRUE``), a request that reaches the DB without ``app.tenant_id``
    set gets zero rows. Loud on write (WITH CHECK fails), silent on
    read — the middleware pattern in ADR 0008 §rationale-5 is what
    converts silent-on-read into caught-by-integration-test.
  * The policy applies to ``PUBLIC`` — every role that isn't
    BYPASSRLS. ``admin_user`` and ``migrator`` skip it because they
    have BYPASSRLS; only ``app_user`` (and any future non-BYPASSRLS
    role) sees the policy in action.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_SCOPED_TABLES = ("ratings", "tags")


def upgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                FOR ALL
                TO PUBLIC
                USING (tenant_id = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            """
        )


def downgrade() -> None:
    for table in _TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
