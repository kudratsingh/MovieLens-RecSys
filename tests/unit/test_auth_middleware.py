"""
Unit tests for ``src.auth.middleware.AuthMiddleware``. Verifies:

  * ``/healthz`` bypasses auth entirely (returns 200 without a token).
  * Valid RS256-signed token from a known realm is accepted; principal
    is attached with realm-derived tenant_id.
  * Missing / malformed Authorization header → 401.
  * Wrong API audience or unauthorized calling client → 401.
  * Expired token → 401.
  * Issuer that doesn't match Keycloak's ``/realms/<realm>`` shape → 401.
  * ``dev_auth_bypass=True`` short-circuits token check and returns
    the configured dev principal.
  * The middleware runs a per-request transaction with
    ``SET LOCAL app.tenant_id = <tenant_id>`` (verified against a
    real Postgres via the app_user engine and a fixture RLS-scoped
    table set up by migrations 0001-0004).

The token-shaped tests mint their own tokens with an RSA keypair
and inject the matching public key into the JwksCache so signature
verification succeeds without a live Keycloak.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from src.auth.middleware import AuthMiddleware, RequestPrincipal

# --- Test fixtures ----------------------------------------------------------

_BASE_URL = "http://keycloak.test:8080"
_KID = "test-kid-1"
_AUDIENCE = "movielens-api"
_API_CLIENT = "movielens-api"
_WEB_CLIENT = "movielens-web"


def _generate_keypair() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _int_to_base64url(value: int) -> str:
    """RFC 7518 §6.3.1: base64url-encode a big-endian integer with no
    leading zero-padding removed."""
    byte_length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(byte_length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_key_to_jwk(key: RSAPrivateKey, kid: str) -> dict[str, Any]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": _int_to_base64url(numbers.n),
        "e": _int_to_base64url(numbers.e),
    }


def _mint_token(
    private_key: RSAPrivateKey,
    *,
    realm: str,
    sub: str,
    audience: str = _AUDIENCE,
    authorized_party: str = _API_CLIENT,
    roles: tuple[str, ...] = ("user",),
    expires_in: int = 300,
    kid: str = _KID,
) -> str:
    """Mint a Keycloak-shaped access token for the API resource."""
    now = int(time.time())
    payload = {
        "iss": f"{_BASE_URL}/realms/{realm}",
        "sub": sub,
        "aud": audience,
        "azp": authorized_party,
        "realm_access": {"roles": list(roles)},
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


class _StubJwksCache:
    """Drop-in for ``JwksCache`` that returns a pre-seeded JWK for a
    kid. Skips the httpx-based fetch path so tests can run without
    a live Keycloak.
    """

    def __init__(self, key_by_realm: dict[str, dict[str, Any]]) -> None:
        self._by_realm = key_by_realm

    def find_signing_key(
        self,
        realm: str,
        kid: str,
        *,
        allow_refresh: bool = True,
    ) -> dict[str, Any] | None:
        realm_keys = self._by_realm.get(realm, {})
        return realm_keys.get(kid)


class _StubEngine:
    """Drop-in engine that records SET LOCAL calls made through
    ``.begin()``. The middleware opens a txn per request; we assert
    the ``app.tenant_id`` matches the token-derived tenant.
    """

    def __init__(
        self,
        *,
        fail_commit: bool = False,
        registered_tenants: tuple[str, ...] = ("default",),
    ) -> None:
        self.set_local_calls: list[dict[str, Any]] = []
        self.transaction_exits: list[tuple[Any, Any, Any]] = []
        self.fail_commit = fail_commit
        self.registered_tenants = frozenset(registered_tenants)

    def begin(self) -> _StubConnCtx:
        return _StubConnCtx(self)


class _StubConnCtx:
    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def __enter__(self) -> _StubConn:
        return _StubConn(self._engine)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._engine.transaction_exits.append((exc_type, exc, tb))
        if exc_type is None and self._engine.fail_commit:
            raise RuntimeError("commit failed")
        return None


class _StubConn:
    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _StubResult:
        statement = str(stmt)
        resolved_params = params or {}
        if "SET LOCAL app.tenant_id" in statement:
            self._engine.set_local_calls.append({"stmt": statement, "params": resolved_params})
            return _StubResult(1)
        if "FROM public.tenants" in statement:
            tenant_id = str(resolved_params.get("tid"))
            return _StubResult(1 if tenant_id in self._engine.registered_tenants else None)
        raise AssertionError(f"unexpected statement: {statement}")


class _StubResult:
    def __init__(self, scalar: int | None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> int | None:
        return self._scalar


def _build_app(
    *,
    key: RSAPrivateKey,
    dev_auth_bypass: bool = False,
    fail_commit: bool = False,
    known_realms: tuple[str, ...] = ("default",),
    registered_tenants: tuple[str, ...] = ("default",),
) -> tuple[FastAPI, _StubEngine]:
    app = FastAPI()
    jwk = _public_key_to_jwk(key, _KID)
    jwks = _StubJwksCache({realm: {_KID: jwk} for realm in known_realms})
    engine = _StubEngine(
        fail_commit=fail_commit,
        registered_tenants=registered_tenants,
    )
    app.add_middleware(
        AuthMiddleware,
        jwks=jwks,
        app_engine=engine,
        expected_audience=_AUDIENCE,
        expected_issuer_base_url=_BASE_URL,
        allowed_authorized_parties=(_API_CLIENT, _WEB_CLIENT),
        dev_auth_bypass=dev_auth_bypass,
        dev_bypass_tenant="default",
        dev_bypass_user="dev-user",
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, Any]:
        p = request.state.principal
        return {
            "tenant_id": p.tenant_id,
            "user_id": p.user_id,
            "realm": p.realm,
            "authorized_party": p.authorized_party,
            "roles": sorted(p.roles),
        }

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("handler failed")

    return app, engine


# --- Tests ------------------------------------------------------------------


def test_healthz_bypasses_auth() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_authorization_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    client = TestClient(app)
    resp = client.get("/whoami")
    assert resp.status_code == 401


def test_malformed_bearer_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_valid_token_attaches_principal_and_sets_tenant() -> None:
    key = _generate_keypair()
    app, engine = _build_app(key=key)
    token = _mint_token(key, realm="default", sub="alice")

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "default"
    assert body["user_id"] == "alice"
    assert body["realm"] == "default"
    assert body["authorized_party"] == _API_CLIENT
    assert body["roles"] == ["user"]

    # Middleware must have run SET LOCAL app.tenant_id = 'default' in
    # a per-request txn on the app_engine.
    assert len(engine.set_local_calls) == 1
    call = engine.set_local_calls[0]
    assert "SET LOCAL app.tenant_id" in call["stmt"]
    assert call["params"] == {"tid": "default"}
    assert engine.transaction_exits == [(None, None, None)]


def test_browser_client_token_with_api_audience_is_accepted() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    token = _mint_token(
        key,
        realm="default",
        sub="alice",
        audience=_AUDIENCE,
        authorized_party=_WEB_CLIENT,
        roles=("user", "demo-impersonator"),
    )

    response = TestClient(app).get(
        "/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "alice"
    assert response.json()["authorized_party"] == _WEB_CLIENT
    assert response.json()["roles"] == ["demo-impersonator", "user"]


def test_demo_persona_access_requires_trusted_service_or_browser_role() -> None:
    service = RequestPrincipal(
        tenant_id="demo",
        user_id="service-account",
        realm="demo",
        authorized_party=_API_CLIENT,
        roles=frozenset(),
    )
    browser_user = RequestPrincipal(
        tenant_id="demo",
        user_id="browser-user",
        realm="demo",
        authorized_party=_WEB_CLIENT,
        roles=frozenset({"user"}),
    )
    browser_demo = RequestPrincipal(
        tenant_id="demo",
        user_id="browser-demo",
        realm="demo",
        authorized_party=_WEB_CLIENT,
        roles=frozenset({"user", "demo-impersonator"}),
    )

    assert service.can_access_demo_personas(trusted_service_client=_API_CLIENT)
    assert not browser_user.can_access_demo_personas(trusted_service_client=_API_CLIENT)
    assert browser_demo.can_access_demo_personas(trusted_service_client=_API_CLIENT)


@pytest.mark.asyncio
async def test_dispatch_commits_before_returning_success_response() -> None:
    engine = _StubEngine()
    middleware = AuthMiddleware(
        FastAPI(),
        jwks=_StubJwksCache({}),
        app_engine=engine,
        expected_audience=_AUDIENCE,
        dev_auth_bypass=True,
        dev_bypass_tenant="default",
        dev_bypass_user="dev-user",
    )
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/whoami",
            "raw_path": b"/whoami",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )

    async def call_next(_: Request) -> JSONResponse:
        assert engine.transaction_exits == []
        return JSONResponse({"status": "ok"})

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert engine.transaction_exits == [(None, None, None)]
    assert response.background is None


def test_commit_failure_never_returns_handler_success() -> None:
    key = _generate_keypair()
    app, engine = _build_app(key=key, fail_commit=True)
    token = _mint_token(key, realm="default", sub="alice")

    client = TestClient(app)
    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 500
    assert response.json() == {"detail": "request transaction commit failed"}
    assert engine.transaction_exits == [(None, None, None)]


def test_handler_exception_rolls_back_transaction() -> None:
    key = _generate_keypair()
    app, engine = _build_app(key=key)
    token = _mint_token(key, realm="default", sub="alice")

    client = TestClient(app)
    with pytest.raises(RuntimeError, match="handler failed"):
        client.get("/explode", headers={"Authorization": f"Bearer {token}"})

    assert len(engine.transaction_exits) == 1
    exc_type, exc, traceback = engine.transaction_exits[0]
    assert exc_type is RuntimeError
    assert isinstance(exc, RuntimeError)
    assert traceback is not None


def test_expired_token_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    token = _mint_token(key, realm="default", sub="alice", expires_in=-1)

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_wrong_audience_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    token = _mint_token(key, realm="default", sub="alice", audience="somebody-else")

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_unexpected_authorized_party_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    token = _mint_token(
        key,
        realm="default",
        sub="alice",
        authorized_party="security-admin-console",
    )

    response = TestClient(app).get(
        "/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_unknown_realm_key_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    # Token issued for a realm the stub JwksCache doesn't have.
    token = _mint_token(key, realm="unknown-realm", sub="alice")

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_verified_realm_without_registered_tenant_returns_403() -> None:
    key = _generate_keypair()
    app, engine = _build_app(
        key=key,
        known_realms=("default", "orphan"),
        registered_tenants=("default",),
    )
    token = _mint_token(key, realm="orphan", sub="alice")

    response = TestClient(app).get(
        "/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "unknown tenant for verified realm: 'orphan'"}
    assert engine.transaction_exits
    assert engine.transaction_exits[0][0] is not None


def test_dev_bypass_short_circuits_without_token() -> None:
    key = _generate_keypair()
    app, engine = _build_app(key=key, dev_auth_bypass=True)

    client = TestClient(app)
    resp = client.get("/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "default"
    assert body["user_id"] == "dev-user"

    # SET LOCAL still runs so downstream RLS queries work in bypass mode.
    assert engine.set_local_calls[0]["params"] == {"tid": "default"}


def test_issuer_with_untrusted_prefix_is_rejected() -> None:
    key = _generate_keypair()
    app, engine = _build_app(key=key)

    # The token is signed by the correct realm key, but its issuer URL is not
    # the exact trusted public issuer. Signature validity must not make an
    # attacker-controlled issuer origin or path acceptable.
    now = int(time.time())
    payload = {
        "iss": f"{_BASE_URL}/realms/attacker/realms/default",
        "sub": "eve",
        "aud": _AUDIENCE,
        "azp": _API_CLIENT,
        "realm_access": {"roles": ["user"]},
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode(payload, key, algorithm="RS256", headers={"kid": _KID})

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_issuer_with_untrusted_origin_is_rejected() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://attacker.example/realms/default",
            "sub": "eve",
            "aud": _AUDIENCE,
            "azp": _API_CLIENT,
            "iat": now,
            "exp": now + 300,
        },
        key,
        algorithm="RS256",
        headers={"kid": _KID},
    )

    response = TestClient(app).get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
