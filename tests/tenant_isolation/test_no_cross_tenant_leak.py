"""
Non-negotiable #9: no code path may return one tenant's data in
response to another tenant's request. Cross-tenant leakage is the
highest-severity bug class. This test authenticates as tenants A and
B against the live docker-compose stack and hits every authenticated
endpoint, asserting the returned payload matches the caller's tenant.

Covered today: ``/whoami``, ``/users/{id}/recommendations`` (including the
serving-policy and exclusion evidence it now returns), ``/users/{id}/history``,
``/users/{id}/audits``, ``/personas``, ``/users/{id}/features``,
``/users/{id}/catalog``, ``/users/{id}/library``, the rating write path, and
``DELETE /users/{id}/ratings``. Not yet covered: ``/users/{id}/movies/{id}``
and ``/users/{id}/taste-profile``. Every new endpoint gains coverage here —
the test's job is to be the tenant-isolation gate every serving PR passes
through in CI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.serving.app import app
from tests.tenant_isolation.conftest import (
    CANARY_USER_ID,
    DEFAULT_HISTORY_TITLE,
    DEFAULT_PERSONA_NAME,
    DEFAULT_RECOMMENDATION_TITLE,
    DEMO_HISTORY_TITLE,
    DEMO_PERSONA_NAME,
    DEMO_RECOMMENDATION_TITLE,
    TokenMinter,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI TestClient that triggers the app's lifespan (startup
    checks + engine construction + middleware wire-up)."""
    with TestClient(app) as c:
        yield c


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
    """The same user lookup must be scoped independently for each tenant."""
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
        assert DEMO_RECOMMENDATION_TITLE in demo_response.text


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
