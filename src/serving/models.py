"""Strict slim-client contract for the private learned-model sidecar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx


class ModelServerContractError(ValueError):
    """The sidecar response is unsafe or incompatible with this API."""


@dataclass(frozen=True)
class RankedModelItem:
    movie_id: int
    score: float


@dataclass(frozen=True)
class ModelRankingResult:
    tenant_id: str
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    latency_ms: float
    items: list[RankedModelItem]


class ModelServerClient:
    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        timeout_seconds: float = 0.5,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {auth_token}"},
        )

    async def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        history_movie_ids: list[int],
        limit: int,
        candidate_limit: int = 100,
    ) -> ModelRankingResult:
        response = await self._client.post(
            "/rank",
            json={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "history_movie_ids": history_movie_ids,
                "limit": limit,
                "candidate_limit": candidate_limit,
            },
        )
        response.raise_for_status()
        try:
            return _parse_result(
                response.json(),
                expected_tenant=tenant_id,
                seen_movie_ids=set(history_movie_ids),
                limit=limit,
            )
        except ModelServerContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelServerContractError("model-server response has an invalid shape") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_result(
    value: Any,
    *,
    expected_tenant: str,
    seen_movie_ids: set[int],
    limit: int,
) -> ModelRankingResult:
    if not isinstance(value, dict):
        raise ModelServerContractError("model-server response must be an object")
    tenant_id = _nonempty_string(value, "tenant_id")
    if tenant_id != expected_tenant:
        raise ModelServerContractError(
            f"model-server returned tenant {tenant_id!r} for {expected_tenant!r} request"
        )
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > limit:
        raise ModelServerContractError("model-server items must be a limit-bounded list")
    items: list[RankedModelItem] = []
    returned_ids: set[int] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ModelServerContractError("model-server item must be an object")
        try:
            movie_id = int(raw["movie_id"])
            score = float(raw["score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelServerContractError("model-server item fields are invalid") from exc
        if movie_id <= 0:
            raise ModelServerContractError(f"model-server returned invalid movie {movie_id}")
        if movie_id in seen_movie_ids:
            raise ModelServerContractError(f"model-server returned seen movie {movie_id}")
        if movie_id in returned_ids:
            raise ModelServerContractError(f"model-server returned duplicate movie {movie_id}")
        if not math.isfinite(score):
            raise ModelServerContractError(f"model-server returned non-finite score for {movie_id}")
        returned_ids.add(movie_id)
        items.append(RankedModelItem(movie_id=movie_id, score=score))
    latency_ms = float(value["latency_ms"])
    if not math.isfinite(latency_ms) or latency_ms < 0:
        raise ModelServerContractError("model-server returned invalid latency")
    return ModelRankingResult(
        tenant_id=tenant_id,
        candidate_policy=_nonempty_string(value, "candidate_policy"),
        candidate_version=_nonempty_string(value, "candidate_version"),
        ranker_version=_nonempty_string(value, "ranker_version"),
        feature_version=_nonempty_string(value, "feature_version"),
        latency_ms=latency_ms,
        items=items,
    )


def _nonempty_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ModelServerContractError(f"model-server response has no valid {key!r}")
    return result
