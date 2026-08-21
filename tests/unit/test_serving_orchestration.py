from __future__ import annotations

import httpx
import pytest
from sqlalchemy import Connection, bindparam, text

from src.features import FEATURE_COLUMNS
from src.serving.models import ModelRankingResult, RankedModelItem
from src.serving.orchestration import RecommendationCoordinator
from src.serving.policy import EXCLUSION_FILTER_POLICY, id_set_digest
from src.serving.recommendations import RecommendationService, RecommendedMovie
from tests.unit.test_serving_recommendations import _connection


class _LearnedModels:
    """Sidecar stub that records exactly what the coordinator handed it."""

    def __init__(self, movie_id: int | list[int] = 3) -> None:
        self._movie_ids = [movie_id] if isinstance(movie_id, int) else list(movie_id)
        self.calls: list[dict[str, object]] = []

    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
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
                    movie_id=movie_id,
                    score=0.75,
                    features={column: 1.0 for column in FEATURE_COLUMNS},
                    candidate_source="item-item-cosine",
                    seed_movie_id=1,
                )
                for movie_id in self._movie_ids
            ],
            candidate_sources={"item-item-cosine": len(self._movie_ids)},
            seed_count=len(list(kwargs["positive_history_movie_ids"])),
            excluded_count=len(list(kwargs["excluded_movie_ids"] or ())),
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=1_760_000_000.0,
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


def _dismiss(connection: Connection, *, user_id: int, movie_id: int, timestamp: int) -> None:
    connection.execute(
        text("""
            INSERT INTO user_movie_state (
                tenant_id, user_id, movie_id, dismissed_at, state_version, updated_at
            ) VALUES ('demo', :user_id, :movie_id, :timestamp, 1, :timestamp)
            ON CONFLICT (tenant_id, user_id, movie_id) DO UPDATE SET
                dismissed_at = excluded.dismissed_at,
                state_version = user_movie_state.state_version + 1,
                updated_at = excluded.updated_at
            """),
        {"user_id": user_id, "movie_id": movie_id, "timestamp": timestamp},
    )


@pytest.mark.asyncio
async def test_positive_history_and_exclusions_reach_the_sidecar_separately() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=11)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        _dismiss(connection, user_id=77, movie_id=9, timestamp=4000)
        await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2
        )
    finally:
        connection.close()

    call = models.calls[0]
    positives = list(call["positive_history_movie_ids"])  # type: ignore[arg-type]
    excluded = list(call["excluded_movie_ids"])  # type: ignore[arg-type]
    assert positives == [6, 5, 4, 3, 2, 1]
    assert 9 not in positives
    assert 9 in excluded
    # Already-seen filtering stays part of the exclusion set, so the two inputs
    # overlap on watched titles without collapsing into one list.
    assert set(positives).issubset(set(excluded))


@pytest.mark.asyncio
async def test_dismissed_movie_is_never_a_positive_or_a_seed() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=11)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        # Movie 2 was watched first and then dismissed: it must stop being a
        # positive signal and must not seed retrieval.
        _dismiss(connection, user_id=77, movie_id=2, timestamp=4100)
        decision = await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2
        )
    finally:
        connection.close()

    call = models.calls[0]
    assert 2 not in list(call["positive_history_movie_ids"])  # type: ignore[arg-type]
    assert 2 in list(call["excluded_movie_ids"])  # type: ignore[arg-type]
    assert decision.positive_signal_count == 5


@pytest.mark.asyncio
async def test_dismissal_does_not_write_a_training_negative() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        before = connection.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one()
        _dismiss(connection, user_id=77, movie_id=9, timestamp=4200)
        await RecommendationCoordinator(RecommendationService(), _LearnedModels(11)).recommend(
            connection, tenant_id="demo", user_id=77, limit=2
        )
        after = connection.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one()
        negative_rows = connection.execute(
            text('SELECT COUNT(*) FROM ratings WHERE "userId" = 77 AND "movieId" = 9')
        ).scalar_one()
    finally:
        connection.close()

    assert after == before
    assert negative_rows == 0


@pytest.mark.asyncio
async def test_dismissed_movie_is_excluded_from_the_popularity_fallback() -> None:
    connection = _connection()
    try:
        # User 999 has no history, so this is the cold-start fallback path.
        _dismiss(connection, user_id=999, movie_id=1, timestamp=4300)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=999, limit=5)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert 1 not in [item.movie_id for item in decision.items]
    assert decision.excluded_count == 1


@pytest.mark.asyncio
async def test_dismissed_movie_is_dropped_during_metadata_hydration() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        _dismiss(connection, user_id=77, movie_id=11, timestamp=4400)
        # The sidecar stub still offers movie 11; hydration must not return it.
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels([11, 10])
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert 11 not in [item.movie_id for item in decision.items]
    assert 11 not in [prediction.movie_id for prediction in decision.predictions]


@pytest.mark.asyncio
async def test_final_validation_drops_an_excluded_id_that_survived_hydration() -> None:
    connection = _connection()
    models = _LearnedModels([11, 10])
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        _dismiss(connection, user_id=77, movie_id=11, timestamp=4500)
        decision = await RecommendationCoordinator(_LeakyHydration(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2
        )
    finally:
        connection.close()

    # Hydration handed back the dismissed title; the last check has to catch it
    # rather than let an explicit "not for me" reappear.
    assert [item.movie_id for item in decision.items] == [10]
    assert 11 not in [prediction.movie_id for prediction in decision.predictions]
    assert decision.fallback_reason is None
    assert "excluded-id-blocked: [11]" in decision.reason


@pytest.mark.asyncio
async def test_learned_output_holding_only_excluded_ids_fails_closed() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=11)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        _dismiss(connection, user_id=77, movie_id=11, timestamp=4600)
        decision = await RecommendationCoordinator(_LeakyHydration(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2
        )
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "excluded-id-blocked"
    assert decision.reason.startswith("excluded-id-blocked")
    assert 11 not in [item.movie_id for item in decision.items]


class _LeakyHydration(RecommendationService):
    """Hydration that ignores the caller's exclusion set.

    Stands in for a stage that has drifted from the request's view of state, so
    the coordinator's final validation has something real to catch.
    """

    def hydrate_ranked_movies(  # type: ignore[override]
        self,
        connection: Connection,
        **kwargs: object,
    ) -> list[RecommendedMovie]:
        ranked_items = list(kwargs["ranked_items"])  # type: ignore[call-overload]
        query = text(
            'SELECT "movieId", title FROM movies WHERE "movieId" IN :movie_ids'
        ).bindparams(bindparam("movie_ids", expanding=True))
        titles = {
            int(row[0]): str(row[1])
            for row in connection.execute(
                query, {"movie_ids": [movie_id for movie_id, _ in ranked_items]}
            )
        }
        return [
            RecommendedMovie(
                movie_id=movie_id,
                title=titles[movie_id],
                genres=[],
                tmdb_id=None,
                interaction_count=0,
                score=score,
                reason=str(kwargs["reason"]),
            )
            for movie_id, score in ranked_items
            if movie_id in titles
        ]


@pytest.mark.parametrize("history_count", [0, 1, 3])
@pytest.mark.asyncio
async def test_policy_reports_fallback_below_the_threshold(history_count: int) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert decision.serving_policy.name == "popularity"
    assert decision.serving_policy.learned is False
    assert decision.serving_policy.positive_signal_count == history_count
    assert decision.serving_policy.threshold == 5
    assert decision.serving_policy.reason.startswith("cold-start")
    assert decision.serving_policy.score_scale == "tenant-interaction-count"
    assert decision.serving_policy.filter_policy == EXCLUSION_FILTER_POLICY


@pytest.mark.parametrize("history_count", [5, 10])
@pytest.mark.asyncio
async def test_policy_reports_learned_serving_at_or_above_the_threshold(
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

    assert decision.serving_policy.name == "item-item-cosine+lightgbm"
    assert decision.serving_policy.learned is True
    assert decision.serving_policy.positive_signal_count == history_count
    assert decision.serving_policy.threshold == 5
    assert decision.serving_policy.reason.startswith("learned-two-stage")
    # A LambdaRank score is an ordering, not a probability. The response has to
    # say so or a client will render it as a match percentage.
    assert decision.serving_policy.score_scale == "lightgbm-rank-score"
    assert decision.policy == decision.serving_policy.name


@pytest.mark.asyncio
async def test_audit_payload_carries_input_state_exclusions_and_freshness() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        _dismiss(connection, user_id=77, movie_id=9, timestamp=4700)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=11)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert decision.input_state_revision > 0
    assert decision.input_state_hash == id_set_digest([1, 2, 3, 4, 5, 6])
    assert decision.exclusion_hash == id_set_digest([1, 2, 3, 4, 5, 6, 9])
    assert decision.positive_signal_count == 6
    assert decision.excluded_count == 7
    assert decision.filter_policy == EXCLUSION_FILTER_POLICY
    assert decision.feature_event_time is not None
    assert decision.candidate_sources == {"item-item-cosine": 1}
    assert decision.predictions[0].candidate_source == "item-item-cosine"
    assert decision.predictions[0].seed_movie_id == 1


@pytest.mark.asyncio
async def test_input_digests_are_order_independent_and_revision_moves_with_state() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=6)
        first = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=11)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
        second = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=11)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
        _dismiss(connection, user_id=77, movie_id=9, timestamp=4800)
        third = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=11)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2)
    finally:
        connection.close()

    assert first.input_state_hash == second.input_state_hash
    assert first.exclusion_hash == second.exclusion_hash
    assert first.input_state_revision == second.input_state_revision
    assert third.exclusion_hash != first.exclusion_hash
    assert third.input_state_revision > first.input_state_revision
