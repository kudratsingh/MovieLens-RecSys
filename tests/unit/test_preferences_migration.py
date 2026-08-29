from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("alembic/versions/0014_user_preferences.py")


def test_preferences_migration_is_the_linear_head_after_the_audit_columns() -> None:
    source = MIGRATION.read_text()

    assert 'revision: str = "0014_user_preferences"' in source
    assert 'down_revision: str | None = "0013_catalog_details"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source


def test_the_table_carries_the_same_forced_rls_shape_as_movie_state() -> None:
    """Non-negotiable #9 does not have a small-table exemption.

    A preferences row names what one persona in one tenant is shown, so it is
    exactly as tenant-owned as the state rows beside it and gets migration
    0010's isolation shape verbatim: forced RLS, the ``app.tenant_id`` policy,
    a tenant foreign key, and least-privilege grants to the two runtime roles.
    """
    source = MIGRATION.read_text()

    for required in (
        "ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;",
        "ALTER TABLE user_preferences FORCE ROW LEVEL SECURITY;",
        "CREATE POLICY user_preferences_tenant_isolation ON user_preferences",
        "USING (tenant_id = current_setting('app.tenant_id', true))",
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
        'sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"])',
        'sa.PrimaryKeyConstraint("tenant_id", "user_id")',
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO app_user;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO admin_user;",
    ):
        assert required in source

    downgrade = source.split("def downgrade() -> None:", 1)[1]
    for reversal in (
        "DROP POLICY IF EXISTS user_preferences_tenant_isolation",
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON user_preferences FROM app_user;",
        'op.drop_table("user_preferences")',
    ):
        assert reversal in downgrade


def test_the_columns_are_typed_rather_than_a_settings_bag() -> None:
    """A preference the API and the frontend both name can be checked.

    A JSON blob could grow a key nobody validates and nobody renders; a boolean
    column costs a migration, which is the right amount of friction for
    something that changes what a viewer is shown.
    """
    source = MIGRATION.read_text()

    assert '"feature_watchlisted_titles"' in source
    assert "sa.Boolean" in source
    assert "server_default=sa.true()" in source
    assert 'sa.CheckConstraint("revision >= 0", name="ck_user_preferences_revision")' in source
    assert "JSONB" not in source
    assert "sa.JSON" not in source


def test_the_table_records_who_changed_it() -> None:
    source = MIGRATION.read_text()

    assert '"updated_by_actor"' in source
    # The append-only feedback log is not reused: it is movie-scoped end to end
    # (`movie_id` NOT NULL with an FK, and a CHECK over movie-state actions), so
    # a preference could only be filed there by pretending to be a movie
    # decision. The migration says so where a reader will look.
    assert "user_feedback_events" in source


def test_the_migration_graph_has_not_branched() -> None:
    """The newest migration pins the head; every earlier one pins the invariant.

    Two heads is a branch nobody chose, and `alembic upgrade head` refuses to
    run against one — which turns up as a failed deploy rather than as a failed
    test unless it is checked here. 0014 stopped being the newest when
    0015_synth_cold_tenant landed, so per that rule this now holds the
    invariant and `tests/unit/test_synthetic_cold_start.py` names the head.
    """
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in Path("alembic/versions").glob("*.py"):
        source = path.read_text()
        revision = re.search(r'^revision: str = "([^"]+)"', source, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', source, re.MULTILINE)
        assert revision is not None
        revisions.add(revision.group(1))
        if down is not None and down.group(1):
            parents.add(down.group(1))

    heads = revisions - parents
    assert len(heads) == 1, f"the migration graph has branched: {sorted(heads)}"
    assert "0014_user_preferences" in parents
