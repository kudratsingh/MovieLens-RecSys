from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, create_engine, text

from synthetic.personas.enrich_posters import poster_url_shape_error
from synthetic.personas.seed import load_demo_catalog, load_personas, seed_demo_personas
from synthetic.smoke.demo import DemoSmokeError, check_catalog_coverage


def _fixture_engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text('CREATE TABLE movies ("movieId" INTEGER PRIMARY KEY, title TEXT, genres TEXT)')
        )
        connection.execute(
            text(
                "CREATE TABLE movie_catalog_metadata ("
                "movie_id INTEGER PRIMARY KEY, sort_title TEXT, release_year INTEGER, "
                "poster_url TEXT, overview TEXT, metadata_source TEXT, source_status TEXT, "
                "visible BOOLEAN, source_updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text('CREATE TABLE links ("movieId" INTEGER PRIMARY KEY, "tmdbId" TEXT)')
        )
        connection.execute(
            text(
                'CREATE TABLE ratings (tenant_id TEXT, "userId" INTEGER, '
                '"movieId" INTEGER, rating FLOAT, timestamp INTEGER)'
            )
        )
        connection.execute(
            text(
                "CREATE TABLE demo_personas (tenant_id TEXT, user_id INTEGER, slug TEXT, "
                "display_name TEXT, description TEXT, sort_order INTEGER, synthetic BOOLEAN, "
                "PRIMARY KEY (tenant_id, user_id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE user_movie_state ("
                "tenant_id TEXT, user_id INTEGER, movie_id INTEGER, watched_at DATETIME, "
                "rating FLOAT, rating_updated_at DATETIME, watchlisted_at DATETIME, "
                "dismissed_at DATETIME, state_version INTEGER, updated_at DATETIME, "
                "PRIMARY KEY (tenant_id, user_id, movie_id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE user_feedback_events ("
                "tenant_id TEXT, event_id TEXT, actor_user_id TEXT, user_id INTEGER, "
                "movie_id INTEGER, action TEXT, old_value JSON, new_value JSON, "
                "state_version INTEGER, outcome TEXT, created_at DATETIME, "
                "PRIMARY KEY (tenant_id, event_id))"
            )
        )
    return engine


def test_persona_fixture_has_stable_required_profiles() -> None:
    tenant_id, personas = load_personas()

    assert tenant_id == "demo"
    assert [persona.slug for persona in personas] == [
        "action-fan",
        "drama-fan",
        "eclectic-viewer",
        "cold-start",
    ]
    assert personas[0].history != personas[1].history
    assert len(personas[0].history) == 8
    assert len(personas[1].history) == 8
    assert len(personas[2].history) == 11
    assert personas[3].history == ()


def test_demo_catalog_is_stable_and_covers_every_history_movie() -> None:
    _, personas = load_personas()
    movies, background_user_ids = load_demo_catalog()

    catalog_ids = {movie.movie_id for movie in movies}
    history_ids = {movie_id for persona in personas for movie_id in persona.history}
    assert len(movies) == 120
    assert len(background_user_ids) == 5
    assert history_ids <= catalog_ids
    assert all(movie.tmdb_id is None or movie.tmdb_id.isdigit() for movie in movies)
    # Every visible title carries a poster. Browse renders straight from this
    # fixture and never calls TMDB at request time, so a title without a poster
    # URL here is a permanent placeholder in the product.
    assert sum(movie.poster_url is not None for movie in movies) == 120
    assert all(poster_url_shape_error(movie.poster_url) is None for movie in movies)
    # Every title also carries a synopsis now. The 24 reviewed sentences are
    # still hand-written; the other 96 came from the same offline TMDB pass that
    # filled the posters, which is what takes `source_status` to 'complete' and
    # retires the "Partial details" eyebrow from real catalog rows. The
    # partial/unavailable states keep their coverage in the fixture-mode preview
    # (docs/frontend/catalog-contract.md), where they are meant to live.
    assert sum(movie.overview is not None for movie in movies) == 120


def test_seed_is_idempotent_and_preserves_cold_start() -> None:
    engine = _fixture_engine()
    first = seed_demo_personas(engine)
    second = seed_demo_personas(engine)

    with engine.connect() as connection:
        persona_count = connection.scalar(
            text("SELECT COUNT(*) FROM demo_personas WHERE tenant_id = 'demo'")
        )
        rating_count = connection.scalar(
            text("SELECT COUNT(*) FROM ratings WHERE tenant_id = 'demo'")
        )
        cold_rating_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM ratings "
                "WHERE tenant_id = 'demo' AND \"userId\" = 900000104"
            )
        )
        state_count = connection.scalar(
            text("SELECT COUNT(*) FROM user_movie_state WHERE tenant_id = 'demo'")
        )
        event_count = connection.scalar(
            text("SELECT COUNT(*) FROM user_feedback_events WHERE tenant_id = 'demo'")
        )

    assert first == second
    assert persona_count == first.persona_count
    assert rating_count == first.persona_rating_count + first.background_rating_count
    assert cold_rating_count == 0
    assert state_count == rating_count
    assert event_count == rating_count
    assert first.visible_movie_count == 120
    assert first.recommendable_movie_count == 120
    assert first.poster_movie_count == 120
    assert first.background_rating_count == 480
    engine.dispose()


def test_seed_does_not_overwrite_existing_full_catalog_rows() -> None:
    engine = _fixture_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                'INSERT INTO movies ("movieId", title, genres) '
                "VALUES (1, 'Canonical full-ingest title', 'Canonical')"
            )
        )
        connection.execute(text('INSERT INTO links ("movieId", "tmdbId") VALUES (1, \'999\')'))

    seed_demo_personas(engine)

    with engine.connect() as connection:
        title = connection.scalar(text('SELECT title FROM movies WHERE "movieId" = 1'))
        tmdb_id = connection.scalar(text('SELECT "tmdbId" FROM links WHERE "movieId" = 1'))
    assert title == "Canonical full-ingest title"
    assert tmdb_id == "999"
    engine.dispose()


def test_reseeding_refreshes_fixture_owned_catalog_metadata() -> None:
    """A database seeded before the fixture was enriched must pick the new URLs up."""
    engine = _fixture_engine()
    seed_demo_personas(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE movie_catalog_metadata "
                "SET poster_url = NULL, overview = NULL, source_status = 'partial' "
                "WHERE movie_id = 1"
            )
        )
        # The shape an older seed left behind: a links row that exists but has
        # never carried a TMDB id.
        connection.execute(text('UPDATE links SET "tmdbId" = NULL WHERE "movieId" = 1'))

    seed_demo_personas(engine)

    with engine.connect() as connection:
        poster_url = connection.scalar(
            text("SELECT poster_url FROM movie_catalog_metadata WHERE movie_id = 1")
        )
        overview = connection.scalar(
            text("SELECT overview FROM movie_catalog_metadata WHERE movie_id = 1")
        )
        status = connection.scalar(
            text("SELECT source_status FROM movie_catalog_metadata WHERE movie_id = 1")
        )
        tmdb_id = connection.scalar(text('SELECT "tmdbId" FROM links WHERE "movieId" = 1'))

    assert poster_url is not None and poster_url.startswith("https://image.tmdb.org/t/p/w500/")
    assert overview is not None
    assert status == "complete"
    assert tmdb_id == "862"
    engine.dispose()


def test_seed_persists_reviewed_metadata_without_live_enrichment() -> None:
    engine = _fixture_engine()
    seed_demo_personas(engine)

    with engine.connect() as connection:
        visible = connection.scalar(
            text("SELECT COUNT(*) FROM movie_catalog_metadata WHERE visible IS TRUE")
        )
        complete = connection.scalar(
            text("SELECT COUNT(*) FROM movie_catalog_metadata " "WHERE source_status = 'complete'")
        )
        with_poster = connection.scalar(
            text("SELECT COUNT(*) FROM movie_catalog_metadata WHERE poster_url IS NOT NULL")
        )
        source = connection.scalar(
            text("SELECT metadata_source FROM movie_catalog_metadata WHERE movie_id = 1")
        )

    assert visible == 120
    assert with_poster == 120
    # 'complete' means poster *and* overview. Both are now filled for every
    # visible title, so a 'partial' row in a seeded database means the fixture
    # regressed, not that the snapshot is merely young.
    assert complete == 120
    assert source == "reviewed-fixture"
    engine.dispose()


# --- The staleness gate the smoke run performs --------------------------------
#
# S5's failure mode had nothing to do with the code: the fixture reached 120
# posters while the running database still held a 24-poster snapshot, and every
# test stayed green because nothing compared the two. These cover the check that
# now does, driven against a mocked catalog endpoint rather than a live stack.


def _catalog_page(items: list[dict[str, Any]], next_cursor: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "tenant_id": "demo",
            "user_id": 900000101,
            "items": items,
            "page": {"next_cursor": next_cursor, "has_more": next_cursor is not None},
        },
    )


def _served(movie_id: int, *, poster: bool = True, overview: bool = True) -> dict[str, Any]:
    return {
        "movie_id": movie_id,
        "poster_url": f"https://image.tmdb.org/t/p/w500/{movie_id}.jpg" if poster else None,
        "overview": "A synopsis." if overview else None,
    }


def _coverage(handler: Any) -> Any:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return check_catalog_coverage(
            client,
            api_url="http://api.test",
            headers={"Authorization": "Bearer service-token"},
        )


def test_catalog_coverage_passes_on_a_snapshot_that_matches_the_fixture() -> None:
    movies, _ = load_demo_catalog()
    pages = [
        [_served(movie.movie_id) for movie in movies[index : index + 48]]
        for index in range(0, len(movies), 48)
    ]
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer service-token"
        assert request.url.path == "/users/900000101/catalog"
        requested.append(str(request.url.params))
        page_index = int(request.url.params.get("cursor", "0"))
        cursor = str(page_index + 1) if page_index + 1 < len(pages) else None
        return _catalog_page(pages[page_index], cursor)

    coverage = _coverage(handler)

    assert coverage.fixture_movie_count == 120
    assert coverage.served_movie_count == 120
    assert coverage.served_poster_count == 120
    assert coverage.served_overview_count == 120
    # The whole fixture is walked, and the walk stops when the cursor does.
    assert len(requested) == len(pages)


def test_catalog_coverage_fails_on_the_pre_backfill_snapshot() -> None:
    """The exact state the running demo was in: rows present, artwork missing."""
    movies, _ = load_demo_catalog()

    def handler(request: httpx.Request) -> httpx.Response:
        return _catalog_page(
            [
                _served(movie.movie_id, poster=index >= 24, overview=index >= 24)
                for index, movie in enumerate(movies[:48])
            ]
        )

    with pytest.raises(DemoSmokeError) as failure:
        _coverage(handler)

    message = str(failure.value)
    assert "make demo-seed" in message
    assert "24 titles are served without a poster" in message
    assert "24 titles are served without an overview" in message


def test_catalog_coverage_only_judges_titles_the_fixture_owns() -> None:
    """A deployment whose catalog is wider than the fixture is not asked about the rest."""
    movies, _ = load_demo_catalog()

    def handler(request: httpx.Request) -> httpx.Response:
        return _catalog_page(
            [_served(movies[0].movie_id)] + [_served(10_000_001, poster=False, overview=False)]
        )

    coverage = _coverage(handler)

    assert coverage.served_movie_count == 1
    assert coverage.served_poster_count == 1
