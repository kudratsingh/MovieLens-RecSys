from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

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


_PROVENANCE = _VERSIONS / "0019_audit_retrieval_provenance.py"
_PROVENANCE_COLUMNS = (
    "retriever_family",
    "retriever_sha256",
    "ranker_route",
    "encoder_ms",
)


class _RecordingOp:
    """A stand-in for ``alembic.op`` that remembers what a migration asked for.

    Executing the two functions beats grepping their source: it proves the
    columns are really added and really dropped, and that the pair is symmetric,
    without a live Postgres in the unit suite. Everything a migration can do
    here is recorded rather than performed.
    """

    def __init__(self) -> None:
        self.added: list[tuple[str, str, bool, object]] = []
        self.dropped: list[tuple[str, str]] = []
        self.checks: list[tuple[str, str, str]] = []
        self.dropped_checks: list[tuple[str, str]] = []
        self.statements: list[str] = []

    def add_column(self, table: str, column: Any) -> None:
        self.added.append((table, column.name, column.nullable, column.server_default))

    def drop_column(self, table: str, column_name: str) -> None:
        self.dropped.append((table, column_name))

    def create_check_constraint(self, name: str, table: str, expression: str) -> None:
        self.checks.append((name, table, expression))

    def drop_constraint(self, name: str, table: str, type_: str) -> None:
        self.dropped_checks.append((name, table))

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _load_provenance_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_migration_0019", _PROVENANCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provenance_migration_extends_the_single_linear_head() -> None:
    source = _PROVENANCE.read_text()

    assert 'revision: str = "0019_audit_retrieval_provenance"' in source
    assert 'down_revision: str | None = "0018_tmdb_catalog"' in source

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

    assert revisions - parents == {"0019_audit_retrieval_provenance"}


def test_provenance_migration_applies_the_four_columns_as_nullable() -> None:
    """Nullable with no default, so no existing row is backfilled with a fiction.

    A pre-0019 row has no record of which retriever answered. NOT NULL with a
    default would rewrite every one of them with a claim about a request nobody
    measured; NULL says "older than the question" and stays distinguishable from
    the writer's own ``not-run``/``0.0``, which describe a request that *was*
    measured and ran no model.
    """
    module = _load_provenance_migration()
    recorder = _RecordingOp()
    module.op = recorder

    module.upgrade()

    assert [name for _table, name, _nullable, _default in recorder.added] == list(
        _PROVENANCE_COLUMNS
    )
    for table, _name, nullable, server_default in recorder.added:
        assert table == "recommendation_audits"
        assert nullable is True
        assert server_default is None
    assert recorder.checks == [("ck_audit_encoder_ms", "recommendation_audits", "encoder_ms >= 0")]


def test_provenance_migration_downgrade_drops_exactly_what_upgrade_added() -> None:
    module = _load_provenance_migration()
    recorder = _RecordingOp()
    module.op = recorder

    module.upgrade()
    module.downgrade()

    added = [name for _table, name, _nullable, _default in recorder.added]
    assert [name for _table, name in recorder.dropped] == list(reversed(added))
    assert [name for name, _table in recorder.dropped_checks] == [
        name for name, _table, _expression in recorder.checks
    ]


def test_provenance_migration_keeps_tenant_isolation_and_the_0008_grants() -> None:
    source = _PROVENANCE.read_text()

    for grant in (
        "GRANT SELECT, INSERT ON recommendation_audits TO app_user;",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON recommendation_audits TO admin_user;",
    ):
        assert grant in source

    # Adding provenance must not touch isolation or widen writes.
    assert "DISABLE ROW LEVEL SECURITY" not in source
    assert "NO FORCE ROW LEVEL SECURITY" not in source
    assert "DROP POLICY" not in source
    assert "TO PUBLIC" not in source
