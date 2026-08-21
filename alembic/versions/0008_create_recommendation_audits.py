"""create tenant-scoped recommendation audit log

Revision ID: 0008
Revises: 0007

The application role can insert and read only rows whose tenant matches the
request transaction's ``SET LOCAL app.tenant_id`` value.  The admin role keeps
cross-tenant operator access through BYPASSRLS.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommendation_audits",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("actor_user_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("http_status", sa.Integer, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("policy", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("candidate_version", sa.Text, nullable=False),
        sa.Column("ranker_version", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("model_latency_ms", sa.Float, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
        sa.Column(
            "predictions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("http_status BETWEEN 100 AND 599", name="ck_audit_http_status"),
        sa.CheckConstraint("model_latency_ms >= 0", name="ck_audit_model_latency"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_audit_latency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "idx_recommendation_audits_tenant_user_created",
        "recommendation_audits",
        ["tenant_id", "user_id", sa.text("created_at DESC")],
    )
    op.execute("GRANT SELECT, INSERT ON recommendation_audits TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recommendation_audits TO admin_user;")
    op.execute("ALTER TABLE recommendation_audits ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE recommendation_audits FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY recommendation_audits_tenant_isolation ON recommendation_audits
            FOR ALL
            TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS recommendation_audits_tenant_isolation " "ON recommendation_audits;"
    )
    op.execute("ALTER TABLE recommendation_audits NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE recommendation_audits DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON recommendation_audits FROM admin_user;")
    op.execute("REVOKE SELECT, INSERT ON recommendation_audits FROM app_user;")
    op.drop_index(
        "idx_recommendation_audits_tenant_user_created",
        table_name="recommendation_audits",
    )
    op.drop_table("recommendation_audits")
