"""
Fixtures for the tenant-isolation integration tests. Requires the
docker-compose stack to be running (Postgres + Keycloak + pgBouncer)
with migrations applied and both `default` + `demo` realms seeded.

Skips every test when the stack isn't reachable so the file can live
in the same repo as unit tests without breaking CI on runners that
don't boot Docker.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import httpx
import pytest
from sqlalchemy import create_engine, text

from src.config import Settings

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

CANARY_USER_ID = 987654321
DEFAULT_HISTORY_TITLE = "RLS default history canary"
DEMO_HISTORY_TITLE = "RLS demo history canary"
DEFAULT_RECOMMENDATION_TITLE = "RLS default recommendation canary"
DEMO_RECOMMENDATION_TITLE = "RLS demo recommendation canary"


@pytest.fixture(scope="module", autouse=True)
def tenant_canary_rows() -> Generator[None, None, None]:
    """Seed distinct rows so endpoint isolation assertions test real data."""
    engine = create_engine(Settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM ratings "
                'WHERE "movieId" IN (900000001, 900000002, 900000003, 900000004)'
            ),
        )
        connection.execute(
            text("""
                INSERT INTO movies ("movieId", title, genres)
                VALUES
                    (900000001, :default_history_title, 'Test'),
                    (900000002, :demo_history_title, 'Test'),
                    (900000003, :default_recommendation_title, 'Test'),
                    (900000004, :demo_recommendation_title, 'Test')
                ON CONFLICT ("movieId") DO UPDATE SET title = EXCLUDED.title
                """),
            {
                "default_history_title": DEFAULT_HISTORY_TITLE,
                "demo_history_title": DEMO_HISTORY_TITLE,
                "default_recommendation_title": DEFAULT_RECOMMENDATION_TITLE,
                "demo_recommendation_title": DEMO_RECOMMENDATION_TITLE,
            },
        )
        connection.execute(
            text("""
                INSERT INTO ratings ("userId", "movieId", rating, timestamp, tenant_id)
                VALUES
                    (:user_id, 900000001, 5.0, 2000000001, 'default'),
                    (:user_id, 900000002, 5.0, 2000000002, 'demo'),
                    (987654322, 900000003, 5.0, 2000000003, 'default'),
                    (987654322, 900000004, 5.0, 2000000004, 'demo')
                """),
            {"user_id": CANARY_USER_ID},
        )

    yield

    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM ratings "
                'WHERE "movieId" IN (900000001, 900000002, 900000003, 900000004)'
            ),
        )
        connection.execute(
            text(
                'DELETE FROM movies WHERE "movieId" '
                "IN (900000001, 900000002, 900000003, 900000004)"
            )
        )
    engine.dispose()


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
