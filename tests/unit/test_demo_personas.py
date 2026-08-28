from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from synthetic.personas.seed import load_demo_catalog, load_personas, seed_demo_personas


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
    assert all(
        movie.poster_url is None or movie.poster_url.startswith("https://image.tmdb.org/t/p/w500/")
        for movie in movies
    )
    # Overviews are still the hand-written subset; the synopsis fallback is a
    # real path and stays exercised by the fixture.
    assert sum(movie.overview is not None for movie in movies) == 24


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
    # 'complete' still means poster *and* overview, so it tracks the reviewed
    # overview subset rather than poster coverage.
    assert complete == 24
    assert source == "reviewed-fixture"
    engine.dispose()
