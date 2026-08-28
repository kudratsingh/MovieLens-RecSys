from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.serving.feedback import (
    FeedbackService,
    IdempotencyConflictError,
    InvalidLibraryCursorError,
    InvalidStateTransitionError,
    StateRevisionConflictError,
)
from src.serving.recommendations import RecommendationService, UnknownDemoPersonaError
from tests.unit.test_serving_recommendations import _connection

TENANT = "demo"
ACTOR = "oidc-actor"
USER = 900000101
START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _mutate(
    service: FeedbackService,
    connection: object,
    *,
    movie_id: int,
    action: str,
    minute: int,
    rating: float | None = None,
    request_id: object | None = None,
    expected_revision: int | None = None,
):
    return service.mutate(  # type: ignore[arg-type]
        connection,
        tenant_id=TENANT,
        actor_user_id=ACTOR,
        user_id=USER,
        movie_id=movie_id,
        action=action,  # type: ignore[arg-type]
        request_id=request_id or uuid4(),  # type: ignore[arg-type]
        rating=rating,
        expected_revision=expected_revision,
        now=START + timedelta(minutes=minute),
    )


def test_rating_edit_and_delete_preserve_original_watched_time() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        first = _mutate(service, connection, movie_id=1, action="rating_set", minute=0, rating=4.5)
        edited = _mutate(service, connection, movie_id=1, action="rating_set", minute=1, rating=3.5)
        deleted = _mutate(service, connection, movie_id=1, action="rating_deleted", minute=2)
    finally:
        connection.close()

    assert first.state.watched_at == START
    assert edited.state.watched_at == START
    assert edited.state.rating == 3.5
    assert edited.state.state_version == 2
    assert deleted.state.watched_at == START
    assert deleted.state.rating is None
    assert deleted.state.rating_updated_at is None
    assert deleted.state.state_version == 3


def test_remove_history_is_separate_and_clears_rating() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="rating_set", minute=0, rating=5.0)
        removed = _mutate(service, connection, movie_id=1, action="history_removed", minute=1)
    finally:
        connection.close()

    assert removed.state.watched_at is None
    assert removed.state.rating is None


def test_watchlist_is_organizational_and_watched_clears_it() -> None:
    connection = _connection()
    service = FeedbackService()
    recommendations = RecommendationService()
    try:
        saved = _mutate(service, connection, movie_id=3, action="watchlist_set", minute=0)
        before = recommendations.popular_for_user(connection, user_id=USER, limit=10)
        watched = _mutate(service, connection, movie_id=3, action="watched_set", minute=1)
        after = recommendations.popular_for_user(connection, user_id=USER, limit=10)
    finally:
        connection.close()

    assert saved.state.watchlisted_at == START
    assert [movie.movie_id for movie in before] == [1, 2, 3]
    assert watched.state.watchlisted_at is None
    assert watched.state.watched_at == START + timedelta(minutes=1)
    assert 3 not in [movie.movie_id for movie in after]


def test_dismissal_excludes_without_becoming_positive_history_and_is_undoable() -> None:
    connection = _connection()
    service = FeedbackService()
    recommendations = RecommendationService()
    try:
        _mutate(service, connection, movie_id=3, action="watched_set", minute=0)
        dismissed = _mutate(service, connection, movie_id=3, action="dismissal_set", minute=1)
        history = recommendations.recent_history(connection, user_id=USER, limit=10)
        excluded = recommendations.popular_for_user(connection, user_id=USER, limit=10)
        restored = _mutate(service, connection, movie_id=3, action="dismissal_deleted", minute=2)
        eligible = recommendations.popular_for_user(connection, user_id=USER, limit=10)
        positive_history = recommendations.recent_history(connection, user_id=USER, limit=10)
    finally:
        connection.close()

    assert dismissed.state.dismissed_at == START + timedelta(minutes=1)
    assert history == []
    assert 3 not in [movie.movie_id for movie in excluded]
    assert restored.state.dismissed_at is None
    assert 3 not in [movie.movie_id for movie in eligible]
    assert [movie.movie_id for movie in positive_history] == [3]


def test_idempotency_replays_original_canonical_response_once() -> None:
    connection = _connection()
    service = FeedbackService()
    request_id = uuid4()
    try:
        first = _mutate(
            service,
            connection,
            movie_id=1,
            action="watched_set",
            minute=0,
            request_id=request_id,
        )
        replay = _mutate(
            service,
            connection,
            movie_id=1,
            action="watched_set",
            minute=10,
            request_id=request_id,
        )
        event_count = connection.scalar(
            text("SELECT COUNT(*) FROM user_feedback_events WHERE event_id = :event_id"),
            {"event_id": str(request_id)},
        )
    finally:
        connection.close()

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.state == first.state
    assert event_count == 1


def test_idempotency_key_cannot_be_reused_for_another_action() -> None:
    connection = _connection()
    service = FeedbackService()
    request_id = uuid4()
    try:
        _mutate(
            service,
            connection,
            movie_id=1,
            action="watched_set",
            minute=0,
            request_id=request_id,
        )
        with pytest.raises(IdempotencyConflictError):
            _mutate(
                service,
                connection,
                movie_id=1,
                action="dismissal_set",
                minute=1,
                request_id=request_id,
            )
        rating_request_id = uuid4()
        _mutate(
            service,
            connection,
            movie_id=2,
            action="rating_set",
            minute=2,
            rating=4.0,
            request_id=rating_request_id,
        )
        with pytest.raises(IdempotencyConflictError):
            _mutate(
                service,
                connection,
                movie_id=2,
                action="rating_set",
                minute=3,
                rating=5.0,
                request_id=rating_request_id,
            )
    finally:
        connection.close()


def test_stale_revision_is_rejected_without_an_event() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        current = _mutate(service, connection, movie_id=1, action="watched_set", minute=0)
        with pytest.raises(StateRevisionConflictError):
            _mutate(
                service,
                connection,
                movie_id=1,
                action="dismissal_set",
                minute=1,
                expected_revision=current.state.state_version - 1,
            )
        event_count = connection.scalar(
            text("SELECT COUNT(*) FROM user_feedback_events WHERE user_id = :user_id"),
            {"user_id": USER},
        )
    finally:
        connection.close()

    assert event_count == 1


def test_watchlist_rejects_watched_or_dismissed_movie() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="watched_set", minute=0)
        with pytest.raises(InvalidStateTransitionError):
            _mutate(service, connection, movie_id=1, action="watchlist_set", minute=1)
        _mutate(service, connection, movie_id=2, action="dismissal_set", minute=2)
        with pytest.raises(InvalidStateTransitionError):
            _mutate(service, connection, movie_id=2, action="watchlist_set", minute=3)
    finally:
        connection.close()


def test_non_persona_cannot_read_or_mutate_same_tenant_library() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        with pytest.raises(UnknownDemoPersonaError):
            service.library(
                connection,
                user_id=10,
                tab="history",
                sort="recent",
                limit=20,
                cursor=None,
                query=None,
            )
        with pytest.raises(UnknownDemoPersonaError):
            service.mutate(
                connection,
                tenant_id=TENANT,
                actor_user_id=ACTOR,
                user_id=10,
                movie_id=1,
                action="watched_set",
                request_id=uuid4(),
            )
    finally:
        connection.close()


def test_library_uses_stable_filter_bound_cursor_and_counts() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        for movie_id in (1, 2, 3):
            _mutate(
                service,
                connection,
                movie_id=movie_id,
                action="rating_set",
                minute=movie_id,
                rating=float(movie_id),
            )
        first = service.library(
            connection,
            user_id=USER,
            tab="rated",
            sort="recent",
            limit=2,
            cursor=None,
            query=None,
        )
        second = service.library(
            connection,
            user_id=USER,
            tab="rated",
            sort="recent",
            limit=2,
            cursor=first.next_cursor,
            query=None,
        )
        with pytest.raises(InvalidLibraryCursorError):
            service.library(
                connection,
                user_id=USER,
                tab="history",
                sort="recent",
                limit=2,
                cursor=first.next_cursor,
                query=None,
            )
    finally:
        connection.close()

    assert [item.movie_id for item in first.items] == [3, 2]
    assert [item.movie_id for item in second.items] == [1]
    assert first.counts.rated == 3
    assert first.counts.history == 3
    assert first.counts.watchlist == 0
    assert first.has_more is True
    assert second.has_more is False


def test_library_title_search_and_live_rating_taste_copy_are_truthful() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="rating_set", minute=1, rating=4.0)
        _mutate(service, connection, movie_id=2, action="rating_set", minute=2, rating=2.0)
        page = service.library(
            connection,
            user_id=USER,
            tab="rated",
            sort="title",
            limit=10,
            cursor=None,
            query="ACTION",
        )
        summary = service.taste_summary(connection, user_id=USER)
    finally:
        connection.close()

    assert [item.title for item in page.items] == ["Action One"]
    assert summary.source == "live-ratings-v1"
    assert summary.rating_count == 2
    assert summary.average_rating == 3.0
    assert "not a deployed-model explanation" in summary.explanation
    assert summary.top_genres[0].genre in {"Action", "Drama", "Thriller"}


def test_library_rows_carry_local_artwork_and_a_structured_year() -> None:
    """A Library row is rendered at the same poster density as everything else,
    so it reads the shared snapshot in its own query. Movie 3 has no snapshot
    row: it still appears, with both fields NULL, because the row is about the
    viewer's state and not about how well enriched the title is."""
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="watched_set", minute=1)
        _mutate(service, connection, movie_id=3, action="watched_set", minute=2)
        page = service.library(
            connection,
            user_id=USER,
            tab="history",
            sort="recent",
            limit=10,
            cursor=None,
            query=None,
        )
    finally:
        connection.close()

    rows = {item.movie_id: item for item in page.items}
    assert set(rows) == {1, 3}
    assert rows[1].poster_url == "https://images.example/action-one.jpg"
    assert rows[1].release_year == 1998
    assert rows[3].poster_url is None
    assert rows[3].release_year is None


def test_states_for_movies_reports_a_revision_left_by_an_undone_write() -> None:
    """The case the recommendation overlay exists for: a title whose watchlist
    entry was added and taken off again holds no product flag at all, but its
    revision has moved twice. A client that assumed 0 would have its first
    write rejected as stale."""
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="watchlist_set", minute=0)
        _mutate(service, connection, movie_id=1, action="watchlist_deleted", minute=1)
        _mutate(service, connection, movie_id=2, action="watchlist_set", minute=2)
        states = service.states_for_movies(connection, user_id=USER, movie_ids=[1, 2, 3, 1])
        empty = service.states_for_movies(connection, user_id=USER, movie_ids=[])
    finally:
        connection.close()

    assert set(states) == {1, 2}
    assert states[1].state_version == 2
    assert states[1].watchlisted_at is None
    assert states[1].watched_at is None
    assert states[2].watchlisted_at is not None
    assert states[2].state_version == 1
    assert all(state.tenant_id == TENANT for state in states.values())
    assert empty == {}


def test_states_for_movies_is_scoped_to_the_requested_user() -> None:
    connection = _connection()
    service = FeedbackService()
    try:
        _mutate(service, connection, movie_id=1, action="watchlist_set", minute=0)
        other = service.states_for_movies(connection, user_id=900000102, movie_ids=[1])
    finally:
        connection.close()

    assert other == {}
