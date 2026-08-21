"""
Non-negotiable #9: no code path may return one tenant's data in
response to another tenant's request. Cross-tenant leakage is the
highest-severity bug class. This test authenticates as tenants A and
B against the live docker-compose stack and hits every authenticated
endpoint, asserting the returned payload matches the caller's tenant.

Covered today: ``/whoami``, ``/users/{id}/recommendations``,
``/users/{id}/history``, ``/users/{id}/audits``, ``/personas``, and the
rating write path. Not yet covered: ``/users/{id}/features``,
``/users/{id}/catalog``, and ``DELETE /users/{id}/ratings``. Every new
endpoint gains coverage here — the test's job is to be the
tenant-isolation gate every serving PR passes through in CI.
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
