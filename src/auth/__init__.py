"""
Auth layer per ADR 0007 (Keycloak) + ADR 0008 (tenant-scoped RLS).

The public surface is intentionally small:
  * ``JwksCache`` — TTL-cached JWKS-per-realm, with a one-retry
    refresh on signature-verification failure so a Keycloak-side key
    rotation propagates without any request seeing a false negative.
  * ``AuthMiddleware`` — FastAPI middleware that validates a Bearer
    token, validates its resource audience and calling client, resolves its
    issuer URL to a registered tenant slug (realm-per-tenant), and attaches a
    principal with tenant, actor, client, and roles to ``request.state``. It
    also opens a Postgres transaction and runs ``SET LOCAL app.tenant_id`` so
    RLS on tenant-scoped tables filters correctly.
  * ``RequestPrincipal`` — the resolved-identity object handlers pull
    off ``request.state.principal`` (rather than reaching into the
    token themselves).

Nothing outside ``src.auth`` should validate JWTs directly — the
middleware is the one place tenant identity is derived from a token,
and downstream code trusts ``request.state`` exclusively.
"""

from src.auth.jwks import JwksCache, JwksFetchError
from src.auth.middleware import (
    AuthMiddleware,
    RequestPrincipal,
    UnauthenticatedError,
    UnauthorizedError,
)

__all__ = [
    "JwksCache",
    "JwksFetchError",
    "AuthMiddleware",
    "RequestPrincipal",
    "UnauthenticatedError",
    "UnauthorizedError",
]
