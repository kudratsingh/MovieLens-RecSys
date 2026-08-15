"""Candidate → feature → ranker orchestration with explicit safe fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy import Connection

from src.serving.models import (
    ModelRankingResult,
    ModelServerClient,
    ModelServerContractError,
)
from src.serving.recommendations import RecommendationService, RecommendedMovie


class ModelRanker(Protocol):
    async def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        history_movie_ids: list[int],
        limit: int,
        candidate_limit: int = 100,
    ) -> ModelRankingResult: ...


@dataclass(frozen=True)
class RecommendationDecision:
    policy: str
    model_version: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    model_latency_ms: float
    fallback_reason: str | None
    items: list[RecommendedMovie]


class RecommendationCoordinator:
    def __init__(
        self,
        recommendations: RecommendationService,
        models: ModelRanker | ModelServerClient,
    ) -> None:
        self._recommendations = recommendations
        self._models = models

    async def recommend(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        user_id: int,
        limit: int,
    ) -> RecommendationDecision:
        history = self._recommendations.recent_history(
            connection,
            user_id=user_id,
            limit=500,
        )
        if not history:
            return self._popularity(
                connection,
                user_id=user_id,
                limit=limit,
                reason="cold-start",
            )
        try:
            learned = await self._models.rank(
                tenant_id=tenant_id,
                user_id=user_id,
                history_movie_ids=[movie.movie_id for movie in history],
                limit=limit,
                candidate_limit=max(100, limit * 10),
            )
        except (httpx.HTTPError, ModelServerContractError):
            return self._popularity(
                connection,
                user_id=user_id,
                limit=limit,
                reason="model-server-unavailable",
            )
        items = self._recommendations.hydrate_ranked_movies(
            connection,
            user_id=user_id,
            ranked_items=[(item.movie_id, item.score) for item in learned.items],
            reason="LightGBM rank over learned item-item candidates",
        )
        if not items:
            return self._popularity(
                connection,
                user_id=user_id,
                limit=limit,
                reason="empty-learned-result",
            )
        return RecommendationDecision(
            policy=f"{learned.candidate_policy}+lightgbm",
            model_version=f"{learned.candidate_version}/{learned.ranker_version}",
            candidate_version=learned.candidate_version,
            ranker_version=learned.ranker_version,
            feature_version=learned.feature_version,
            model_latency_ms=learned.latency_ms,
            fallback_reason=None,
            items=items,
        )

    def _popularity(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
        reason: str,
    ) -> RecommendationDecision:
        return RecommendationDecision(
            policy="popularity",
            model_version="popularity-v1",
            candidate_version="popularity-v1",
            ranker_version="not-run",
            feature_version="not-read",
            model_latency_ms=0.0,
            fallback_reason=reason,
            items=self._recommendations.popular_for_user(
                connection,
                user_id=user_id,
                limit=limit,
            ),
        )
