"""RLS-scoped persistence for recommendation prediction audits."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request, Response
from sqlalchemy import JSON, Connection, bindparam, text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.serving.policy import FILTER_POLICY_NOT_RUN
from src.serving.request_id import REQUEST_ID_ADOPTED_STATE_KEY

logger = logging.getLogger(__name__)

# Public because `src.serving.request_audit` skips whatever this middleware
# owns, and it has to skip on the same terms. The route template is what the
# generic audit records, but the *path* pattern is what this middleware matches
# on — and the two disagree on a trailing slash, which the router answers with a
# redirect and no matched route. A second copy of either would let the two
# audits both claim one request.
RECOMMENDATION_PATH = re.compile(r"^/users/(?P<user_id>-?\d+)/recommendations/?$")
RECOMMENDATION_ENDPOINT = "/users/{user_id}/recommendations"


@dataclass(frozen=True)
class PredictionAudit:
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str = "not-run"
    seed_movie_id: int | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "movie_id": self.movie_id,
            "score": self.score,
            "features": self.features,
            "candidate_source": self.candidate_source,
            "seed_movie_id": self.seed_movie_id,
        }


@dataclass(frozen=True)
class RecommendationAuditContext:
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
    predictions: list[PredictionAudit]
    # Bundle 6 evidence: what state the decision was made against, what was
    # filtered out of it, and how fresh the features were. Without these a
    # stored audit cannot be replayed or compared against a later request.
    input_state_revision: int = 0
    input_state_hash: str = ""
    exclusion_hash: str = ""
    positive_signal_count: int = 0
    excluded_count: int = 0
    filter_policy: str = FILTER_POLICY_NOT_RUN
    feature_event_time: datetime | None = None
    candidate_sources: dict[str, int] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class RecommendationAuditRecord:
    request_id: UUID
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
    predictions: list[dict[str, Any]]
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


_INSERT_AUDIT = text("""
    INSERT INTO recommendation_audits (
        request_id,
        correlation_id,
        tenant_id,
        actor_user_id,
        user_id,
        endpoint,
        http_status,
        outcome,
        policy,
        model_version,
        candidate_version,
        ranker_version,
        feature_version,
        fallback_reason,
        candidate_latency_ms,
        feature_latency_ms,
        ranker_latency_ms,
        model_latency_ms,
        latency_ms,
        predictions,
        input_state_revision,
        input_state_hash,
        exclusion_hash,
        positive_signal_count,
        excluded_count,
        filter_policy,
        feature_event_time,
        candidate_sources,
        reason
    ) VALUES (
        :request_id,
        :correlation_id,
        :tenant_id,
        :actor_user_id,
        :user_id,
        :endpoint,
        :http_status,
        :outcome,
        :policy,
        :model_version,
        :candidate_version,
        :ranker_version,
        :feature_version,
        :fallback_reason,
        :candidate_latency_ms,
        :feature_latency_ms,
        :ranker_latency_ms,
        :model_latency_ms,
        :latency_ms,
        :predictions,
        :input_state_revision,
        :input_state_hash,
        :exclusion_hash,
        :positive_signal_count,
        :excluded_count,
        :filter_policy,
        :feature_event_time,
        :candidate_sources,
        :reason
    )
    """).bindparams(
    bindparam("predictions", type_=JSON),
    bindparam("candidate_sources", type_=JSON),
)


class RecommendationAuditService:
    """Write and read audits through the authenticated request connection."""

    def record(
        self,
        connection: Connection,
        *,
        request_id: UUID,
        correlation_id: str,
        tenant_id: str,
        actor_user_id: str,
        user_id: int,
        endpoint: str,
        http_status: int,
        outcome: str,
        latency_ms: float,
        context: RecommendationAuditContext | None,
    ) -> None:
        resolved = context or RecommendationAuditContext(
            policy="not-run",
            model_version="not-run",
            candidate_version="not-run",
            ranker_version="not-run",
            feature_version="not-read",
            fallback_reason=None,
            candidate_latency_ms=0.0,
            feature_latency_ms=0.0,
            ranker_latency_ms=0.0,
            model_latency_ms=0.0,
            predictions=[],
            reason="not-run",
        )
        connection.execute(
            _INSERT_AUDIT,
            {
                "request_id": request_id,
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "user_id": user_id,
                "endpoint": endpoint,
                "http_status": http_status,
                "outcome": outcome,
                "policy": resolved.policy,
                "model_version": resolved.model_version,
                "candidate_version": resolved.candidate_version,
                "ranker_version": resolved.ranker_version,
                "feature_version": resolved.feature_version,
                "fallback_reason": resolved.fallback_reason,
                "candidate_latency_ms": resolved.candidate_latency_ms,
                "feature_latency_ms": resolved.feature_latency_ms,
                "ranker_latency_ms": resolved.ranker_latency_ms,
                "model_latency_ms": resolved.model_latency_ms,
                "latency_ms": latency_ms,
                "predictions": [prediction.as_json() for prediction in resolved.predictions],
                "input_state_revision": resolved.input_state_revision,
                "input_state_hash": resolved.input_state_hash,
                "exclusion_hash": resolved.exclusion_hash,
                "positive_signal_count": resolved.positive_signal_count,
                "excluded_count": resolved.excluded_count,
                "filter_policy": resolved.filter_policy,
                "feature_event_time": resolved.feature_event_time,
                "candidate_sources": dict(resolved.candidate_sources),
                "reason": resolved.reason,
            },
        )

    def list_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
    ) -> list[RecommendationAuditRecord]:
        rows = connection.execute(
            text("""
                SELECT
                    request_id,
                    correlation_id,
                    tenant_id,
                    actor_user_id,
                    user_id,
                    endpoint,
                    http_status,
                    outcome,
                    policy,
                    model_version,
                    candidate_version,
                    ranker_version,
                    feature_version,
                    fallback_reason,
                    candidate_latency_ms,
                    feature_latency_ms,
                    ranker_latency_ms,
                    model_latency_ms,
                    latency_ms,
                    predictions,
                    created_at,
                    input_state_revision,
                    input_state_hash,
                    exclusion_hash,
                    positive_signal_count,
                    excluded_count,
                    filter_policy,
                    feature_event_time,
                    candidate_sources,
                    reason
                FROM recommendation_audits
                WHERE user_id = :user_id
                ORDER BY created_at DESC, request_id DESC
                LIMIT :limit
                """),
            {"user_id": user_id, "limit": limit},
        )
        return [
            RecommendationAuditRecord(
                request_id=UUID(str(row.request_id)),
                correlation_id=str(row.correlation_id),
                tenant_id=str(row.tenant_id),
                actor_user_id=str(row.actor_user_id),
                user_id=int(row.user_id),
                endpoint=str(row.endpoint),
                http_status=int(row.http_status),
                outcome=str(row.outcome),
                policy=str(row.policy),
                model_version=str(row.model_version),
                candidate_version=str(row.candidate_version),
                ranker_version=str(row.ranker_version),
                feature_version=str(row.feature_version),
                fallback_reason=(
                    str(row.fallback_reason) if row.fallback_reason is not None else None
                ),
                candidate_latency_ms=float(row.candidate_latency_ms),
                feature_latency_ms=float(row.feature_latency_ms),
                ranker_latency_ms=float(row.ranker_latency_ms),
                model_latency_ms=float(row.model_latency_ms),
                latency_ms=float(row.latency_ms),
                predictions=list(row.predictions),
                created_at=row.created_at,
                input_state_revision=int(row.input_state_revision),
                input_state_hash=str(row.input_state_hash),
                exclusion_hash=str(row.exclusion_hash),
                positive_signal_count=int(row.positive_signal_count),
                excluded_count=int(row.excluded_count),
                filter_policy=str(row.filter_policy),
                feature_event_time=row.feature_event_time,
                candidate_sources=dict(row.candidate_sources or {}),
                reason=str(row.reason),
            )
            for row in rows
        ]


class RecommendationAuditMiddleware(BaseHTTPMiddleware):
    """Emit exactly one durable audit for each authenticated recommendation GET.

    ``AuthMiddleware`` is registered outside this middleware, so the RLS-bound
    connection remains open until the audit insert completes. An insert failure
    therefore fails the request; ``AuthMiddleware`` commits the successful
    transaction before returning the response.
    """

    def __init__(self, app: ASGIApp, *, audits: RecommendationAuditService) -> None:
        super().__init__(app)
        self._audits = audits

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        match = RECOMMENDATION_PATH.match(request.url.path)
        if request.method != "GET" or match is None:
            return await call_next(request)

        started = time.perf_counter()
        request_id, correlation_id = resolve_audit_identity(request)
        request.state.recommendation_audit_context = None
        user_id = int(match.group("user_id"))
        try:
            response = await call_next(request)
        except Exception:
            await run_in_threadpool(
                self._record,
                request,
                request_id=request_id,
                correlation_id=correlation_id,
                user_id=user_id,
                http_status=500,
                outcome="server-error",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            logger.exception(
                "recommendation request failed request_id=%s correlation_id=%s",
                request_id,
                correlation_id,
            )
            return JSONResponse(
                {"detail": "recommendation request failed"},
                status_code=500,
            )

        await run_in_threadpool(
            self._record,
            request,
            request_id=request_id,
            correlation_id=correlation_id,
            user_id=user_id,
            http_status=response.status_code,
            outcome=outcome_for_status(response.status_code),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return response

    def _record(
        self,
        request: Request,
        *,
        request_id: UUID,
        correlation_id: str,
        user_id: int,
        http_status: int,
        outcome: str,
        latency_ms: float,
    ) -> None:
        principal = request.state.principal
        connection: Connection = request.state.db
        context: RecommendationAuditContext | None = request.state.recommendation_audit_context
        self._audits.record(
            connection,
            request_id=request_id,
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=user_id,
            endpoint=RECOMMENDATION_ENDPOINT,
            http_status=http_status,
            outcome=outcome,
            latency_ms=latency_ms,
            context=context,
        )


def resolve_audit_identity(request: Request) -> tuple[UUID, str]:
    """Return the audit row's primary key and the caller-visible correlation id.

    Shared with ``src.serving.request_audit``: both audit tables key on a UUID
    of their own and store the echoed correlation id beside it, and a second
    copy of this rule would let the two drift.

    When we minted the correlation id ourselves it is already a UUID, so the
    two stay identical and an audit remains findable by the id handed back in
    ``X-Request-ID``. An *adopted* id gets a freshly minted key instead: a
    caller that replays the same correlation header must not be able to
    collide with an existing audit's primary key.
    """
    correlation = getattr(request.state, "request_id", None)
    if not isinstance(correlation, str) or not correlation:
        minted = uuid4()
        return minted, str(minted)
    if getattr(request.state, REQUEST_ID_ADOPTED_STATE_KEY, False):
        return uuid4(), correlation
    return UUID(correlation), correlation


def outcome_for_status(status_code: int) -> str:
    """One outcome vocabulary for both audit tables."""
    if status_code < 400:
        return "success"
    if status_code < 500:
        return "client-error"
    return "server-error"
