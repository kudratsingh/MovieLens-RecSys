from __future__ import annotations

import httpx
import pytest

from src.serving.models import ModelRankingResult, RankedModelItem
from src.serving.orchestration import RecommendationCoordinator
from src.serving.recommendations import RecommendationService
from tests.unit.test_serving_recommendations import _connection


class _LearnedModels:
    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        return ModelRankingResult(
            tenant_id=str(kwargs["tenant_id"]),
            candidate_policy="item-item-cosine",
            candidate_version="candidate-v1",
            ranker_version="ranker-v1",
            feature_version="features-v1",
            latency_ms=3.2,
            items=[RankedModelItem(movie_id=3, score=0.75)],
        )


class _UnavailableModels:
    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("offline")


@pytest.mark.asyncio
async def test_warm_user_routes_through_learned_two_stage_policy() -> None:
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=5)
    finally:
        connection.close()

    assert decision.policy == "item-item-cosine+lightgbm"
    assert decision.model_version == "candidate-v1/ranker-v1"
    assert decision.fallback_reason is None
    assert [item.movie_id for item in decision.items] == [3]


@pytest.mark.asyncio
async def test_cold_user_routes_to_popularity_without_calling_models() -> None:
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=999, limit=2)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"
    assert [item.movie_id for item in decision.items] == [1, 2]


@pytest.mark.asyncio
async def test_model_failure_routes_warm_user_to_popularity() -> None:
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=2)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "model-server-unavailable"
    assert [item.movie_id for item in decision.items] == [3]
