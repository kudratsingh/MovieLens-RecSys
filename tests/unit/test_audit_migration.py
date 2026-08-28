from __future__ import annotations

import re
from pathlib import Path

_VERSIONS = Path("alembic/versions")
_MIGRATION = _VERSIONS / "0012_audit_serving_inputs.py"


def test_audit_migration_extends_the_single_linear_head() -> None:
    source = _MIGRATION.read_text()

    assert 'revision: str = "0012_audit_serving_inputs"' in source
    assert 'down_revision: str | None = "0011_catalog_metadata"' in source

    revisions: set[str] = set()
    parents: set[str] = set()
    for path in _VERSIONS.glob("*.py"):
        text = path.read_text()
        revision = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert revision is not None
        revisions.add(revision.group(1))
        if down is not None and down.group(1):
            parents.add(down.group(1))

    heads = revisions - parents
    # One head, whoever holds it. Alembic cannot `upgrade head` through a
    # branched graph without an explicit merge, and this line has always been
    # straight; pinning the name here instead would only mean editing this
    # assertion every time a migration lands, which proves nothing.
    assert len(heads) == 1, f"the migration graph has branched: {sorted(heads)}"
    assert "0012_audit_serving_inputs" in revisions


def test_audit_migration_adds_the_serving_input_evidence_columns() -> None:
    source = _MIGRATION.read_text()

    for column in (
        "input_state_revision",
        "input_state_hash",
        "exclusion_hash",
        "positive_signal_count",
        "excluded_count",
        "filter_policy",
        "feature_event_time",
        "candidate_sources",
        "reason",
    ):
        assert f'"{column}"' in source

    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "op.drop_column" in source


def test_audit_migration_pins_non_negative_counts() -> None:
    source = _MIGRATION.read_text()

    for constraint in (
        "ck_audit_input_state_revision",
        "ck_audit_positive_signal_count",
        "ck_audit_excluded_count",
    ):
        assert constraint in source
    assert "input_state_revision >= 0" in source


def test_audit_migration_keeps_the_least_privilege_grants_from_0008() -> None:
    source = _MIGRATION.read_text()
    original = (_VERSIONS / "0008_create_recommendation_audits.py").read_text()

    for grant in (
        "GRANT SELECT, INSERT ON recommendation_audits TO app_user;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON recommendation_audits TO admin_user;",
    ):
        assert grant in original
        assert grant in source

    # Adding audit evidence must not touch tenant isolation or widen writes.
    assert "DISABLE ROW LEVEL SECURITY" not in source
    assert "NO FORCE ROW LEVEL SECURITY" not in source
    assert "DROP POLICY" not in source
    assert "TO PUBLIC" not in source
