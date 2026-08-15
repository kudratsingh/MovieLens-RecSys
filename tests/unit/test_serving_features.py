from __future__ import annotations

import json

import httpx
import pytest

from src.serving.features import FeatureServerClient


@pytest.mark.asyncio
async def test_feature_server_request_always_includes_tenant_key() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "metadata": {
                    "feature_names": [
                        "tenant_id",
                        "user_id",
                        "user_interaction_count",
                        "user_days_active",
                        "user_days_since_last_interaction",
                    ]
                },
                "results": [
                    {"values": ["demo"], "event_timestamps": ["1970-01-01T00:00:00Z"]},
                    {"values": [1001], "event_timestamps": ["1970-01-01T00:00:00Z"]},
                    {"values": [3], "event_timestamps": ["2026-08-15T00:00:00Z"]},
                    {"values": [2.0], "event_timestamps": ["2026-08-15T00:00:00Z"]},
                    {"values": [1.0], "event_timestamps": ["2026-08-15T00:00:00Z"]},
                ],
            },
        )

    client = FeatureServerClient(base_url="http://features")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://features"
    )
    try:
        values = await client.get_user_features(tenant_id="demo", user_id=1001)
    finally:
        await client.aclose()

    assert observed["entities"] == {"tenant_id": ["demo"], "user_id": [1001]}
    assert values == {
        "user_interaction_count": 3,
        "user_days_active": 2.0,
        "user_days_since_last_interaction": 1.0,
        "feature_timestamp": "2026-08-15T00:00:00Z",
    }
