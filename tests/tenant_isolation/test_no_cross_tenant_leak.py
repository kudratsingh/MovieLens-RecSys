"""
Non-negotiable #9: no code path may return one tenant's data in
response to another tenant's request. Cross-tenant leakage is the
highest-severity bug class. This test authenticates as tenants A and
B against the live docker-compose stack and hits every authenticated
endpoint, asserting the returned payload matches the caller's tenant.

Bundle #1b's authenticated surface is just ``/whoami``. As Phase 3
adds recommendations / features / model-metadata endpoints, each
new endpoint gains coverage here — the test's job is to be the
tenant-isolation gate every serving PR passes through in CI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.serving.app import app
from tests.tenant_isolation.conftest import TokenMinter


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
    demo tenant. As more endpoints land, they all get added to the
    loop here.
    """
    token = mint_token("default", "alice", "alice")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body_text = resp.text.lower()
    # Strong assertion — no reference to the demo tenant should
    # appear anywhere in the response payload.
    assert "demo" not in body_text, (
        f"demo tenant data leaked into a default-tenant response: {resp.text}"
    )


def test_demo_token_never_returns_default_data(
    client: TestClient, mint_token: TokenMinter
) -> None:
    token = mint_token("demo", "demo", "demo")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body_text = resp.text.lower()
    assert "default" not in body_text, (
        f"default tenant data leaked into a demo-tenant response: {resp.text}"
    )
