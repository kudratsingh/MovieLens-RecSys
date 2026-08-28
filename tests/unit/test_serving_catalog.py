from __future__ import annotations

import json

import pytest
from sqlalchemy import Connection, create_engine, text

from src.serving.catalog import CatalogQuery, CatalogService, InvalidCatalogCursorError
from src.serving.recommendations import UnknownMovieError

_ALPHA_DETAILS = {
    "tagline": "One line",
    "runtime_minutes": 101,
    "release_date": "1990-04-01",
    "backdrop_url": "https://image.tmdb.org/t/p/w1280/alpha.jpg",
    "tmdb_rating": {"average": 7.4, "count": 12},
    "directors": ["A Director"],
    "cast": [{"name": "A Star", "character": "Alpha", "profile_url": None}],
    "trailer": {"provider": "youtube", "key": "abc123", "name": "Official Trailer"},
    "fetched_at": "2026-08-28T00:00:00+00:00",
}
_ALPHA_DETAILS_JSON = json.dumps(_ALPHA_DETAILS)


def _connection() -> Connection:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(
        text('CREATE TABLE movies ("movieId" INTEGER PRIMARY KEY, title TEXT, genres TEXT)')
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
            "tenant_id TEXT, user_id INTEGER, movie_id INTEGER, watched_at TEXT, "
            "rating FLOAT, rating_updated_at TEXT, watchlisted_at TEXT, dismissed_at TEXT, "
            "state_version INTEGER, updated_at TEXT)"
        )
    )
    connection.execute(text("CREATE TABLE demo_personas (user_id INTEGER, synthetic BOOLEAN)"))
    connection.execute(
        text(
            "CREATE TABLE movie_catalog_metadata ("
            "movie_id INTEGER PRIMARY KEY, sort_title TEXT, release_year INTEGER, "
            "poster_url TEXT, overview TEXT, metadata_source TEXT, source_status TEXT, "
            "visible BOOLEAN, details TEXT)"
        )
    )
    connection.execute(text("INSERT INTO demo_personas VALUES (9001, TRUE)"))
    connection.execute(
        text(
            "INSERT INTO movies VALUES "
            "(1, 'Alpha (1990)', 'Drama'), "
            "(2, 'Beta (2001)', 'Action|Drama'), "
            "(3, 'Beta Again (2001)', 'Action'), "
            "(4, 'Delta (1980)', '(no genres listed)'), "
            "(5, 'Hidden (2020)', 'Drama')"
        )
    )
    connection.execute(
        text("INSERT INTO links VALUES (1, '101'), (2, NULL), (3, '303'), (4, NULL), (5, NULL)")
    )
    connection.execute(
        text(
            "INSERT INTO movie_catalog_metadata VALUES "
            "(1, 'alpha', 1990, '/alpha.jpg', 'Alpha overview', "
            f"'reviewed-fixture', 'complete', TRUE, '{_ALPHA_DETAILS_JSON}'), "
            "(2, 'beta', 2001, NULL, NULL, 'movielens', 'partial', TRUE, NULL), "
            "(3, 'beta again', 2001, NULL, 'Three', 'movielens', 'partial', TRUE, "
            "'not json at all'), "
            "(4, 'delta', 1980, NULL, NULL, 'movielens', 'unavailable', TRUE, NULL), "
            "(5, 'hidden', 2020, NULL, NULL, 'movielens', 'partial', FALSE, NULL)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO ratings VALUES "
            "('demo', 9001, 2, 4.5, 100), "
            "('demo', 44, 1, 4.0, 101), "
            "('demo', 45, 1, 5.0, 102), "
            "('demo', 44, 2, 3.0, 103), "
            "('demo', 44, 3, 4.0, 104)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO user_movie_state VALUES "
            "('demo', 9001, 2, '2026-08-21T08:00:00+00:00', 4.5, "
            "'2026-08-21T08:00:00+00:00', NULL, NULL, 3, "
            "'2026-08-21T08:00:00+00:00'), "
            "('demo', 9001, 3, NULL, NULL, NULL, "
            "'2026-08-21T09:00:00+00:00', NULL, 1, "
            "'2026-08-21T09:00:00+00:00'), "
            "('demo', 9001, 4, NULL, NULL, NULL, NULL, "
            "'2026-08-21T10:00:00+00:00', 2, "
            "'2026-08-21T10:00:00+00:00')"
        )
    )
    return connection


def test_title_cursor_is_stable_and_filter_bound() -> None:
    connection = _connection()
    service = CatalogService()
    try:
        first = service.list_for_user(
            connection,
            user_id=9001,
            query=CatalogQuery(limit=2),
        )
        second = service.list_for_user(
            connection,
            user_id=9001,
            query=CatalogQuery(limit=2, cursor=first.next_cursor),
        )
        with pytest.raises(InvalidCatalogCursorError):
            service.list_for_user(
                connection,
                user_id=9001,
                query=CatalogQuery(search="beta", limit=2, cursor=first.next_cursor),
            )
    finally:
        connection.close()

    assert [item.movie_id for item in first.items] == [1, 2]
    assert [item.movie_id for item in second.items] == [3, 4]
    assert first.has_more is True
    assert second.has_more is False
    assert second.next_cursor is None
    assert second.items[0].state is not None
    assert second.items[0].state.watchlisted_at is not None
    assert second.items[1].state is not None
    assert second.items[1].state.dismissed_at is not None


def test_search_genre_year_and_rating_overlay_compose() -> None:
    connection = _connection()
    try:
        page = CatalogService().list_for_user(
            connection,
            user_id=9001,
            query=CatalogQuery(
                search="beta",
                genre="Drama",
                year_from=2000,
                year_to=2010,
            ),
        )
    finally:
        connection.close()

    assert [item.movie_id for item in page.items] == [2]
    assert page.items[0].state is not None
    assert page.items[0].state.rating == 4.5
    assert page.items[0].state.state_version == 3
    assert page.items[0].state.watched_at is not None
    assert page.items[0].poster_url is None
    assert page.items[0].source_status == "partial"


def test_newest_and_popular_sorts_use_movie_id_tie_breakers() -> None:
    connection = _connection()
    service = CatalogService()
    try:
        newest = service.list_for_user(connection, user_id=9001, query=CatalogQuery(sort="newest"))
        popular = service.list_for_user(
            connection, user_id=9001, query=CatalogQuery(sort="popular")
        )
    finally:
        connection.close()

    assert [item.movie_id for item in newest.items] == [2, 3, 1, 4]
    assert [item.movie_id for item in popular.items] == [1, 2, 3, 4]


def test_movie_detail_and_batch_metadata_never_call_an_upstream() -> None:
    connection = _connection()
    service = CatalogService()
    try:
        item = service.get_for_user(connection, user_id=9001, movie_id=1)
        rated_item = service.get_for_user(connection, user_id=9001, movie_id=2)
        metadata = service.metadata_for_movies(connection, movie_ids=[1, 2, 1])
        with pytest.raises(UnknownMovieError):
            service.get_for_user(connection, user_id=9001, movie_id=5)
    finally:
        connection.close()

    assert item.overview == "Alpha overview"
    assert item.details == _ALPHA_DETAILS
    assert item.state is None
    assert rated_item.state is not None
    assert rated_item.state.rating == 4.5
    assert item.interaction_count == 2
    assert metadata[1].poster_url == "/alpha.jpg"
    assert metadata[2].metadata_source == "movielens"


def test_cursor_rejects_malformed_and_year_range_rejects_inversion() -> None:
    connection = _connection()
    service = CatalogService()
    try:
        with pytest.raises(InvalidCatalogCursorError):
            service.list_for_user(
                connection,
                user_id=9001,
                query=CatalogQuery(cursor="not-a-cursor"),
            )
        with pytest.raises(ValueError, match="year_from"):
            service.list_for_user(
                connection,
                user_id=9001,
                query=CatalogQuery(year_from=2010, year_to=2000),
            )
    finally:
        connection.close()


def test_the_detail_payload_is_a_detail_read_and_never_a_page_of_them() -> None:
    """The list query must not select ``details``.

    Not a style preference: the payload carries a backdrop, six cast members
    and a trailer per title, and a 48-row Browse page would ship 48 of them to
    render a grid of posters (``docs/frontend/catalog-contract.md``).
    """
    connection = _connection()
    service = CatalogService()
    try:
        page = service.list_for_user(connection, user_id=9001, query=CatalogQuery(limit=48))
        detail = service.get_for_user(connection, user_id=9001, movie_id=1)
    finally:
        connection.close()

    assert [item.movie_id for item in page.items] == [1, 2, 3, 4]
    assert all(item.details is None for item in page.items)
    assert detail.details is not None
    assert detail.details["trailer"] == _ALPHA_DETAILS["trailer"]


def test_a_title_with_no_details_and_one_with_a_broken_payload_both_read_as_none() -> None:
    """A detail page without its extra module is still a detail page.

    The snapshot is fixture-owned and shape-checked offline, so a row that does
    not parse predates the contract or was written by hand. Reading it as "no
    details" keeps the poster, the synopsis and the rating panel on the page;
    raising would take the whole page down over the module that was added last.
    """
    connection = _connection()
    service = CatalogService()
    try:
        missing = service.get_for_user(connection, user_id=9001, movie_id=2)
        broken = service.get_for_user(connection, user_id=9001, movie_id=3)
    finally:
        connection.close()

    assert missing.details is None
    assert broken.details is None
    assert broken.overview == "Three"
