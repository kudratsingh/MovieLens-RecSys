"""
Auth middleware per ADR 0007 + ADR 0008.

Every request except ``/healthz`` passes through here. Successful auth
attaches a ``RequestPrincipal(tenant_id, user_id)`` to ``request.state``
and opens a per-request Postgres transaction with
``SET LOCAL app.tenant_id = '<tenant_id>'`` so RLS on tenant-scoped
tables filters correctly for whatever the handler queries next.

The middleware is intentionally the *only* place the raw token is
inspected. Handlers pull identity off ``request.state.principal`` and
their DB queries run on ``request.state.db`` (the transaction-bound
connection). This keeps the tenant-derivation surface small and makes
the tenant-isolation integration test's job concrete: authenticate as
tenant A, hit every endpoint, assert the response contains no tenant B
rows — the middleware is the one thing that could break that invariant.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Request, Response
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.auth.jwks import JwksCache, JwksFetchError

logger = logging.getLogger(__name__)

_COMMIT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="request-commit")


# Endpoints that skip auth. `/healthz` per ADR 0007's decision — every
# other endpoint requires a valid Bearer token or fails the middleware
# before reaching a handler.
_UNAUTHENTICATED_PATHS: frozenset[str] = frozenset(["/healthz"])
_TENANT_EXISTS = text("SELECT 1 FROM public.tenants WHERE id = :tid")


@dataclass(frozen=True)
class RequestPrincipal:
    """Resolved identity for the current request. Attached to
    ``request.state.principal``. Handlers read this instead of
    inspecting headers or tokens themselves.
    """

    tenant_id: str
    user_id: str
    # The Keycloak realm slug the token was issued from. Same as
    # tenant_id under realm-per-tenant, kept as a separate field so
    # downstream code doesn't couple to "realm slug == tenant id"
    # if that ever changes.
    realm: str
    authorized_party: str
    roles: frozenset[str]

    def can_access_demo_personas(self, *, trusted_service_client: str) -> bool:
        """Return whether this actor may select arbitrary demo personas.

        The confidential service client is trusted for synthetic load and
        isolation harnesses. Browser actors need the explicit realm role;
        merely belonging to the tenant is insufficient.
        """
        return (
            self.authorized_party in {trusted_service_client, "dev-bypass"}
            or "demo-impersonator" in self.roles
        )


class UnauthenticatedError(Exception):
    """No usable token in the request."""


class UnauthorizedError(Exception):
    """Token was present but failed validation (signature, exp, aud, iss)."""


class UnregisteredTenantError(Exception):
    """The verified token realm has no matching tenant registry row."""


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that validates OIDC access tokens and opens a
    tenant-scoped Postgres transaction for the request.

    Wired into the app via ``app.add_middleware(AuthMiddleware, ...)`` in
    ``src.serving.app``. Ordering matters — this middleware must run
    before any handler that touches ``request.state.principal`` or
    ``request.state.db``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        jwks: JwksCache,
        app_engine: Engine,
        expected_audience: str,
        expected_issuer_base_url: str | None = None,
        allowed_authorized_parties: tuple[str, ...] | None = None,
        dev_auth_bypass: bool = False,
        dev_bypass_tenant: str = "default",
        dev_bypass_user: str = "dev-user",
    ) -> None:
        super().__init__(app)
        self._jwks = jwks
        self._engine = app_engine
        self._expected_audience = expected_audience
        self._expected_issuer_base_url = (
            expected_issuer_base_url or "http://localhost:8080"
        ).rstrip("/")
        self._allowed_authorized_parties = frozenset(
            allowed_authorized_parties or (expected_audience,)
        )
        self._dev_bypass = dev_auth_bypass
        self._dev_bypass_tenant = dev_bypass_tenant
        self._dev_bypass_user = dev_bypass_user

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in _UNAUTHENTICATED_PATHS:
            return await call_next(request)

        try:
            # Token parsing can perform RSA signature verification and a
            # cache-miss JWKS fetch. Neither belongs on the event loop: under
            # concurrent recommendation traffic it would otherwise delay
            # unrelated requests handled by the same worker.
            principal = await run_in_threadpool(self._resolve_principal, request)
        except UnauthenticatedError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        except UnauthorizedError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        except JwksFetchError as exc:
            logger.error("JWKS fetch failed: %s", exc)
            return JSONResponse(
                {"detail": "auth provider unreachable"},
                status_code=503,
            )

        request.state.principal = principal

        # Open a per-request transaction on the RLS-applied engine and
        # run SET LOCAL app.tenant_id. Every query the handler runs on
        # request.state.db is scoped to the tenant. Rollback on any
        # exception; commit on clean return.
        # psycopg2 is synchronous. Keep the same request transaction and RLS
        # semantics, but move connection setup, rollback, and commit off the
        # event loop. Commit is awaited before returning the response: a client
        # must never receive success for a mutation or fail-closed audit that
        # can still fail to become durable.
        transaction_context = self._engine.begin()
        conn = await run_in_threadpool(transaction_context.__enter__)
        try:
            await run_in_threadpool(
                conn.execute,
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": principal.tenant_id},
            )
            tenant_exists = await run_in_threadpool(
                conn.execute,
                _TENANT_EXISTS,
                {"tid": principal.tenant_id},
            )
            if tenant_exists.scalar_one_or_none() is None:
                raise UnregisteredTenantError(
                    f"unknown tenant for verified realm: {principal.tenant_id!r}"
                )
            request.state.db = conn
            response = await call_next(request)
        except UnregisteredTenantError as exc:
            await run_in_threadpool(
                transaction_context.__exit__,
                type(exc),
                exc,
                exc.__traceback__,
            )
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except BaseException as exc:
            await run_in_threadpool(
                transaction_context.__exit__,
                type(exc),
                exc,
                exc.__traceback__,
            )
            raise
        try:
            await _commit_transaction(transaction_context)
        except Exception:
            logger.exception(
                "request transaction commit failed tenant=%s path=%s",
                principal.tenant_id,
                request.url.path,
            )
            return JSONResponse(
                {"detail": "request transaction commit failed"},
                status_code=500,
            )
        return response

    def _resolve_principal(self, request: Request) -> RequestPrincipal:
        if self._dev_bypass:
            # Dev-only path. Settings.__init__ has already asserted the
            # bypass is only permitted in environment=='dev' — no need
            # to re-check here.
            return RequestPrincipal(
                tenant_id=self._dev_bypass_tenant,
                user_id=self._dev_bypass_user,
                realm=self._dev_bypass_tenant,
                authorized_party="dev-bypass",
                roles=frozenset({"demo-impersonator"}),
            )

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise UnauthenticatedError("missing or malformed Authorization header")
        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            raise UnauthenticatedError("empty bearer token")

        # Peek at the unverified header + payload to learn kid + issuer.
        # We can't verify signature yet because we need the kid to find
        # the right key; the verify step below is the actual check.
        try:
            unverified_header = jwt.get_unverified_header(token)
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise UnauthenticatedError(f"malformed token: {exc}") from exc

        kid = unverified_header.get("kid")
        if not kid:
            raise UnauthenticatedError("token header has no kid")
        issuer = unverified_payload.get("iss")
        if not isinstance(issuer, str) or not issuer:
            raise UnauthenticatedError("token has no iss claim")

        realm = self._realm_from_issuer(issuer)
        expected_issuer = f"{self._expected_issuer_base_url}/realms/{realm}"
        if issuer != expected_issuer:
            raise UnauthenticatedError("token issuer is not a trusted Keycloak realm URL")

        signing_jwk = self._jwks.find_signing_key(realm, kid)
        if signing_jwk is None:
            raise UnauthorizedError("no matching signing key")

        signing_key = jwt.PyJWK(signing_jwk).key
        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=[signing_jwk.get("alg", "RS256")],
                issuer=issuer,
                audience=self._expected_audience,
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError(f"token verification failed: {exc}") from exc

        # `aud` above proves this API is an intended resource. `azp` names the
        # client that obtained the token and must be one of the explicit API or
        # browser callers. A token issued for an admin console or unrelated
        # client never reaches a handler even if it contains a broad audience.
        azp = payload.get("azp")
        if azp not in self._allowed_authorized_parties:
            raise UnauthorizedError(f"unexpected authorized party (azp={azp!r})")

        realm_access = payload.get("realm_access")
        raw_roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
        roles = frozenset(role for role in raw_roles if isinstance(role, str))
        user_id = str(payload.get("sub"))
        return RequestPrincipal(
            tenant_id=realm,
            user_id=user_id,
            realm=realm,
            authorized_party=str(azp),
            roles=roles,
        )

    @staticmethod
    def _realm_from_issuer(issuer: str) -> str:
        """Derive the realm slug from the Keycloak issuer URL.

        Issuer format under Keycloak is
        ``<base>/realms/<realm>``. The realm slug is the tenant id
        (ADR 0007 §decision — realm-per-tenant). Deriving it from the
        issuer URL rather than from a self-declared claim is
        forge-resistant: a client cannot lie about which realm signed
        their token, because the issuer *is* the URL whose JWKS
        verifies the signature.
        """
        # Split on `/realms/` — Keycloak's issuer path is fixed as
        # `<host>/realms/<realm>`. Any other suffix means either a
        # non-Keycloak issuer we don't support or a malformed token.
        marker = "/realms/"
        idx = issuer.rfind(marker)
        if idx < 0:
            raise UnauthenticatedError(f"unrecognized issuer format: {issuer}")
        realm = issuer[idx + len(marker) :].strip("/")
        if not realm or "/" in realm:
            raise UnauthenticatedError(f"unrecognized issuer format: {issuer}")
        return realm


async def _commit_transaction(transaction_context: Any) -> None:
    # Commit is part of the response's correctness and latency boundary, but it
    # still must not consume Starlette's shared AnyIO thread tokens. A dedicated
    # executor keeps WAL flushes from queuing connection setup, SET LOCAL, or
    # audit inserts for unrelated requests.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_COMMIT_EXECUTOR, _commit_transaction_sync, transaction_context)


def _commit_transaction_sync(transaction_context: Any) -> None:
    transaction_context.__exit__(None, None, None)
