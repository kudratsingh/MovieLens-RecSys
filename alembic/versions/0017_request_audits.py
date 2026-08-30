"""create the tenant-scoped generic request audit log

Revision ID: 0017_request_audits
Revises: 0015_synth_cold_tenant

``recommendation_audits`` (0008/0012) answers "why did this user see that
title": it stores predictions, features, model versions and the input state a
ranking was computed against.  It only exists for
``GET /users/{user_id}/recommendations``, so the Phase 3 promise that *every*
authenticated request emits a row has never been true for the other twenty-odd
operations.

This table is the other half, and deliberately a different shape.  It is
operational telemetry — who called what, when, how it ended, how long it took —
so it carries the six columns non-negotiable #8 and the Phase 3 "Real auth"
scope name (``tenant_id``, ``user_id``, ``endpoint``, ``model_version``,
``latency_ms``, ``outcome``) plus the correlation id and the HTTP method, and
nothing else.  A recommendation is *not* duplicated into it; the richer row is
the one that already exists, and writing both would put a second insert inside
the one path with a p99 SLO (ADR 0010, ADR 0012's 2026-08-29 note).

``endpoint`` stores the matched route template (``/users/{user_id}/catalog``),
never the concrete path.  A concrete path would turn one operation into as many
distinct ``endpoint`` values as there are personas, which makes the column
useless for grouping and turns an audit table into a low-cardinality index's
worst case.

Isolation follows 0008 exactly: forced RLS keyed on
``current_setting('app.tenant_id')``, ``SELECT`` + ``INSERT`` for ``app_user``,
and cross-tenant operator access only through ``admin_user``'s BYPASSRLS.

Two indexes, because there are two access patterns and neither serves the
other.  ``(tenant_id, created_at DESC)`` is the operator's sweep — what has
this tenant been doing lately — and ``(tenant_id, user_id, created_at DESC)``
is what ``GET /users/{user_id}/request-audits`` reads; a query for one persona
cannot use the first index without scanning the whole tenant's recent traffic,
and a tenant-wide newest-first scan cannot use the second without a sort.  Both
are append-only right-hand inserts on a monotonic ``created_at``, so the write
cost is two leaf-page touches rather than two random writes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_request_audits"
down_revision: str | None = "0015_synth_cold_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_audits",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The X-Request-ID echoed to the caller. Separate from request_id for
        # the reason 0012 separated them on the prediction audit: an adopted
        # correlation header can be replayed, and a replay must not be able to
        # collide with an existing row's primary key.
        sa.Column("correlation_id", sa.Text, nullable=False),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("actor_user_id", sa.Text, nullable=False),
        # Nullable: `/whoami` and `/personas` are authenticated but address no
        # persona, and inventing a zero there would be a lie a later query
        # would have to know about.
        sa.Column("user_id", sa.BigInteger, nullable=True),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("http_status", sa.Integer, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
        # Nullable and normally null: only an endpoint that actually ran a
        # model has one to report, and the endpoints that do have the richer
        # `recommendation_audits` row instead. The column exists because
        # non-negotiable #8 names it and because Phase 6 puts a per-tenant
        # champion version on requests that are not predictions.
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("http_status BETWEEN 100 AND 599", name="ck_request_audit_http_status"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_request_audit_latency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "idx_request_audits_tenant_created",
        "request_audits",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_request_audits_tenant_user_created",
        "request_audits",
        ["tenant_id", "user_id", sa.text("created_at DESC")],
    )
    op.execute("GRANT SELECT, INSERT ON request_audits TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON request_audits TO admin_user;")
    op.execute("ALTER TABLE request_audits ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE request_audits FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY request_audits_tenant_isolation ON request_audits
            FOR ALL
            TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS request_audits_tenant_isolation ON request_audits;")
    op.execute("ALTER TABLE request_audits NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE request_audits DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON request_audits FROM admin_user;")
    op.execute("REVOKE SELECT, INSERT ON request_audits FROM app_user;")
    op.drop_index("idx_request_audits_tenant_user_created", table_name="request_audits")
    op.drop_index("idx_request_audits_tenant_created", table_name="request_audits")
    op.drop_table("request_audits")
