"""
Unit tests for ``src.auth.jwks.JwksCache``. Verifies:

  * Cache miss fetches from Keycloak's discovery + jwks_uri.
  * TTL expiry re-fetches on the next call.
  * ``force_refresh`` re-fetches unconditionally.
  * ``find_signing_key`` returns the JWK for a matching kid.
  * ``find_signing_key`` with a kid not present triggers a one-shot
    refresh (the ADR 0007 §risks key-rotation mitigation).
  * ``find_signing_key`` with ``allow_refresh=False`` does not refresh.
  * Network / malformed-JSON failures raise ``JwksFetchError``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from src.auth.jwks import JwksCache, JwksFetchError

_BASE_URL = "http://keycloak.test:8080"


def _make_mock_http_client(
    responses: dict[str, tuple[int, dict[str, Any]]],
    call_counter: dict[str, int],
) -> httpx.Client:
    """Return an httpx.Client whose transport returns the canned
    responses from ``responses`` and increments ``call_counter`` per
    URL so tests can assert how many times each endpoint was hit.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        call_counter[url] = call_counter.get(url, 0) + 1
        if url not in responses:
            return httpx.Response(404, json={"error": "not mocked", "url": url})
        status, payload = responses[url]
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(_handler))


def _discovery_response(realm: str) -> dict[str, Any]:
    return {
        "issuer": f"{_BASE_URL}/realms/{realm}",
        "jwks_uri": f"{_BASE_URL}/realms/{realm}/protocol/openid-connect/certs",
    }


def _jwks_response(kids: list[str]) -> dict[str, Any]:
    return {
        "keys": [
            {
                "kid": kid,
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                # Not real key material — the JWKS cache tests don't
                # verify tokens; that's the middleware's job.
                "n": "abc",
                "e": "AQAB",
            }
            for kid in kids
        ]
    }


def test_get_jwks_populates_cache_on_first_call() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    jwks = cache.get_jwks("default")

    assert jwks["keys"][0]["kid"] == "k1"
    # Two fetches: discovery + jwks_uri.
    assert counts[f"{_BASE_URL}/realms/default/.well-known/openid-configuration"] == 1
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 1


def test_get_jwks_serves_from_cache_within_ttl() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    cache.get_jwks("default")
    cache.get_jwks("default")
    cache.get_jwks("default")

    # Still only one fetch of each URL — cached hits didn't call out.
    assert counts[f"{_BASE_URL}/realms/default/.well-known/openid-configuration"] == 1
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 1


def test_force_refresh_re_fetches() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    cache.get_jwks("default")
    cache.get_jwks("default", force_refresh=True)

    assert counts[f"{_BASE_URL}/realms/default/.well-known/openid-configuration"] == 2
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 2


def test_find_signing_key_hits_cache_for_known_kid() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1", "k2"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    key = cache.find_signing_key("default", "k2")

    assert key is not None
    assert key["kid"] == "k2"
    # One fetch cycle only — the kid was already in the fetched JWKS.
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 1


def test_find_signing_key_triggers_refresh_on_unknown_kid() -> None:
    counts: dict[str, int] = {}
    # First fetch returns {k1}; second fetch (post-rotation) returns {k1, k2}.
    responses = {
        f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
            200,
            _discovery_response("default"),
        ),
    }
    call_state = {"jwks_calls": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        counts[url] = counts.get(url, 0) + 1
        if url.endswith("/openid-configuration"):
            return httpx.Response(200, json=_discovery_response("default"))
        if url.endswith("/certs"):
            call_state["jwks_calls"] += 1
            if call_state["jwks_calls"] == 1:
                return httpx.Response(200, json=_jwks_response(["k1"]))
            return httpx.Response(200, json=_jwks_response(["k1", "k2"]))
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(_handler))
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)

    # k2 isn't in the first JWKS fetch. Cache should force-refresh
    # once and find it in the second.
    key = cache.find_signing_key("default", "k2")

    assert key is not None
    assert key["kid"] == "k2"
    assert call_state["jwks_calls"] == 2


def test_find_signing_key_returns_none_when_still_missing_after_refresh() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    key = cache.find_signing_key("default", "unknown-kid")

    assert key is None
    # Discovery + jwks_uri each hit twice (once for initial, once for refresh)
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 2


def test_find_signing_key_allow_refresh_false_does_not_re_fetch() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)
    key = cache.find_signing_key("default", "unknown-kid", allow_refresh=False)

    assert key is None
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 1


def test_jwks_fetch_error_on_missing_discovery() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client({}, counts)  # nothing mocked → 404
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)

    with pytest.raises(JwksFetchError):
        cache.get_jwks("default")


def test_jwks_fetch_error_on_missing_jwks_uri_in_discovery() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                # Discovery response with no jwks_uri.
                {"issuer": f"{_BASE_URL}/realms/default"},
            ),
        },
        counts,
    )
    cache = JwksCache(_BASE_URL, ttl_seconds=300, http_client=http)

    with pytest.raises(JwksFetchError, match="no jwks_uri"):
        cache.get_jwks("default")


def test_ttl_expiry_triggers_refetch() -> None:
    counts: dict[str, int] = {}
    http = _make_mock_http_client(
        {
            f"{_BASE_URL}/realms/default/.well-known/openid-configuration": (
                200,
                _discovery_response("default"),
            ),
            f"{_BASE_URL}/realms/default/protocol/openid-connect/certs": (
                200,
                _jwks_response(["k1"]),
            ),
        },
        counts,
    )
    # TTL of 0 seconds — every call is effectively past-TTL.
    cache = JwksCache(_BASE_URL, ttl_seconds=0, http_client=http)
    cache.get_jwks("default")
    time.sleep(0.01)
    cache.get_jwks("default")

    # Two fetches — TTL expired between calls.
    assert counts[f"{_BASE_URL}/realms/default/protocol/openid-connect/certs"] == 2
