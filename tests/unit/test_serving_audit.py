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
from src.serving.policy import EXCLUSION_FILTER_POLICY, FILTER_POLICY_NOT_RUN
from src.serving.request_id import RequestIdMiddleware


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
    event_time = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

    RecommendationAuditService().record(
        cast(Connection, connection),
        request_id=request_id,
        correlation_id="bff-discover-1",
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
                    candidate_source="item-item-cosine",
                    seed_movie_id=7,
                )
            ],
            input_state_revision=9,
            input_state_hash="positive-digest",
            exclusion_hash="exclusion-digest",
            positive_signal_count=12,
            excluded_count=2,
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=event_time,
            candidate_sources={"item-item-cosine": 40, "popularity-fill": 10},
            reason="learned-two-stage: item-item-cosine retrieval over 12 positive seeds",
        ),
    )

    sql, values = connection.calls[0]
    assert "INSERT INTO recommendation_audits" in sql
    assert values["request_id"] == request_id
    assert values["correlation_id"] == "bff-discover-1"
    assert values["tenant_id"] == "demo"
    assert values["latency_ms"] == 12.5
    assert values["feature_latency_ms"] == 3.0
    assert values["predictions"] == [
        {
            "movie_id": 42,
            "score": 0.8,
            "features": {"user_genre_affinity": 0.9},
            "candidate_source": "item-item-cosine",
            "seed_movie_id": 7,
        }
    ]
    assert values["input_state_revision"] == 9
    assert values["input_state_hash"] == "positive-digest"
    assert values["exclusion_hash"] == "exclusion-digest"
    assert values["positive_signal_count"] == 12
    assert values["excluded_count"] == 2
    assert values["filter_policy"] == EXCLUSION_FILTER_POLICY
    assert values["feature_event_time"] == event_time
    assert values["candidate_sources"] == {"item-item-cosine": 40, "popularity-fill": 10}
    assert values["reason"].startswith("learned-two-stage")


def test_record_without_serving_context_is_explicitly_not_run() -> None:
    connection = _Connection()

    RecommendationAuditService().record(
        cast(Connection, connection),
        request_id=uuid4(),
        correlation_id="bff-discover-2",
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
    assert values["filter_policy"] == FILTER_POLICY_NOT_RUN
    assert values["feature_event_time"] is None
    assert values["candidate_sources"] == {}
    assert values["reason"] == "not-run"


def test_list_for_user_maps_newest_tenant_scoped_rows() -> None:
    request_id = uuid4()
    now = datetime.now(UTC)
    connection = _Connection(
        [
            SimpleNamespace(
                request_id=request_id,
                correlation_id="bff-discover-3",
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
                input_state_revision=4,
                input_state_hash="positive-digest",
                exclusion_hash="exclusion-digest",
                positive_signal_count=3,
                excluded_count=1,
                filter_policy=EXCLUSION_FILTER_POLICY,
                feature_event_time=None,
                candidate_sources={"popularity-fallback": 1},
                reason="cold-start: 3 positive watched signals below threshold 10",
            )
        ]
    )

    records = RecommendationAuditService().list_for_user(
        cast(Connection, connection), user_id=900000101, limit=5
    )

    assert records[0].request_id == UUID(str(request_id))
    assert records[0].correlation_id == "bff-discover-3"
    assert records[0].tenant_id == "demo"
    assert records[0].fallback_reason == "cold-start"
    assert records[0].input_state_revision == 4
    assert records[0].exclusion_hash == "exclusion-digest"
    assert records[0].positive_signal_count == 3
    assert records[0].excluded_count == 1
    assert records[0].filter_policy == EXCLUSION_FILTER_POLICY
    assert records[0].candidate_sources == {"popularity-fallback": 1}
    assert records[0].reason.startswith("cold-start")
    assert connection.calls[0][1] == {"user_id": 900000101, "limit": 5}


class _AuditRecorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def record(self, connection: Any, **values: Any) -> None:
        if self.fail:
            raise RuntimeError("audit insert failed")
        self.calls.append({"connection": connection, **values})


def _audit_app(recorder: _AuditRecorder, *, fail_handler: bool = False) -> FastAPI:
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
        if fail_handler:
            raise RuntimeError("handler failed")
        return {"user_id": user_id}

    # Mirrors production ordering: request-id resolution is outermost and owns
    # the response header the audit row correlates on.
    app.add_middleware(RequestIdMiddleware)
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
    # With no inbound header the two identities agree, so a stored audit is
    # findable by the id the caller was handed.
    assert recorder.calls[0]["correlation_id"] == response.headers["x-request-id"]
    assert str(recorder.calls[0]["request_id"]) == response.headers["x-request-id"]


def test_middleware_records_an_adopted_inbound_request_id() -> None:
    recorder = _AuditRecorder()
    response = TestClient(_audit_app(recorder)).get(
        "/users/42/recommendations", headers={"X-Request-ID": "bff-discover-42"}
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "bff-discover-42"
    assert recorder.calls[0]["correlation_id"] == "bff-discover-42"
    # The row keeps its own UUID identity so a replayed correlation header
    # cannot collide with an existing audit's primary key.
    assert isinstance(recorder.calls[0]["request_id"], UUID)
    assert str(recorder.calls[0]["request_id"]) != "bff-discover-42"


def test_middleware_does_not_acknowledge_failed_audit_insert() -> None:
    recorder = _AuditRecorder(fail=True)
    client = TestClient(_audit_app(recorder), raise_server_exceptions=False)

    response = client.get("/users/42/recommendations", headers={"X-Request-ID": "bff-discover-500"})

    # The insert failure propagates so the RLS transaction is never committed.
    # Starlette builds this response in its own error handler, above every
    # middleware an application can register, so it is the one path that cannot
    # carry the echoed request id.
    assert response.status_code == 500
    assert "x-request-id" not in response.headers


def test_a_failing_handler_is_audited_and_still_echoes_the_request_id() -> None:
    recorder = _AuditRecorder()
    client = TestClient(_audit_app(recorder, fail_handler=True), raise_server_exceptions=False)

    response = client.get(
        "/users/42/recommendations", headers={"X-Request-ID": "bff-discover-boom"}
    )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "bff-discover-boom"
    assert recorder.calls[0]["outcome"] == "server-error"
    assert recorder.calls[0]["correlation_id"] == "bff-discover-boom"
