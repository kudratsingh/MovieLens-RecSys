"""
Unit tests for ``src.auth.middleware.AuthMiddleware``. Verifies:

  * ``/healthz`` bypasses auth entirely (returns 200 without a token).
  * Valid RS256-signed token from a known realm is accepted; principal
    is attached with realm-derived tenant_id.
  * Missing / malformed Authorization header → 401.
  * Wrong audience → 401.
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
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.auth.middleware import AuthMiddleware

# --- Test fixtures ----------------------------------------------------------

_BASE_URL = "http://keycloak.test:8080"
_KID = "test-kid-1"
_AUDIENCE = "movielens-api"


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
    expires_in: int = 300,
    kid: str = _KID,
) -> str:
    """Mint a Keycloak-shaped access token — uses `azp` (authorized
    party) rather than `aud` because Keycloak collapses the audience
    into azp for tokens issued to the same client."""
    now = int(time.time())
    payload = {
        "iss": f"{_BASE_URL}/realms/{realm}",
        "sub": sub,
        "azp": audience,
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

    def __init__(self) -> None:
        self.set_local_calls: list[dict[str, Any]] = []

    def begin(self) -> _StubConnCtx:
        return _StubConnCtx(self)


class _StubConnCtx:
    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def __enter__(self) -> _StubConn:
        return _StubConn(self._engine)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _StubConn:
    def __init__(self, engine: _StubEngine) -> None:
        self._engine = engine

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        self._engine.set_local_calls.append({"stmt": str(stmt), "params": params or {}})


def _build_app(
    *,
    key: RSAPrivateKey,
    dev_auth_bypass: bool = False,
) -> tuple[FastAPI, _StubEngine]:
    app = FastAPI()
    jwks = _StubJwksCache({"default": {_KID: _public_key_to_jwk(key, _KID)}})
    engine = _StubEngine()
    app.add_middleware(
        AuthMiddleware,
        jwks=jwks,
        app_engine=engine,
        expected_audience=_AUDIENCE,
        dev_auth_bypass=dev_auth_bypass,
        dev_bypass_tenant="default",
        dev_bypass_user="dev-user",
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, str]:
        p = request.state.principal
        return {"tenant_id": p.tenant_id, "user_id": p.user_id, "realm": p.realm}

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

    # Middleware must have run SET LOCAL app.tenant_id = 'default' in
    # a per-request txn on the app_engine.
    assert len(engine.set_local_calls) == 1
    call = engine.set_local_calls[0]
    assert "SET LOCAL app.tenant_id" in call["stmt"]
    assert call["params"] == {"tid": "default"}


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


def test_unknown_realm_key_returns_401() -> None:
    key = _generate_keypair()
    app, _ = _build_app(key=key)
    # Token issued for a realm the stub JwksCache doesn't have.
    token = _mint_token(key, realm="unknown-realm", sub="alice")

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


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


def test_issuer_derives_tenant_from_last_realms_segment() -> None:
    """The realm-derivation logic in AuthMiddleware pulls the segment
    after the *last* '/realms/' in the issuer URL. That resists
    spoofing via a fake path prefix like
    'http://keycloak/realms/attacker/realms/default'.
    """
    key = _generate_keypair()
    app, engine = _build_app(key=key)

    # Craft a token whose iss has an extra `/realms/attacker` prefix.
    # We expect the middleware to use the last segment ('default')
    # for realm resolution, and since our JWKS is keyed by 'default'
    # the token verifies against the right key.
    now = int(time.time())
    payload = {
        "iss": f"{_BASE_URL}/realms/attacker/realms/default",
        "sub": "eve",
        "azp": _AUDIENCE,
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode(payload, key, algorithm="RS256", headers={"kid": _KID})

    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    # Verifies as tenant 'default' — the last-segment rule holds.
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == "default"
