"""
Fixtures for the tenant-isolation integration tests. Requires the
docker-compose stack to be running (Postgres + Keycloak + pgBouncer)
with migrations applied and both `default` + `demo` realms seeded.

Skips every test when the stack isn't reachable so the file can live
in the same repo as unit tests without breaking CI on runners that
don't boot Docker — *unless* the caller declared that a stack is
mandatory by setting ``REQUIRE_TENANT_ISOLATION_STACK=1``, in which
case an absent stack is a failure. The distinction matters because a
skip and a pass look identical in a job summary: pointed at a
deployment, this file would otherwise report success while executing
nothing, on the one bug class the project calls highest-severity.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator

import httpx
import pytest
from sqlalchemy import bindparam, create_engine, text

from src.config import Settings
from synthetic.tenant_isolation.remote_canary import REQUIRE_STACK_ENV, live_stack_required

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
    if live_stack_required():
        pytest.fail(
            f"{REQUIRE_STACK_ENV}=1 was set, so these tests must run: Keycloak did not "
            f"answer at {_KEYCLOAK_URL}/realms/default and the cross-tenant leakage "
            "canaries were never executed. Skipping here would report a pass for a "
            "gate that never ran — bring the stack up, or point the deployed-stack "
            "canary (`python -m synthetic.tenant_isolation.remote_canary`) at the target.",
            pytrace=False,
        )
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
DEFAULT_PERSONA_NAME = "RLS Default Persona Canary"
DEMO_PERSONA_NAME = "RLS Demo Persona Canary"
DEFAULT_CANARY_POSTER_URL = "https://images.example/rls-default-canary.jpg"
DEMO_CANARY_POSTER_URL = "https://images.example/rls-demo-canary.jpg"

# The detail route reads ``movie_catalog_metadata.details`` and is gated on
# ``visible = TRUE``, so unlike the four canaries above these three have to be
# visible to be reachable at all. They carry no ratings, so nothing else in the
# suite -- popularity, item-item, Library -- can pick them up.
DEFAULT_DETAIL_MOVIE_ID = 900000005
DEMO_DETAIL_MOVIE_ID = 900000006
NO_DETAIL_MOVIE_ID = 900000007
DEFAULT_DETAIL_TITLE = "RLS default detail canary"
DEMO_DETAIL_TITLE = "RLS demo detail canary"
NO_DETAIL_TITLE = "RLS detail-less canary"
DEFAULT_DETAIL_TRAILER_KEY = "rls-default-trailer"
DEMO_DETAIL_TRAILER_KEY = "rls-demo-trailer"

_DETAIL_MOVIE_IDS = (DEFAULT_DETAIL_MOVIE_ID, DEMO_DETAIL_MOVIE_ID, NO_DETAIL_MOVIE_ID)
_CANARY_MOVIE_IDS = (900000001, 900000002, 900000003, 900000004) + _DETAIL_MOVIE_IDS


def _detail_payload(trailer_key: str, director: str) -> str:
    """One catalog detail payload, in the shape the fixture writes."""
    return json.dumps(
        {
            "tagline": f"{director} canary",
            "runtime_minutes": 101,
            "release_date": "1994-10-14",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/rls-canary.jpg",
            "tmdb_rating": {"average": 8.1, "count": 42},
            "directors": [director],
            "cast": [{"name": "Canary Lead", "character": "Self", "profile_url": None}],
            "trailer": {"provider": "youtube", "key": trailer_key, "name": "Trailer"},
            "fetched_at": "2026-08-28T00:00:00+00:00",
        }
    )


@pytest.fixture(scope="module", autouse=True)
def tenant_canary_rows() -> Generator[None, None, None]:
    """Seed distinct rows so endpoint isolation assertions test real data."""
    engine = create_engine(Settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM user_feedback_events "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text(
                "DELETE FROM user_movie_state "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text(
                "DELETE FROM user_preferences "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM recommendation_audits " "WHERE user_id = :user_id"),
            {"user_id": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM request_audits WHERE user_id = :user_id"),
            {"user_id": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM demo_personas " "WHERE user_id IN (987654323, 987654324)")
        )
        connection.execute(
            text('DELETE FROM ratings WHERE "movieId" IN :movie_ids').bindparams(
                bindparam("movie_ids", expanding=True)
            ),
            {"movie_ids": list(_CANARY_MOVIE_IDS)},
        )
        connection.execute(
            text("""
                INSERT INTO movies ("movieId", title, genres)
                VALUES
                    (900000001, :default_history_title, 'Test'),
                    (900000002, :demo_history_title, 'Test'),
                    (900000003, :default_recommendation_title, 'Test'),
                    (900000004, :demo_recommendation_title, 'Test'),
                    (:default_detail_id, :default_detail_title, 'Test'),
                    (:demo_detail_id, :demo_detail_title, 'Test'),
                    (:no_detail_id, :no_detail_title, 'Test')
                ON CONFLICT ("movieId") DO UPDATE SET title = EXCLUDED.title
                """),
            {
                "default_history_title": DEFAULT_HISTORY_TITLE,
                "demo_history_title": DEMO_HISTORY_TITLE,
                "default_recommendation_title": DEFAULT_RECOMMENDATION_TITLE,
                "demo_recommendation_title": DEMO_RECOMMENDATION_TITLE,
                "default_detail_id": DEFAULT_DETAIL_MOVIE_ID,
                "demo_detail_id": DEMO_DETAIL_MOVIE_ID,
                "no_detail_id": NO_DETAIL_MOVIE_ID,
                "default_detail_title": DEFAULT_DETAIL_TITLE,
                "demo_detail_title": DEMO_DETAIL_TITLE,
                "no_detail_title": NO_DETAIL_TITLE,
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
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, watched_at, rating,
                    rating_updated_at, state_version, updated_at
                ) VALUES
                    ('default', :user_id, 900000001, to_timestamp(2000000001), 5.0,
                     to_timestamp(2000000001), 1, to_timestamp(2000000001)),
                    ('demo', :user_id, 900000002, to_timestamp(2000000002), 5.0,
                     to_timestamp(2000000002), 1, to_timestamp(2000000002)),
                    ('default', 987654323, 900000003, to_timestamp(2000000003), 4.5,
                     to_timestamp(2000000003), 1, to_timestamp(2000000003)),
                    ('demo', 987654324, 900000004, to_timestamp(2000000004), NULL,
                     NULL, 1, to_timestamp(2000000004)),
                    ('default', 987654323, :default_detail_id, to_timestamp(2000000005), 4.0,
                     to_timestamp(2000000005), 1, to_timestamp(2000000005)),
                    ('demo', 987654324, :demo_detail_id, to_timestamp(2000000006), 3.5,
                     to_timestamp(2000000006), 1, to_timestamp(2000000006))
                """),
            {
                "user_id": CANARY_USER_ID,
                "default_detail_id": DEFAULT_DETAIL_MOVIE_ID,
                "demo_detail_id": DEMO_DETAIL_MOVIE_ID,
            },
        )
        connection.execute(
            text("""
                INSERT INTO demo_personas
                    (tenant_id, user_id, slug, display_name, description, sort_order, synthetic)
                VALUES
                    ('default', 987654323, 'default-canary', :default_name, 'Test', 1, TRUE),
                    ('demo', 987654324, 'demo-canary', :demo_name, 'Test', 1, TRUE)
                """),
            {"default_name": DEFAULT_PERSONA_NAME, "demo_name": DEMO_PERSONA_NAME},
        )
        # Artwork for exactly two of the four canary titles, so a single run
        # sees both the populated and the missing case on each read model.
        # ``visible`` is FALSE on purpose: these rows must not surface in a
        # Browse page while the fixture is alive, and Library and history
        # artwork is deliberately not conditioned on catalog visibility.
        # The FK to movies is ON DELETE CASCADE, so teardown takes them with it.
        connection.execute(
            text("""
                INSERT INTO movie_catalog_metadata (
                    movie_id, sort_title, release_year, poster_url, overview,
                    metadata_source, source_status, visible
                ) VALUES
                    (900000001, 'rls default history canary', 1994,
                     :default_poster, NULL, 'reviewed-fixture', 'complete', FALSE),
                    (900000004, 'rls demo recommendation canary', 2004,
                     :demo_poster, NULL, 'reviewed-fixture', 'complete', FALSE)
                ON CONFLICT (movie_id) DO UPDATE SET
                    poster_url = EXCLUDED.poster_url,
                    release_year = EXCLUDED.release_year,
                    visible = EXCLUDED.visible
                """),
            {
                "default_poster": DEFAULT_CANARY_POSTER_URL,
                "demo_poster": DEMO_CANARY_POSTER_URL,
            },
        )
        # The detail canaries: two carrying a payload, one carrying none, so a
        # single run sees both a populated ``details`` object and an explicit
        # null. Movie facts are shared by design (0011) — what must not cross
        # the boundary is the state overlaid on them, which is why each of
        # these has a rating from exactly one tenant's persona.
        connection.execute(
            text("""
                INSERT INTO movie_catalog_metadata (
                    movie_id, sort_title, release_year, poster_url, overview,
                    details, metadata_source, source_status, visible
                ) VALUES
                    (:default_detail_id, 'rls default detail canary', 1994, NULL, NULL,
                     CAST(:default_details AS JSONB), 'reviewed-fixture', 'complete', TRUE),
                    (:demo_detail_id, 'rls demo detail canary', 2004, NULL, NULL,
                     CAST(:demo_details AS JSONB), 'reviewed-fixture', 'complete', TRUE),
                    (:no_detail_id, 'rls detail-less canary', 1999, NULL, NULL,
                     NULL, 'movielens', 'partial', TRUE)
                ON CONFLICT (movie_id) DO UPDATE SET
                    details = EXCLUDED.details,
                    release_year = EXCLUDED.release_year,
                    visible = EXCLUDED.visible
                """),
            {
                "default_detail_id": DEFAULT_DETAIL_MOVIE_ID,
                "demo_detail_id": DEMO_DETAIL_MOVIE_ID,
                "no_detail_id": NO_DETAIL_MOVIE_ID,
                "default_details": _detail_payload(DEFAULT_DETAIL_TRAILER_KEY, "Default Director"),
                "demo_details": _detail_payload(DEMO_DETAIL_TRAILER_KEY, "Demo Director"),
            },
        )

    yield

    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM user_feedback_events "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text(
                "DELETE FROM user_movie_state "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text(
                "DELETE FROM user_preferences "
                "WHERE user_id IN (:canary_user, 987654323, 987654324)"
            ),
            {"canary_user": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM recommendation_audits " "WHERE user_id = :user_id"),
            {"user_id": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM request_audits WHERE user_id = :user_id"),
            {"user_id": CANARY_USER_ID},
        )
        connection.execute(
            text("DELETE FROM demo_personas " "WHERE user_id IN (987654323, 987654324)")
        )
        connection.execute(
            text('DELETE FROM ratings WHERE "movieId" IN :movie_ids').bindparams(
                bindparam("movie_ids", expanding=True)
            ),
            {"movie_ids": list(_CANARY_MOVIE_IDS)},
        )
        # ``movie_catalog_metadata`` has an ON DELETE CASCADE to movies, so the
        # catalog rows -- including the visible detail canaries -- go with these.
        connection.execute(
            text('DELETE FROM movies WHERE "movieId" IN :movie_ids').bindparams(
                bindparam("movie_ids", expanding=True)
            ),
            {"movie_ids": list(_CANARY_MOVIE_IDS)},
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
