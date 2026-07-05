"""
FastAPI entrypoint per ADR 0007 + 0008.

Bundle #1b ships two endpoints:
  * ``GET /healthz`` — unauthenticated, always 200.
  * ``GET /whoami`` — authenticated, returns the resolved
    ``(tenant_id, user_id)`` plus tenant metadata.

Wiring shape: engines, JWKS cache, and tenant router are built at
module import time so ``AuthMiddleware`` can be added before FastAPI
locks its middleware stack (Starlette forbids ``add_middleware`` after
the app has started). The lifespan hook then runs the startup
assertions — DB role isn't BYPASSRLS, pgBouncer is in transaction
pool mode, dev_auth_bypass is off in non-dev — before the app accepts
its first request. A failed assertion propagates out of the lifespan
and the process exits non-zero.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine

from src.auth import AuthMiddleware, JwksCache
from src.config import Settings
from src.serving.startup_checks import run_startup_checks
from src.serving.tenancy import TenantRouter, UnknownTenantError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_settings = Settings()

# RLS-applied engine (through pgBouncer alias movielens_app → upstream
# app_user). Every handler that queries tenant-scoped tables goes
# through this engine, and the middleware wraps each request in a
# transaction with SET LOCAL app.tenant_id.
_app_engine = create_engine(
    _settings.app_user_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

# Admin engine for cross-tenant metadata reads (public.tenants).
# BYPASSRLS. Only the tenant router uses it — handlers don't need
# admin access, and gating it here (not via a helper anyone can import)
# keeps the "who can bypass RLS" surface small.
_admin_engine = create_engine(
    _settings.admin_user_database_url,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=2,
    future=True,
)

_jwks = JwksCache(
    keycloak_base_url=_settings.keycloak_base_url,
    ttl_seconds=_settings.jwks_cache_ttl_seconds,
)

_tenant_router = TenantRouter(_admin_engine)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run startup assertions before accepting traffic. Failure raises
    out of lifespan and the process exits non-zero — the orchestrator
    (dev: docker-compose; CI: GitHub Actions; prod: later) surfaces
    the failure before any traffic reaches the app.
    """
    run_startup_checks(
        settings=_settings,
        app_engine=_app_engine,
        admin_engine=_admin_engine,
    )
    logger.info(
        "MovieLens API ready — environment=%s dev_auth_bypass=%s",
        _settings.environment,
        _settings.dev_auth_bypass,
    )
    yield
    _app_engine.dispose()
    _admin_engine.dispose()


app = FastAPI(
    title="MovieLens Recommender API",
    description="Two-stage recommender service (candidate → ranker) per CLAUDE.md.",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware is added at module import, before the first request.
# Starlette forbids add_middleware after the app has started.
app.add_middleware(
    AuthMiddleware,
    jwks=_jwks,
    app_engine=_app_engine,
    expected_audience=_settings.keycloak_audience,
    dev_auth_bypass=_settings.dev_auth_bypass,
    dev_bypass_tenant=_settings.dev_bypass_tenant,
    dev_bypass_user=_settings.dev_bypass_user,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Unauthenticated liveness probe. Skipped by the auth middleware
    (see ``_UNAUTHENTICATED_PATHS`` in ``src.auth.middleware``). DB
    connectivity is deliberately not checked here — pool_pre_ping
    recycles dead connections, and a health endpoint that depends on
    Postgres would false-positive during rolling restarts.
    """
    return {"status": "ok"}


@app.get("/whoami")
async def whoami(request: Request) -> dict[str, str]:
    """Authenticated echo of the resolved identity."""
    principal = request.state.principal
    try:
        tenant = _tenant_router.resolve(principal.tenant_id)
    except UnknownTenantError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "realm": principal.realm,
        "tenant_display_name": tenant.display_name,
        "redis_prefix": tenant.redis_prefix,
    }
