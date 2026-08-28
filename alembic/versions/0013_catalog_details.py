"""add the fixture-owned movie detail payload to the catalog read model

Revision ID: 0013_catalog_details
Revises: 0012_audit_serving_inputs

The detail page had only what Browse already had. Trailer, tagline, runtime,
release date, backdrop, crowd score, directors and billed cast now arrive with
the rest of the snapshot, filled offline by
``synthetic/personas/enrich_details.py`` and read on the detail path alone —
the catalog list never selects this column, because a page of 24 titles would
carry two dozen cast lists nobody asked for.

One nullable JSONB column, not a table of its own. The payload is written and
replaced as a unit by one offline pass, is never queried by any of its parts,
and every title's object has the identical shape, so a normalized cast/crew
schema would buy joins and a migration surface in exchange for nothing this
read path does. A title enriched before this column existed, or one TMDB has
nothing for, keeps ``NULL`` and the detail page renders exactly as it does
today (``docs/frontend/catalog-contract.md``).

``movie_catalog_metadata`` is deliberately shared rather than tenant scoped
(0011): movie facts do not vary by tenant, and the tenant-owned state overlay
is unaffected here. Table-level grants already cover an added column; they are
re-issued so the intended privilege surface stays visible in one place and the
application role's read-only access to shared metadata is restated rather than
assumed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_catalog_details"
down_revision: str | None = "0012_audit_serving_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "movie_catalog_metadata",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute("GRANT SELECT ON movie_catalog_metadata TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON movie_catalog_metadata TO admin_user;")


def downgrade() -> None:
    op.drop_column("movie_catalog_metadata", "details")
