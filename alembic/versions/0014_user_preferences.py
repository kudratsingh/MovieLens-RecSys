"""create per-persona presentation preferences

Revision ID: 0014_user_preferences
Revises: 0013_catalog_details

Discover's featured slot may pass over a title the viewer has already saved to
their watchlist.  That is a presentation choice a viewer makes and expects to
find again, so it needs a durable home — but it is emphatically *not* feedback:
nothing here is a model input, a training signal, or an exclusion (ADR 0012's
2026-08-28 note).  Keeping it in its own table rather than as another column on
``user_movie_state`` is what stops it drifting into the feedback vocabulary: the
grain is one row per (tenant, user), not per movie, and no serving path reads
it.

Columns are typed rather than a JSON settings bag.  A boolean the API and the
frontend both name is checkable by the contract test, greppable, and cannot
silently grow a key nobody validates; a new preference is a migration, which is
the right amount of friction for something that changes what a viewer is shown.

The isolation shape is copied from 0010 deliberately: forced RLS with the same
``app.tenant_id`` policy, least-privilege grants, and a tenant FK.  A
preferences row names what one persona in one tenant is shown, so it is exactly
as tenant-owned as the state rows beside it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_user_preferences"
down_revision: str | None = "0013_catalog_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        # The one preference this table exists for: whether a title already on
        # the viewer's watchlist is allowed to take Discover's featured slot.
        sa.Column(
            "feature_watchlisted_titles",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="0"),
        # Who last wrote the row, on the same terms as a feedback event's
        # ``actor_user_id``: the OIDC subject that made the change, not the
        # persona it was made for. The append-only ``user_feedback_events``
        # log is deliberately not reused — every row there is movie-scoped
        # (``movie_id`` is NOT NULL with an FK) and its action vocabulary is a
        # CHECK constraint over movie-state transitions, so a preference would
        # have to be misfiled as a movie decision to fit. Attribution without
        # history is the honest half to keep here.
        sa.Column("updated_by_actor", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("revision >= 0", name="ck_user_preferences_revision"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"]),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO admin_user;")
    op.execute("ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_preferences FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY user_preferences_tenant_isolation ON user_preferences
            FOR ALL
            TO PUBLIC
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_preferences_tenant_isolation ON user_preferences;")
    op.execute("ALTER TABLE user_preferences NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_preferences DISABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON user_preferences FROM admin_user;")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON user_preferences FROM app_user;")
    op.drop_table("user_preferences")
