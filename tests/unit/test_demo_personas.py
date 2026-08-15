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
    assert len(movies) == 24
    assert len(background_user_ids) == 5
    assert history_ids <= catalog_ids
    assert all(movie.tmdb_id.isdigit() for movie in movies)


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

    assert first == second
    assert persona_count == first.persona_count
    assert rating_count == first.persona_rating_count + first.background_rating_count
    assert cold_rating_count == 0
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
