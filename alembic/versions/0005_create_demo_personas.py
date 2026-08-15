"""create tenant-scoped demo persona registry

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14

The registry gives synthetic users a durable identity independent of ratings.
That distinction is required for cold-start personas, which intentionally have
no interactions.  Persona rows are tenant-scoped and protected by the same RLS
contract as ratings and tags.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_personas",
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_demo_personas_tenant_slug"),
    )
    op.create_index(
        "idx_demo_personas_tenant_sort",
        "demo_personas",
        ["tenant_id", "sort_order"],
    )
    op.execute("GRANT SELECT ON demo_personas TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON demo_personas TO admin_user;")
    op.execute("ALTER TABLE demo_personas ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE demo_personas FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY demo_personas_tenant_isolation ON demo_personas
            FOR ALL
            TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS demo_personas_tenant_isolation ON demo_personas;")
    op.execute("ALTER TABLE demo_personas NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE demo_personas DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON demo_personas FROM admin_user;")
    op.execute("REVOKE SELECT ON demo_personas FROM app_user;")
    op.drop_index("idx_demo_personas_tenant_sort", table_name="demo_personas")
    op.drop_table("demo_personas")
