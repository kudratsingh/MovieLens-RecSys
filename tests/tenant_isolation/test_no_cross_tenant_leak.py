"""
Non-negotiable #9: no code path may return one tenant's data in
response to another tenant's request. Cross-tenant leakage is the
highest-severity bug class. This test authenticates as tenants A and
B against the live docker-compose stack and hits every authenticated
endpoint, asserting the returned payload matches the caller's tenant.

Covered today: ``/whoami``, ``/users/{id}/recommendations`` (including the
serving-policy and exclusion evidence it now returns, and the per-item movie
state it overlays), ``/users/{id}/history``, ``/users/{id}/audits``,
``/personas``, ``/users/{id}/features``, ``/users/{id}/catalog``,
``/users/{id}/library`` (including the shared artwork its rows now carry, the
Seen tab's search, genre, year and ranking parameters, and the exact
``page.matched`` count they produce),
``/users/{id}/movies/{id}`` (including the TMDB detail payload it now carries),
the rating write path, ``DELETE /users/{id}/ratings``, and
``GET|PUT /users/{id}/preferences``. Not yet covered:
``/users/{id}/taste-profile``. Every new endpoint gains coverage here —
the test's job is to be the tenant-isolation gate every serving PR passes
through in CI.

**Every "not in the other tenant's response" assertion is paired.** On its own
that assertion is satisfied by a system with no data in it, which is how the
recommendation control came to pass on CI's empty database and fail on a seeded
one (issue #75). So each read below is made twice, once per tenant, and the
same string that must be absent from one response has to be *present* in the
other: the sentinel is the caller's own, and the read that would have carried a
leak is demonstrably the read that carries the caller's own rows. What proves
the row exists at all, and that only the policy is hiding it, is one layer down
in ``test_rls_is_engaged.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from src.serving.app import app
from tests.tenant_isolation.conftest import (
    CANARY_USER_ID,
    DEFAULT_CANARY_POSTER_URL,
    DEFAULT_DETAIL_MOVIE_ID,
    DEFAULT_DETAIL_TRAILER_KEY,
    DEFAULT_HISTORY_TITLE,
    DEFAULT_PERSONA_NAME,
    DEFAULT_RECOMMENDATION_TITLE,
    DEFAULT_TENANT,
    DEMO_CANARY_POSTER_URL,
    DEMO_DETAIL_MOVIE_ID,
    DEMO_DETAIL_TRAILER_KEY,
    DEMO_HISTORY_TITLE,
    DEMO_PERSONA_NAME,
    DEMO_RECOMMENDATION_TITLE,
    DEMO_TENANT,
    NO_DETAIL_MOVIE_ID,
    SEEDED_DEMO_PERSONA_IDS,
    TENANT_PAIRS,
    TENANTS,
    DatabaseState,
    TenantCanary,
    TokenMinter,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI TestClient that triggers the app's lifespan (startup
    checks + engine construction + middleware wire-up)."""
    with TestClient(app) as c:
        yield c


@dataclass(frozen=True)
class SentinelRead:
    """One authenticated read, addressed to whichever tenant is making it."""

    name: str
    template: str

    def path(self, tenant: TenantCanary) -> str:
        return self.template.format(
            canary_user=CANARY_USER_ID, persona_user_id=tenant.persona_user_id
        )


# One read per read model that can carry a tenant-owned string: the persona
# registry, the history join over `ratings`, the popularity ranking the
# fallback produces, and the Library's own view of `user_movie_state`. Each
# tenant asks the same question about its own rows, so the pair of answers is
# both halves of the control.
SENTINEL_READS = (
    SentinelRead("personas", "/personas"),
    SentinelRead("history", "/users/{canary_user}/history"),
    SentinelRead("recommendations", "/users/{canary_user}/recommendations?limit=50"),
    SentinelRead("library-seen", "/users/{persona_user_id}/library?tab=history"),
)


def test_healthz_needs_no_auth(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_whoami_rejects_missing_token(client: TestClient) -> None:
    resp = client.get("/whoami")
    assert resp.status_code == 401


def test_whoami_returns_default_tenant_for_alice(
    client: TestClient, mint_token: TokenMinter
) -> None:
    token = mint_token("default", "alice", "alice")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "default"
    assert body["realm"] == "default"
    assert body["tenant_display_name"] == "MovieLens default tenant"
    assert body["redis_prefix"] == "tenant:default:"


def test_whoami_returns_demo_tenant_for_demo_user(
    client: TestClient, mint_token: TokenMinter
) -> None:
    token = mint_token("demo", "demo", "demo")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "demo"
    assert body["realm"] == "demo"
    assert body["tenant_display_name"] == "Portfolio walkthrough demo tenant"
    assert body["redis_prefix"] == "tenant:demo:"


def test_alice_default_token_never_returns_demo_data(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """Cross-tenant canary: authenticate as alice (tenant=default),
    hit /whoami, and assert no field in the response mentions the
    demo tenant. The user-scoped endpoints get the same treatment in
    the tests further down.
    """
    token = mint_token("default", "alice", "alice")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body_text = resp.text.lower()
    # Strong assertion — no reference to the demo tenant should
    # appear anywhere in the response payload.
    assert (
        "demo" not in body_text
    ), f"demo tenant data leaked into a default-tenant response: {resp.text}"


def test_demo_token_never_returns_default_data(client: TestClient, mint_token: TokenMinter) -> None:
    token = mint_token("demo", "demo", "demo")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body_text = resp.text.lower()
    assert (
        "default" not in body_text
    ), f"default tenant data leaked into a demo-tenant response: {resp.text}"


@pytest.mark.parametrize("endpoint", ["recommendations", "history"])
def test_user_endpoints_never_cross_tenant_boundary(
    client: TestClient,
    mint_token: TokenMinter,
    endpoint: str,
) -> None:
    """The same user lookup must be scoped independently for each tenant.

    Both presence assertions hold in either database state now: history reads
    the caller's own state rows directly, and the recommendation canary is
    seeded past whatever the tenant's most-interacted title happens to be
    (``POPULARITY_HEADROOM``). Before that it was seeded with one rating and
    was on the first page only while nothing else was — the failure issue #75
    recorded on the seeded demo database.
    """
    default_token = mint_token("default", "alice", "alice")
    demo_token = mint_token("demo", "demo", "demo")
    query = "?limit=50" if endpoint == "recommendations" else ""

    default_response = client.get(
        f"/users/{CANARY_USER_ID}/{endpoint}{query}",
        headers={"Authorization": f"Bearer {default_token}"},
    )
    demo_response = client.get(
        f"/users/{CANARY_USER_ID}/{endpoint}{query}",
        headers={"Authorization": f"Bearer {demo_token}"},
    )

    assert default_response.status_code == 200
    assert demo_response.status_code == 200
    assert default_response.json()["tenant_id"] == "default"
    assert demo_response.json()["tenant_id"] == "demo"
    assert DEMO_HISTORY_TITLE not in default_response.text
    assert DEMO_RECOMMENDATION_TITLE not in default_response.text
    assert DEFAULT_HISTORY_TITLE not in demo_response.text
    assert DEFAULT_RECOMMENDATION_TITLE not in demo_response.text
    if endpoint == "history":
        assert DEFAULT_HISTORY_TITLE in default_response.text
        assert DEMO_HISTORY_TITLE in demo_response.text
    else:
        assert DEFAULT_RECOMMENDATION_TITLE in default_response.text
        assert DEMO_RECOMMENDATION_TITLE in demo_response.text


@pytest.mark.parametrize("read", SENTINEL_READS, ids=lambda read: read.name)
def test_a_read_carries_its_callers_sentinel_and_never_the_other_tenants(
    client: TestClient, mint_token: TokenMinter, read: SentinelRead
) -> None:
    """The positive control and the isolation assertion, in one statement.

    Each tenant makes the same read against its own rows. Its own sentinel has
    to come back — so this read model does surface tenant-owned strings, and a
    leak would have been visible here — and the other tenant's sentinel must
    not, though it exists at that moment in a row of the same table.

    Neither half means much alone. "The demo sentinel was absent" is trivially
    true of an empty database; "the default sentinel was present" says nothing
    about isolation. Asserted together on the same endpoint, in the same run,
    they are the pair issue #75 found missing.
    """
    for owner, other in TENANT_PAIRS:
        token = mint_token(owner.realm, owner.username, owner.password)
        response = client.get(read.path(owner), headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200, response.text
        assert response.json()["tenant_id"] == owner.tenant_id
        assert owner.sentinel in response.text, (
            f"{read.name} did not return tenant {owner.tenant_id!r}'s own sentinel, so the "
            "absence of the other tenant's sentinel below proves nothing"
        )
        assert other.sentinel not in response.text, (
            f"{read.name} returned tenant {other.tenant_id!r}'s sentinel to tenant "
            f"{owner.tenant_id!r}: {response.text}"
        )


def test_the_recommendation_canary_tops_its_own_tenants_ranking(
    client: TestClient, mint_token: TokenMinter, database_state: DatabaseState
) -> None:
    """Rank 1, in both database shapes, for the reason the fixture arranged.

    The popularity fallback orders by interaction count inside the tenant, so
    what "on the first page" means depends on what else that tenant holds.
    Rank 1 does not: the canary is seeded past the incumbent top either way.
    The list around it is what differs, and the two branches say so — a tenant
    with no catalog of its own has nothing else to rank, one with a catalog
    ranks it behind the canary rather than instead of it.
    """
    for owner, other in TENANT_PAIRS:
        token = mint_token(owner.realm, owner.username, owner.password)
        response = client.get(
            f"/users/{CANARY_USER_ID}/recommendations?limit=50",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        ranked = [item["movie_id"] for item in response.json()["items"]]
        assert ranked[:1] == [owner.recommendation_movie_id]
        assert other.recommendation_movie_id not in ranked
        if database_state.has_catalog(owner):
            # The case the old control could not survive: a real catalog the
            # canary has to out-rank rather than be alone in.
            assert len(ranked) > 1
        else:
            # Migrations and no seed for this tenant: the canary rows are the
            # whole of it. The caller's own history title is already seen.
            assert ranked == [owner.recommendation_movie_id]


def test_the_persona_list_a_caller_receives_is_only_ever_its_own_tenants(
    client: TestClient, mint_token: TokenMinter, database_state: DatabaseState
) -> None:
    """``/personas`` is a whole-tenant read, so it is the clearest place to
    watch the seeded rows the canaries do not own.

    The walkthrough personas live in ``demo`` and only there. On a seeded
    database that makes them a second, independent sentinel — rows this suite
    did not write, which the default tenant must never be shown. On an empty
    database there are none, and the assertion available instead is exact:
    each tenant sees its own canary persona and nothing else.
    """
    listings = {}
    for tenant in TENANTS:
        token = mint_token(tenant.realm, tenant.username, tenant.password)
        response = client.get("/personas", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["tenant_id"] == tenant.tenant_id
        listings[tenant.tenant_id] = {item["user_id"] for item in response.json()["items"]}

    for owner, other in TENANT_PAIRS:
        assert owner.persona_user_id in listings[owner.tenant_id]
        assert other.persona_user_id not in listings[owner.tenant_id]

    seeded = set(SEEDED_DEMO_PERSONA_IDS)
    if database_state.seeded_personas:
        assert seeded <= listings["demo"]
        assert not seeded & listings["default"]
    else:
        assert listings["demo"] == {DEMO_TENANT.persona_user_id}
        assert listings["default"] == {DEFAULT_TENANT.persona_user_id}


def test_persona_endpoint_never_crosses_tenant_boundary(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_token = mint_token("default", "alice", "alice")
    demo_token = mint_token("demo", "demo", "demo")

    default_response = client.get("/personas", headers={"Authorization": f"Bearer {default_token}"})
    demo_response = client.get("/personas", headers={"Authorization": f"Bearer {demo_token}"})

    assert default_response.status_code == 200
    assert demo_response.status_code == 200
    assert DEFAULT_PERSONA_NAME in default_response.text
    assert DEMO_PERSONA_NAME not in default_response.text
    assert DEMO_PERSONA_NAME in demo_response.text
    assert DEFAULT_PERSONA_NAME not in demo_response.text


def test_recommendation_audits_are_visible_only_inside_active_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_token = mint_token("default", "alice", "alice")
    demo_token = mint_token("demo", "demo", "demo")
    headers_by_tenant = {
        "default": {"Authorization": f"Bearer {default_token}"},
        "demo": {"Authorization": f"Bearer {demo_token}"},
    }

    for headers in headers_by_tenant.values():
        response = client.get(
            f"/users/{CANARY_USER_ID}/recommendations",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"]

    default_audits = client.get(
        f"/users/{CANARY_USER_ID}/audits",
        headers=headers_by_tenant["default"],
    )
    demo_audits = client.get(
        f"/users/{CANARY_USER_ID}/audits",
        headers=headers_by_tenant["demo"],
    )

    assert default_audits.status_code == 200
    assert demo_audits.status_code == 200
    assert default_audits.json()["items"]
    assert demo_audits.json()["items"]
    assert all(item["tenant_id"] == "default" for item in default_audits.json()["items"])
    assert all(item["tenant_id"] == "demo" for item in demo_audits.json()["items"])
    assert "900000004" not in default_audits.text
    assert "900000003" not in demo_audits.text


def test_rating_write_is_confined_to_active_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_token = mint_token("default", "alice", "alice")
    demo_token = mint_token("demo", "demo", "demo")

    blocked = client.put(
        "/users/987654324/ratings/900000004",
        json={"rating": 5},
        headers={"Authorization": f"Bearer {default_token}"},
    )
    written = client.put(
        "/users/987654324/ratings/900000004",
        json={"rating": 5},
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    default_history = client.get(
        "/users/987654324/history",
        headers={"Authorization": f"Bearer {default_token}"},
    )
    demo_history = client.get(
        "/users/987654324/history",
        headers={"Authorization": f"Bearer {demo_token}"},
    )

    assert blocked.status_code == 404
    assert written.status_code == 200
    assert DEMO_RECOMMENDATION_TITLE not in default_history.text
    assert DEMO_RECOMMENDATION_TITLE in demo_history.text


def test_library_state_and_mutations_are_tenant_scoped(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_token = mint_token("default", "alice", "alice")
    demo_token = mint_token("demo", "demo", "demo")
    default_headers = {"Authorization": f"Bearer {default_token}"}
    demo_headers = {"Authorization": f"Bearer {demo_token}"}

    default_library = client.get(
        "/users/987654323/library?tab=rated",
        headers=default_headers,
    )
    demo_library = client.get(
        "/users/987654324/library?tab=history",
        headers=demo_headers,
    )
    blocked_cross_tenant = client.put(
        "/users/987654324/movies/900000004/rating",
        json={"rating": 5.0},
        headers=default_headers,
    )
    mutation = client.put(
        "/users/987654324/movies/900000004/rating",
        json={"rating": 4.0},
        headers={**demo_headers, "Idempotency-Key": "a53400f5-aa61-4424-ac68-3210c00c6098"},
    )
    immediate_read = client.get(
        "/users/987654324/library?tab=rated",
        headers=demo_headers,
    )

    assert default_library.status_code == 200
    assert demo_library.status_code == 200
    assert DEFAULT_RECOMMENDATION_TITLE in default_library.text
    assert DEMO_RECOMMENDATION_TITLE not in default_library.text
    assert DEMO_RECOMMENDATION_TITLE in demo_library.text
    assert DEFAULT_RECOMMENDATION_TITLE not in demo_library.text
    assert blocked_cross_tenant.status_code == 404
    assert mutation.status_code == 200
    assert mutation.json()["state"]["rating"] == 4.0
    assert DEMO_RECOMMENDATION_TITLE in immediate_read.text


def test_seen_filters_and_the_matched_count_stay_inside_the_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """The Seen tab's filters are each another way to ask for rows.

    A search term, a genre, a year window and a ranking are four more places a
    missing tenant predicate could widen a query, so they are fired together —
    and with the other tenant's titles named in ``q``, which is the request a
    curious client would actually make. ``page.matched`` gets the same
    treatment for a different reason: it answers with a number rather than a
    row, so a count computed across the boundary would leak the size of the
    other tenant's collection without ever printing a title.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}
    # A window both tenants' enriched canaries sit inside, so an escaped row
    # would be returned rather than filtered out by accident.
    view = "tab=history&sort=tmdb&genre=Test&year_from=1990&year_to=2005"

    default_seen = client.get(f"/users/987654323/library?{view}", headers=default_headers)
    demo_seen = client.get(f"/users/987654324/library?{view}", headers=demo_headers)
    hunting = client.get(f"/users/987654323/library?{view}&q=demo", headers=default_headers)

    assert default_seen.status_code == 200
    assert demo_seen.status_code == 200
    assert hunting.status_code == 200

    # Positive control first: the filters select real rows on both sides, so an
    # empty cross-tenant answer below is isolation rather than a typo.
    assert [item["movie_id"] for item in default_seen.json()["items"]] == [DEFAULT_DETAIL_MOVIE_ID]
    assert [item["movie_id"] for item in demo_seen.json()["items"]] == [
        DEMO_DETAIL_MOVIE_ID,
        900000004,
    ]
    assert default_seen.json()["page"]["matched"] == 1
    assert demo_seen.json()["page"]["matched"] == 2
    assert DEMO_RECOMMENDATION_TITLE not in default_seen.text
    assert DEFAULT_RECOMMENDATION_TITLE not in demo_seen.text

    # The other tenant's titles, asked for by name through every filter.
    assert hunting.json()["items"] == []
    assert hunting.json()["page"]["matched"] == 0
    assert DEMO_RECOMMENDATION_TITLE not in hunting.text
    assert DEMO_HISTORY_TITLE not in hunting.text

    # The unfiltered tab totals are the caller's own as well, and they are not
    # the filtered count: both numbers are on one screen and neither may be
    # the other tenant's.
    assert default_seen.json()["counts"]["history"] == 2
    assert demo_seen.json()["counts"]["history"] == 2


def test_a_seen_cursor_does_not_survive_a_changed_view(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """A cursor is bound to the fingerprint of the query that issued it, so a
    link kept from another ranking is refused rather than answered with a page
    from somewhere in the middle of a different order."""
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}
    issued = client.get(
        "/users/987654324/library?tab=history&sort=tmdb&limit=1", headers=demo_headers
    )
    assert issued.status_code == 200
    cursor = issued.json()["page"]["next_cursor"]
    assert cursor, "a limit of one over two rows must hand back a cursor"

    same_view = client.get(
        f"/users/987654324/library?tab=history&sort=tmdb&limit=1&cursor={cursor}",
        headers=demo_headers,
    )
    other_view = client.get(
        f"/users/987654324/library?tab=history&sort=release&limit=1&cursor={cursor}",
        headers=demo_headers,
    )

    assert same_view.status_code == 200
    assert other_view.status_code == 400
    assert other_view.json()["detail"] == "library cursor is invalid for this query"


def test_serving_policy_and_exclusion_evidence_are_tenant_scoped(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """Bundle 6 reshapes the recommendation and audit payloads.

    Every field it adds is derived from the caller's own tenant state, so each
    one needs a canary: a digest or count computed from the other tenant's rows
    would be a leak even though it is not a title.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    default_recs = client.get(f"/users/{CANARY_USER_ID}/recommendations", headers=default_headers)
    demo_recs = client.get(f"/users/{CANARY_USER_ID}/recommendations", headers=demo_headers)

    assert default_recs.status_code == 200
    assert demo_recs.status_code == 200
    for response in (default_recs, demo_recs):
        policy = response.json()["serving_policy"]
        assert policy["name"] == response.json()["policy"]
        assert policy["threshold"] == 5
        assert policy["filter_policy"].endswith("-v1")
        # A rank score must never be advertised as a probability.
        assert policy["score_scale"] in {"lightgbm-rank-score", "tenant-interaction-count"}
        # Learned serving implies the threshold was met. The converse does
        # not hold: a warm user can still fall back if the sidecar is down.
        if policy["learned"]:
            assert policy["positive_signal_count"] >= policy["threshold"]
    assert DEMO_RECOMMENDATION_TITLE not in default_recs.text
    assert DEFAULT_RECOMMENDATION_TITLE not in demo_recs.text

    default_audits = client.get(f"/users/{CANARY_USER_ID}/audits", headers=default_headers)
    demo_audits = client.get(f"/users/{CANARY_USER_ID}/audits", headers=demo_headers)

    assert default_audits.status_code == 200
    assert demo_audits.status_code == 200
    for audits, tenant in ((default_audits, "default"), (demo_audits, "demo")):
        newest = audits.json()["items"][0]
        assert newest["tenant_id"] == tenant
        for key in (
            "input_state_revision",
            "input_state_hash",
            "exclusion_hash",
            "positive_signal_count",
            "excluded_count",
            "filter_policy",
            "candidate_sources",
            "reason",
        ):
            assert key in newest
        assert newest["reason"]
        assert newest["excluded_count"] >= 0
    # Each tenant sees only its own canary rows, so the digests over those rows
    # must differ across tenants.
    default_newest = default_audits.json()["items"][0]
    demo_newest = demo_audits.json()["items"][0]
    assert default_newest["exclusion_hash"] != demo_newest["exclusion_hash"]


def test_audit_reads_reject_a_token_from_the_other_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.get(f"/users/{CANARY_USER_ID}/audits")
    authenticated = client.get(f"/users/{CANARY_USER_ID}/audits", headers=demo_headers)

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert all(item["tenant_id"] == "demo" for item in authenticated.json()["items"])


def test_online_user_features_never_answer_for_the_other_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """`/features` reads Feast/Redis with the caller's tenant as part of the key.

    The tenant-isolation job boots Postgres, pgBouncer, and Keycloak but not the
    feature server, so this canary pins the two properties that hold either way:
    the endpoint is authenticated, and it answers for the caller's tenant or
    fails closed — never for the other tenant.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.get(f"/users/{CANARY_USER_ID}/features")
    default_features = client.get(f"/users/{CANARY_USER_ID}/features", headers=default_headers)
    demo_features = client.get(f"/users/{CANARY_USER_ID}/features", headers=demo_headers)

    assert unauthenticated.status_code == 401
    for response, tenant in ((default_features, "default"), (demo_features, "demo")):
        assert response.status_code in {200, 503}
        if response.status_code == 200:
            assert response.json()["tenant_id"] == tenant
            assert response.json()["source"] == "feast-redis"
    if default_features.status_code == 200 and demo_features.status_code == 200:
        assert default_features.json()["tenant_id"] != demo_features.json()["tenant_id"]


def test_catalog_state_overlay_is_confined_to_the_active_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.get("/users/987654323/catalog")
    default_catalog = client.get("/users/987654323/catalog?limit=48", headers=default_headers)
    demo_catalog = client.get("/users/987654324/catalog?limit=48", headers=demo_headers)
    # 987654323 is a default-tenant persona, so a demo caller must not find it.
    cross_tenant = client.get("/users/987654323/catalog", headers=demo_headers)

    assert unauthenticated.status_code == 401
    assert default_catalog.status_code == 200
    assert demo_catalog.status_code == 200
    assert cross_tenant.status_code == 404
    assert default_catalog.json()["tenant_id"] == "default"
    assert demo_catalog.json()["tenant_id"] == "demo"
    # Shared movie metadata is global; only the state overlay is tenant-owned.
    for response, tenant in ((default_catalog, "default"), (demo_catalog, "demo")):
        states = [item["state"] for item in response.json()["items"] if item["state"]]
        assert all(state["tenant_id"] == tenant for state in states)


def test_rating_reset_cannot_reach_a_persona_in_another_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.delete("/users/987654324/ratings")
    blocked = client.delete("/users/987654324/ratings", headers=default_headers)
    survivors = client.get("/users/987654324/library?tab=history", headers=demo_headers)

    assert unauthenticated.status_code == 401
    # A destructive call aimed across the tenant boundary must be a no-op, not
    # a partially applied write, so the target's state is checked afterwards.
    assert blocked.status_code == 404
    assert survivors.status_code == 200
    assert DEMO_RECOMMENDATION_TITLE in survivors.text


def test_library_and_history_artwork_reads_the_shared_snapshot(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """Poster and year on Library and history rows come from the shared
    ``movie_catalog_metadata`` snapshot, which is global by design (migration
    0011). The rows they hang off are not: each tenant sees its own titles,
    each carries the artwork of the title it names, and a title with no
    snapshot row still returns with both fields explicitly null.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    default_history = client.get(f"/users/{CANARY_USER_ID}/history", headers=default_headers)
    demo_history = client.get(f"/users/{CANARY_USER_ID}/history", headers=demo_headers)
    default_library = client.get("/users/987654323/library?tab=rated", headers=default_headers)
    demo_library = client.get("/users/987654324/library?tab=history", headers=demo_headers)

    for response in (default_history, demo_history, default_library, demo_library):
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert "poster_url" in item
            assert "release_year" in item

    default_history_rows = {item["movie_id"]: item for item in default_history.json()["items"]}
    demo_history_rows = {item["movie_id"]: item for item in demo_history.json()["items"]}
    default_library_rows = {item["movie_id"]: item for item in default_library.json()["items"]}
    demo_library_rows = {item["movie_id"]: item for item in demo_library.json()["items"]}

    # Enriched on one side of each pair, absent on the other.
    assert default_history_rows[900000001]["poster_url"] == DEFAULT_CANARY_POSTER_URL
    assert default_history_rows[900000001]["release_year"] == 1994
    assert demo_history_rows[900000002]["poster_url"] is None
    assert demo_history_rows[900000002]["release_year"] is None
    assert default_library_rows[900000003]["poster_url"] is None
    assert default_library_rows[900000003]["release_year"] is None
    assert demo_library_rows[900000004]["poster_url"] == DEMO_CANARY_POSTER_URL
    assert demo_library_rows[900000004]["release_year"] == 2004
    # The other tenant's artwork is not reachable through either read model.
    assert DEMO_CANARY_POSTER_URL not in default_history.text
    assert DEMO_CANARY_POSTER_URL not in default_library.text
    assert DEFAULT_CANARY_POSTER_URL not in demo_history.text
    assert DEFAULT_CANARY_POSTER_URL not in demo_library.text


def test_recommendation_state_overlay_is_confined_to_the_active_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """A ranked item now carries the caller's own state for that title.

    That is tenant-owned data on a payload that was previously derived from the
    shared catalog alone, so it needs its own canary. The write also proves the
    reason the field exists: after a watchlist entry is added and taken off
    again the row survives at a higher revision with no product flag set, and a
    client that could not see it would address its next write to revision 0.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    first = client.get("/users/987654324/recommendations?limit=5", headers=demo_headers)
    cross_tenant = client.get("/users/987654324/recommendations?limit=5", headers=default_headers)

    assert first.status_code == 200
    # Not a 404: a user id this tenant has never seen is a cold-start user, and
    # answering it with the tenant's own popular titles is the documented path
    # (ADR 0011), not a miss. The boundary is therefore proven by what the
    # payload carries, never by the status code — the same read answered for
    # `default` is scoped to `default` and reaches none of the demo state below.
    assert cross_tenant.status_code == 200
    assert cross_tenant.json()["tenant_id"] == "default"
    assert all(item["state"] is None for item in cross_tenant.json()["items"])
    ranked = first.json()["items"]
    assert ranked, "the demo canary persona must have something to rank"
    for item in ranked:
        assert "state" in item
        if item["state"] is not None:
            assert item["state"]["tenant_id"] == "demo"
    movie_id = ranked[0]["movie_id"]

    added = client.put(
        f"/users/987654324/movies/{movie_id}/watchlist",
        headers=demo_headers,
    )
    assert added.status_code == 200
    watchlisted_revision = added.json()["state"]["revision"]

    try:
        with_state = client.get("/users/987654324/recommendations?limit=5", headers=demo_headers)
        assert with_state.status_code == 200
        overlay = _item_state(with_state.json()["items"], movie_id)
        assert overlay is not None
        assert overlay["tenant_id"] == "demo"
        assert overlay["watchlisted_at"] is not None
        assert overlay["revision"] == watchlisted_revision

        # The probe that matters: with a demo watchlist row standing, the same
        # user id read from the other tenant still carries no state at all.
        leaked = client.get("/users/987654324/recommendations?limit=5", headers=default_headers)
        assert leaked.status_code == 200
        assert leaked.json()["tenant_id"] == "default"
        assert all(item["state"] is None for item in leaked.json()["items"])
        assert '"tenant_id":"demo"' not in leaked.text
    finally:
        removed = client.delete(
            f"/users/987654324/movies/{movie_id}/watchlist",
            headers=demo_headers,
        )
        assert removed.status_code == 200

    settled_revision = removed.json()["state"]["revision"]
    after_undo = client.get("/users/987654324/recommendations?limit=5", headers=demo_headers)
    overlay = _item_state(after_undo.json()["items"], movie_id)

    assert settled_revision > watchlisted_revision
    assert overlay is not None, "a row left behind by an undone write must still be reported"
    assert overlay["watchlisted_at"] is None
    assert overlay["watched_at"] is None
    assert overlay["dismissed_at"] is None
    assert overlay["revision"] == settled_revision


def test_movie_detail_publishes_shared_facts_and_only_the_callers_own_state(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """The detail payload is shared metadata; the overlay on it is not.

    ``details`` comes from ``movie_catalog_metadata``, which is global by
    design (0011) — both tenants are *supposed* to see the same trailer for the
    same title. What must never cross is the state overlaid on it and the
    persona the read is addressed to, so this asserts three separate things:
    the payload is served to its own tenant, the state beside it belongs to the
    caller, and a persona in the other tenant is a 404 that carries neither.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.get(f"/users/987654323/movies/{DEFAULT_DETAIL_MOVIE_ID}")
    own = client.get(f"/users/987654323/movies/{DEFAULT_DETAIL_MOVIE_ID}", headers=default_headers)
    # The same shared title, read by the other tenant's persona: the facts are
    # the same, the state overlay is empty because that row is not theirs.
    shared = client.get(f"/users/987654324/movies/{DEFAULT_DETAIL_MOVIE_ID}", headers=demo_headers)
    cross_tenant = client.get(
        f"/users/987654323/movies/{DEFAULT_DETAIL_MOVIE_ID}", headers=demo_headers
    )
    without_details = client.get(
        f"/users/987654323/movies/{NO_DETAIL_MOVIE_ID}", headers=default_headers
    )

    assert unauthenticated.status_code == 401
    assert own.status_code == 200
    assert shared.status_code == 200
    # 987654323 is a default-tenant persona; a demo caller must not find it.
    assert cross_tenant.status_code == 404
    assert DEFAULT_DETAIL_TRAILER_KEY not in cross_tenant.text

    item = own.json()["item"]
    assert own.json()["tenant_id"] == "default"
    assert item["details"]["trailer"]["key"] == DEFAULT_DETAIL_TRAILER_KEY
    assert item["details"]["directors"] == ["Default Director"]
    assert len(item["details"]["cast"]) == 1
    assert item["state"] is not None
    assert item["state"]["tenant_id"] == "default"
    assert item["state"]["rating"] == 4.0

    shared_item = shared.json()["item"]
    assert shared.json()["tenant_id"] == "demo"
    assert shared_item["details"] == item["details"]
    assert shared_item["state"] is None, "the other tenant's rating is not visible here"
    assert DEMO_DETAIL_TRAILER_KEY not in own.text

    # A title the offline enrichment has not reached returns the field as an
    # explicit null rather than omitting it, so a client can tell "no payload"
    # from "this response did not look".
    assert without_details.status_code == 200
    assert "details" in without_details.json()["item"]
    assert without_details.json()["item"]["details"] is None


def test_movie_detail_state_overlay_survives_a_write_without_crossing_tenants(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """A write through one tenant's detail page is invisible from the other.

    The demo canary already has this title watched at 3.5 stars (the fixture
    seeds it that way so the overlay test has a rating to read), and a watched
    title cannot be watchlisted — the API refuses that transition on purpose.
    So the write that has to stay inside its tenant is a rating change.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    rated = client.put(
        f"/users/987654324/movies/{DEMO_DETAIL_MOVIE_ID}/rating",
        headers=demo_headers,
        json={"rating": 4.5},
    )
    assert rated.status_code == 200, rated.text
    try:
        demo_detail = client.get(
            f"/users/987654324/movies/{DEMO_DETAIL_MOVIE_ID}", headers=demo_headers
        )
        default_detail = client.get(
            f"/users/987654323/movies/{DEMO_DETAIL_MOVIE_ID}", headers=default_headers
        )

        assert demo_detail.status_code == 200
        assert default_detail.status_code == 200
        assert demo_detail.json()["item"]["state"]["rating"] == 4.5
        assert demo_detail.json()["item"]["state"]["watched_at"] is not None
        assert demo_detail.json()["item"]["state"]["tenant_id"] == "demo"
        # Same shared title, same details payload, no demo state on it.
        assert default_detail.json()["item"]["details"]["trailer"]["key"] == (
            DEMO_DETAIL_TRAILER_KEY
        )
        assert default_detail.json()["item"]["state"] is None
        assert '"tenant_id":"demo"' not in default_detail.text
    finally:
        restored = client.put(
            f"/users/987654324/movies/{DEMO_DETAIL_MOVIE_ID}/rating",
            headers=demo_headers,
            json={"rating": 3.5},
        )
        assert restored.status_code == 200, restored.text


def _item_state(items: list[dict[str, object]], movie_id: int) -> dict[str, object] | None:
    for item in items:
        if item["movie_id"] == movie_id:
            state = item["state"]
            assert state is None or isinstance(state, dict)
            return state
    raise AssertionError(f"movie {movie_id} left the ranked set between reads")


def test_preferences_are_owned_by_one_persona_in_one_tenant(
    client: TestClient, mint_token: TokenMinter
) -> None:
    """A presentation preference is tenant-owned state like any other.

    It is not feedback and it reaches no model, but it is still a row that says
    what one persona in one tenant is shown — so the same three properties have
    to hold: no token, no answer; the wrong tenant's token, no answer; and a
    write from one tenant leaves the other tenant's row exactly as it was.

    The pair of user ids matters here. `987654323` is a default-tenant persona
    and `987654324` a demo-tenant one, so each write below is addressed to a
    persona the other caller cannot reach at all.
    """
    default_headers = {"Authorization": f"Bearer {mint_token('default', 'alice', 'alice')}"}
    demo_headers = {"Authorization": f"Bearer {mint_token('demo', 'demo', 'demo')}"}

    unauthenticated = client.get("/users/987654324/preferences")
    cross_tenant_read = client.get("/users/987654324/preferences", headers=default_headers)
    cross_tenant_write = client.put(
        "/users/987654324/preferences",
        json={"feature_watchlisted_titles": False},
        headers=default_headers,
    )

    assert unauthenticated.status_code == 401
    assert cross_tenant_read.status_code == 404
    assert cross_tenant_write.status_code == 404

    # Each tenant's own persona starts from the documented default.
    demo_before = client.get("/users/987654324/preferences", headers=demo_headers)
    default_before = client.get("/users/987654323/preferences", headers=default_headers)

    assert demo_before.status_code == 200
    assert default_before.status_code == 200
    assert demo_before.json()["tenant_id"] == "demo"
    assert default_before.json()["tenant_id"] == "default"
    for response in (demo_before, default_before):
        assert response.json()["feature_watchlisted_titles"] is True
        assert response.json()["revision"] == 0

    written = client.put(
        "/users/987654324/preferences?expected_revision=0",
        json={"feature_watchlisted_titles": False},
        headers=demo_headers,
    )
    try:
        assert written.status_code == 200
        assert written.json()["outcome"] == "changed"
        assert written.json()["preferences"]["tenant_id"] == "demo"
        assert written.json()["preferences"]["feature_watchlisted_titles"] is False
        assert written.json()["preferences"]["revision"] == 1

        demo_after = client.get("/users/987654324/preferences", headers=demo_headers)
        default_after = client.get("/users/987654323/preferences", headers=default_headers)

        assert demo_after.json()["feature_watchlisted_titles"] is False
        # The probe that matters: with a demo row standing, the other tenant's
        # persona still reads its own untouched default rather than this one.
        assert default_after.json()["feature_watchlisted_titles"] is True
        assert default_after.json()["revision"] == 0
        assert '"tenant_id":"demo"' not in default_after.text

        # A stale assertion is refused rather than silently overwriting.
        stale = client.put(
            "/users/987654324/preferences?expected_revision=0",
            json={"feature_watchlisted_titles": True},
            headers=demo_headers,
        )
        assert stale.status_code == 409
        assert (
            client.get("/users/987654324/preferences", headers=demo_headers).json()[
                "feature_watchlisted_titles"
            ]
            is False
        )
    finally:
        restored = client.put(
            "/users/987654324/preferences",
            json={"feature_watchlisted_titles": True},
            headers=demo_headers,
        )
        assert restored.status_code == 200

    settled = client.get("/users/987654324/preferences", headers=demo_headers)
    assert settled.json()["feature_watchlisted_titles"] is True
    # A repeat of the settled value is reported as a repeat, not applied again.
    repeat = client.put(
        "/users/987654324/preferences",
        json={"feature_watchlisted_titles": True},
        headers=demo_headers,
    )
    assert repeat.json()["outcome"] == "no_change"
    assert repeat.json()["preferences"]["revision"] == settled.json()["revision"]
