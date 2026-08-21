from __future__ import annotations

import pytest
from sqlalchemy import Connection, create_engine, text

from src.serving.recommendations import RecommendationService, UnknownDemoPersonaError


def _connection() -> Connection:
    # Recommendation orchestration deliberately runs synchronous DB work in
    # the request thread pool. Production uses psycopg2; allow this test-only
    # SQLite connection to follow the same serialized cross-thread lifecycle.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(
        text('CREATE TABLE movies ("movieId" INTEGER PRIMARY KEY, title TEXT, genres TEXT)')
    )
    connection.execute(
        text(
            "CREATE TABLE demo_personas (user_id INTEGER, slug TEXT, display_name TEXT, "
            "description TEXT, sort_order INTEGER, synthetic BOOLEAN)"
        )
    )
    connection.execute(text('CREATE TABLE links ("movieId" INTEGER PRIMARY KEY, "tmdbId" TEXT)'))
    connection.execute(
        text(
            'CREATE TABLE ratings (tenant_id TEXT, "userId" INTEGER, "movieId" INTEGER, '
            "rating FLOAT, timestamp INTEGER)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE user_movie_state ("
            "tenant_id TEXT NOT NULL, user_id INTEGER NOT NULL, movie_id INTEGER NOT NULL, "
            "watched_at DATETIME, rating FLOAT, rating_updated_at DATETIME, "
            "watchlisted_at DATETIME, dismissed_at DATETIME, state_version INTEGER NOT NULL, "
            "updated_at DATETIME NOT NULL, PRIMARY KEY (tenant_id, user_id, movie_id))"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE user_feedback_events ("
            "tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, actor_user_id TEXT NOT NULL, "
            "user_id INTEGER NOT NULL, movie_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "old_value JSON, new_value JSON NOT NULL, state_version INTEGER NOT NULL, "
            "outcome TEXT NOT NULL, created_at DATETIME NOT NULL, "
            "PRIMARY KEY (tenant_id, event_id))"
        )
    )
    connection.execute(
        text(
            "INSERT INTO demo_personas VALUES "
            "(900000102, 'drama-fan', 'Drama Fan', 'Drama profile', 2, TRUE), "
            "(900000101, 'action-fan', 'Action Fan', 'Action profile', 1, TRUE)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO movies VALUES "
            "(1, 'Action One', 'Action|Thriller'), "
            "(2, 'Drama Two', 'Drama'), "
            "(3, 'Genreless', '(no genres listed)')"
        )
    )
    connection.execute(text("INSERT INTO links VALUES (1, '101'), (2, NULL), (3, '303')"))
    connection.execute(
        text(
            "INSERT INTO ratings VALUES "
            "('demo', 10, 1, 4.5, 100), ('demo', 11, 1, 4.0, 110), "
            "('demo', 10, 2, 3.5, 200), ('demo', 12, 2, 5.0, 210), "
            "('demo', 11, 3, 3.0, 120)"
        )
    )
    connection.execute(text("""
            INSERT INTO user_movie_state (
                tenant_id, user_id, movie_id, watched_at, rating,
                rating_updated_at, state_version, updated_at
            )
            SELECT tenant_id, "userId", "movieId", timestamp, rating,
                   timestamp, 1, timestamp
            FROM ratings
            """))
    return connection


def test_popular_for_user_excludes_seen_movies_and_breaks_ties_by_id() -> None:
    connection = _connection()
    try:
        items = RecommendationService().popular_for_user(connection, user_id=10, limit=10)
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [3]
    assert items[0].genres == []
    assert items[0].tmdb_id == "303"
    assert items[0].interaction_count == 1


def test_popular_for_cold_user_returns_global_order() -> None:
    connection = _connection()
    try:
        items = RecommendationService().popular_for_user(connection, user_id=999, limit=2)
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [1, 2]
    assert items[0].genres == ["Action", "Thriller"]
    assert items[0].tmdb_id == "101"


def test_personalized_ranking_uses_rating_weighted_genres() -> None:
    connection = _connection()
    try:
        connection.execute(text("INSERT INTO movies VALUES (4, 'Action Four', 'Action')"))
        connection.execute(text("INSERT INTO links VALUES (4, NULL)"))
        connection.execute(text("INSERT INTO ratings VALUES ('demo', 13, 4, 4.0, 220)"))
        policy, items = RecommendationService().personalized_for_user(
            connection, user_id=10, limit=2
        )
    finally:
        connection.close()

    assert policy == "genre-affinity"
    assert [item.movie_id for item in items] == [4, 3]
    assert items[0].reason == "Matches your Action ratings"


def test_personalized_ranking_keeps_cold_start_popularity_fallback() -> None:
    connection = _connection()
    try:
        policy, items = RecommendationService().personalized_for_user(
            connection, user_id=999, limit=2
        )
    finally:
        connection.close()

    assert policy == "popularity"
    assert [item.movie_id for item in items] == [1, 2]


def test_hydrate_ranked_movies_preserves_scores_and_excludes_seen() -> None:
    connection = _connection()
    try:
        items = RecommendationService().hydrate_ranked_movies(
            connection,
            user_id=10,
            ranked_items=[(3, 0.9), (1, 0.8), (2, 0.7)],
            reason="learned",
        )
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [3]
    assert items[0].score == 0.9
    assert items[0].reason == "learned"


def test_recent_history_is_descending_and_limited() -> None:
    connection = _connection()
    try:
        items = RecommendationService().recent_history(connection, user_id=10, limit=1)
    finally:
        connection.close()

    assert len(items) == 1
    assert items[0].movie_id == 2
    assert items[0].rating == 3.5
    assert items[0].timestamp == 200


def test_list_demo_personas_uses_stable_display_order() -> None:
    connection = _connection()
    try:
        personas = RecommendationService().list_demo_personas(connection)
    finally:
        connection.close()

    assert [persona.slug for persona in personas] == ["action-fan", "drama-fan"]
    assert personas[0].user_id == 900000101


def test_catalog_shows_current_rating_for_demo_persona() -> None:
    connection = _connection()
    try:
        items = RecommendationService().catalog_for_user(connection, user_id=900000101)
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [1, 2, 3]
    assert all(item.rating is None for item in items)


def test_rate_movie_replaces_rating_and_reset_clears_only_persona() -> None:
    connection = _connection()
    service = RecommendationService()
    try:
        service.rate_movie(
            connection,
            tenant_id="demo",
            user_id=900000101,
            movie_id=2,
            rating=5.0,
            timestamp=300,
        )
        service.rate_movie(
            connection,
            tenant_id="demo",
            user_id=900000101,
            movie_id=2,
            rating=4.0,
            timestamp=301,
        )
        rows = connection.execute(
            text("SELECT rating FROM user_movie_state WHERE user_id = 900000101")
        ).all()
        changed = service.reset_ratings(connection, user_id=900000101)
        unrelated = connection.execute(
            text("SELECT COUNT(*) FROM user_movie_state WHERE user_id = 10")
        ).scalar_one()
    finally:
        connection.close()

    assert rows == [(4.0,)]
    assert changed == 1
    assert unrelated == 2


def test_rating_rejects_non_persona_user() -> None:
    connection = _connection()
    try:
        with pytest.raises(UnknownDemoPersonaError):
            RecommendationService().reset_ratings(connection, user_id=10)
    finally:
        connection.close()


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


def test_serving_input_state_separates_positives_from_exclusions() -> None:
    connection = _connection()
    try:
        _dismiss(connection, user_id=10, movie_id=3, timestamp=300)
        state = RecommendationService().serving_input_state(connection, user_id=10)
    finally:
        connection.close()

    # User 10 watched 1 and 2; 3 is dismissed only.
    assert state.positive_movie_ids == [2, 1]
    assert state.dismissed_movie_ids == [3]
    assert sorted(state.excluded_movie_ids) == [1, 2, 3]
    assert state.positive_signal_count == 2
    assert state.revision == 3


def test_serving_input_state_demotes_a_watched_movie_once_it_is_dismissed() -> None:
    connection = _connection()
    try:
        _dismiss(connection, user_id=10, movie_id=2, timestamp=400)
        state = RecommendationService().serving_input_state(connection, user_id=10)
    finally:
        connection.close()

    assert state.positive_movie_ids == [1]
    assert state.dismissed_movie_ids == [2]
    assert 2 in state.excluded_movie_ids
    assert state.positive_signal_count == 1


def test_serving_input_state_is_empty_for_an_unknown_user() -> None:
    connection = _connection()
    try:
        state = RecommendationService().serving_input_state(connection, user_id=999)
    finally:
        connection.close()

    assert state.positive_movie_ids == []
    assert state.excluded_movie_ids == []
    assert state.revision == 0


def test_popular_for_user_applies_the_caller_supplied_exclusion_set() -> None:
    connection = _connection()
    try:
        items = RecommendationService().popular_for_user(
            connection, user_id=999, limit=5, excluded_movie_ids=[1]
        )
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [2, 3]


def test_hydrate_ranked_movies_applies_the_caller_supplied_exclusion_set() -> None:
    connection = _connection()
    try:
        items = RecommendationService().hydrate_ranked_movies(
            connection,
            user_id=999,
            ranked_items=[(1, 0.9), (2, 0.5), (3, 0.1)],
            reason="learned",
            excluded_movie_ids=[2],
        )
    finally:
        connection.close()

    assert [item.movie_id for item in items] == [1, 3]
    assert [item.score for item in items] == [0.9, 0.1]
