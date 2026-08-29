"""register the synth_cold tenant for ADR 0011's cold-start cohort

Revision ID: 0015_synth_cold_tenant
Revises: 0014_user_preferences

Migration 0002 seeded ``default`` and ``demo`` and said the two synthetic
tenants would arrive "in later migrations bundled with the code that consumes
them". This is that migration for ``synth_cold``.

One row, no table. The cohort is an offline evaluation fixture: its 2 000 users
are generated into a DVC-tracked parquet, appended to the training frame in
memory, scored, and discarded. Nothing about them is persisted in a
tenant-scoped table, and no request is ever made on their behalf — so the row
exists to reserve the identifier and to satisfy the foreign key any future
tenant-scoped cold-start artifact would need, not because something is writing
under it today.

Deliberately **no Keycloak realm**, which is where this departs from ADR 0011's
Consequences section (see the 2026-08-29 addendum at the bottom of that ADR).
The ADR itself notes these users never authenticate; a realm they cannot log
into would be an idle attack surface and a permanent extra row in the
realm-drift CI job's inventory. The tenant tag is the RLS isolation ADR 0008
asks for, and it does that job without one.

Additive per ADR 0013 — an insert with ``ON CONFLICT DO NOTHING``, so a
database already carrying the row is left alone and a rollback to an earlier
image finds a schema it still understands.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_synth_cold_tenant"
down_revision: str | None = "0014_user_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO public.tenants (id, display_name)
        VALUES ('synth_cold', 'Synthetic cold-start cohort tenant')
        ON CONFLICT (id) DO NOTHING;
        """
    )


def downgrade() -> None:
    # No CASCADE. If something has come to reference this tenant since, the
    # foreign key should stop the downgrade rather than quietly take its rows
    # with it.
    op.execute("DELETE FROM public.tenants WHERE id = 'synth_cold';")
