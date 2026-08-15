from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from synthetic.personas.seed import load_personas, seed_demo_personas


def _fixture_engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text('CREATE TABLE movies ("movieId" INTEGER PRIMARY KEY, title TEXT, genres TEXT)')
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
        catalog_path = Path("synthetic/personas/catalog.json")
        movie_ids = json.loads(catalog_path.read_text(encoding="utf-8"))["movie_ids"]
        connection.execute(
            text('INSERT INTO movies ("movieId", title, genres) VALUES (:id, :title, :genres)'),
            [
                {"id": movie_id, "title": f"Movie {movie_id}", "genres": "Test"}
                for movie_id in movie_ids
            ],
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
