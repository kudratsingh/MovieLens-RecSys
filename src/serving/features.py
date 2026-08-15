"""Slim HTTP client for the internal Feast feature-server sidecar."""

from __future__ import annotations

from typing import Any

import httpx

_USER_FEATURES = [
    "user_features:user_interaction_count",
    "user_features:user_days_active",
    "user_features:user_days_since_last_interaction",
]


class FeatureServerClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 1.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def get_user_features(self, *, tenant_id: str, user_id: int) -> dict[str, Any]:
        response = await self._client.post(
            "/get-online-features",
            json={
                "features": _USER_FEATURES,
                "entities": {"tenant_id": [tenant_id], "user_id": [user_id]},
            },
        )
        response.raise_for_status()
        payload = response.json()
        names = payload["metadata"]["feature_names"]
        requested_names = {feature.rsplit(":", 1)[-1] for feature in _USER_FEATURES}
        values = {
            name: result["values"][0]
            for name, result in zip(names, payload["results"])
            if name in requested_names
        }
        feature_result = next(
            result for name, result in zip(names, payload["results"]) if name in requested_names
        )
        values["feature_timestamp"] = feature_result["event_timestamps"][0]
        return values

    async def aclose(self) -> None:
        await self._client.aclose()
