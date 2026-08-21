"""RLS-scoped persistence for recommendation prediction audits."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request, Response
from sqlalchemy import JSON, Connection, bindparam, text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_RECOMMENDATION_PATH = re.compile(r"^/users/(?P<user_id>-?\d+)/recommendations/?$")
_RECOMMENDATION_ENDPOINT = "/users/{user_id}/recommendations"


@dataclass(frozen=True)
class PredictionAudit:
    movie_id: int
    score: float
    features: dict[str, float]

    def as_json(self) -> dict[str, object]:
        return {
            "movie_id": self.movie_id,
            "score": self.score,
            "features": self.features,
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


@dataclass(frozen=True)
class RecommendationAuditRecord:
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
    predictions: list[dict[str, Any]]
    created_at: datetime


_INSERT_AUDIT = text("""
    INSERT INTO recommendation_audits (
        request_id,
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
        predictions
    ) VALUES (
        :request_id,
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
        :predictions
    )
    """).bindparams(bindparam("predictions", type_=JSON))


class RecommendationAuditService:
    """Write and read audits through the authenticated request connection."""

    def record(
        self,
        connection: Connection,
        *,
        request_id: UUID,
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
        )
        connection.execute(
            _INSERT_AUDIT,
            {
                "request_id": request_id,
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
                    created_at
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
            )
            for row in rows
        ]


class RecommendationAuditMiddleware(BaseHTTPMiddleware):
    """Emit exactly one durable audit for each authenticated recommendation GET.

    ``AuthMiddleware`` is registered outside this middleware, so the RLS-bound
    connection remains open until the audit insert completes. An insert failure
    therefore fails the request; the successful transaction commits on the
    response background boundary immediately after the body is flushed.
    """

    def __init__(self, app: ASGIApp, *, audits: RecommendationAuditService) -> None:
        super().__init__(app)
        self._audits = audits

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        match = _RECOMMENDATION_PATH.match(request.url.path)
        if request.method != "GET" or match is None:
            return await call_next(request)

        started = time.perf_counter()
        request_id = uuid4()
        request.state.recommendation_audit_context = None
        user_id = int(match.group("user_id"))
        try:
            response = await call_next(request)
        except Exception:
            await run_in_threadpool(
                self._record,
                request,
                request_id=request_id,
                user_id=user_id,
                http_status=500,
                outcome="server-error",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            logger.exception("recommendation request failed request_id=%s", request_id)
            return JSONResponse(
                {"detail": "recommendation request failed"},
                status_code=500,
                headers={"X-Request-ID": str(request_id)},
            )

        await run_in_threadpool(
            self._record,
            request,
            request_id=request_id,
            user_id=user_id,
            http_status=response.status_code,
            outcome=_outcome(response.status_code),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        response.headers["X-Request-ID"] = str(request_id)
        return response

    def _record(
        self,
        request: Request,
        *,
        request_id: UUID,
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
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=user_id,
            endpoint=_RECOMMENDATION_ENDPOINT,
            http_status=http_status,
            outcome=outcome,
            latency_ms=latency_ms,
            context=context,
        )


def _outcome(status_code: int) -> str:
    if status_code < 400:
        return "success"
    if status_code < 500:
        return "client-error"
    return "server-error"
