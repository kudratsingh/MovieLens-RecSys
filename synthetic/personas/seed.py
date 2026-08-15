"""Idempotently seed the tenant-scoped portfolio demo fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, bindparam, create_engine, text

from src.config import Settings

_FIXTURE_DIR = Path(__file__).parent
_BASE_TIMESTAMP = 1_700_000_000


@dataclass(frozen=True)
class Persona:
    slug: str
    display_name: str
    description: str
    user_id: int
    history: tuple[int, ...]


@dataclass(frozen=True)
class SeedResult:
    tenant_id: str
    persona_count: int
    persona_rating_count: int
    background_rating_count: int


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fixture_file:
        value = json.load(fixture_file)
    if not isinstance(value, dict):
        raise ValueError(f"fixture must contain a JSON object: {path}")
    return value


def load_personas(path: Path = _FIXTURE_DIR / "personas.json") -> tuple[str, list[Persona]]:
    payload = _read_json(path)
    tenant_id = str(payload["tenant_id"])
    personas = [
        Persona(
            slug=str(item["slug"]),
            display_name=str(item["display_name"]),
            description=str(item["description"]),
            user_id=int(item["user_id"]),
            history=tuple(int(movie_id) for movie_id in item["history"]),
        )
        for item in payload["personas"]
    ]
    _validate_personas(personas)
    return tenant_id, personas


def _validate_personas(personas: list[Persona]) -> None:
    if len(personas) != 4:
        raise ValueError("the demo fixture must define exactly four personas")
    if len({persona.slug for persona in personas}) != len(personas):
        raise ValueError("persona slugs must be unique")
    if len({persona.user_id for persona in personas}) != len(personas):
        raise ValueError("persona user IDs must be unique")
    for persona in personas:
        if len(set(persona.history)) != len(persona.history):
            raise ValueError(f"persona {persona.slug!r} contains duplicate history entries")


def seed_demo_personas(
    engine: Engine,
    *,
    persona_path: Path = _FIXTURE_DIR / "personas.json",
    catalog_path: Path = _FIXTURE_DIR / "catalog.json",
) -> SeedResult:
    tenant_id, personas = load_personas(persona_path)
    catalog = _read_json(catalog_path)
    movie_ids = tuple(int(movie_id) for movie_id in catalog["movie_ids"])
    background_user_ids = tuple(int(user_id) for user_id in catalog["background_user_ids"])
    if not movie_ids or not background_user_ids:
        raise ValueError("demo catalog and background user lists must not be empty")

    persona_rows = [
        {
            "tenant_id": tenant_id,
            "user_id": persona.user_id,
            "slug": persona.slug,
            "display_name": persona.display_name,
            "description": persona.description,
            "sort_order": sort_order,
        }
        for sort_order, persona in enumerate(personas)
    ]
    persona_ratings = [
        {
            "tenant_id": tenant_id,
            "user_id": persona.user_id,
            "movie_id": movie_id,
            "rating": 5.0 - (position % 3) * 0.5,
            "timestamp": _BASE_TIMESTAMP + persona_index * 10_000 + position,
        }
        for persona_index, persona in enumerate(personas)
        for position, movie_id in enumerate(persona.history)
    ]
    # A small deterministic cohort gives the popularity policy enough signal
    # to return a stable catalog order without pretending these users are personas.
    background_ratings = [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "movie_id": movie_id,
            "rating": 4.0,
            "timestamp": _BASE_TIMESTAMP - 100_000 + user_index * 1_000 + movie_index,
        }
        for user_index, user_id in enumerate(background_user_ids)
        for movie_index, movie_id in enumerate(movie_ids[: 16 - user_index * 2])
    ]

    with engine.begin() as connection:
        _assert_catalog_exists(connection, movie_ids)
        _replace_seed_rows(
            connection,
            tenant_id=tenant_id,
            personas=personas,
            background_user_ids=background_user_ids,
            persona_rows=persona_rows,
            rating_rows=persona_ratings + background_ratings,
        )

    return SeedResult(
        tenant_id=tenant_id,
        persona_count=len(persona_rows),
        persona_rating_count=len(persona_ratings),
        background_rating_count=len(background_ratings),
    )


def _assert_catalog_exists(connection: Connection, movie_ids: tuple[int, ...]) -> None:
    statement = text('SELECT "movieId" FROM movies WHERE "movieId" IN :movie_ids').bindparams(
        bindparam("movie_ids", expanding=True)
    )
    found = {int(row[0]) for row in connection.execute(statement, {"movie_ids": movie_ids})}
    missing = sorted(set(movie_ids) - found)
    if missing:
        raise RuntimeError(
            "demo catalog movies are missing from Postgres; run `make data-ingest` first. "
            f"Missing movie IDs: {missing}"
        )


def _replace_seed_rows(
    connection: Connection,
    *,
    tenant_id: str,
    personas: list[Persona],
    background_user_ids: tuple[int, ...],
    persona_rows: list[dict[str, object]],
    rating_rows: list[dict[str, object]],
) -> None:
    user_ids = tuple(persona.user_id for persona in personas) + background_user_ids
    delete_ratings = text(
        'DELETE FROM ratings WHERE tenant_id = :tenant_id AND "userId" IN :user_ids'
    ).bindparams(bindparam("user_ids", expanding=True))
    connection.execute(delete_ratings, {"tenant_id": tenant_id, "user_ids": user_ids})
    connection.execute(
        text("DELETE FROM demo_personas WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    )
    connection.execute(
        text("""
            INSERT INTO demo_personas
                (tenant_id, user_id, slug, display_name, description, sort_order, synthetic)
            VALUES
                (:tenant_id, :user_id, :slug, :display_name, :description, :sort_order, TRUE)
            """),
        persona_rows,
    )
    if rating_rows:
        connection.execute(
            text("""
                INSERT INTO ratings (tenant_id, "userId", "movieId", rating, timestamp)
                VALUES (:tenant_id, :user_id, :movie_id, :rating, :timestamp)
                """),
            rating_rows,
        )


def main() -> None:
    engine = create_engine(Settings().admin_user_database_url, future=True)
    try:
        result = seed_demo_personas(engine)
    finally:
        engine.dispose()
    print(
        f"Seeded {result.persona_count} personas, {result.persona_rating_count} persona ratings, "
        f"and {result.background_rating_count} background ratings into tenant {result.tenant_id!r}."
    )


if __name__ == "__main__":
    main()
