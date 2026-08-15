"""allow RLS-scoped application rating writes

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # RLS remains the isolation boundary: WITH CHECK rejects any row whose
    # tenant_id differs from the request's SET LOCAL app.tenant_id value.
    op.execute("GRANT INSERT, UPDATE, DELETE ON ratings TO app_user;")


def downgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE, DELETE ON ratings FROM app_user;")
