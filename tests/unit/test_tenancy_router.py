"""
Unit tests for ``src.serving.tenancy.router.TenantRouter``. Verifies:

  * ``resolve('default')`` returns a TenantConfig with the expected
    Redis prefix ('tenant:default:').
  * ``resolve('unknown-tenant')`` raises ``UnknownTenantError``.
  * ``TenantConfig.redis_prefix`` matches the ADR 0008 key namespace.
  * the migration 0016 columns — champion coordinates, quota overrides and the
    A/B bucketing seed — resolve into typed fields, with NULL meaning "no
    champion" and "global limits" rather than an empty string or a zero.
  * the TTL cache serves repeat reads without touching the database, which is
    what makes the per-request lookup the rate limiter and the recommend path
    both perform affordable.

These tests use an in-memory SQLite database with a stub
``public.tenants`` table so we don't need a live Postgres for the
router's unit-level contract. The integration test (Postgres + RLS)
lives in ``tests/tenant_isolation/``.

The engine is whatever the caller passes — the serving app hands over its
RLS-applied app engine, since ``public.tenants`` carries no RLS policy and
``app_user`` holds SELECT on it (migration 0002). Nothing here depends on the
role, which is the point: the router never needed BYPASSRLS.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text

from src.serving.tenancy import (
    TenantChampion,
    TenantConfig,
    TenantRouter,
    UnknownTenantError,
)

# The columns migration 0016 adds, in the order the router selects them.
_CREATE_TENANTS = """
    CREATE TABLE public.tenants (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        champion_candidate_version TEXT,
        champion_ranker_version TEXT,
        champion_feature_version TEXT,
        rate_limit_requests_per_minute INTEGER,
        rate_limit_burst INTEGER,
        ab_bucketing_seed BIGINT NOT NULL
    )
"""


@pytest.fixture
def engine() -> Engine:
    """SQLite engine with a public.tenants table seeded with two tenants.

    Uses the SQLite attach-database pattern so the router's schema-qualified
    SELECT works. ``demo`` carries a champion and no quota override — the
    deployed shape; ``default`` carries neither, which is what "this tenant has
    no learned serving" looks like on the row.
    """
    eng = create_engine("sqlite:///:memory:", future=True)
    with eng.begin() as conn:
        # SQLite doesn't have schemas; attach a second in-memory DB
        # under the name 'public' so 'public.tenants' resolves.
        conn.execute(text("ATTACH DATABASE ':memory:' AS public"))
        conn.execute(text(_CREATE_TENANTS))
        conn.execute(text("""
                INSERT INTO public.tenants (
                    id, display_name, champion_candidate_version, champion_ranker_version,
                    champion_feature_version, rate_limit_requests_per_minute,
                    rate_limit_burst, ab_bucketing_seed
                ) VALUES
                    ('default', 'MovieLens default tenant', NULL, NULL, NULL,
                     NULL, NULL, 1129937780749148039),
                    ('demo', 'Portfolio walkthrough demo tenant', 'demo-itemitem-v1',
                     'demo-lgbm-v1', 'feast-phase3-v1', NULL, NULL, 153392072542556201)
                """))
    return eng


class _CountingEngine:
    """Wraps an engine and counts how many times a connection is opened."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.connections = 0

    def connect(self) -> object:
        self.connections += 1
        return self._engine.connect()


def test_resolve_returns_tenant_config(engine: Engine) -> None:
    router = TenantRouter(engine)
    cfg = router.resolve("default")
    assert isinstance(cfg, TenantConfig)
    assert cfg.id == "default"
    assert cfg.display_name == "MovieLens default tenant"
    assert cfg.redis_prefix == "tenant:default:"


def test_resolve_demo_tenant(engine: Engine) -> None:
    router = TenantRouter(engine)
    cfg = router.resolve("demo")
    assert cfg.id == "demo"
    assert cfg.redis_prefix == "tenant:demo:"


def test_resolve_unknown_tenant_raises(engine: Engine) -> None:
    router = TenantRouter(engine)
    with pytest.raises(UnknownTenantError, match="unknown-tenant"):
        router.resolve("unknown-tenant")


def test_redis_prefix_shape_matches_adr_0008(engine: Engine) -> None:
    """ADR 0008 pins the online-store key namespace as
    ``tenant:<id>:...`` — proves the router composes it correctly
    without downstream handlers having to remember."""
    router = TenantRouter(engine)
    cfg = router.resolve("default")
    # The prefix is a string that other keys concatenate onto —
    # verify it ends in ':' so `f"{prefix}user:{user_id}"` works
    # without extra bookkeeping.
    assert cfg.redis_prefix.endswith(":")
    assert cfg.redis_prefix.startswith("tenant:")


def test_a_populated_champion_resolves_to_the_three_coordinates(engine: Engine) -> None:
    cfg = TenantRouter(engine).resolve("demo")
    assert cfg.champion == TenantChampion(
        candidate_version="demo-itemitem-v1",
        ranker_version="demo-lgbm-v1",
        feature_version="feast-phase3-v1",
    )
    assert cfg.champion is not None
    assert cfg.champion.describe() == "demo-itemitem-v1/demo-lgbm-v1/feast-phase3-v1"


def test_a_null_champion_is_none_rather_than_empty_strings(engine: Engine) -> None:
    """The distinction the popularity fallback reads.

    ``None`` means the tenant has no learned serving at all. Empty strings
    would compare unequal to any manifest and produce a mismatch — the wrong
    reason for the right outcome.
    """
    assert TenantRouter(engine).resolve("default").champion is None


def test_a_champion_matches_only_when_all_three_coordinates_agree(engine: Engine) -> None:
    champion = TenantRouter(engine).resolve("demo").champion
    assert champion is not None
    assert champion.matches(
        candidate_version="demo-itemitem-v1",
        ranker_version="demo-lgbm-v1",
        feature_version="feast-phase3-v1",
    )
    # A ranker swapped under the same candidate index is the case a single
    # concatenated "model version" string would have hidden.
    assert not champion.matches(
        candidate_version="demo-itemitem-v1",
        ranker_version="demo-lgbm-v2",
        feature_version="feast-phase3-v1",
    )


def test_a_partial_champion_is_treated_as_no_champion(engine: Engine) -> None:
    """Unreachable through the schema, handled anyway.

    ``ck_tenants_champion_complete`` makes this row impossible in Postgres. If
    it ever exists — a restore from somewhere without the constraint — ranking
    under a version nobody registered is worse than a popularity fallback.
    """
    with engine.begin() as conn:
        conn.execute(text("""
                INSERT INTO public.tenants (
                    id, display_name, champion_candidate_version, ab_bucketing_seed
                ) VALUES ('half', 'Half a champion', 'demo-itemitem-v1', 1)
                """))
    assert TenantRouter(engine).resolve("half").champion is None


def test_quota_overrides_resolve_and_default_to_global(engine: Engine) -> None:
    """NULL reads as "use the global setting", per column (ADR 0014)."""
    with engine.begin() as conn:
        conn.execute(text("""
                INSERT INTO public.tenants (
                    id, display_name, rate_limit_requests_per_minute, ab_bucketing_seed
                ) VALUES ('throttled', 'Throttled tenant', 60, 7)
                """))
    router = TenantRouter(engine)

    default_tenant = router.resolve("default")
    assert default_tenant.rate_limit.is_default
    assert default_tenant.rate_limit.requests_per_minute is None
    assert default_tenant.rate_limit.burst is None

    throttled = router.resolve("throttled")
    assert not throttled.rate_limit.is_default
    assert throttled.rate_limit.requests_per_minute == 60
    # Unset on the row, so the limiter reads the global burst rather than
    # inventing one from the per-minute rate.
    assert throttled.rate_limit.burst is None


def test_the_bucketing_seed_is_resolved_even_though_nothing_reads_it_yet(
    engine: Engine,
) -> None:
    """Phase 6 hashes this with a user id. Resolving it now keeps one read path."""
    assert TenantRouter(engine).resolve("demo").ab_bucketing_seed == 153392072542556201


def test_a_second_resolve_inside_the_ttl_does_not_touch_the_database(engine: Engine) -> None:
    counting = _CountingEngine(engine)
    router = TenantRouter(counting)  # type: ignore[arg-type]

    first = router.resolve("demo")
    second = router.resolve("demo")

    assert counting.connections == 1
    assert first is second
    assert router.cached("demo") is first


def test_the_cache_expires_and_a_promotion_is_picked_up(engine: Engine) -> None:
    """The TTL is the propagation mechanism for a champion change.

    Without an expiry a promotion would need a restart to take effect, which is
    the thing a config row exists to avoid.
    """
    now = [1000.0]
    counting = _CountingEngine(engine)
    router = TenantRouter(
        counting,  # type: ignore[arg-type]
        cache_ttl_seconds=30.0,
        clock=lambda: now[0],
    )

    assert router.resolve("demo").champion is not None
    with engine.begin() as conn:
        conn.execute(text("""
                UPDATE public.tenants
                SET champion_ranker_version = 'demo-lgbm-v2'
                WHERE id = 'demo'
                """))
    # Still inside the window: the old row, and no second query.
    assert router.resolve("demo").champion == TenantChampion(
        candidate_version="demo-itemitem-v1",
        ranker_version="demo-lgbm-v1",
        feature_version="feast-phase3-v1",
    )
    assert counting.connections == 1

    now[0] += 31.0
    assert router.cached("demo") is None
    refreshed = router.resolve("demo")
    assert refreshed.champion is not None
    assert refreshed.champion.ranker_version == "demo-lgbm-v2"
    assert counting.connections == 2


def test_invalidate_drops_the_cached_row(engine: Engine) -> None:
    """The escape hatch Phase 6's promotion job gets instead of a restart."""
    router = TenantRouter(engine)
    router.resolve("demo")
    assert router.cached("demo") is not None

    router.invalidate("demo")
    assert router.cached("demo") is None

    router.resolve("demo")
    router.resolve("default")
    router.invalidate()
    assert router.cached("demo") is None
    assert router.cached("default") is None


def test_an_unknown_tenant_is_not_cached_as_a_failure(engine: Engine) -> None:
    """A tenant registered a moment ago must work a moment later.

    Caching the miss would leave a freshly provisioned tenant broken for a TTL
    after someone fixed the thing that made it unknown.
    """
    router = TenantRouter(engine)
    with pytest.raises(UnknownTenantError):
        router.resolve("later")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, display_name, ab_bucketing_seed) "
                "VALUES ('later', 'Registered later', 5)"
            )
        )
    assert router.resolve("later").id == "later"
