"""
Unit tests for the unauthenticated readiness probe and for the API process no
longer holding a BYPASSRLS Postgres engine.

The probe is exercised against the real application object, middleware stack
included, because two of the properties under test are properties of that stack:
a deploy probe carries no Authorization header, and neither the request-id
middleware nor the recommendation-audit middleware may choke on a request that
has no principal and no request transaction.

Dependencies are substituted rather than mocked away — a SQLite engine with the
tenant registry attached, a JWKS cache over a mock transport, and a mock-transport
HTTP client for the sidecars — so the handler runs its real code path.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, StaticPool, create_engine, text
from sqlalchemy.exc import OperationalError

from src.auth.jwks import JwksCache
from src.auth.middleware import UNAUTHENTICATED_PATHS, AuthMiddleware
from src.config import Settings
from src.serving import app as app_module
from src.serving.audit import RecommendationAuditMiddleware
from src.serving.request_id import REQUEST_ID_HEADER

_KEYCLOAK_URL = "http://keycloak.test:8080"


def _registry_engine() -> Engine:
    """SQLite stand-in for the app engine, carrying a ``public.tenants`` table.

    StaticPool keeps one connection alive across threads because the probe runs
    its query in a worker thread, and a fresh in-memory SQLite connection would
    not have the attached schema.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
        connection.execute(
            text("CREATE TABLE public.tenants (id TEXT PRIMARY KEY, display_name TEXT NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO public.tenants (id, display_name) VALUES ('demo', 'Demo tenant')")
        )
    return engine


class _UnreachableEngine:
    """Stands in for an app engine whose pool cannot reach Postgres."""

    def connect(self) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("could not connect to server"))


def _jwks_cache(*, reachable: bool, recorder: list[str] | None = None) -> JwksCache:
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request.url.path)
        if not reachable:
            raise httpx.ConnectError("auth provider unreachable", request=request)
        realm = request.url.path.split("/realms/")[1].split("/")[0]
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={"jwks_uri": f"{_KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/certs"},
            )
        return httpx.Response(200, json={"keys": [{"kid": "readiness-kid", "kty": "RSA"}]})

    return JwksCache(
        _KEYCLOAK_URL,
        ttl_seconds=300,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _sidecar_client(*, answering: bool) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if not answering:
            raise httpx.ConnectError("sidecar unreachable", request=request)
        return httpx.Response(200, json={"status": "ok"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _NoTransactionEngine:
    """An auth-middleware engine that refuses to be used at all.

    Anything the middleware does with its engine — ``begin()`` for the
    tenant-scoped transaction, or a bare connection — is a failure here, which
    is the whole assertion: an unauthenticated path must return before it can
    reach either.
    """

    def begin(self) -> Any:
        raise AssertionError("/readyz opened a tenant-scoped transaction")

    def connect(self) -> Any:
        raise AssertionError("/readyz took a connection from the auth engine")


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: bool = True,
    auth_provider: bool = True,
    sidecars: bool = True,
    jwks_recorder: list[str] | None = None,
) -> None:
    engine = _registry_engine() if database else _UnreachableEngine()
    monkeypatch.setattr(app_module, "_app_engine", engine)
    monkeypatch.setattr(
        app_module,
        "_jwks",
        _jwks_cache(reachable=auth_provider, recorder=jwks_recorder),
    )
    monkeypatch.setattr(app_module, "_readiness_probe", _sidecar_client(answering=sidecars))


def _with_limiter(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """Turn the limiter on and pin what its backend reports.

    The probe answers ``disabled`` from the settings rather than from the
    limiter, because the limiter object exists on every stack — it is built at
    import so a bad ``RATE_LIMIT_BURST`` fails the boot — while the middleware
    is installed only where ADR 0014 turns it on.
    """

    class _Limiter:
        async def report(self) -> str:
            return state

    monkeypatch.setattr(
        app_module,
        "_settings",
        Settings(_env_file=None, environment="dev", rate_limit_enabled=True),
    )
    monkeypatch.setattr(app_module, "_rate_limiter", _Limiter())


def _replace_auth_engine(monkeypatch: pytest.MonkeyPatch, engine: object) -> None:
    """Swap the engine the ``AuthMiddleware`` instance will be built with.

    The middleware is constructed from the kwargs recorded on ``app`` when the
    stack is first built, so the substitution has to happen there and the built
    stack has to be dropped. ``monkeypatch`` restores both afterwards.
    """
    for middleware in app_module.app.user_middleware:
        if middleware.cls is AuthMiddleware:
            monkeypatch.setitem(middleware.kwargs, "app_engine", engine)
            break
    else:  # pragma: no cover - only reachable if the app stops authenticating
        raise AssertionError("the app has no AuthMiddleware")
    monkeypatch.setattr(app_module.app, "middleware_stack", None)


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Deliberately not the context-manager form: entering it would run the
    # lifespan, whose startup checks need a live Postgres and pgBouncer.
    yield TestClient(app_module.app)


def test_readyz_and_healthz_are_the_only_unauthenticated_paths() -> None:
    """Non-negotiable #10 allows exactly these two, and ADR 0013 records why the
    second one widens its literal wording."""
    assert UNAUTHENTICATED_PATHS == frozenset({"/healthz", "/readyz"})
    # Same object, not an equal copy: the contract generator annotates bearer
    # security off this list, so a third path added for one and not the other
    # would publish a security requirement nothing enforces.
    assert app_module.UNAUTHENTICATED_PATHS is UNAUTHENTICATED_PATHS


def test_readyz_answers_200_with_no_authorization_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "jwks": "ok",
        "model_server": "ok",
        "feature_server": "ok",
        # The unit-test process is a dev environment, where ADR 0014 turns the
        # limiter off; the states a deployment reports are covered below.
        "rate_limit": "disabled",
    }


def test_readyz_reports_dead_sidecars_without_failing_the_deploy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, sidecars=False)

    response = client.get("/readyz")

    # A popularity-serving API beats no API: the degraded state is visible, and
    # the deployment still promotes.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["model_server"] == "unavailable"
    assert body["feature_server"] == "unavailable"


@pytest.mark.parametrize("state", ["shared", "degraded", "in-process"])
def test_readyz_reports_the_rate_limit_bucket_without_gating_on_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """ADR 0014's shared bucket fails open onto the per-worker one when Redis
    is unreachable, which weakens a promise the response headers keep making —
    and leaves no other trace, since a 429 writes no audit row and the deployed
    API runs with ``--no-access-log``. So it is reported. It does not gate: a
    limiter is backpressure, not an auth boundary, and a deploy that stalls
    because Redis blinked would be trading a bounded weakening for an outage.
    """
    _wire(monkeypatch)
    _with_limiter(monkeypatch, state)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["rate_limit"] == state


def test_readyz_fails_when_the_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, database=False)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not-ready"
    assert body["database"] == "error"
    assert body["jwks"] == "ok"


def test_readyz_fails_when_the_auth_provider_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, auth_provider=False)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not-ready"
    assert body["jwks"] == "error"
    assert body["database"] == "ok"


def test_readyz_probes_the_realm_this_deployment_serves(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []
    _wire(monkeypatch, jwks_recorder=requested)

    client.get("/readyz")

    realm = app_module._settings.model_tenant_id
    assert requested
    assert all(path.startswith(f"/realms/{realm}/") for path in requested)


def test_readyz_echoes_the_correlation_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    response = client.get("/readyz", headers={REQUEST_ID_HEADER: "deploy-probe-7"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "deploy-probe-7"


def test_the_audit_middleware_lets_the_probe_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe has no principal and no request transaction to audit against."""
    _wire(monkeypatch)
    assert any(
        middleware.cls is RecommendationAuditMiddleware
        for middleware in app_module.app.user_middleware
    )

    assert client.get("/readyz").status_code == 200


def test_readyz_opens_no_tenant_scoped_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe carries no token, so there is no tenant to scope a transaction to.

    If the middleware ever reached ``SET LOCAL app.tenant_id`` on an
    unauthenticated path it would have to invent a tenant id to put there —
    which is the one thing ADR 0008 says nothing may do. The probe's own
    database round trip is a separate engine and stays untouched by this.
    """
    _wire(monkeypatch)
    _replace_auth_engine(monkeypatch, _NoTransactionEngine())

    response = TestClient(app_module.app).get("/readyz")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_healthz_stays_liveness_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, database=False, auth_provider=False, sidecars=False)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_api_process_holds_no_bypassrls_engine() -> None:
    """Non-negotiable #9's blast radius: an RCE in this process must not reach a
    role that defeats RLS. The tenant registry read moved onto the app engine,
    which is the only engine left."""
    assert not hasattr(app_module, "_admin_engine")
    assert app_module._tenant_router._engine is app_module._app_engine
    assert "admin_user" not in inspect.getsource(app_module)
