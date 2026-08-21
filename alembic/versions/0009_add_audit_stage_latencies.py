"""add candidate, feature, and ranker stage audit latencies

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("candidate_latency_ms", "feature_latency_ms", "ranker_latency_ms"):
        op.add_column(
            "recommendation_audits",
            sa.Column(column, sa.Float, nullable=False, server_default="0"),
        )
        op.create_check_constraint(
            f"ck_audit_{column.removesuffix('_ms')}",
            "recommendation_audits",
            f"{column} >= 0",
        )


def downgrade() -> None:
    for column in ("ranker_latency_ms", "feature_latency_ms", "candidate_latency_ms"):
        op.drop_constraint(
            f"ck_audit_{column.removesuffix('_ms')}",
            "recommendation_audits",
            type_="check",
        )
        op.drop_column("recommendation_audits", column)
