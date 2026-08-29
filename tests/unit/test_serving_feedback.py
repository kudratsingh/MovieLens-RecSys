from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.serving.feedback import (
    FeedbackService,
    IdempotencyConflictError,
    InvalidLibraryCursorError,
    InvalidStateTransitionError,
    LibraryQuery,
    StateRevisionConflictError,
    _normalize_library_query,
    _query_fingerprint,
)
from src.serving.recommendations import RecommendationService, UnknownDemoPersonaError
from tests.unit.test_serving_recommendations import _connection

TENANT = "demo"
ACTOR = "oidc-actor"
USER = 900000101
START = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _b64(payload: dict[str, object]) -> str:
    """Mint a cursor by hand, the way a stale link or a forgery would arrive."""
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _decoded(cursor: str) -> dict[str, object]:
    return dict(json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))))


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
                query=LibraryQuery(tab="history", limit=20),
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
            query=LibraryQuery(tab="rated", limit=2),
        )
        second = service.library(
            connection,
            user_id=USER,
            query=LibraryQuery(tab="rated", limit=2, cursor=first.next_cursor),
        )
        with pytest.raises(InvalidLibraryCursorError):
            service.library(
                connection,
                user_id=USER,
                query=LibraryQuery(tab="history", limit=2, cursor=first.next_cursor),
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
            query=LibraryQuery(tab="rated", sort="title", limit=10, q="ACTION"),
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
            query=LibraryQuery(tab="history", limit=10),
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


# --- The Seen tab: search, filters and rankings over one persona's own rows ---


def _seen_connection():
    """The shared fixture plus the titles the Seen views need to be distinct.

    Deliberate shape: a tie in every sort key so the ``movie_id`` tie-break is
    exercised rather than assumed, one title the catalog snapshot has never
    covered (3), and one row per way a TMDB score can be absent — no snapshot
    row, details with no ``tmdb_rating``, unparseable details, a zero vote
    count, and a non-numeric average.
    """
    connection = _connection()
    connection.execute(
        text(
            "INSERT INTO movies VALUES "
            "(4, 'Sci Fi Four (1994)', 'Sci-Fi|Drama'), "
            "(5, 'Noir Five (1974)', 'Film-Noir|Drama'), "
            "(6, 'Broken Six (2010)', 'Drama'), "
            "(7, 'Percent %Seven (2015)', 'Drama'), "
            "(8, 'Plain Eight (2015)', 'Drama')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO movie_catalog_metadata VALUES "
            "(4, 'sci fi four', 1994, NULL, NULL, 'movielens', 'partial', TRUE, "
            """'{"tmdb_rating": {"average": 7.0, "count": 10}}'), """
            "(5, 'noir five', 1974, NULL, NULL, 'movielens', 'partial', TRUE, "
            """'{"tmdb_rating": {"average": 9.1, "count": 3}}'), """
            "(6, 'broken six', 2010, NULL, NULL, 'movielens', 'partial', TRUE, "
            "'not json at all'), "
            "(7, 'percent seven', 2015, NULL, NULL, 'movielens', 'partial', TRUE, "
            """'{"tmdb_rating": {"average": 6.0, "count": 0}}'), """
            "(8, 'plain eight', 2015, NULL, NULL, 'movielens', 'partial', TRUE, "
            """'{"tmdb_rating": {"average": "high", "count": 4}}')"""
        )
    )
    connection.execute(
        text("""UPDATE movie_catalog_metadata SET details = '{"tagline": "no score here"}'
                WHERE movie_id = 2""")
    )
    service = FeedbackService()
    for movie_id, minute, rating in (
        (1, 10, 4.0),
        (2, 20, 4.0),
        (3, 30, None),
        (4, 40, 2.5),
        (5, 50, None),
        (6, 60, 4.0),
        (7, 70, None),
        (8, 70, None),
    ):
        action = "watched_set" if rating is None else "rating_set"
        _mutate(service, connection, movie_id=movie_id, action=action, minute=minute, rating=rating)
    return connection


def _seen(service: FeedbackService, connection: object, **overrides: object):
    return service.library(
        connection,  # type: ignore[arg-type]
        user_id=USER,
        query=LibraryQuery(tab="history", **overrides),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("recent", [7, 8, 6, 5, 4, 3, 2, 1]),
        ("title", [1, 6, 2, 3, 5, 7, 8, 4]),
        ("rating", [6, 2, 1, 4, 7, 8, 5, 3]),
        ("release", [7, 8, 6, 2, 1, 4, 5, 3]),
        ("tmdb", [5, 1, 4, 2, 3, 6, 7, 8]),
    ],
)
def test_every_seen_sort_is_a_total_order_with_unknowns_last(
    sort: str, expected: list[int]
) -> None:
    """Each sort's ties resolve by movie id and each one's unknowns land last.

    ``rating`` is the interesting row: 1, 2 and 6 all hold four stars, so the
    watched-date tie-break is what orders them 6, 2, 1 rather than 1, 2, 6.
    """
    connection = _seen_connection()
    try:
        page = _seen(FeedbackService(), connection, sort=sort, limit=50)
    finally:
        connection.close()

    assert [item.movie_id for item in page.items] == expected


@pytest.mark.parametrize("sort", ["recent", "title", "rating", "release", "tmdb"])
def test_paging_one_row_at_a_time_reproduces_the_whole_order(sort: str) -> None:
    """A cursor walk over ties must neither repeat a row nor skip one."""
    connection = _seen_connection()
    service = FeedbackService()
    try:
        whole = _seen(service, connection, sort=sort, limit=50)
        walked: list[int] = []
        cursor: str | None = None
        while True:
            page = _seen(service, connection, sort=sort, limit=1, cursor=cursor)
            walked.extend(item.movie_id for item in page.items)
            cursor = page.next_cursor
            if not page.has_more:
                break
    finally:
        connection.close()

    assert walked == [item.movie_id for item in whole.items]


def _rated_connection():
    """The Seen fixture, with three of the watched-only titles also rated.

    ``release`` and ``tmdb`` order movie facts rather than feedback, so the rows
    worth having on the Rated tab are the ones whose facts are missing — the
    title the snapshot has never covered (3) and one of the ways a TMDB score
    can be absent (7). Both are watched-only in ``_seen_connection``, so without
    this the Rated tab would not see a single unknown under either sort. Movie 8
    is deliberately left unrated: the tab condition still has to hold.
    """
    connection = _seen_connection()
    service = FeedbackService()
    for movie_id, minute in ((3, 80), (5, 90), (7, 100)):
        _mutate(
            service, connection, movie_id=movie_id, action="rating_set", minute=minute, rating=3.0
        )
    return connection


def _rated(service: FeedbackService, connection: object, **overrides: object):
    return service.library(
        connection,  # type: ignore[arg-type]
        user_id=USER,
        query=LibraryQuery(tab="rated", **overrides),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("release", [7, 6, 2, 1, 4, 5, 3]),
        ("tmdb", [5, 1, 4, 2, 3, 6, 7]),
    ],
)
def test_rated_ranks_by_movie_facts_with_the_unknowns_last(sort: str, expected: list[int]) -> None:
    """The two orderings the Rated tab now offers, over its own row set.

    Both keys come from the left-joined snapshot rather than from the state
    row, so they are the two sorts whose answer does not depend on which tab
    asked. What the tab still decides is *which rows* — movie 8 is watched but
    unrated and appears under neither sort, while it leads ``release`` on Seen.
    """
    connection = _rated_connection()
    try:
        page = _rated(FeedbackService(), connection, sort=sort, limit=50)
    finally:
        connection.close()

    assert [item.movie_id for item in page.items] == expected
    assert page.matched == len(expected)


@pytest.mark.parametrize("sort", ["release", "tmdb"])
def test_a_rated_cursor_walks_the_new_sorts_without_a_repeat_or_a_gap(sort: str) -> None:
    """A ``COALESCE(..., -1)`` sentinel is a real key value, so it pages.

    Worth asserting on this tab specifically: the sentinel ties every unknown
    row together, and it is the ``movie_id`` half of the keyset predicate that
    has to break those ties one page at a time.
    """
    connection = _rated_connection()
    service = FeedbackService()
    try:
        whole = _rated(service, connection, sort=sort, limit=50)
        walked: list[int] = []
        cursor: str | None = None
        while True:
            page = _rated(service, connection, sort=sort, limit=1, cursor=cursor)
            walked.extend(item.movie_id for item in page.items)
            cursor = page.next_cursor
            if not page.has_more:
                break
    finally:
        connection.close()

    assert walked == [item.movie_id for item in whole.items]


def test_a_cursor_is_refused_by_every_other_view() -> None:
    """The fingerprint covers tab, sort and all four filters, and nothing else.

    Paging deeper, or asking for a different page size, is the same view — so
    the same cursor has to survive a changed ``limit`` and be refused by a
    changed anything-else.
    """
    connection = _seen_connection()
    service = FeedbackService()
    try:
        issued = _seen(service, connection, sort="release", limit=2, genre="Drama").next_cursor
        assert issued is not None
        resized = _seen(service, connection, sort="release", limit=5, genre="Drama", cursor=issued)
        for changed in (
            {"sort": "tmdb", "genre": "Drama"},
            {"sort": "release"},
            {"sort": "release", "genre": "Sci-Fi"},
            {"sort": "release", "genre": "Drama", "q": "plain"},
            {"sort": "release", "genre": "Drama", "year_from": 1990},
            {"sort": "release", "genre": "Drama", "year_to": 2020},
        ):
            with pytest.raises(InvalidLibraryCursorError):
                _seen(service, connection, limit=2, cursor=issued, **changed)
        with pytest.raises(InvalidLibraryCursorError):
            service.library(
                connection,
                user_id=USER,
                query=LibraryQuery(tab="rated", sort="release", genre="Drama", cursor=issued),
            )
    finally:
        connection.close()

    assert [item.movie_id for item in resized.items] == [6, 2, 4, 5]


def test_whitespace_and_case_variants_of_a_query_are_one_view() -> None:
    """Normalization happens once, before anything else touches the query, so a
    cursor minted under one spelling of a search term is still this view's."""
    connection = _seen_connection()
    service = FeedbackService()
    try:
        issued = _seen(service, connection, sort="title", q="  E  ", limit=2)
        reused = _seen(service, connection, sort="title", q="e", limit=2, cursor=issued.next_cursor)
    finally:
        connection.close()

    assert issued.query.q == "E"
    assert [item.movie_id for item in issued.items] == [1, 6]
    assert [item.movie_id for item in reused.items] == [3, 5]


def test_the_fingerprint_covers_the_view_and_nothing_about_the_page() -> None:
    """What a cursor is bound to, stated directly.

    Every field that changes which rows come back, or in what order, has to
    change it; page size and page position must not, or paging deeper would
    invalidate the cursor that got you there.
    """
    base = LibraryQuery(tab="history", sort="tmdb", q="blade", genre="Drama", year_from=1990)
    fingerprint = _query_fingerprint(_normalize_library_query(base))

    for changed in (
        replace(base, tab="rated"),
        replace(base, sort="release"),
        replace(base, q="blad"),
        replace(base, genre="Sci-Fi"),
        replace(base, year_from=1991),
        replace(base, year_to=2001),
    ):
        assert _query_fingerprint(_normalize_library_query(changed)) != fingerprint

    for equivalent in (
        replace(base, limit=50),
        replace(base, cursor="anything at all"),
        replace(base, q="  blade "),
        replace(base, genre=" Drama "),
    ):
        assert _query_fingerprint(_normalize_library_query(equivalent)) == fingerprint

    # Case survives into the echo but not into the identity of the view: the
    # title match is case-insensitive, so "Blade" and "blade" are one query.
    cased = _normalize_library_query(replace(base, q="BLADE"))
    assert cased.q == "BLADE"
    assert _query_fingerprint(cased) == fingerprint


@pytest.mark.parametrize(
    "payload",
    [
        "not base64 at all!!",
        _b64({"v": 1, "tab": "history", "sort": "recent", "q": "", "value": 1, "movie_id": 8}),
        _b64({"v": 2, "f": "0" * 16, "k": ["2026-08-21T13:10:00+00:00"], "id": 8}),
        _b64({"v": 2, "k": ["2026-08-21T13:10:00+00:00"], "id": 8}),
    ],
)
def test_a_cursor_that_is_not_this_views_own_is_refused(payload: str) -> None:
    """A stale link, a version-1 cursor and a forged fingerprint are one case."""
    connection = _seen_connection()
    try:
        with pytest.raises(InvalidLibraryCursorError):
            _seen(FeedbackService(), connection, limit=2, cursor=payload)
    finally:
        connection.close()


def test_a_cursor_whose_keys_do_not_match_the_sort_is_refused() -> None:
    """Arity and element type are part of what a cursor has to prove.

    Without the check a key vector from a one-key sort would silently bind to
    the first term of a two-key predicate, and a bool would compare as 0 or 1.
    """
    connection = _seen_connection()
    service = FeedbackService()
    try:
        rating_cursor = _seen(service, connection, sort="rating", limit=2).next_cursor
        assert rating_cursor is not None
        fingerprint = _decoded(rating_cursor)["f"]
        for keys in ([4.0], [4.0, "x", 1.0], [4.0, True], ["4.0", "x"], [4.0, 1.0]):
            forged = _b64({"v": 2, "f": fingerprint, "k": keys, "id": 4})
            with pytest.raises(InvalidLibraryCursorError):
                _seen(service, connection, sort="rating", limit=2, cursor=forged)
        for movie_id in (0, True, "8"):
            forged = _b64({"v": 2, "f": fingerprint, "k": [4.0, "x"], "id": movie_id})
            with pytest.raises(InvalidLibraryCursorError):
                _seen(service, connection, sort="rating", limit=2, cursor=forged)
    finally:
        connection.close()


def test_rating_sort_is_refused_on_the_watchlist_tab() -> None:
    """A watchlisted title cannot hold a star value, so the order has no meaning
    there. Rated and Seen both can, and both offer it."""
    connection = _seen_connection()
    service = FeedbackService()
    try:
        with pytest.raises(InvalidLibraryCursorError) as refused:
            service.library(
                connection,
                user_id=USER,
                query=LibraryQuery(tab="watchlist", sort="rating"),
            )
    finally:
        connection.close()

    assert "Rated and Seen" in str(refused.value)


def test_an_inverted_year_range_is_a_bad_request_not_an_empty_page() -> None:
    connection = _seen_connection()
    try:
        with pytest.raises(ValueError) as refused:
            _seen(FeedbackService(), connection, year_from=2001, year_to=1990)
    finally:
        connection.close()

    assert not isinstance(refused.value, InvalidLibraryCursorError)


def test_a_genre_filter_matches_whole_tokens_only() -> None:
    """``Film-Noir`` contains ``Noir``; the pipe-delimited match must not."""
    connection = _seen_connection()
    service = FeedbackService()
    try:
        drama = _seen(service, connection, sort="title", genre="Drama", limit=50)
        noir = _seen(service, connection, genre="Noir", limit=50)
        unknown = _seen(service, connection, genre="Documentary", limit=50)
    finally:
        connection.close()

    assert [item.movie_id for item in drama.items] == [6, 2, 5, 7, 8, 4]
    assert noir.items == []
    assert noir.matched == 0
    assert unknown.items == []


def test_a_search_term_is_escaped_so_a_wildcard_matches_itself() -> None:
    connection = _seen_connection()
    service = FeedbackService()
    try:
        wildcard = _seen(service, connection, q="%", limit=50)
        underscore = _seen(service, connection, q="_", limit=50)
    finally:
        connection.close()

    assert [item.movie_id for item in wildcard.items] == [7]
    assert underscore.items == []


def test_a_year_bound_drops_the_rows_the_snapshot_has_never_covered() -> None:
    """The one filter that can hide a row the tab contains.

    An unknown release year cannot satisfy "between 1990 and 2001", so movie 3
    drops out — but only while the filter is on. Unfiltered it is still listed,
    with a null year, because the row is about the viewer and not about how
    well enriched the title is.
    """
    connection = _seen_connection()
    service = FeedbackService()
    try:
        bounded = _seen(service, connection, sort="release", year_from=1990, year_to=2001)
        open_ended = _seen(service, connection, sort="release", year_from=1990)
        unfiltered = _seen(service, connection, sort="release", limit=50)
    finally:
        connection.close()

    assert [item.movie_id for item in bounded.items] == [2, 1, 4]
    assert [item.movie_id for item in open_ended.items] == [7, 8, 6, 2, 1, 4]
    assert unfiltered.items[-1].movie_id == 3
    assert unfiltered.items[-1].release_year is None


def test_matched_counts_the_filtered_set_on_every_page() -> None:
    """``matched`` is what the spotlight's "3 of 42" reads, so it has to be
    exact, stable across an appended page, and independent of the page size.
    ``counts`` stays unfiltered: the two agree only when no filter is on."""
    connection = _seen_connection()
    service = FeedbackService()
    try:
        unfiltered = _seen(service, connection, limit=3)
        first = _seen(service, connection, sort="title", genre="Drama", limit=4)
        second = _seen(
            service, connection, sort="title", genre="Drama", limit=4, cursor=first.next_cursor
        )
        wider = _seen(service, connection, sort="title", genre="Drama", limit=50)
        narrow = _seen(service, connection, genre="Drama", year_from=2010, limit=50)
    finally:
        connection.close()

    assert unfiltered.matched == 8
    assert unfiltered.matched == unfiltered.counts.history
    assert first.matched == second.matched == wider.matched == 6
    assert first.counts.history == 8
    assert narrow.matched == 3
    assert len(second.items) == 2


@pytest.mark.parametrize(
    ("movie_id", "expected"),
    [
        (1, 8.4),
        (4, 7.0),
        (5, 9.1),
        (2, None),
        (3, None),
        (6, None),
        (7, None),
        (8, None),
    ],
)
def test_a_row_carries_the_crowd_average_or_nothing(movie_id: int, expected: float | None) -> None:
    """One SQL expression answers both the row's mark and the ``tmdb`` order.

    A score is absent for a title with no snapshot row (3), details carrying no
    ``tmdb_rating`` (2), details that do not parse (6), an average nobody voted
    for (7), and a non-numeric average (8) — an average with no votes behind it
    is not a score, and that rule is spelled in SQL rather than in a caller.
    """
    connection = _seen_connection()
    try:
        page = _seen(FeedbackService(), connection, limit=50)
    finally:
        connection.close()

    rows = {item.movie_id: item for item in page.items}
    assert rows[movie_id].tmdb_rating == expected
