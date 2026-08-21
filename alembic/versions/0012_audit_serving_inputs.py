"""record serving inputs, exclusions, and freshness on recommendation audits

Revision ID: 0012_audit_serving_inputs
Revises: 0011_catalog_metadata

An audit that only records which model ran cannot answer "why did this user
see that title" once the state has moved on.  These columns pin the input the
decision was made against (revision plus an order-independent digest of the
positive and excluded id sets), how stale the features were, which filter
policy was in force, where the candidates came from, and a structured reason.

``recommendation_audits`` keeps its forced RLS policy and the least-privilege
grants from 0008.  Table-level grants already cover added columns; the grants
are re-issued here so the intended privilege surface stays visible in one
place and a future column addition cannot quietly widen it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_audit_serving_inputs"
down_revision: str | None = "0011_catalog_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_NAMES = (
    "correlation_id",
    "input_state_revision",
    "input_state_hash",
    "exclusion_hash",
    "positive_signal_count",
    "excluded_count",
    "filter_policy",
    "feature_event_time",
    "candidate_sources",
    "reason",
)

_CHECKS = (
    ("ck_audit_input_state_revision", "input_state_revision >= 0"),
    ("ck_audit_positive_signal_count", "positive_signal_count >= 0"),
    ("ck_audit_excluded_count", "excluded_count >= 0"),
)


def _columns() -> list[sa.Column[object]]:
    # Built fresh on each call: a Column instance binds to the table it is
    # added to, so the same object cannot be reused across operations.
    return [
        sa.Column("correlation_id", sa.Text, nullable=False, server_default=""),
        sa.Column("input_state_revision", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("input_state_hash", sa.Text, nullable=False, server_default=""),
        sa.Column("exclusion_hash", sa.Text, nullable=False, server_default=""),
        sa.Column("positive_signal_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("excluded_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("filter_policy", sa.Text, nullable=False, server_default="not-run"),
        sa.Column("feature_event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "candidate_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
    ]


def upgrade() -> None:
    for column in _columns():
        op.add_column("recommendation_audits", column)
    for name, expression in _CHECKS:
        op.create_check_constraint(name, "recommendation_audits", expression)
    op.execute("GRANT SELECT, INSERT ON recommendation_audits TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recommendation_audits TO admin_user;")


def downgrade() -> None:
    for name, _ in reversed(_CHECKS):
        op.drop_constraint(name, "recommendation_audits", type_="check")
    for column_name in reversed(_COLUMN_NAMES):
        op.drop_column("recommendation_audits", column_name)
