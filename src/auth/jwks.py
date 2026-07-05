"""
JWKS cache per ADR 0007.

Fetches JWKS (JSON Web Key Set) from each Keycloak realm's OIDC
discovery document, caches with a TTL, and provides a
force-refresh path so signature-verification failures can retry
once against fresh keys before rejecting a token — the key rotation
mitigation ADR 0007 §risks calls out.

Design notes:

  1. **One cache entry per realm.** Realm-per-tenant means every realm
     signs with its own key material; the cache is keyed by realm
     slug. Adding a tenant means the middleware will lazily populate
     the cache on the next request for that tenant — no bootstrap step.

  2. **TTL is time.monotonic-based** so it's immune to wall-clock
     jumps (NTP corrections, container restarts with skewed clocks).

  3. **Refresh-on-failure retry.** When ``get_signing_key_by_kid`` is
     called with a kid that isn't in the cached JWKS, we force-refresh
     and try once more. If the kid still isn't present, the caller
     rejects the token. This handles the common case where Keycloak
     rotated its keys mid-request without waiting a full TTL for the
     cache to expire naturally.

  4. **Fetches are synchronous.** JWKS is small (~1 KB) and the fetch
     happens off the hot path (once per TTL per realm). Keeping it
     sync avoids threading an event loop through the middleware's
     JWT-decode call, which is itself sync (PyJWT is not async).

  5. **Errors are explicit** — ``JwksFetchError`` is raised for network
     failures, missing discovery documents, or malformed JWKS. The
     middleware maps these to 503 (auth provider unreachable) rather
     than 401 (token invalid) so the distinction is visible in logs.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class JwksFetchError(RuntimeError):
    """Raised when the JWKS or discovery document can't be fetched
    or parsed. Distinct from token-validation errors — this one
    means the auth provider itself is misbehaving."""


@dataclass(frozen=True)
class _CachedJwks:
    jwks: dict[str, Any]
    fetched_at: float  # time.monotonic()


class JwksCache:
    """Thread-safe, per-realm JWKS cache with TTL and force-refresh."""

    def __init__(
        self,
        keycloak_base_url: str,
        ttl_seconds: int,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = keycloak_base_url.rstrip("/")
        self._ttl = ttl_seconds
        # http_client is injectable so tests can supply a MockTransport.
        # Production path uses a default sync client with a small timeout
        # so a hanging Keycloak doesn't stall the middleware indefinitely.
        self._http = http_client or httpx.Client(timeout=5.0)
        self._cache: dict[str, _CachedJwks] = {}
        self._lock = threading.RLock()

    def get_jwks(self, realm: str, *, force_refresh: bool = False) -> dict[str, Any]:
        """Return the JWKS dict for ``realm``. Fetches on cache miss,
        expired TTL, or ``force_refresh=True``.
        """
        with self._lock:
            entry = self._cache.get(realm)
            now = time.monotonic()
            if entry is None or force_refresh or now - entry.fetched_at > self._ttl:
                if entry is not None:
                    logger.info(
                        "JWKS cache miss for realm=%s (force=%s, age=%.1fs)",
                        realm,
                        force_refresh,
                        now - entry.fetched_at,
                    )
                jwks = self._fetch_jwks(realm)
                self._cache[realm] = _CachedJwks(jwks=jwks, fetched_at=now)
                return jwks
            return entry.jwks

    def find_signing_key(
        self,
        realm: str,
        kid: str,
        *,
        allow_refresh: bool = True,
    ) -> dict[str, Any] | None:
        """Return the JWK matching ``kid`` in the realm's JWKS. If the
        kid isn't in the cached JWKS and ``allow_refresh`` is True,
        force-refresh once and try again — this is the key-rotation
        mitigation from ADR 0007 §risks.
        """
        jwks = self.get_jwks(realm)
        key = self._find_key_in_jwks(jwks, kid)
        if key is not None:
            return key
        if not allow_refresh:
            return None
        # Kid not found — Keycloak may have rotated. Force-refresh once
        # and retry. Failure here is a real "unknown key" and the caller
        # should reject the token.
        logger.info("kid=%s not in cached JWKS for realm=%s; forcing refresh", kid, realm)
        jwks = self.get_jwks(realm, force_refresh=True)
        return self._find_key_in_jwks(jwks, kid)

    def _fetch_jwks(self, realm: str) -> dict[str, Any]:
        discovery_url = f"{self._base_url}/realms/{realm}/.well-known/openid-configuration"
        try:
            discovery = self._http.get(discovery_url)
            discovery.raise_for_status()
            jwks_uri = discovery.json().get("jwks_uri")
            if not jwks_uri:
                raise JwksFetchError(f"discovery document at {discovery_url} has no jwks_uri")
            jwks_resp = self._http.get(jwks_uri)
            jwks_resp.raise_for_status()
            jwks: dict[str, Any] = jwks_resp.json()
            if "keys" not in jwks:
                raise JwksFetchError(f"JWKS at {jwks_uri} has no 'keys' array")
            return jwks
        except httpx.HTTPError as exc:
            raise JwksFetchError(f"failed to fetch JWKS for realm={realm}: {exc}") from exc

    @staticmethod
    def _find_key_in_jwks(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return dict(key)
        return None
