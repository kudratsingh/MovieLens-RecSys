"""The generic request audit: what gets a row, what does not, and in what order.

The prediction audit's tests live next door in ``test_serving_audit.py``; this
file is about the other table — one operational row per authenticated request,
written on the request's own RLS-bound transaction and therefore before the
auth middleware commits it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from src.serving.audit import RECOMMENDATION_ENDPOINT
from src.serving.ratelimit import RateLimitMiddleware, TokenBucketLimiter
from src.serving.request_audit import (
    UNMATCHED_ENDPOINT,
    RequestAuditMiddleware,
    RequestAuditRecord,
    RequestAuditService,
)
from src.serving.request_id import RequestIdMiddleware


class _Result:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def __iter__(self) -> Iterator[SimpleNamespace]:
        return iter(self._rows)


class _Connection:
    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), params))
        return _Result(self.rows)


def test_record_persists_the_six_columns_the_non_negotiable_names() -> None:
    connection = _Connection()
    request_id = uuid4()

    RequestAuditService().record(
        cast(Connection, connection),
        request_id=request_id,
        correlation_id="bff-library-1",
        tenant_id="demo",
        actor_user_id="keycloak-user",
        user_id=900000101,
        endpoint="/users/{user_id}/library",
        method="GET",
        http_status=200,
        outcome="success",
        latency_ms=4.5,
    )

    sql, values = connection.calls[0]
    assert "INSERT INTO request_audits" in sql
    assert values["request_id"] == request_id
    assert values["correlation_id"] == "bff-library-1"
    assert values["tenant_id"] == "demo"
    assert values["user_id"] == 900000101
    assert values["endpoint"] == "/users/{user_id}/library"
    assert values["method"] == "GET"
    assert values["outcome"] == "success"
    assert values["latency_ms"] == 4.5
    # No route runs a model outside the ones the prediction audit owns, so the
    # column is null rather than carrying a placeholder that reads like a fact.
    assert values["model_version"] is None


def test_list_for_user_maps_newest_tenant_scoped_rows() -> None:
    request_id = uuid4()
    now = datetime.now(UTC)
    connection = _Connection(
        [
            SimpleNamespace(
                request_id=request_id,
                correlation_id="bff-library-2",
                tenant_id="demo",
                actor_user_id="keycloak-user",
                user_id=900000101,
                endpoint="/users/{user_id}/catalog",
                method="GET",
                http_status=200,
                outcome="success",
                latency_ms=6.25,
                model_version=None,
                created_at=now,
            )
        ]
    )

    records = RequestAuditService().list_for_user(
        cast(Connection, connection), user_id=900000101, limit=5
    )

    assert records == [
        RequestAuditRecord(
            request_id=UUID(str(request_id)),
            correlation_id="bff-library-2",
            tenant_id="demo",
            actor_user_id="keycloak-user",
            user_id=900000101,
            endpoint="/users/{user_id}/catalog",
            method="GET",
            http_status=200,
            outcome="success",
            latency_ms=6.25,
            model_version=None,
            created_at=now,
        )
    ]
    sql, params = connection.calls[0]
    assert "FROM request_audits" in sql
    assert "WHERE user_id = :user_id" in sql
    assert params == {"user_id": 900000101, "limit": 5}


class _AuditRecorder:
    """Stands in for the service, and records the connection it was handed."""

    def __init__(self, *, journal: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.journal = journal if journal is not None else []

    def record(self, connection: Any, **values: Any) -> None:
        self.journal.append("insert")
        self.calls.append({"connection": connection, **values})


class _FakeEngine:
    """Hands out one connection for the out-of-band failure path."""

    def __init__(self) -> None:
        self.connections: list[_Connection] = []
        self.set_local: list[dict[str, Any]] = []

    @contextmanager
    def begin(self) -> Iterator[_Connection]:
        connection = _Connection()
        self.connections.append(connection)
        yield connection
        # A real engine commits here; the fake only needs to have handed the
        # middleware a connection that is not the request's.

    def record_set_local(self, params: dict[str, Any]) -> None:
        self.set_local.append(params)


REQUEST_CONNECTION = object()


def _audit_app(
    recorder: _AuditRecorder,
    *,
    engine: _FakeEngine | None = None,
    journal: list[str] | None = None,
    limiter: TokenBucketLimiter | None = None,
) -> FastAPI:
    """Mirror production's middleware order around a handful of stand-in routes.

    Outermost to innermost: request id, the auth stand-in that owns the
    transaction, the rate limiter, then the audit middleware. The auth stand-in
    appends ``commit`` to the journal after ``call_next`` returns, which is what
    makes the ordering assertion below meaningful rather than incidental.
    """
    app = FastAPI()
    app.add_middleware(
        RequestAuditMiddleware,
        audits=cast(RequestAuditService, recorder),
        engine=cast(Engine, engine or _FakeEngine()),
    )
    if limiter is not None:
        app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path not in {"/healthz", "/readyz"} and not request.headers.get(
            "x-anonymous"
        ):
            request.state.principal = SimpleNamespace(tenant_id="demo", user_id="actor")
            request.state.db = REQUEST_CONNECTION
        response = await call_next(request)
        if journal is not None:
            journal.append("commit")
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/whoami")
    async def whoami() -> dict[str, str]:
        return {"tenant_id": "demo"}

    @app.get("/users/{user_id}/recommendations")
    async def recommendations(user_id: int) -> dict[str, int]:
        return {"user_id": user_id}

    @app.get("/users/{user_id}/catalog")
    async def catalog(user_id: int) -> dict[str, int]:
        return {"user_id": user_id}

    @app.put("/users/{user_id}/movies/{movie_id}/rating")
    async def rate(user_id: int, movie_id: int) -> dict[str, int]:
        return {"user_id": user_id, "movie_id": movie_id}

    @app.get("/users/{user_id}/boom")
    async def boom(user_id: int) -> dict[str, int]:
        raise RuntimeError("handler failed")

    app.add_middleware(RequestIdMiddleware)
    return app


def test_a_read_is_audited_under_its_route_template() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get("/users/42/catalog")

    assert response.status_code == 200
    assert len(recorder.calls) == 1
    row = recorder.calls[0]
    # The template, not `/users/42/catalog` — a concrete path would mint one
    # endpoint value per persona and make the column useless for grouping.
    assert row["endpoint"] == "/users/{user_id}/catalog"
    assert row["method"] == "GET"
    assert row["user_id"] == 42
    assert row["tenant_id"] == "demo"
    assert row["actor_user_id"] == "actor"
    assert row["outcome"] == "success"
    assert row["http_status"] == 200
    assert row["latency_ms"] >= 0
    assert row["correlation_id"] == response.headers["x-request-id"]


def test_a_mutation_is_audited_with_its_method() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).put("/users/42/movies/7/rating")

    assert response.status_code == 200
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["endpoint"] == "/users/{user_id}/movies/{movie_id}/rating"
    assert recorder.calls[0]["method"] == "PUT"


def test_the_row_is_written_on_the_request_connection_before_the_commit() -> None:
    journal: list[str] = []
    recorder = _AuditRecorder(journal=journal)

    response = TestClient(_audit_app(recorder, journal=journal)).get("/users/42/catalog")

    assert response.status_code == 200
    # The auth middleware owns the commit and it happens outside this one, so
    # the insert has to already be on the same connection when it runs.
    assert journal == ["insert", "commit"]
    assert recorder.calls[0]["connection"] is REQUEST_CONNECTION


def test_an_authenticated_route_without_a_persona_records_a_null_user() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get("/whoami")

    assert response.status_code == 200
    assert recorder.calls[0]["endpoint"] == "/whoami"
    assert recorder.calls[0]["user_id"] is None


def test_an_unmatched_path_cannot_fan_out_into_distinct_endpoints() -> None:
    recorder = _AuditRecorder()
    client = TestClient(_audit_app(recorder))

    first = client.get("/users/42/does-not-exist")
    second = client.get("/users/43/also-not-a-route")

    assert first.status_code == 404
    assert second.status_code == 404
    assert [call["endpoint"] for call in recorder.calls] == [
        UNMATCHED_ENDPOINT,
        UNMATCHED_ENDPOINT,
    ]


def test_the_unauthenticated_probes_are_not_audited() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get("/healthz")

    assert response.status_code == 200
    assert recorder.calls == []


def test_a_request_auth_refused_is_not_audited() -> None:
    """No principal, no tenant to scope a forced-RLS row to, no row.

    In production the auth middleware answers the 401 before this middleware is
    reached at all; the stand-in reproduces the state that would have to hold
    for it to be reached anyway.
    """
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get(
        "/users/42/catalog", headers={"x-anonymous": "1"}
    )

    assert response.status_code == 200
    assert recorder.calls == []


def test_recommendations_are_left_to_the_prediction_audit() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get("/users/42/recommendations")

    assert response.status_code == 200
    assert RECOMMENDATION_ENDPOINT == "/users/{user_id}/recommendations"
    # One authenticated request, one audit row — the richer one, written by
    # `RecommendationAuditMiddleware`. A second insert here would sit inside the
    # p99 the k6 gate measures and duplicate what is already stored.
    assert recorder.calls == []


def test_a_throttled_request_writes_no_audit() -> None:
    """ADR 0014: a limiter that turns each refusal into a write amplifies the
    burst it exists to shed. The limiter is registered outside this middleware
    precisely so a 429 never reaches it."""
    recorder = _AuditRecorder()
    limiter = TokenBucketLimiter(requests_per_minute=60, burst=1)
    client = TestClient(_audit_app(recorder, limiter=limiter))

    allowed = client.get("/users/42/catalog")
    throttled = client.get("/users/42/catalog")

    assert allowed.status_code == 200
    assert throttled.status_code == 429
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["http_status"] == 200


def test_a_failing_handler_is_audited_out_of_band_and_still_fails() -> None:
    """The handler's transaction must still roll back — a mutation that raised
    mid-flight cannot be allowed to commit just because we want the audit. So
    the row goes on its own RLS-scoped transaction and the exception is
    re-raised unchanged."""
    engine = _FakeEngine()
    recorder = _AuditRecorder()
    client = TestClient(_audit_app(recorder, engine=engine), raise_server_exceptions=False)

    response = client.get("/users/42/boom")

    assert response.status_code == 500
    assert len(recorder.calls) == 1
    row = recorder.calls[0]
    assert row["endpoint"] == "/users/{user_id}/boom"
    assert row["http_status"] == 500
    assert row["outcome"] == "server-error"
    # Not the request's connection: that one is about to be rolled back.
    assert row["connection"] is not REQUEST_CONNECTION
    assert row["connection"] is engine.connections[0]
    # The fresh transaction carries the same verified tenant, so the forced-RLS
    # policy admits the row for exactly the tenant that made the request.
    set_local_sql, set_local_params = engine.connections[0].calls[0]
    assert "SET LOCAL app.tenant_id" in set_local_sql
    assert set_local_params == {"tid": "demo"}


def test_an_audit_failure_never_replaces_the_error_that_caused_it() -> None:
    class _BrokenEngine(_FakeEngine):
        @contextmanager
        def begin(self) -> Iterator[_Connection]:
            raise RuntimeError("database unreachable")
            yield _Connection()  # pragma: no cover - unreachable, satisfies the type

    recorder = _AuditRecorder()
    client = TestClient(
        _audit_app(recorder, engine=cast(_FakeEngine, _BrokenEngine())),
        raise_server_exceptions=True,
    )

    with pytest.raises(RuntimeError, match="handler failed"):
        client.get("/users/42/boom")
