"""Migration 0017 creates the generic request audit log.

The single-linear-head assertion lives once, in ``test_audit_migration.py``;
this file checks what is specific to this table — the columns the
non-negotiable names, the isolation it inherits from 0008, and the two indexes
its two access patterns need.
"""

from __future__ import annotations

from pathlib import Path

_VERSIONS = Path("alembic/versions")
_MIGRATION = _VERSIONS / "0017_request_audits.py"


def test_the_migration_follows_the_tenant_champion_columns() -> None:
    source = _MIGRATION.read_text()

    assert 'revision: str = "0017_request_audits"' in source
    assert 'down_revision: str | None = "0016_tenant_champion_columns"' in source


def test_the_table_carries_the_columns_the_non_negotiable_names() -> None:
    source = _MIGRATION.read_text()

    # Non-negotiable #8 and the Phase 3 "Real auth" scope name these six by
    # hand; the correlation id and method are what make a row joinable to the
    # caller's own log and readable without guessing.
    for column in (
        "tenant_id",
        "user_id",
        "endpoint",
        "model_version",
        "latency_ms",
        "outcome",
        "correlation_id",
        "method",
        "http_status",
        "actor_user_id",
        "created_at",
    ):
        assert f'"{column}"' in source

    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "op.drop_table" in source


def test_the_row_identity_and_persona_nullability_are_pinned() -> None:
    source = _MIGRATION.read_text()

    # The primary key is the row's own UUID, never the adopted correlation id:
    # a caller replaying `X-Request-ID` must not be able to collide with a
    # stored audit (the rule migration 0012 established next door).
    assert 'sa.PrimaryKeyConstraint("request_id")' in source
    assert 'sa.Column("correlation_id", sa.Text, nullable=False)' in source
    # `/whoami` and `/personas` address no persona.
    assert 'sa.Column("user_id", sa.BigInteger, nullable=True)' in source


def test_the_table_is_isolated_exactly_like_the_prediction_audit() -> None:
    source = _MIGRATION.read_text()
    prediction_audit = (_VERSIONS / "0008_create_recommendation_audits.py").read_text()

    assert "ALTER TABLE request_audits ENABLE ROW LEVEL SECURITY;" in source
    assert "ALTER TABLE request_audits FORCE ROW LEVEL SECURITY;" in source
    assert "current_setting('app.tenant_id', true)" in source
    assert "GRANT SELECT, INSERT ON request_audits TO app_user;" in source
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON request_audits TO admin_user;" in source
    # The application role never gains UPDATE or DELETE on an audit table; that
    # is what makes the log append-only from the request path's point of view.
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON request_audits TO app_user;" not in source
    assert 'sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"])' in source
    # Same policy shape as 0008, so a reader comparing the two sees one rule.
    assert "current_setting('app.tenant_id', true)" in prediction_audit


def test_both_access_patterns_have_an_index() -> None:
    source = _MIGRATION.read_text()

    # The operator's tenant-wide sweep, and the per-persona read
    # `/users/{user_id}/request-audits` serves. Neither index serves the other:
    # a persona query cannot use the first without scanning the tenant's whole
    # recent traffic, and a tenant-wide newest-first scan cannot use the second
    # without a sort.
    assert 'op.create_index(\n        "idx_request_audits_tenant_created"' in source
    assert 'op.create_index(\n        "idx_request_audits_tenant_user_created"' in source
    assert 'sa.text("created_at DESC")' in source


def _code(source: str) -> str:
    """The migration with its module docstring and comment lines removed.

    Prose is allowed to name the neighbouring table; statements are not. This
    strips exactly the prose — dropping *lines* by shape would keep only the
    first and last line of a multi-line ``op.execute(\"\"\"...\"\"\")`` and
    silently discard the SQL in between, which is where a destructive statement
    would actually live in this file.
    """
    _, _, after_open = source.partition('"""')
    _, _, body = after_open.partition('"""')
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


def test_the_migration_is_additive_and_touches_nothing_that_exists() -> None:
    source = _MIGRATION.read_text()
    code = _code(source)

    # Guard the guard: if the docstring/comment strip ever stopped working, the
    # assertions below would pass over anything.
    assert "CREATE POLICY request_audits_tenant_isolation" in code
    assert "DROP POLICY IF EXISTS request_audits_tenant_isolation" in code

    # Production is additive-migrations-only (ADR 0013). Nothing here may drop,
    # alter or re-grant an existing table, and in particular the prediction
    # audit's own isolation must be left exactly as 0008 and 0012 left it.
    assert "recommendation_audits" not in code
    assert "op.alter_column" not in code
    # The upgrade half creates; only the downgrade half tears down.
    upgrade = code.split("def downgrade")[0]
    assert "DISABLE ROW LEVEL SECURITY" not in upgrade
    assert "DROP POLICY" not in upgrade
    assert "op.drop_table" not in upgrade
