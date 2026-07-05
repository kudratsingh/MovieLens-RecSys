"""
Fixtures for the tenant-isolation integration tests. Requires the
docker-compose stack to be running (Postgres + Keycloak + pgBouncer)
with migrations applied and both `default` + `demo` realms seeded.

Skips every test when the stack isn't reachable so the file can live
in the same repo as unit tests without breaking CI on runners that
don't boot Docker.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx
import pytest

_KEYCLOAK_URL = "http://localhost:8080"
_API_CLIENT_ID = "movielens-api"
_API_CLIENT_SECRET = "movielens-api-secret-dev-only"


def _stack_reachable() -> bool:
    """Best-effort probe: does Keycloak's health endpoint respond?
    If not, treat the whole file as skipped (docker-compose isn't up).
    """
    try:
        resp = httpx.get(f"{_KEYCLOAK_URL}/realms/default", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


if not _stack_reachable():
    pytest.skip(
        "docker-compose stack not reachable at localhost:8080 — "
        "run `make infra-up && make db-migrate` before invoking these tests",
        allow_module_level=True,
    )


TokenMinter = Callable[[str, str, str], str]


@pytest.fixture
def mint_token() -> TokenMinter:
    """Return a helper that mints an access token via Keycloak's
    direct password grant for a given (realm, username, password).
    """

    def _mint(realm: str, username: str, password: str) -> str:
        resp = httpx.post(
            f"{_KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": _API_CLIENT_ID,
                "client_secret": _API_CLIENT_SECRET,
                "username": username,
                "password": password,
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError(f"no access_token in response: {resp.json()}")
        return token

    return _mint
