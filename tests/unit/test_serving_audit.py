from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from src.serving.audit import (
    PredictionAudit,
    RecommendationAuditContext,
    RecommendationAuditMiddleware,
    RecommendationAuditService,
)


class _Result:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._rows)


class _Connection:
    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), params))
        return _Result(self.rows)


def test_record_persists_versions_predictions_features_and_latency() -> None:
    connection = _Connection()
    request_id = uuid4()

    RecommendationAuditService().record(
        cast(Connection, connection),
        request_id=request_id,
        tenant_id="demo",
        actor_user_id="keycloak-user",
        user_id=900000101,
        endpoint="/users/{user_id}/recommendations",
        http_status=200,
        outcome="success",
        latency_ms=12.5,
        context=RecommendationAuditContext(
            policy="item-item-cosine+lightgbm",
            model_version="candidate-v1/ranker-v1",
            candidate_version="candidate-v1",
            ranker_version="ranker-v1",
            feature_version="features-v1",
            fallback_reason=None,
            candidate_latency_ms=0.2,
            feature_latency_ms=3.0,
            ranker_latency_ms=0.5,
            model_latency_ms=4.25,
            predictions=[
                PredictionAudit(
                    movie_id=42,
                    score=0.8,
                    features={"user_genre_affinity": 0.9},
                )
            ],
        ),
    )

    sql, values = connection.calls[0]
    assert "INSERT INTO recommendation_audits" in sql
    assert values["request_id"] == request_id
    assert values["tenant_id"] == "demo"
    assert values["latency_ms"] == 12.5
    assert values["feature_latency_ms"] == 3.0
    assert values["predictions"] == [
        {
            "movie_id": 42,
            "score": 0.8,
            "features": {"user_genre_affinity": 0.9},
        }
    ]


def test_record_without_serving_context_is_explicitly_not_run() -> None:
    connection = _Connection()

    RecommendationAuditService().record(
        cast(Connection, connection),
        request_id=uuid4(),
        tenant_id="demo",
        actor_user_id="keycloak-user",
        user_id=900000101,
        endpoint="/users/{user_id}/recommendations",
        http_status=422,
        outcome="client-error",
        latency_ms=0.5,
        context=None,
    )

    values = connection.calls[0][1]
    assert values["policy"] == "not-run"
    assert values["feature_version"] == "not-read"
    assert values["predictions"] == []


def test_list_for_user_maps_newest_tenant_scoped_rows() -> None:
    request_id = uuid4()
    now = datetime.now(UTC)
    connection = _Connection(
        [
            SimpleNamespace(
                request_id=request_id,
                tenant_id="demo",
                actor_user_id="keycloak-user",
                user_id=900000101,
                endpoint="/users/{user_id}/recommendations",
                http_status=200,
                outcome="success",
                policy="popularity",
                model_version="popularity-v1",
                candidate_version="popularity-v1",
                ranker_version="not-run",
                feature_version="not-read",
                fallback_reason="cold-start",
                candidate_latency_ms=0.0,
                feature_latency_ms=0.0,
                ranker_latency_ms=0.0,
                model_latency_ms=0.0,
                latency_ms=2.0,
                predictions=[{"movie_id": 1, "score": 2.0, "features": {}}],
                created_at=now,
            )
        ]
    )

    records = RecommendationAuditService().list_for_user(
        cast(Connection, connection), user_id=900000101, limit=5
    )

    assert records[0].request_id == UUID(str(request_id))
    assert records[0].tenant_id == "demo"
    assert records[0].fallback_reason == "cold-start"
    assert connection.calls[0][1] == {"user_id": 900000101, "limit": 5}


class _AuditRecorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def record(self, connection: Any, **values: Any) -> None:
        if self.fail:
            raise RuntimeError("audit insert failed")
        self.calls.append({"connection": connection, **values})


def _audit_app(recorder: _AuditRecorder) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RecommendationAuditMiddleware,
        audits=cast(RecommendationAuditService, recorder),
    )

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.principal = SimpleNamespace(tenant_id="demo", user_id="actor")
        request.state.db = object()
        return await call_next(request)

    @app.get("/users/{user_id}/recommendations")
    async def recommendations(user_id: int, request: Request) -> dict[str, int]:
        request.state.recommendation_audit_context = None
        return {"user_id": user_id}

    return app


def test_middleware_emits_exactly_one_audit_and_request_id() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get("/users/42/recommendations")

    assert response.status_code == 200
    assert UUID(response.headers["x-request-id"])
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["tenant_id"] == "demo"
    assert recorder.calls[0]["user_id"] == 42
    assert recorder.calls[0]["outcome"] == "success"


def test_middleware_does_not_acknowledge_failed_audit_insert() -> None:
    recorder = _AuditRecorder(fail=True)
    client = TestClient(_audit_app(recorder), raise_server_exceptions=False)

    response = client.get("/users/42/recommendations")

    assert response.status_code == 500
