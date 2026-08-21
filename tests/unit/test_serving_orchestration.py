from __future__ import annotations

import httpx
import pytest
from sqlalchemy import Connection, text

from src.features import FEATURE_COLUMNS
from src.serving.models import ModelRankingResult, RankedModelItem
from src.serving.orchestration import RecommendationCoordinator
from src.serving.recommendations import RecommendationService
from tests.unit.test_serving_recommendations import _connection


class _LearnedModels:
    def __init__(self, movie_id: int = 3) -> None:
        self._movie_id = movie_id

    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        return ModelRankingResult(
            tenant_id=str(kwargs["tenant_id"]),
            candidate_policy="item-item-cosine",
            candidate_version="candidate-v1",
            ranker_version="ranker-v1",
            feature_version="features-v1",
            candidate_latency_ms=0.2,
            feature_latency_ms=2.5,
            ranker_latency_ms=0.4,
            latency_ms=3.2,
            items=[
                RankedModelItem(
                    movie_id=self._movie_id,
                    score=0.75,
                    features={column: 1.0 for column in FEATURE_COLUMNS},
                )
            ],
        )


class _UnavailableModels:
    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("offline")


def _add_catalog_and_history(
    connection: Connection,
    *,
    user_id: int,
    history_count: int,
) -> None:
    # The shared SQLite fixture starts with movies 1-3. Add enough rated
    # catalog items to test both sides of the five-interaction boundary while
    # leaving movie 11 unseen for learned-result hydration.
    for movie_id in range(4, 12):
        connection.execute(
            text("INSERT INTO movies VALUES (:movie_id, :title, 'Drama')"),
            {"movie_id": movie_id, "title": f"Movie {movie_id}"},
        )
        connection.execute(
            text('INSERT INTO links ("movieId", "tmdbId") VALUES (:movie_id, NULL)'),
            {"movie_id": movie_id},
        )
    for movie_id in range(1, 12):
        connection.execute(
            text(
                "INSERT INTO ratings VALUES "
                "('demo', :background_user, :movie_id, 4.0, :timestamp)"
            ),
            {
                "background_user": 8000 + movie_id,
                "movie_id": movie_id,
                "timestamp": 1000 + movie_id,
            },
        )
    for movie_id in range(1, history_count + 1):
        connection.execute(
            text("INSERT INTO ratings VALUES " "('demo', :user_id, :movie_id, 4.0, :timestamp)"),
            {
                "user_id": user_id,
                "movie_id": movie_id,
                "timestamp": 2000 + movie_id,
            },
        )
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, watched_at, rating,
                    rating_updated_at, state_version, updated_at
                ) VALUES ('demo', :user_id, :movie_id, :timestamp, 4.0,
                          :timestamp, 1, :timestamp)
                """),
            {
                "user_id": user_id,
                "movie_id": movie_id,
                "timestamp": 2000 + movie_id,
            },
        )


def _make_existing_user_warm(connection: Connection) -> None:
    # User 10 starts with two interactions; add three more without marking
    # learned result movie 3 as seen.
    connection.execute(
        text(
            "INSERT INTO movies VALUES "
            "(4, 'Warm Four', 'Drama'), (5, 'Warm Five', 'Drama'), "
            "(6, 'Warm Six', 'Drama')"
        )
    )
    connection.execute(text("INSERT INTO links VALUES (4, NULL), (5, NULL), (6, NULL)"))
    connection.execute(
        text(
            "INSERT INTO ratings VALUES "
            "('demo', 10, 4, 4.0, 300), ('demo', 10, 5, 4.0, 301), "
            "('demo', 10, 6, 4.0, 302)"
        )
    )
    connection.execute(text("""
            INSERT INTO user_movie_state (
                tenant_id, user_id, movie_id, watched_at, rating,
                rating_updated_at, state_version, updated_at
            ) VALUES
                ('demo', 10, 4, 300, 4.0, 300, 1, 300),
                ('demo', 10, 5, 301, 4.0, 301, 1, 301),
                ('demo', 10, 6, 302, 4.0, 302, 1, 302)
            """))


@pytest.mark.asyncio
async def test_warm_user_routes_through_learned_two_stage_policy() -> None:
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=5)
    finally:
        connection.close()

    assert decision.policy == "item-item-cosine+lightgbm"
    assert decision.model_version == "candidate-v1/ranker-v1"
    assert decision.fallback_reason is None
    assert decision.feature_latency_ms == 2.5
    assert [item.movie_id for item in decision.items] == [3]
    assert decision.predictions[0].features == {column: 1.0 for column in FEATURE_COLUMNS}


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
    assert decision.feature_latency_ms == 0.0
    assert [item.movie_id for item in decision.items] == [1, 2]
    assert [prediction.features for prediction in decision.predictions] == [{}, {}]


@pytest.mark.parametrize("history_count", [0, 1, 3, 4])
@pytest.mark.asyncio
async def test_short_history_routes_to_cold_start_fallback(history_count: int) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"


@pytest.mark.parametrize("history_count", [5, 10])
@pytest.mark.asyncio
async def test_history_at_or_above_threshold_routes_to_learned_policy(
    history_count: int,
) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=11)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert decision.policy == "item-item-cosine+lightgbm"
    assert decision.fallback_reason is None


@pytest.mark.asyncio
async def test_duplicate_rating_rows_do_not_satisfy_warm_threshold() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=1)
        for timestamp in range(3000, 3004):
            connection.execute(
                text("INSERT INTO ratings VALUES " "('demo', 77, 1, 4.0, :timestamp)"),
                {"timestamp": timestamp},
            )
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"


@pytest.mark.asyncio
async def test_model_failure_routes_warm_user_to_popularity() -> None:
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=2)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "model-server-unavailable"
    assert [item.movie_id for item in decision.items] == [3]
