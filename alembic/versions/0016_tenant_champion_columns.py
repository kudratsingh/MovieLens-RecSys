"""give the tenant registry per-tenant serving coordinates, quotas and a bucketing seed

Revision ID: 0016_tenant_champion_columns
Revises: 0015_synth_cold_tenant

``public.tenants`` has carried an id, a display name and a creation timestamp
since migration 0002, and every per-tenant serving decision since has had to be
expressed somewhere else: the model sidecar is pinned to one tenant by
``MODEL_TENANT_ID``, and ADR 0014's rate limits are process-global because
"``public.tenants`` is where a per-tenant quota column belongs" and it did not
have one. This migration gives the registry the three things Phase 6's
champion/challenger routing has to read (CLAUDE.md, Phase 3 platform track
item (d)).

**Champion coordinates.** ``champion_candidate_version``,
``champion_ranker_version`` and ``champion_feature_version`` name the exact
``ServingManifest`` a tenant's requests may be served by
(``src/models/artifacts.py``). Three columns rather than one opaque
"model version" string because the manifest has three independently versioned
coordinates and a rollback can move one of them: a bundle that swaps the ranker
while keeping the candidate index is a real event, and a single string would
force it to be parsed to be understood. All three NULL means *this tenant has
no learned serving* — the coordinator takes the popularity fallback and audits
the reason — which is the correct state for ``default`` (MovieLens data, no
trained bundle) and ``synth_cold`` (an offline evaluation fixture that issues no
requests). ``ck_tenants_champion_complete`` makes the third possibility
impossible: a champion is all three or none, because a half-named champion is a
deployment nobody can reason about and the database is the enforcer of last
resort (ADR 0008).

**Quotas.** ``rate_limit_requests_per_minute`` and ``rate_limit_burst`` are
NULL by default, and NULL reads as "use the global setting" (ADR 0014's
``RATE_LIMIT_REQUESTS_PER_MINUTE`` / ``RATE_LIMIT_BURST``). Each column falls
back on its own, so lowering one tenant's sustained rate does not require
restating a burst that was already right. Both are constrained positive: a zero
or negative quota would be a bucket that refuses every request, which is an
outage expressed as configuration.

**A/B bucketing seed.** ``ab_bucketing_seed`` is NOT NULL and is derived from
the tenant id, which is what makes bucketing reproducible: two databases that
have run these migrations — a laptop, CI, staging, the box — assign the same
user to the same arm, and a fixture can recompute the seed rather than read it.
It cannot be a column ``DEFAULT``, because a default expression may not
reference the row it is defaulting into; so the derivation lives in an IMMUTABLE
function and a BEFORE INSERT trigger applies it when the inserter did not supply
one. NOT NULL is checked after BEFORE-row triggers run, so the column can be
NOT NULL without a default and still never be violated. The value is
deliberately overridable — reseeding is how an experiment is restarted, and a
generated column would have taken that away.

**``updated_at``** is maintained by the same trigger. Every column added here is
something an operator changes while the system is running, and "when did this
tenant's champion last move" is the first question a bad promotion raises;
``created_at`` cannot answer it.

Additive per ADR 0013: only ``ADD COLUMN``, one function, one trigger, and an
``UPDATE`` of rows the seed leaves NULL. An older image running against this
schema neither sees nor needs any of it, so a rollback across this migration is
a no-op rather than a failure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_tenant_champion_columns"
down_revision: str | None = "0015_synth_cold_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None



def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("champion_candidate_version", sa.Text, nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("champion_ranker_version", sa.Text, nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("champion_feature_version", sa.Text, nullable=True),
        schema="public",
    )
    op.execute(
        """
        ALTER TABLE public.tenants
            ADD CONSTRAINT ck_tenants_champion_complete
            CHECK (
                num_nonnulls(
                    champion_candidate_version,
                    champion_ranker_version,
                    champion_feature_version
                ) IN (0, 3)
            );
        """
    )

    op.add_column(
        "tenants",
        sa.Column("rate_limit_requests_per_minute", sa.Integer, nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("rate_limit_burst", sa.Integer, nullable=True),
        schema="public",
    )
    op.execute(
        """
        ALTER TABLE public.tenants
            ADD CONSTRAINT ck_tenants_rate_limit_positive
            CHECK (
                (rate_limit_requests_per_minute IS NULL OR rate_limit_requests_per_minute > 0)
                AND (rate_limit_burst IS NULL OR rate_limit_burst > 0)
            );
        """
    )

    # IMMUTABLE and STRICT: the same tenant id must hash to the same seed in
    # every database and every process that asks, and a NULL id has no seed to
    # derive rather than a zero one. The hash is truncated to 15 hex digits —
    # 60 bits — so every seed is positive and well inside bigint rather than
    # carrying a sign the reader has to think about.
    op.execute(
        """
        CREATE FUNCTION public.tenant_ab_bucketing_seed(tenant_id text)
            RETURNS bigint
            LANGUAGE sql
            IMMUTABLE
            STRICT
        AS $$
            SELECT ('x' || substr(md5('movielens-ab-bucketing:' || tenant_id), 1, 15))
                ::bit(60)::bigint
        $$;
        """
    )
    op.add_column(
        "tenants",
        sa.Column("ab_bucketing_seed", sa.BigInteger, nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.tenants
        SET ab_bucketing_seed = public.tenant_ab_bucketing_seed(id)
        WHERE ab_bucketing_seed IS NULL;
        """
    )
    op.execute("ALTER TABLE public.tenants ALTER COLUMN ab_bucketing_seed SET NOT NULL;")
    op.execute(
        """
        CREATE FUNCTION public.tenants_before_write()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.ab_bucketing_seed IS NULL THEN
                NEW.ab_bucketing_seed := public.tenant_ab_bucketing_seed(NEW.id);
            END IF;
            IF TG_OP = 'UPDATE' THEN
                NEW.updated_at := now();
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER tenants_before_write
            BEFORE INSERT OR UPDATE ON public.tenants
            FOR EACH ROW
            EXECUTE FUNCTION public.tenants_before_write();
        """
    )

    # The one tenant with a trained bundle. These three values are the committed
    # serving manifest the sidecar image bakes (infra/model-bundle/manifest.json),
    # so the deployed tenant has a champion on its first boot rather than a
    # popularity fallback until someone remembers to set one. They are literals
    # rather than a read of that file because a migration is history: it has to
    # keep meaning the same thing after the bundle moves on. When it does move,
    # updating this row is a promotion — Phase 6's gate owns that, and until then
    # a bundle that no longer matches this row fails closed to popularity with an
    # audited reason instead of serving under a version nobody registered.
    op.execute(
        """
        UPDATE public.tenants
        SET champion_candidate_version = 'demo-itemitem-v1',
            champion_ranker_version = 'demo-lgbm-v1',
            champion_feature_version = 'feast-phase3-v1'
        WHERE id = 'demo'
          AND champion_candidate_version IS NULL
          AND champion_ranker_version IS NULL
          AND champion_feature_version IS NULL;
        """
    )

    # Table-level grants already cover added columns; re-issued so the intended
    # privilege surface stays readable in one place. The registry stays
    # read-only to both runtime roles — a champion moves by migration or by the
    # promotion job, never by a request handler.
    op.execute("GRANT SELECT ON public.tenants TO app_user, admin_user;")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tenants_before_write ON public.tenants;")
    op.execute("DROP FUNCTION IF EXISTS public.tenants_before_write();")
    op.execute(
        "ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS ck_tenants_champion_complete;"
    )
    op.execute(
        "ALTER TABLE public.tenants DROP CONSTRAINT IF EXISTS ck_tenants_rate_limit_positive;"
    )
    op.drop_column("tenants", "updated_at", schema="public")
    op.drop_column("tenants", "ab_bucketing_seed", schema="public")
    op.execute("DROP FUNCTION IF EXISTS public.tenant_ab_bucketing_seed(text);")
    op.drop_column("tenants", "rate_limit_burst", schema="public")
    op.drop_column("tenants", "rate_limit_requests_per_minute", schema="public")
    op.drop_column("tenants", "champion_feature_version", schema="public")
    op.drop_column("tenants", "champion_ranker_version", schema="public")
    op.drop_column("tenants", "champion_candidate_version", schema="public")
