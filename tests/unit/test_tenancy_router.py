"""
Unit tests for ``src.serving.tenancy.router.TenantRouter``. Verifies:

  * ``resolve('default')`` returns a TenantConfig with the expected
    Redis prefix ('tenant:default:').
  * ``resolve('unknown-tenant')`` raises ``UnknownTenantError``.
  * ``TenantConfig.redis_prefix`` matches the ADR 0008 key namespace.

These tests use an in-memory SQLite database with a stub
``public.tenants`` table so we don't need a live Postgres for the
router's unit-level contract. The integration test (Postgres + RLS)
lives in ``tests/tenant_isolation/``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from src.serving.tenancy import TenantConfig, TenantRouter, UnknownTenantError


@pytest.fixture
def engine() -> object:
    """SQLite engine with a public.tenants table seeded with the two
    Bundle #1a tenants. Uses the `public.` schema-qualified name via
    the SQLite attach-database pattern so the router's schema-qualified
    SELECT works."""
    eng = create_engine("sqlite:///:memory:", future=True)
    with eng.begin() as conn:
        # SQLite doesn't have schemas; attach a second in-memory DB
        # under the name 'public' so 'public.tenants' resolves.
        conn.execute(text("ATTACH DATABASE ':memory:' AS public"))
        conn.execute(
            text("CREATE TABLE public.tenants (" "id TEXT PRIMARY KEY, display_name TEXT NOT NULL)")
        )
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, display_name) VALUES "
                "('default', 'MovieLens default tenant'), "
                "('demo', 'Portfolio walkthrough demo tenant')"
            )
        )
    return eng


def test_resolve_returns_tenant_config(engine: object) -> None:
    router = TenantRouter(engine)  # type: ignore[arg-type]
    cfg = router.resolve("default")
    assert isinstance(cfg, TenantConfig)
    assert cfg.id == "default"
    assert cfg.display_name == "MovieLens default tenant"
    assert cfg.redis_prefix == "tenant:default:"


def test_resolve_demo_tenant(engine: object) -> None:
    router = TenantRouter(engine)  # type: ignore[arg-type]
    cfg = router.resolve("demo")
    assert cfg.id == "demo"
    assert cfg.redis_prefix == "tenant:demo:"


def test_resolve_unknown_tenant_raises(engine: object) -> None:
    router = TenantRouter(engine)  # type: ignore[arg-type]
    with pytest.raises(UnknownTenantError, match="unknown-tenant"):
        router.resolve("unknown-tenant")


def test_redis_prefix_shape_matches_adr_0008(engine: object) -> None:
    """ADR 0008 pins the online-store key namespace as
    ``tenant:<id>:...`` — proves the router composes it correctly
    without downstream handlers having to remember."""
    router = TenantRouter(engine)  # type: ignore[arg-type]
    cfg = router.resolve("default")
    # The prefix is a string that other keys concatenate onto —
    # verify it ends in ':' so `f"{prefix}user:{user_id}"` works
    # without extra bookkeeping.
    assert cfg.redis_prefix.endswith(":")
    assert cfg.redis_prefix.startswith("tenant:")
