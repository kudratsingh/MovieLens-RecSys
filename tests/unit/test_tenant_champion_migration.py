"""Migration 0016 — the per-tenant serving coordinates on ``public.tenants``.

Read as text, the way every other migration test here reads its subject: these
statements run once against a database this suite does not have, and what they
have to keep saying is checkable without one. The live shape is exercised by
``tests/tenant_isolation/`` against the real Compose stack.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("alembic/versions/0016_tenant_champion_columns.py")


def test_the_migration_is_the_linear_head_after_the_synthetic_cold_tenant() -> None:
    source = MIGRATION.read_text()

    assert 'revision: str = "0016_tenant_champion_columns"' in source
    assert 'down_revision: str | None = "0015_synth_cold_tenant"' in source

    revisions: set[str] = set()
    parents: set[str] = set()
    for path in Path("alembic/versions").glob("*.py"):
        text = path.read_text()
        revision = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert revision is not None
        revisions.add(revision.group(1))
        if down is not None and down.group(1):
            parents.add(down.group(1))

    assert revisions - parents == {"0016_tenant_champion_columns"}


def test_every_column_the_tenant_router_reads_is_added_here() -> None:
    """The registry has to be able to express what CLAUDE.md's Phase 3 scope says.

    "Tenant configuration in Postgres — one row per tenant, columns include API
    quotas, current champion model versions per stage, A/B bucketing seed."
    Each of those is one column, and the router selects them by name, so a
    missing one is an error at the first request rather than at migration time.
    """
    source = MIGRATION.read_text()

    for column in (
        "champion_candidate_version",
        "champion_ranker_version",
        "champion_feature_version",
        "rate_limit_requests_per_minute",
        "rate_limit_burst",
        "ab_bucketing_seed",
        "updated_at",
    ):
        assert f'"{column}"' in source


def test_the_champion_is_all_three_coordinates_or_none_of_them() -> None:
    """A half-named champion is a deployment nobody can reason about.

    The application already treats a partial champion as "no champion" and logs
    it, but that is the belt. This constraint is the suspenders, and it is the
    one that holds when a row is written by something other than this codebase.
    """
    source = MIGRATION.read_text()

    assert "ck_tenants_champion_complete" in source
    assert "num_nonnulls(" in source
    assert "IN (0, 3)" in source


def test_a_quota_column_cannot_be_set_to_something_that_refuses_every_request() -> None:
    source = MIGRATION.read_text()

    assert "ck_tenants_rate_limit_positive" in source
    assert "rate_limit_requests_per_minute > 0" in source
    assert "rate_limit_burst > 0" in source


def test_the_bucketing_seed_is_derived_from_the_tenant_id_by_a_trigger() -> None:
    """Reproducible bucketing is the point, and a DEFAULT cannot deliver it.

    A column default expression may not reference the row it is defaulting
    into, so an id-derived seed has to be applied by a BEFORE INSERT trigger —
    which runs before NOT NULL is checked, so the column stays NOT NULL without
    a default. The function is IMMUTABLE so a fixture, a test and a fresh
    database all compute the same seed for the same tenant.
    """
    source = MIGRATION.read_text()
    upgrade = source.split("def downgrade() -> None:", 1)[0]

    assert "CREATE FUNCTION public.tenant_ab_bucketing_seed(tenant_id text)" in upgrade
    assert "IMMUTABLE" in upgrade
    assert "CREATE TRIGGER tenants_before_write" in upgrade
    assert "BEFORE INSERT OR UPDATE ON public.tenants" in upgrade
    assert "SET ab_bucketing_seed = public.tenant_ab_bucketing_seed(id)" in upgrade
    assert "ALTER COLUMN ab_bucketing_seed SET NOT NULL" in upgrade
    # The seed is overridable on purpose: reseeding is how an experiment is
    # restarted, which a generated column would have made impossible.
    assert "GENERATED ALWAYS" not in upgrade


def test_the_deployed_tenant_is_seeded_from_the_committed_bundle() -> None:
    """The demo tenant has a champion on its first boot, not after a manual step.

    The three literals are the manifest the sidecar image bakes. They are
    checked against the committed file rather than retyped here, because the
    failure this guards against is exactly the two drifting apart.
    """
    import json

    manifest = json.loads(Path("infra/model-bundle/manifest.json").read_text())
    source = MIGRATION.read_text()

    assert f"champion_candidate_version = '{manifest['candidate']['version']}'" in source
    assert f"champion_ranker_version = '{manifest['ranker']['version']}'" in source
    assert f"champion_feature_version = '{manifest['feature_version']}'" in source
    assert manifest["tenant_id"] == "demo"
    # Only the tenant that has a trained bundle. `default` carries MovieLens
    # data with no model, and `synth_cold` is an offline evaluation fixture; a
    # champion on either would promise learned serving neither can deliver.
    assert "WHERE id = 'demo'" in source


def test_the_seeding_update_is_re_runnable() -> None:
    """Production is additive-migrations-only (ADR 0013).

    A database whose champion has since moved on must not be dragged back to
    the bundle this migration was written against, so the update only fills a
    row that has no champion at all.
    """
    source = MIGRATION.read_text()

    assert "AND champion_candidate_version IS NULL" in source
    assert "WHERE ab_bucketing_seed IS NULL" in source


def test_the_registry_stays_readable_and_unwritable_by_the_runtime_roles() -> None:
    """A champion moves by migration or promotion job, never by a request.

    ``public.tenants`` carries no RLS by design (ADR 0008 — it is the
    cross-tenant registry), so the grant *is* the boundary here.
    """
    source = MIGRATION.read_text()

    assert "GRANT SELECT ON public.tenants TO app_user, admin_user;" in source
    assert "INSERT" not in source.split("GRANT SELECT ON public.tenants")[1]


def test_the_downgrade_removes_everything_the_upgrade_created() -> None:
    source = MIGRATION.read_text()
    downgrade = source.split("def downgrade() -> None:", 1)[1]

    for reversal in (
        "DROP TRIGGER IF EXISTS tenants_before_write",
        "DROP FUNCTION IF EXISTS public.tenants_before_write()",
        "DROP FUNCTION IF EXISTS public.tenant_ab_bucketing_seed(text)",
        "ck_tenants_champion_complete",
        "ck_tenants_rate_limit_positive",
    ):
        assert reversal in downgrade
    for column in (
        "champion_candidate_version",
        "champion_ranker_version",
        "champion_feature_version",
        "rate_limit_requests_per_minute",
        "rate_limit_burst",
        "ab_bucketing_seed",
        "updated_at",
    ):
        assert f'op.drop_column("tenants", "{column}", schema="public")' in downgrade
