"""create public.tenants registry and seed the default + demo tenants

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-04

`public.tenants` is the tenant registry — one row per tenant. The
``id`` column is the Keycloak realm slug (ADR 0007's identifier); it's
what the RLS session variable ``app.tenant_id`` will match against on
tenant-scoped tables (ADR 0008).

Seeds two tenants:
  * ``default`` — the MovieLens data tenant. All existing rows will be
    backfilled to this tenant in migration 0003.
  * ``demo`` — used for portfolio walkthroughs (see CLAUDE.md's
    frontend scope).

Two more tenants (``synth_load`` for the k6 harness per ADR 0010,
``synth_cold`` for the cold-start cohort per ADR 0011) are added in
later migrations bundled with the code that consumes them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="public",
    )

    op.execute(
        """
        INSERT INTO public.tenants (id, display_name)
        VALUES
            ('default', 'MovieLens default tenant'),
            ('demo', 'Portfolio walkthrough demo tenant')
        ON CONFLICT (id) DO NOTHING;
        """
    )

    # Everyone reads the tenant registry; only migrator writes.
    op.execute("GRANT SELECT ON public.tenants TO app_user, admin_user;")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON public.tenants FROM app_user, admin_user;")
    op.drop_table("tenants", schema="public")
