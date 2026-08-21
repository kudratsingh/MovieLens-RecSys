from __future__ import annotations

import json

import httpx
import pytest

from src.features import FEATURE_COLUMNS
from src.serving.models import ModelServerClient, ModelServerContractError


def _features(value: float = 0.0) -> dict[str, float]:
    return {column: value for column in FEATURE_COLUMNS}


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
                "candidate_latency_ms": 0.2,
                "feature_latency_ms": 3.0,
                "ranker_latency_ms": 0.5,
                "latency_ms": 4.2,
                "items": [{"movie_id": 3, "score": 0.8, "features": _features()}],
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
        result = await client.rank(
            tenant_id="demo",
            user_id=10,
            positive_history_movie_ids=[1, 2],
            excluded_movie_ids=[7],
            limit=5,
        )
    finally:
        await client.aclose()

    assert observed["tenant_id"] == "demo"
    assert observed["positive_history_movie_ids"] == [1, 2]
    assert observed["excluded_movie_ids"] == [7]
    assert observed["authorization"] == "Bearer secret"
    assert result.items[0].movie_id == 3
    assert result.items[0].features == _features()


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
                "candidate_latency_ms": 0.1,
                "feature_latency_ms": 0.5,
                "ranker_latency_ms": 0.2,
                "latency_ms": 1.0,
                "items": [{"movie_id": 2, "score": 0.8, "features": _features()}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://models"
    )
    try:
        with pytest.raises(ModelServerContractError, match="seen movie"):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1, 2],
                limit=5,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_incomplete_feature_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.1,
                "feature_latency_ms": 0.5,
                "ranker_latency_ms": 0.2,
                "latency_ms": 1.0,
                "items": [{"movie_id": 3, "score": 0.8, "features": {}}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://models"
    )
    try:
        with pytest.raises(ModelServerContractError, match="incompatible features"):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1],
                limit=5,
            )
    finally:
        await client.aclose()
