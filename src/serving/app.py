"""
FastAPI entrypoint per ADR 0007 + 0008.

The authenticated surface currently includes:
  * ``GET /healthz`` — unauthenticated, always 200.
  * ``GET /whoami`` — authenticated, returns the resolved
    ``(tenant_id, user_id)`` plus tenant metadata.
  * ``GET /users/{user_id}/recommendations`` — tenant-scoped item-item
    candidate retrieval, Feast features, and LightGBM ranking with fallback.
  * ``GET /users/{user_id}/history`` — tenant-scoped recent history.
  * ``GET /personas`` — tenant-scoped named demo identities.
  * ``GET /users/{user_id}/catalog`` — rateable demo catalog.
  * ``PUT /users/{user_id}/ratings/{movie_id}`` — RLS-scoped feedback write.
  * ``DELETE /users/{user_id}/ratings`` — reset one demo profile.

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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from sqlalchemy import Connection, create_engine

from src.auth import AuthMiddleware, JwksCache
from src.config import Settings
from src.serving.audit import (
    RecommendationAuditContext,
    RecommendationAuditMiddleware,
    RecommendationAuditService,
)
from src.serving.features import FeatureServerClient
from src.serving.models import ModelServerClient
from src.serving.orchestration import RecommendationCoordinator
from src.serving.recommendations import (
    RecommendationService,
    UnknownDemoPersonaError,
    UnknownMovieError,
)
from src.serving.startup_checks import run_startup_checks
from src.serving.tenancy import TenantRouter, UnknownTenantError
from src.serving.tmdb import TmdbMetadataClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

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
_recommendations = RecommendationService()
_audits = RecommendationAuditService()
_feature_server = FeatureServerClient(base_url=_settings.feast_feature_server_url)
_model_server = ModelServerClient(
    base_url=_settings.model_server_url,
    auth_token=_settings.model_server_auth_token.get_secret_value(),
    timeout_seconds=_settings.model_server_timeout_seconds,
)
_recommendation_coordinator = RecommendationCoordinator(_recommendations, _model_server)
_tmdb = TmdbMetadataClient(
    read_access_token=(
        _settings.tmdb_read_access_token.get_secret_value()
        if _settings.tmdb_read_access_token is not None
        else None
    ),
    api_base_url=_settings.tmdb_api_base_url,
    image_base_url=_settings.tmdb_image_base_url,
    timeout_seconds=_settings.tmdb_timeout_seconds,
    cache_ttl_seconds=_settings.tmdb_cache_ttl_seconds,
    cache_max_entries=_settings.tmdb_cache_max_entries,
)


class RecommendationItem(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    tmdb_id: str | None
    score: float
    reason: str
    poster_url: str | None
    overview: str | None
    release_year: int | None
    metadata_source: str


class RecommendationResponse(BaseModel):
    tenant_id: str
    user_id: int
    model_version: str
    policy: str
    items: list[RecommendationItem]


class HistoryItem(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    rating: float
    timestamp: int


class HistoryResponse(BaseModel):
    tenant_id: str
    user_id: int
    items: list[HistoryItem]


class PersonaItem(BaseModel):
    user_id: int
    slug: str
    display_name: str
    description: str


class PersonaResponse(BaseModel):
    tenant_id: str
    items: list[PersonaItem]


class CatalogItem(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    rating: float | None


class CatalogResponse(BaseModel):
    tenant_id: str
    user_id: int
    items: list[CatalogItem]


class RatingRequest(BaseModel):
    rating: float = Field(ge=0.5, le=5.0, multiple_of=0.5, allow_inf_nan=False)


class RatingMutationResponse(BaseModel):
    tenant_id: str
    user_id: int
    changed: int


class CurrentActorResponse(BaseModel):
    tenant_id: str
    user_id: str
    realm: str
    authorized_party: str
    roles: list[str]
    tenant_display_name: str
    redis_prefix: str


class OnlineUserFeaturesResponse(BaseModel):
    tenant_id: str
    user_id: int
    source: str
    feature_timestamp: str
    user_interaction_count: int | None
    user_days_active: float | None
    user_days_since_last_interaction: float | None


class AuditPredictionItem(BaseModel):
    movie_id: int
    score: float
    features: dict[str, float]


class RecommendationAuditItem(BaseModel):
    request_id: UUID
    tenant_id: str
    actor_user_id: str
    user_id: int
    endpoint: str
    http_status: int
    outcome: str
    policy: str
    model_version: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    fallback_reason: str | None
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    model_latency_ms: float
    latency_ms: float
    predictions: list[AuditPredictionItem]
    created_at: datetime


class RecommendationAuditResponse(BaseModel):
    tenant_id: str
    user_id: int
    items: list[RecommendationAuditItem]


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
    await _feature_server.aclose()
    await _model_server.aclose()
    await _tmdb.aclose()
    _app_engine.dispose()
    _admin_engine.dispose()


app = FastAPI(
    title="MovieLens Recommender API",
    description="Two-stage recommender service (candidate → ranker) per CLAUDE.md.",
    version="0.1.0",
    lifespan=lifespan,
)


def _openapi_schema() -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Keycloak access token with aud=movielens-api",
    }
    components.setdefault("schemas", {})["ErrorResponse"] = {
        "type": "object",
        "title": "ErrorResponse",
        "required": ["detail"],
        "properties": {"detail": {"type": "string"}},
    }
    for path, path_item in schema.get("paths", {}).items():
        if path == "/healthz":
            continue
        for method, operation in path_item.items():
            if method not in {"get", "put", "post", "patch", "delete"}:
                continue
            operation["security"] = [{"BearerAuth": []}]
            responses = operation.setdefault("responses", {})
            for status, description in (
                ("401", "Missing or invalid access token"),
                ("403", "Authenticated actor is not authorized"),
                ("500", "Request transaction failed"),
            ):
                responses.setdefault(
                    status,
                    {
                        "description": description,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                            }
                        },
                    },
                )
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_schema  # type: ignore[method-assign]

# Middleware is added at module import, before the first request. Starlette
# evaluates the last-added middleware first: AuthMiddleware opens the RLS
# transaction, then the audit middleware persists before that transaction
# commits.
app.add_middleware(RecommendationAuditMiddleware, audits=_audits)
app.add_middleware(
    AuthMiddleware,
    jwks=_jwks,
    app_engine=_app_engine,
    expected_audience=_settings.keycloak_audience,
    expected_issuer_base_url=_settings.keycloak_public_base_url,
    allowed_authorized_parties=_settings.keycloak_authorized_parties,
    dev_auth_bypass=_settings.dev_auth_bypass,
    dev_bypass_tenant=_settings.dev_bypass_tenant,
    dev_bypass_user=_settings.dev_bypass_user,
)


def _require_demo_persona_access(request: Request) -> None:
    principal = request.state.principal
    if not principal.can_access_demo_personas(
        trusted_service_client=_settings.keycloak_service_client_id
    ):
        raise HTTPException(
            status_code=403,
            detail="demo persona access requires the demo-impersonator role",
        )


@app.get("/healthz", operation_id="healthCheck")
async def healthz() -> dict[str, str]:
    """Unauthenticated liveness probe. Skipped by the auth middleware
    (see ``_UNAUTHENTICATED_PATHS`` in ``src.auth.middleware``). DB
    connectivity is deliberately not checked here — pool_pre_ping
    recycles dead connections, and a health endpoint that depends on
    Postgres would false-positive during rolling restarts.
    """
    return {"status": "ok"}


@app.get(
    "/whoami",
    response_model=CurrentActorResponse,
    operation_id="getCurrentActor",
)
async def whoami(request: Request) -> CurrentActorResponse:
    """Authenticated echo of the resolved identity."""
    principal = request.state.principal
    try:
        tenant = _tenant_router.resolve(principal.tenant_id)
    except UnknownTenantError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return CurrentActorResponse(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        realm=principal.realm,
        authorized_party=principal.authorized_party,
        roles=sorted(principal.roles),
        tenant_display_name=tenant.display_name,
        redis_prefix=tenant.redis_prefix,
    )


@app.get(
    "/users/{user_id}/recommendations",
    response_model=RecommendationResponse,
    operation_id="recommendMovies",
)
async def recommendations(
    user_id: int,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> RecommendationResponse:
    """Run learned two-stage serving or an explicit popularity fallback."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    decision = await _recommendation_coordinator.recommend(
        connection,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        limit=limit,
    )
    request.state.recommendation_audit_context = RecommendationAuditContext(
        policy=decision.policy,
        model_version=decision.model_version,
        candidate_version=decision.candidate_version,
        ranker_version=decision.ranker_version,
        feature_version=decision.feature_version,
        fallback_reason=decision.fallback_reason,
        candidate_latency_ms=decision.candidate_latency_ms,
        feature_latency_ms=decision.feature_latency_ms,
        ranker_latency_ms=decision.ranker_latency_ms,
        model_latency_ms=decision.model_latency_ms,
        predictions=decision.predictions,
    )
    items = decision.items
    logger.debug(
        "recommendations tenant_id=%s user_id=%s policy=%s candidate_version=%s "
        "ranker_version=%s feature_version=%s model_latency_ms=%.3f fallback_reason=%s",
        principal.tenant_id,
        user_id,
        decision.policy,
        decision.candidate_version,
        decision.ranker_version,
        decision.feature_version,
        decision.model_latency_ms,
        decision.fallback_reason,
    )
    metadata_by_id = await _tmdb.get_many(item.tmdb_id for item in items)
    return RecommendationResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        model_version=decision.model_version,
        policy=decision.policy,
        items=[
            RecommendationItem(
                movie_id=item.movie_id,
                title=item.title,
                genres=item.genres,
                tmdb_id=item.tmdb_id,
                score=item.score,
                reason=item.reason,
                poster_url=(
                    metadata_by_id[item.tmdb_id].poster_url
                    if item.tmdb_id in metadata_by_id
                    else None
                ),
                overview=(
                    metadata_by_id[item.tmdb_id].overview
                    if item.tmdb_id in metadata_by_id
                    else None
                ),
                release_year=(
                    metadata_by_id[item.tmdb_id].release_year
                    if item.tmdb_id in metadata_by_id
                    else None
                ),
                metadata_source="tmdb" if item.tmdb_id in metadata_by_id else "movielens",
            )
            for item in items
        ],
    )


@app.get(
    "/users/{user_id}/audits",
    response_model=RecommendationAuditResponse,
    operation_id="listRecommendationAudits",
)
async def recommendation_audits(
    user_id: int,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> RecommendationAuditResponse:
    """Return newest prediction audits visible inside the request tenant."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    items = _audits.list_for_user(connection, user_id=user_id, limit=limit)
    return RecommendationAuditResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        items=[RecommendationAuditItem(**item.__dict__) for item in items],
    )


@app.get(
    "/users/{user_id}/history",
    response_model=HistoryResponse,
    operation_id="listRatingHistory",
)
async def history(
    user_id: int,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> HistoryResponse:
    """Return recent interactions for the demo's watch-history panel."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    items = _recommendations.recent_history(connection, user_id=user_id, limit=limit)
    return HistoryResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        items=[
            HistoryItem(
                movie_id=item.movie_id,
                title=item.title,
                genres=item.genres,
                rating=item.rating,
                timestamp=item.timestamp,
            )
            for item in items
        ],
    )


@app.get("/personas", response_model=PersonaResponse, operation_id="listDemoPersonas")
async def personas(request: Request) -> PersonaResponse:
    """Return stable synthetic identities for the current tenant's demo."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    items = _recommendations.list_demo_personas(connection)
    return PersonaResponse(
        tenant_id=principal.tenant_id,
        items=[
            PersonaItem(
                user_id=item.user_id,
                slug=item.slug,
                display_name=item.display_name,
                description=item.description,
            )
            for item in items
        ],
    )


@app.get(
    "/users/{user_id}/features",
    response_model=OnlineUserFeaturesResponse,
    operation_id="getOnlineUserFeatures",
)
async def online_user_features(user_id: int, request: Request) -> OnlineUserFeaturesResponse:
    """Expose the tenant-keyed Redis-backed Feast read used by online ranking."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    try:
        values = await _feature_server.get_user_features(
            tenant_id=principal.tenant_id, user_id=user_id
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="online feature store unavailable") from exc
    return OnlineUserFeaturesResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        source="feast-redis",
        **values,
    )


@app.get(
    "/users/{user_id}/catalog",
    response_model=CatalogResponse,
    operation_id="listDemoCatalog",
)
async def catalog(user_id: int, request: Request) -> CatalogResponse:
    """Return rateable movies and current ratings for one demo persona."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        items = _recommendations.catalog_for_user(connection, user_id=user_id)
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CatalogResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        items=[CatalogItem(**item.__dict__) for item in items],
    )


@app.put(
    "/users/{user_id}/ratings/{movie_id}",
    response_model=RatingMutationResponse,
    operation_id="setMovieRating",
)
async def rate_movie(
    user_id: int,
    movie_id: int,
    payload: RatingRequest,
    request: Request,
) -> RatingMutationResponse:
    """Create or replace one rating for a tenant-scoped demo persona."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        _recommendations.rate_movie(
            connection,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            movie_id=movie_id,
            rating=payload.rating,
            timestamp=int(time.time()),
        )
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnknownMovieError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RatingMutationResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        changed=1,
    )


@app.delete(
    "/users/{user_id}/ratings",
    response_model=RatingMutationResponse,
    operation_id="resetDemoRatings",
)
async def reset_ratings(user_id: int, request: Request) -> RatingMutationResponse:
    """Clear a demo persona so the cold-start experience can be rebuilt."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        changed = _recommendations.reset_ratings(connection, user_id=user_id)
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RatingMutationResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        changed=changed,
    )
