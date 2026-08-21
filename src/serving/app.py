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
  * ``GET /users/{user_id}/catalog`` — searchable, cursor-paginated local catalog.
  * ``GET /users/{user_id}/movies/{movie_id}`` — local detail plus durable state.
  * ``GET /users/{user_id}/library`` — cursor-paginated durable movie state.
  * ``GET /users/{user_id}/taste-profile`` — live, non-model rating summary.
  * ``PUT|DELETE /users/{user_id}/movies/{movie_id}/*`` — idempotent watched,
    rating, watchlist, and dismissal resources.
  * ``PUT /users/{user_id}/ratings/{movie_id}`` — RLS-scoped feedback write.
  * ``DELETE /users/{user_id}/ratings`` — reset one demo profile.

Every response carries ``X-Request-ID``. A well-formed inbound value is
adopted so a caller's correlation id survives the hop; otherwise one is
minted. Recommendation audits store it alongside their own row identity.

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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from sqlalchemy import Connection, create_engine, text
from starlette.concurrency import run_in_threadpool

from src.auth import AuthMiddleware, JwksCache
from src.config import Settings
from src.serving.audit import (
    RecommendationAuditContext,
    RecommendationAuditMiddleware,
    RecommendationAuditService,
)
from src.serving.catalog import (
    CatalogMovie,
    CatalogQuery,
    CatalogService,
    InvalidCatalogCursorError,
)
from src.serving.features import FeatureServerClient
from src.serving.feedback import (
    FeedbackAction,
    FeedbackService,
    IdempotencyConflictError,
    InvalidLibraryCursorError,
    InvalidStateTransitionError,
    LibraryPage,
    MovieState,
    MutationResult,
    StateRevisionConflictError,
)
from src.serving.models import ModelServerClient
from src.serving.orchestration import RecommendationCoordinator
from src.serving.recommendations import (
    RecommendationService,
    UnknownDemoPersonaError,
    UnknownMovieError,
)
from src.serving.request_id import RequestIdMiddleware
from src.serving.startup_checks import run_startup_checks
from src.serving.tenancy import TenantRouter, UnknownTenantError

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
_feedback = FeedbackService()
_audits = RecommendationAuditService()
_feature_server = FeatureServerClient(base_url=_settings.feast_feature_server_url)
_model_server = ModelServerClient(
    base_url=_settings.model_server_url,
    auth_token=_settings.model_server_auth_token.get_secret_value(),
    timeout_seconds=_settings.model_server_timeout_seconds,
)
_recommendation_coordinator = RecommendationCoordinator(_recommendations, _model_server)
_catalog = CatalogService()


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
    metadata_source: Literal["reviewed-fixture", "tmdb-snapshot", "movielens"]


class ErrorResponse(BaseModel):
    detail: str


class ServingPolicyResponse(BaseModel):
    """Machine-readable proof of which policy served this response.

    ``positive_signal_count`` and ``threshold`` are what let a client show
    progress toward learned serving without guessing at the rule, and
    ``score_scale`` names what ``RecommendationItem.score`` actually is so it
    is never rendered as a probability or a match percentage (ADR 0012).
    """

    name: str
    learned: bool
    positive_signal_count: int
    threshold: int
    reason: str
    score_scale: str
    filter_policy: str
    excluded_count: int


class RecommendationResponse(BaseModel):
    tenant_id: str
    user_id: int
    model_version: str
    # Retained as the flat legacy field; it always equals serving_policy.name.
    policy: str
    serving_policy: ServingPolicyResponse
    items: list[RecommendationItem]


class HistoryItem(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    rating: float | None
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


class MovieStateResponse(BaseModel):
    tenant_id: str
    user_id: int
    movie_id: int
    watched_at: datetime | None
    rating: float | None
    rating_updated_at: datetime | None
    watchlisted_at: datetime | None
    dismissed_at: datetime | None
    revision: int
    updated_at: datetime


class CatalogItem(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    tmdb_id: str | None
    release_year: int | None
    poster_url: str | None
    overview: str | None
    metadata_source: Literal["reviewed-fixture", "tmdb-snapshot", "movielens"]
    source_status: Literal["complete", "partial", "unavailable"]
    state: MovieStateResponse | None
    interaction_count: int


class CatalogPageInfo(BaseModel):
    next_cursor: str | None
    has_more: bool


class CatalogResponse(BaseModel):
    tenant_id: str
    user_id: int
    items: list[CatalogItem]
    page: CatalogPageInfo


class MovieDetailResponse(BaseModel):
    tenant_id: str
    user_id: int
    item: CatalogItem


class RatingRequest(BaseModel):
    rating: float = Field(ge=0.5, le=5.0, multiple_of=0.5, allow_inf_nan=False)


class RatingMutationResponse(BaseModel):
    tenant_id: str
    user_id: int
    changed: int


class FeedbackMutationResponse(BaseModel):
    request_id: UUID
    replayed: bool
    outcome: Literal["changed", "no_change"]
    state: MovieStateResponse


class LibraryMovieResponse(BaseModel):
    movie_id: int
    title: str
    genres: list[str]
    state: MovieStateResponse


class LibraryCountsResponse(BaseModel):
    rated: int
    watchlist: int
    history: int


class CursorPageResponse(BaseModel):
    next_cursor: str | None
    has_more: bool


class LibraryResponse(BaseModel):
    tenant_id: str
    user_id: int
    tab: Literal["rated", "watchlist", "history"]
    sort: Literal["recent", "title", "rating"]
    query: str | None
    counts: LibraryCountsResponse
    page: CursorPageResponse
    items: list[LibraryMovieResponse]


class TasteGenreResponse(BaseModel):
    genre: str
    rated_count: int
    average_rating: float


class TasteSummaryResponse(BaseModel):
    tenant_id: str
    user_id: int
    source: Literal["live-ratings-v1"]
    generated_at: datetime
    rating_count: int
    average_rating: float | None
    top_genres: list[TasteGenreResponse]
    explanation: str


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
    # Audits written before Bundle 6 have no attribution in their JSON payload.
    candidate_source: str = "unknown"
    seed_movie_id: int | None = None


class RecommendationAuditItem(BaseModel):
    request_id: UUID
    # The X-Request-ID echoed to the caller: the caller's value when it sent a
    # usable one, otherwise the id this service minted.
    correlation_id: str
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
    input_state_revision: int
    input_state_hash: str
    exclusion_hash: str
    positive_signal_count: int
    excluded_count: int
    filter_policy: str
    feature_event_time: datetime | None
    candidate_sources: dict[str, int]
    reason: str


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
                ("400", "Request parameters are invalid or cursor does not match query"),
                ("401", "Missing or invalid access token"),
                ("403", "Authenticated actor is not authorized"),
                ("404", "Requested persona or movie does not exist"),
                ("409", "Idempotency, state revision, or transition conflict"),
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
# evaluates the last-added middleware first: RequestIdMiddleware resolves the
# correlation id and owns the response header, AuthMiddleware opens the RLS
# transaction, then the audit middleware persists before that transaction
# commits. Request-id resolution is outermost so even a 401 carries the header
# the caller can correlate on.
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
app.add_middleware(RequestIdMiddleware)


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
        input_state_revision=decision.input_state_revision,
        input_state_hash=decision.input_state_hash,
        exclusion_hash=decision.exclusion_hash,
        positive_signal_count=decision.positive_signal_count,
        excluded_count=decision.excluded_count,
        filter_policy=decision.filter_policy,
        feature_event_time=decision.feature_event_time,
        candidate_sources=decision.candidate_sources,
        reason=decision.reason,
    )
    items = decision.items
    logger.debug(
        "recommendations tenant_id=%s user_id=%s policy=%s candidate_version=%s "
        "ranker_version=%s feature_version=%s model_latency_ms=%.3f fallback_reason=%s "
        "positive_signal_count=%s excluded_count=%s exclusion_hash=%s request_id=%s",
        principal.tenant_id,
        user_id,
        decision.policy,
        decision.candidate_version,
        decision.ranker_version,
        decision.feature_version,
        decision.model_latency_ms,
        decision.fallback_reason,
        decision.positive_signal_count,
        decision.excluded_count,
        decision.exclusion_hash,
        getattr(request.state, "request_id", None),
    )
    metadata_by_id = await run_in_threadpool(
        _catalog.metadata_for_movies,
        connection,
        movie_ids=[item.movie_id for item in items],
    )
    return RecommendationResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        model_version=decision.model_version,
        policy=decision.policy,
        serving_policy=ServingPolicyResponse(
            name=decision.serving_policy.name,
            learned=decision.serving_policy.learned,
            positive_signal_count=decision.serving_policy.positive_signal_count,
            threshold=decision.serving_policy.threshold,
            reason=decision.serving_policy.reason,
            score_scale=decision.serving_policy.score_scale,
            filter_policy=decision.serving_policy.filter_policy,
            excluded_count=decision.serving_policy.excluded_count,
        ),
        items=[
            RecommendationItem(
                movie_id=item.movie_id,
                title=item.title,
                genres=item.genres,
                tmdb_id=item.tmdb_id,
                score=item.score,
                reason=item.reason,
                poster_url=(
                    metadata_by_id[item.movie_id].poster_url
                    if item.movie_id in metadata_by_id
                    else None
                ),
                overview=(
                    metadata_by_id[item.movie_id].overview
                    if item.movie_id in metadata_by_id
                    else None
                ),
                release_year=(
                    metadata_by_id[item.movie_id].release_year
                    if item.movie_id in metadata_by_id
                    else None
                ),
                metadata_source=(
                    metadata_by_id[item.movie_id].metadata_source
                    if item.movie_id in metadata_by_id
                    else "movielens"
                ),
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
    responses={
        400: {"model": ErrorResponse, "description": "Cursor is invalid for this query"},
        404: {"model": ErrorResponse, "description": "Demo persona was not found"},
    },
)
async def catalog(
    user_id: int,
    request: Request,
    q: str | None = Query(default=None, max_length=120),
    genre: str | None = Query(default=None, max_length=40),
    year_from: int | None = Query(default=None, ge=1878, le=2100),
    year_to: int | None = Query(default=None, ge=1878, le=2100),
    sort: Literal["title", "newest", "popular"] = "title",
    limit: int = Query(default=24, ge=1, le=48),
    cursor: str | None = Query(default=None, max_length=1024),
) -> CatalogResponse:
    """Return deterministic local metadata with the persona's durable state overlay."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        page = await run_in_threadpool(
            _catalog.list_for_user,
            connection,
            user_id=user_id,
            query=CatalogQuery(
                search=q,
                genre=genre,
                year_from=year_from,
                year_to=year_to,
                sort=sort,
                limit=limit,
                cursor=cursor,
            ),
        )
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidCatalogCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CatalogResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        items=[_catalog_item_response(item) for item in page.items],
        page=CatalogPageInfo(
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        ),
    )


@app.get(
    "/users/{user_id}/movies/{movie_id}",
    response_model=MovieDetailResponse,
    operation_id="getMovieDetail",
    responses={404: {"model": ErrorResponse, "description": "Movie or demo persona was not found"}},
)
async def movie_detail(
    user_id: int,
    movie_id: int,
    request: Request,
) -> MovieDetailResponse:
    """Return persisted detail metadata and the persona's durable movie state."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        item = await run_in_threadpool(
            _catalog.get_for_user,
            connection,
            user_id=user_id,
            movie_id=movie_id,
        )
    except (UnknownDemoPersonaError, UnknownMovieError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MovieDetailResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        item=_catalog_item_response(item),
    )


@app.get(
    "/users/{user_id}/movies/{movie_id}/state",
    response_model=MovieStateResponse | None,
    operation_id="getMovieState",
)
async def movie_state(user_id: int, movie_id: int, request: Request) -> MovieStateResponse | None:
    """Return one selected persona's canonical live state for a movie."""
    _require_demo_persona_access(request)
    connection: Connection = request.state.db
    try:
        state = await run_in_threadpool(
            _feedback.get_state,
            connection,
            user_id=user_id,
            movie_id=movie_id,
        )
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _movie_state_response(state) if state is not None else None


@app.get(
    "/users/{user_id}/library",
    response_model=LibraryResponse,
    operation_id="listLibrary",
)
async def library(
    user_id: int,
    request: Request,
    tab: Literal["rated", "watchlist", "history"] = "rated",
    sort: Literal["recent", "title", "rating"] = "recent",
    limit: int = Query(default=24, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=1024),
    q: str | None = Query(default=None, max_length=120),
) -> LibraryResponse:
    """Return one bounded keyset page plus counts for all Library tabs."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        page = await run_in_threadpool(
            _feedback.library,
            connection,
            user_id=user_id,
            tab=tab,
            sort=sort,
            limit=limit,
            cursor=cursor,
            query=q,
        )
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidLibraryCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _library_response(
        page,
        tenant_id=principal.tenant_id,
        user_id=user_id,
        tab=tab,
        sort=sort,
        query=q.strip() if q and q.strip() else None,
    )


@app.get(
    "/users/{user_id}/taste-profile",
    response_model=TasteSummaryResponse,
    operation_id="getLiveRatingsTasteSummary",
)
async def taste_profile(user_id: int, request: Request) -> TasteSummaryResponse:
    """Summarize live ratings without claiming deployed-model attribution."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        summary = await run_in_threadpool(
            _feedback.taste_summary,
            connection,
            user_id=user_id,
        )
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return TasteSummaryResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        source="live-ratings-v1",
        generated_at=summary.generated_at,
        rating_count=summary.rating_count,
        average_rating=summary.average_rating,
        top_genres=[TasteGenreResponse(**item.__dict__) for item in summary.top_genres],
        explanation=summary.explanation,
    )


@app.put(
    "/users/{user_id}/movies/{movie_id}/watched",
    response_model=FeedbackMutationResponse,
    operation_id="setMovieWatched",
)
async def set_watched(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="watched_set",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.delete(
    "/users/{user_id}/movies/{movie_id}/watched",
    response_model=FeedbackMutationResponse,
    operation_id="removeMovieFromHistory",
)
async def remove_from_history(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="history_removed",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.put(
    "/users/{user_id}/movies/{movie_id}/rating",
    response_model=FeedbackMutationResponse,
    operation_id="setMovieStateRating",
)
async def set_state_rating(
    user_id: int,
    movie_id: int,
    payload: RatingRequest,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="rating_set",
        request_id=request_id,
        expected_revision=expected_revision,
        rating=payload.rating,
    )


@app.delete(
    "/users/{user_id}/movies/{movie_id}/rating",
    response_model=FeedbackMutationResponse,
    operation_id="deleteMovieStateRating",
)
async def delete_state_rating(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="rating_deleted",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.put(
    "/users/{user_id}/movies/{movie_id}/watchlist",
    response_model=FeedbackMutationResponse,
    operation_id="addMovieToWatchlist",
)
async def set_watchlist(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="watchlist_set",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.delete(
    "/users/{user_id}/movies/{movie_id}/watchlist",
    response_model=FeedbackMutationResponse,
    operation_id="removeMovieFromWatchlist",
)
async def delete_watchlist(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="watchlist_deleted",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.put(
    "/users/{user_id}/movies/{movie_id}/dismissal",
    response_model=FeedbackMutationResponse,
    operation_id="dismissMovie",
)
async def set_dismissal(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="dismissal_set",
        request_id=request_id,
        expected_revision=expected_revision,
    )


@app.delete(
    "/users/{user_id}/movies/{movie_id}/dismissal",
    response_model=FeedbackMutationResponse,
    operation_id="undoMovieDismissal",
)
async def delete_dismissal(
    user_id: int,
    movie_id: int,
    request: Request,
    request_id: UUID | None = Header(default=None, alias="Idempotency-Key"),
    expected_revision: int | None = Query(default=None, ge=0),
) -> FeedbackMutationResponse:
    return await _feedback_mutation(
        request,
        user_id=user_id,
        movie_id=movie_id,
        action="dismissal_deleted",
        request_id=request_id,
        expected_revision=expected_revision,
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
        await run_in_threadpool(
            _feedback.mutate,
            connection,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=user_id,
            movie_id=movie_id,
            action="rating_set",
            request_id=uuid4(),
            rating=payload.rating,
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
    """Compatibility bulk rating clear; watched history is preserved."""
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    try:
        _feedback.require_persona(connection, user_id=user_id)
    except UnknownDemoPersonaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    movie_ids = [
        int(row.movie_id)
        for row in connection.execute(
            text("""
                SELECT movie_id
                FROM user_movie_state
                WHERE user_id = :user_id AND rating IS NOT NULL
                ORDER BY movie_id ASC
                """),
            {"user_id": user_id},
        )
    ]
    for movie_id in movie_ids:
        await run_in_threadpool(
            _feedback.mutate,
            connection,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=user_id,
            movie_id=movie_id,
            action="rating_deleted",
            request_id=uuid4(),
        )
    return RatingMutationResponse(
        tenant_id=principal.tenant_id,
        user_id=user_id,
        changed=len(movie_ids),
    )


async def _feedback_mutation(
    request: Request,
    *,
    user_id: int,
    movie_id: int,
    action: FeedbackAction,
    request_id: UUID | None,
    expected_revision: int | None,
    rating: float | None = None,
) -> FeedbackMutationResponse:
    _require_demo_persona_access(request)
    principal = request.state.principal
    connection: Connection = request.state.db
    resolved_request_id = request_id or uuid4()
    try:
        result = await run_in_threadpool(
            _feedback.mutate,
            connection,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=user_id,
            movie_id=movie_id,
            action=action,
            request_id=resolved_request_id,
            rating=rating,
            expected_revision=expected_revision,
        )
    except (UnknownDemoPersonaError, UnknownMovieError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        StateRevisionConflictError,
        IdempotencyConflictError,
        InvalidStateTransitionError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _mutation_response(result)


def _movie_state_response(state: MovieState) -> MovieStateResponse:
    return MovieStateResponse(
        tenant_id=state.tenant_id,
        user_id=state.user_id,
        movie_id=state.movie_id,
        watched_at=state.watched_at,
        rating=state.rating,
        rating_updated_at=state.rating_updated_at,
        watchlisted_at=state.watchlisted_at,
        dismissed_at=state.dismissed_at,
        revision=state.state_version,
        updated_at=state.updated_at,
    )


def _catalog_item_response(item: CatalogMovie) -> CatalogItem:
    return CatalogItem(
        movie_id=item.movie_id,
        title=item.title,
        genres=item.genres,
        tmdb_id=item.tmdb_id,
        release_year=item.release_year,
        poster_url=item.poster_url,
        overview=item.overview,
        metadata_source=item.metadata_source,
        source_status=item.source_status,
        state=_movie_state_response(item.state) if item.state is not None else None,
        interaction_count=item.interaction_count,
    )


def _mutation_response(result: MutationResult) -> FeedbackMutationResponse:
    return FeedbackMutationResponse(
        request_id=result.request_id,
        replayed=result.replayed,
        outcome=result.outcome,
        state=_movie_state_response(result.state),
    )


def _library_response(
    page: LibraryPage,
    *,
    tenant_id: str,
    user_id: int,
    tab: Literal["rated", "watchlist", "history"],
    sort: Literal["recent", "title", "rating"],
    query: str | None,
) -> LibraryResponse:
    return LibraryResponse(
        tenant_id=tenant_id,
        user_id=user_id,
        tab=tab,
        sort=sort,
        query=query,
        counts=LibraryCountsResponse(**page.counts.__dict__),
        page=CursorPageResponse(next_cursor=page.next_cursor, has_more=page.has_more),
        items=[
            LibraryMovieResponse(
                movie_id=item.movie_id,
                title=item.title,
                genres=item.genres,
                state=_movie_state_response(item.state),
            )
            for item in page.items
        ],
    )
