"""A durable operational audit row for every authenticated request.

``src.serving.audit`` answers *why did this user see that title*: predictions,
online feature values, artifact versions, the input state a ranking was
computed against. It exists for one route. This module answers the other
question — *who called what, when, how did it end, how long did it take* — for
every authenticated route, which is what non-negotiable #8 and the Phase 3
"Real auth" scope ask for and what the deployment has never had.

**Why the row is written inline, inside the request's own transaction.**
``AuthMiddleware`` already opens a transaction per authenticated request and
commits it before the response goes out, reads included, so the row costs the
insert itself and joins a commit that was going to happen anyway. On a read
that commit is currently a no-op — a read-only transaction writes no WAL and
flushes nothing — so the honest accounting is that the row turns a free commit
into one `fdatasync` on reads, and is close to free on the mutations that were
already paying for one. That is the whole cost, it is bounded by the storage
under the WAL, and ADR 0012's 2026-08-29 note argues it out against the
alternative (an in-process queue flushed off the request path) and against the
one path where it is not worth paying: recommendations, which already write a
richer row and sit inside the p99 SLO the k6 gate measures.

The tradeoff is reversible without a migration: ``REQUEST_AUDIT_MODE=off``
leaves the table in place and installs no middleware.

**Why ``BaseHTTPMiddleware`` here** when ``RequestIdMiddleware`` and
``RateLimitMiddleware`` are deliberately raw ASGI. Those two decide before the
handler runs and never need to hold a response. This one must write its row
*before* ``AuthMiddleware`` commits, and a raw ASGI middleware forwards
``http.response.start`` the instant the router emits it — which releases the
outer ``call_next`` and lets the commit race the insert. Buffering the response
by hand to close that race is what ``BaseHTTPMiddleware`` already does
correctly, so this uses it, exactly as ``RecommendationAuditMiddleware`` does.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy import Connection, Engine, text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.auth.middleware import UNAUTHENTICATED_PATHS
from src.serving.audit import (
    RECOMMENDATION_ENDPOINT,
    outcome_for_status,
    resolve_audit_identity,
)

logger = logging.getLogger(__name__)

# What ``endpoint`` holds when the router matched nothing — an authenticated
# request for a path this service does not serve. Recorded rather than dropped
# (a caller probing the surface is exactly what an audit is for), but recorded
# under one constant so unknown paths cannot fan out into distinct rows.
UNMATCHED_ENDPOINT = "<unmatched>"

# Routes whose audit is somebody else's. ``recommendation_audits`` already
# stores a strictly richer row for this operation, and writing a second one
# would put another insert inside the one path with a latency SLO. Skipping is
# simpler than a cross-table pointer and loses nothing: an operator reading
# request traffic for a tenant unions two tables, and the join key — the
# correlation id — is on both.
RECOMMENDATION_OWNED_ENDPOINTS: frozenset[str] = frozenset({RECOMMENDATION_ENDPOINT})

# Where a handler may leave a model version for the audit to pick up. Nothing
# writes it today: the endpoints that run a model are the ones skipped above.
# The key exists because non-negotiable #8 names the column and Phase 6's
# per-tenant champion routing is what will fill it.
MODEL_VERSION_STATE_KEY = "request_audit_model_version"


@dataclass(frozen=True)
class RequestAuditRecord:
    request_id: UUID
    correlation_id: str
    tenant_id: str
    actor_user_id: str
    user_id: int | None
    endpoint: str
    method: str
    http_status: int
    outcome: str
    latency_ms: float
    model_version: str | None
    created_at: datetime


_INSERT_REQUEST_AUDIT = text("""
    INSERT INTO request_audits (
        request_id,
        correlation_id,
        tenant_id,
        actor_user_id,
        user_id,
        endpoint,
        method,
        http_status,
        outcome,
        latency_ms,
        model_version
    ) VALUES (
        :request_id,
        :correlation_id,
        :tenant_id,
        :actor_user_id,
        :user_id,
        :endpoint,
        :method,
        :http_status,
        :outcome,
        :latency_ms,
        :model_version
    )
    """)

_SELECT_REQUEST_AUDITS = text("""
    SELECT
        request_id,
        correlation_id,
        tenant_id,
        actor_user_id,
        user_id,
        endpoint,
        method,
        http_status,
        outcome,
        latency_ms,
        model_version,
        created_at
    FROM request_audits
    WHERE user_id = :user_id
    ORDER BY created_at DESC, request_id DESC
    LIMIT :limit
    """)


class RequestAuditService:
    """Write and read generic request audits through an RLS-bound connection."""

    def record(
        self,
        connection: Connection,
        *,
        request_id: UUID,
        correlation_id: str,
        tenant_id: str,
        actor_user_id: str,
        user_id: int | None,
        endpoint: str,
        method: str,
        http_status: int,
        outcome: str,
        latency_ms: float,
        model_version: str | None = None,
    ) -> None:
        connection.execute(
            _INSERT_REQUEST_AUDIT,
            {
                "request_id": request_id,
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "user_id": user_id,
                "endpoint": endpoint,
                "method": method,
                "http_status": http_status,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "model_version": model_version,
            },
        )

    def list_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
    ) -> list[RequestAuditRecord]:
        """Newest rows addressed to one persona, inside the request's tenant.

        Requests that address no persona — ``/whoami``, ``/personas`` — store a
        null ``user_id`` and are deliberately not returned here: this resource
        answers "what has been done to this persona", and a row that names no
        persona is not an answer to it. Those rows stay readable to an operator
        holding the ``admin_user`` role.
        """
        rows = connection.execute(
            _SELECT_REQUEST_AUDITS,
            {"user_id": user_id, "limit": limit},
        )
        return [
            RequestAuditRecord(
                request_id=UUID(str(row.request_id)),
                correlation_id=str(row.correlation_id),
                tenant_id=str(row.tenant_id),
                actor_user_id=str(row.actor_user_id),
                user_id=int(row.user_id) if row.user_id is not None else None,
                endpoint=str(row.endpoint),
                method=str(row.method),
                http_status=int(row.http_status),
                outcome=str(row.outcome),
                latency_ms=float(row.latency_ms),
                model_version=(str(row.model_version) if row.model_version is not None else None),
                created_at=row.created_at,
            )
            for row in rows
        ]


class RequestAuditMiddleware(BaseHTTPMiddleware):
    """Persist one operational audit row per authenticated request.

    Registered inside ``AuthMiddleware`` (which resolves the principal and owns
    the transaction) and inside ``RateLimitMiddleware``, so a throttled request
    is never audited — it reached no handler, and ADR 0014's limiter must not
    turn a burst it is shedding into a write per rejected request.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        audits: RequestAuditService,
        engine: Engine,
        skip_endpoints: frozenset[str] = RECOMMENDATION_OWNED_ENDPOINTS,
        exempt_paths: frozenset[str] = UNAUTHENTICATED_PATHS,
    ) -> None:
        super().__init__(app)
        self._audits = audits
        self._engine = engine
        self._skip_endpoints = skip_endpoints
        # The same frozenset the auth middleware, the limiter and the OpenAPI
        # generator use: an unauthenticated probe carries no tenant to key a
        # forced-RLS row on, so there is nothing to write even in principle.
        self._exempt_paths = exempt_paths

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The handler's transaction is about to be rolled back by
            # AuthMiddleware, and rightly so — a mutation that raised
            # mid-flight must not become durable. So this one row is written on
            # its own short-lived transaction on the same RLS-applied engine,
            # with the tenant set from the same verified principal, and the
            # exception is re-raised unchanged. The request's own semantics do
            # not move; the audit simply outlives the rollback.
            #
            # `Exception` rather than `BaseException` on purpose: a cancelled
            # request is a client that hung up, not an outcome worth a durable
            # row and a fresh connection to write it on.
            await self._record_out_of_band(
                request,
                http_status=500,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise

        if not self._should_audit(request):
            return response

        await run_in_threadpool(
            self._record,
            request,
            connection=request.state.db,
            http_status=response.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return response

    def _should_audit(self, request: Request) -> bool:
        # No principal means auth answered before us — a 401 or an unreachable
        # JWKS. There is no tenant to scope the row to and no caller identity
        # worth recording beyond what the auth log already carries.
        if getattr(request.state, "principal", None) is None:
            return False
        return _endpoint_template(request) not in self._skip_endpoints

    def _record(
        self,
        request: Request,
        *,
        connection: Connection,
        http_status: int,
        latency_ms: float,
    ) -> None:
        request_id, correlation_id = resolve_audit_identity(request)
        principal = request.state.principal
        self._audits.record(
            connection,
            request_id=request_id,
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            user_id=_persona_id(request),
            endpoint=_endpoint_template(request),
            method=request.method,
            http_status=http_status,
            outcome=outcome_for_status(http_status),
            latency_ms=latency_ms,
            model_version=getattr(request.state, MODEL_VERSION_STATE_KEY, None),
        )

    async def _record_out_of_band(
        self,
        request: Request,
        *,
        http_status: int,
        latency_ms: float,
    ) -> None:
        if not self._should_audit(request):
            return
        principal = request.state.principal
        try:
            await run_in_threadpool(
                self._insert_on_a_fresh_transaction,
                request,
                tenant_id=principal.tenant_id,
                http_status=http_status,
                latency_ms=latency_ms,
            )
        except Exception:
            # Never let the audit's own failure replace the exception that
            # actually broke the request — that is the one the operator needs,
            # and Starlette's error handler is about to log it.
            logger.exception(
                "request audit could not be written for a failed request path=%s",
                request.url.path,
            )

    def _insert_on_a_fresh_transaction(
        self,
        request: Request,
        *,
        tenant_id: str,
        http_status: int,
        latency_ms: float,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            self._record(
                request,
                connection=connection,
                http_status=http_status,
                latency_ms=latency_ms,
            )


def _endpoint_template(request: Request) -> str:
    """The matched route's template, never the concrete path.

    FastAPI puts the matched ``APIRoute`` on the scope before the handler runs
    (and on a 405's partial match too), so this is the router's own answer
    rather than a second attempt at routing. Recording ``request.url.path``
    instead would mint one ``endpoint`` value per persona and per movie id,
    which makes the column useless for grouping and unbounded in cardinality.
    """
    route: Any = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ENDPOINT


def _persona_id(request: Request) -> int | None:
    """The persona a ``/users/{user_id}/...`` route addresses, if any.

    Path parameters arrive as strings — FastAPI converts them inside the
    handler, not in the router — so a route that names a ``user_id`` this
    service could never serve is recorded with a null rather than failing the
    audit of a request that was itself refused with a 422.
    """
    raw = request.scope.get("path_params", {}).get("user_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
