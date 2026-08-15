from __future__ import annotations

import json

import httpx
import pytest

from src.serving.models import ModelServerClient, ModelServerContractError


@pytest.mark.asyncio
async def test_model_client_sends_tenant_history_and_service_token() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.read()))
        observed["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "latency_ms": 4.2,
                "items": [{"movie_id": 3, "score": 0.8}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://models",
        headers={"Authorization": "Bearer secret"},
    )
    try:
        result = await client.rank(tenant_id="demo", user_id=10, history_movie_ids=[1, 2], limit=5)
    finally:
        await client.aclose()

    assert observed["tenant_id"] == "demo"
    assert observed["history_movie_ids"] == [1, 2]
    assert observed["authorization"] == "Bearer secret"
    assert result.items[0].movie_id == 3


@pytest.mark.asyncio
async def test_model_client_rejects_seen_item_from_sidecar() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "latency_ms": 1.0,
                "items": [{"movie_id": 2, "score": 0.8}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://models"
    )
    try:
        with pytest.raises(ModelServerContractError, match="seen movie"):
            await client.rank(tenant_id="demo", user_id=10, history_movie_ids=[1, 2], limit=5)
    finally:
        await client.aclose()
