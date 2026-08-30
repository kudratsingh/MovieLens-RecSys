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

**Two database states, one set of canaries.** CI applies migrations to an
empty database and runs this suite against it; the demo stack and every
deployment run it against a seeded one (120 titles, four personas, ~500
ratings). Anything the canaries assert has to be true in both, and issue #75
was one assertion that was not: the recommendation positive control held only
while the canary title could reach a top-50 popularity list, which it can on an
empty database and cannot on a seeded one. Two rules follow, and this module
exists to enforce them:

  * every fixture row this file writes is *made* decisive rather than assumed
    to be — see ``POPULARITY_HEADROOM``, which measures the tenant's current
    most-interacted title and seeds past it;
  * anything genuinely specific to one state takes the ``database_state``
    fixture, which is parametrized at collection so the state it ran under is
    part of the test id. Both branches assert; neither skips.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import Engine, bindparam, create_engine, text
from sqlalchemy.engine import Connection

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

# One opaque token per tenant, embedded in every string that tenant's rows own.
# A single substring search then answers the two questions a leakage canary has
# to ask together: is the caller's own data here (so this read model would have
# shown a leak), and is the other tenant's data absent?
#
# The tokens carry no tenant word on purpose. "demo" and "default" already
# appear all over both payloads — in issuer URLs, policy names and Redis
# prefixes — so a token spelling either one would match text that is not a row
# and turn a leak assertion into a coincidence.
DEFAULT_SENTINEL = "sentinel-a-4d1f9a2b"
DEMO_SENTINEL = "sentinel-b-7c02e531"

DEFAULT_HISTORY_TITLE = f"RLS default history canary {DEFAULT_SENTINEL}"
DEMO_HISTORY_TITLE = f"RLS demo history canary {DEMO_SENTINEL}"
DEFAULT_RECOMMENDATION_TITLE = f"RLS default recommendation canary {DEFAULT_SENTINEL}"
DEMO_RECOMMENDATION_TITLE = f"RLS demo recommendation canary {DEMO_SENTINEL}"
DEFAULT_PERSONA_NAME = f"RLS Default Persona Canary {DEFAULT_SENTINEL}"
DEMO_PERSONA_NAME = f"RLS Demo Persona Canary {DEMO_SENTINEL}"
DEFAULT_CANARY_POSTER_URL = "https://images.example/rls-default-canary.jpg"
DEMO_CANARY_POSTER_URL = "https://images.example/rls-demo-canary.jpg"

# The detail route reads ``movie_catalog_metadata.details`` and is gated on
# ``visible = TRUE``, so unlike the four canaries above these three have to be
# visible to be reachable at all. They carry no ratings, so nothing else in the
# suite -- popularity, item-item, Library -- can pick them up.
#
# They also deliberately carry **no sentinel**. A visible catalog row is a
# shared movie fact (migration 0011): both tenants are supposed to read the same
# title and the same trailer, and the suite asserts exactly that below. A
# sentinel on one of these would be found in the other tenant's catalog page and
# reported as a leak when it is the design.
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

# The persona rows the fixture owns, one per tenant. Distinct from the four
# walkthrough personas a seeded database carries (`SEEDED_DEMO_PERSONA_IDS`),
# which this suite only ever reads.
DEFAULT_PERSONA_USER_ID = 987654323
DEMO_PERSONA_USER_ID = 987654324
_CANARY_PERSONA_IDS = (DEFAULT_PERSONA_USER_ID, DEMO_PERSONA_USER_ID)

# The demo personas `make demo-seed` loads. Present on the demo stack and on
# every deployment, absent on the database CI builds from migrations alone.
SEEDED_DEMO_PERSONA_IDS = (900000101, 900000102, 900000103, 900000104)

# How far above the tenant's current most-interacted title the recommendation
# canary is seeded. The popularity fallback orders by interaction count within
# the tenant, so "the canary is on the first page" is a claim about whatever
# else happens to be in the database — one rating is enough against migrations
# with no seed and nowhere near enough against a seeded catalog (issue #75).
# The fixture measures the incumbent top and inserts past it instead, which
# makes the canary rank 1 in its own tenant in either state.
POPULARITY_HEADROOM = 5

# Distinct user ids for the filler ratings that buy that headroom. They are
# ratings and nothing else: no persona, no movie state, no Keycloak identity.
# Teardown removes them with everything else keyed on the canary movie ids.
_FILLER_USER_ID_BASE = 987700000


@dataclass(frozen=True)
class TenantCanary:
    """One tenant's identity, sentinel, and the rows that carry it.

    Every isolation assertion in this suite is a statement about a pair of
    these, so they are described once rather than spelled out per test.
    """

    tenant_id: str
    username: str
    password: str
    sentinel: str
    persona_user_id: int
    persona_name: str
    history_movie_id: int
    history_title: str
    recommendation_movie_id: int
    recommendation_title: str

    @property
    def realm(self) -> str:
        """Realm per tenant (ADR 0007): the realm slug *is* the tenant id."""
        return self.tenant_id


DEFAULT_TENANT = TenantCanary(
    tenant_id="default",
    username="alice",
    password="alice",
    sentinel=DEFAULT_SENTINEL,
    persona_user_id=DEFAULT_PERSONA_USER_ID,
    persona_name=DEFAULT_PERSONA_NAME,
    history_movie_id=900000001,
    history_title=DEFAULT_HISTORY_TITLE,
    recommendation_movie_id=900000003,
    recommendation_title=DEFAULT_RECOMMENDATION_TITLE,
)
DEMO_TENANT = TenantCanary(
    tenant_id="demo",
    username="demo",
    password="demo",
    sentinel=DEMO_SENTINEL,
    persona_user_id=DEMO_PERSONA_USER_ID,
    persona_name=DEMO_PERSONA_NAME,
    history_movie_id=900000002,
    history_title=DEMO_HISTORY_TITLE,
    recommendation_movie_id=900000004,
    recommendation_title=DEMO_RECOMMENDATION_TITLE,
)
# Ordered so `TENANT_PAIRS` reads as "owner, other" in both directions.
TENANTS = (DEFAULT_TENANT, DEMO_TENANT)
TENANT_PAIRS = ((DEFAULT_TENANT, DEMO_TENANT), (DEMO_TENANT, DEFAULT_TENANT))


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


@pytest.fixture(scope="session", autouse=True)
def tenant_canary_rows() -> Generator[None, None, None]:
    """Seed distinct rows so endpoint isolation assertions test real data.

    Session-scoped rather than module-scoped: two modules read these rows now,
    and seeding them twice would mean the second module's teardown deleting
    rows the first had already restored — the same work, twice, with a window
    in which a shared stack is missing them for no reason.
    """
    engine = create_engine(Settings().database_url)
    with engine.begin() as connection:
        _delete_canary_rows(connection)
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
        for tenant in TENANTS:
            _seed_popularity_headroom(connection, tenant)
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
        _delete_canary_rows(connection)
        # ``movie_catalog_metadata`` has an ON DELETE CASCADE to movies, so the
        # catalog rows -- including the visible detail canaries -- go with these.
        connection.execute(
            text('DELETE FROM movies WHERE "movieId" IN :movie_ids').bindparams(
                bindparam("movie_ids", expanding=True)
            ),
            {"movie_ids": list(_CANARY_MOVIE_IDS)},
        )
    engine.dispose()


def _delete_canary_rows(connection: Connection) -> None:
    """Remove every row this fixture owns, in FK-safe order.

    Setup and teardown run the same statements: a stack the suite is pointed at
    may be a shared one (the demo stack is), so a run has to be able to start
    from a half-finished predecessor and has to leave nothing of its own behind.
    Everything here is keyed on a canary id — the ``ratings`` delete covers the
    popularity headroom rows too, since they name only canary movies.
    """
    scoped = {"canary_user": CANARY_USER_ID, "personas": list(_CANARY_PERSONA_IDS)}
    for table in ("user_feedback_events", "user_movie_state", "user_preferences"):
        connection.execute(
            text(
                f"DELETE FROM {table} WHERE user_id = :canary_user OR user_id IN :personas"
            ).bindparams(bindparam("personas", expanding=True)),
            scoped,
        )
    connection.execute(
        text("DELETE FROM recommendation_audits WHERE user_id = :canary_user"),
        {"canary_user": CANARY_USER_ID},
    )
    connection.execute(
        text("DELETE FROM demo_personas WHERE user_id IN :personas").bindparams(
            bindparam("personas", expanding=True)
        ),
        {"personas": list(_CANARY_PERSONA_IDS)},
    )
    connection.execute(
        text('DELETE FROM ratings WHERE "movieId" IN :movie_ids').bindparams(
            bindparam("movie_ids", expanding=True)
        ),
        {"movie_ids": list(_CANARY_MOVIE_IDS)},
    )


def _seed_popularity_headroom(connection: Connection, tenant: TenantCanary) -> None:
    """Make the tenant's recommendation canary its most-interacted title.

    The popularity fallback orders by ``COUNT(ratings)`` inside the tenant, so
    the incumbent top is read first and the canary is seeded one clear step
    past it. That is what makes "the caller's own canary came back" an
    assertion about isolation rather than about how much else the database
    happens to hold — the failure issue #75 records.

    The filler rows are ratings and nothing else. They carry no persona, no
    movie state and no identity, so they move exactly one thing: the count this
    title is ranked by, inside this tenant, until teardown removes them.
    """
    incumbent = connection.execute(
        text("""
            SELECT COALESCE(MAX(interactions), 0)
            FROM (
                SELECT COUNT(*) AS interactions
                FROM ratings
                WHERE tenant_id = :tenant_id AND "movieId" NOT IN :movie_ids
                GROUP BY "movieId"
            ) AS per_movie
            """).bindparams(bindparam("movie_ids", expanding=True)),
        {"tenant_id": tenant.tenant_id, "movie_ids": list(_CANARY_MOVIE_IDS)},
    ).scalar_one()
    # The incumbent is measured over the tenant's own titles only — the canary
    # rows inserted a moment ago would otherwise be counted as the field the
    # canary has to beat. It already carries one rating from that block.
    filler = int(incumbent) + POPULARITY_HEADROOM - 1
    connection.execute(
        text("""
            INSERT INTO ratings ("userId", "movieId", rating, timestamp, tenant_id)
            SELECT :first_user + offset_, :movie_id, 4.0, 2000000100 + offset_, :tenant_id
            FROM generate_series(1, CAST(:filler AS INTEGER)) AS offset_
            """),
        {
            "first_user": _FILLER_USER_ID_BASE,
            "movie_id": tenant.recommendation_movie_id,
            "tenant_id": tenant.tenant_id,
            "filler": filler,
        },
    )


@dataclass(frozen=True)
class DatabaseState:
    """What this run's database holds besides the canaries.

    CI applies migrations to an empty database; the demo stack and every
    deployment carry the seeded walkthrough fixture. The suite's canaries hold
    in both, and this exists so the handful of facts that are true in only one
    of them say which one in the test id rather than quietly changing meaning.

    It is deliberately two facts and not one, because the seeding is not
    uniform: `make demo-seed` loads personas and ratings into ``demo`` and
    leaves ``default`` empty, so on the demo stack one tenant's popularity list
    has a catalog behind the canary and the other's does not.
    """

    seeded_personas: tuple[int, ...]
    tenants_with_ratings: frozenset[str]

    @property
    def seeded(self) -> bool:
        return bool(self.seeded_personas or self.tenants_with_ratings)

    @property
    def name(self) -> str:
        return "seeded-database" if self.seeded else "empty-database"

    def has_catalog(self, tenant: TenantCanary) -> bool:
        """Whether this tenant has anything of its own to rank behind the canary."""
        return tenant.tenant_id in self.tenants_with_ratings


_probed_state: DatabaseState | None = None


def _probe_database_state() -> DatabaseState:
    """Read the state once, at collection time, before the canaries are seeded.

    Both probes exclude the canary rows themselves: a previous run killed
    mid-suite leaves them behind, and a state probe that counted them would
    report a database seeded by the very fixture whose behaviour it is meant to
    calibrate.
    """
    global _probed_state
    if _probed_state is None:
        engine = create_engine(Settings().database_url)
        try:
            with engine.connect() as connection:
                personas = connection.execute(
                    text("SELECT user_id FROM demo_personas WHERE user_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": list(SEEDED_DEMO_PERSONA_IDS)},
                ).scalars()
                # EXISTS rather than a count: `default` can hold the whole 25M
                # MovieLens ingest, and the only question here is whether the
                # tenant has anything at all.
                with_ratings = {
                    tenant.tenant_id
                    for tenant in TENANTS
                    if connection.execute(
                        text("""
                            SELECT EXISTS (
                                SELECT 1 FROM ratings
                                WHERE tenant_id = :tenant_id AND "movieId" NOT IN :movie_ids
                            )
                            """).bindparams(bindparam("movie_ids", expanding=True)),
                        {"tenant_id": tenant.tenant_id, "movie_ids": list(_CANARY_MOVIE_IDS)},
                    ).scalar_one()
                }
                _probed_state = DatabaseState(
                    tuple(sorted(int(row) for row in personas)), frozenset(with_ratings)
                )
        finally:
            engine.dispose()
    return _probed_state


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Put the database state into the id of every test that depends on it.

    Parametrizing with a single value is the point: the id gains a
    ``[seeded-database]`` or ``[empty-database]`` suffix, so a run's output says
    which shape it exercised instead of leaving a reader to infer it from the
    job that produced it.
    """
    if "database_state" in metafunc.fixturenames:
        state = _probe_database_state()
        metafunc.parametrize("database_state", [state], ids=[state.name], scope="session")


@pytest.fixture(scope="session")
def rls_engine() -> Generator[Engine, None, None]:
    """``app_user`` through pgBouncer — the connection the API itself uses.

    RLS applies here: this role holds no BYPASSRLS, which the first assertion in
    ``test_rls_is_engaged.py`` checks rather than assumes.
    """
    engine = create_engine(Settings().app_user_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def bypass_rls_engine() -> Generator[Engine, None, None]:
    """``admin_user`` direct — BYPASSRLS, the way offline materialization runs."""
    engine = create_engine(Settings().admin_user_database_url)
    yield engine
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
