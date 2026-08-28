"""Idempotently seed the tenant-scoped portfolio demo fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import JSON, Connection, Engine, bindparam, create_engine, text

from src.config import Settings

_FIXTURE_DIR = Path(__file__).parent
_BASE_TIMESTAMP = 1_700_000_000
_CATALOG_SNAPSHOT_AT = "2026-08-21T00:00:00+00:00"


@dataclass(frozen=True)
class Persona:
    slug: str
    display_name: str
    description: str
    user_id: int
    history: tuple[int, ...]


@dataclass(frozen=True)
class CatalogMovie:
    movie_id: int
    title: str
    genres: str
    tmdb_id: str | None
    release_year: int | None
    poster_url: str | None
    overview: str | None


@dataclass(frozen=True)
class SeedResult:
    tenant_id: str
    persona_count: int
    persona_rating_count: int
    background_rating_count: int
    visible_movie_count: int
    recommendable_movie_count: int
    poster_movie_count: int


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


def load_demo_catalog(
    path: Path = _FIXTURE_DIR / "catalog.json",
) -> tuple[list[CatalogMovie], tuple[int, ...]]:
    payload = _read_json(path)
    movies = [
        CatalogMovie(
            movie_id=int(item["movie_id"]),
            title=str(item["title"]),
            genres=str(item["genres"]),
            tmdb_id=(str(item["tmdb_id"]) if item.get("tmdb_id") is not None else None),
            release_year=(
                int(item["release_year"])
                if item.get("release_year") is not None
                else _release_year_from_title(str(item["title"]))
            ),
            poster_url=(str(item["poster_url"]) if item.get("poster_url") is not None else None),
            overview=(str(item["overview"]) if item.get("overview") is not None else None),
        )
        for item in payload["movies"]
    ]
    background_user_ids = tuple(int(user_id) for user_id in payload["background_user_ids"])
    if not movies or not background_user_ids:
        raise ValueError("demo catalog and background user lists must not be empty")
    if len({movie.movie_id for movie in movies}) != len(movies):
        raise ValueError("demo catalog movie IDs must be unique")
    return movies, background_user_ids


def seed_demo_personas(
    engine: Engine,
    *,
    persona_path: Path = _FIXTURE_DIR / "personas.json",
    catalog_path: Path = _FIXTURE_DIR / "catalog.json",
) -> SeedResult:
    tenant_id, personas = load_personas(persona_path)
    catalog_movies, background_user_ids = load_demo_catalog(catalog_path)
    movie_ids = tuple(movie.movie_id for movie in catalog_movies)

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
    # A deterministic background cohort gives every title popularity and
    # candidate-graph support without pretending these users are personas.
    # Every visible movie receives four deterministic background interactions.
    # Browse coverage and recommendation eligibility are therefore measured
    # independently but intentionally equal in the reviewed demo fixture.
    background_ratings = [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "movie_id": movie_id,
            "rating": 4.0,
            "timestamp": _BASE_TIMESTAMP - 100_000 + user_index * 1_000 + movie_index,
        }
        for user_index, user_id in enumerate(background_user_ids)
        for movie_index, movie_id in enumerate(movie_ids)
        if (movie_index + user_index) % len(background_user_ids) != 0
    ]

    with engine.begin() as connection:
        _ensure_demo_catalog(connection, catalog_movies)
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
        visible_movie_count=len(catalog_movies),
        recommendable_movie_count=len({row["movie_id"] for row in background_ratings}),
        poster_movie_count=sum(movie.poster_url is not None for movie in catalog_movies),
    )


def _ensure_demo_catalog(connection: Connection, movies: list[CatalogMovie]) -> None:
    """Add the snapshot's rows without overwriting a full MovieLens ingest.

    MovieLens-owned rows (``movies``, ``links``) are only ever filled in, never
    replaced: a real ingest is the better source and must win. The catalog read
    model is the other way round — it is fixture-owned, carries no user data,
    and is regenerated offline (``synthetic/personas/enrich_posters.py``), so a
    re-seed is how a refreshed poster or synopsis reaches an existing database.
    """
    connection.execute(
        text("""
            INSERT INTO movies ("movieId", title, genres)
            VALUES (:movie_id, :title, :genres)
            ON CONFLICT ("movieId") DO NOTHING
            """),
        [
            {"movie_id": movie.movie_id, "title": movie.title, "genres": movie.genres}
            for movie in movies
        ],
    )
    # A TMDB id the fixture has learned since the database was first seeded is
    # worth filling in, but an id that is already there came from an ingest we
    # do not get to second-guess.
    connection.execute(
        text("""
            INSERT INTO links ("movieId", "tmdbId")
            VALUES (:movie_id, :tmdb_id)
            ON CONFLICT ("movieId") DO UPDATE SET "tmdbId" = EXCLUDED."tmdbId"
            WHERE links."tmdbId" IS NULL AND EXCLUDED."tmdbId" IS NOT NULL
            """),
        [{"movie_id": movie.movie_id, "tmdb_id": movie.tmdb_id} for movie in movies],
    )
    connection.execute(
        text("""
            INSERT INTO movie_catalog_metadata
                (movie_id, sort_title, release_year, poster_url, overview,
                 metadata_source, source_status, visible, source_updated_at)
            VALUES
                (:movie_id, :sort_title, :release_year, :poster_url, :overview,
                 'reviewed-fixture', :source_status, TRUE, :source_updated_at)
            ON CONFLICT (movie_id) DO UPDATE SET
                sort_title = EXCLUDED.sort_title,
                release_year = EXCLUDED.release_year,
                poster_url = EXCLUDED.poster_url,
                overview = EXCLUDED.overview,
                metadata_source = EXCLUDED.metadata_source,
                source_status = EXCLUDED.source_status,
                visible = EXCLUDED.visible,
                source_updated_at = EXCLUDED.source_updated_at
            """),
        [
            {
                "movie_id": movie.movie_id,
                "sort_title": _sort_title(movie.title),
                "release_year": movie.release_year,
                "poster_url": movie.poster_url,
                "overview": movie.overview,
                "source_status": ("complete" if movie.poster_url and movie.overview else "partial"),
                "source_updated_at": _CATALOG_SNAPSHOT_AT,
            }
            for movie in movies
        ],
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
    delete_state = text(
        "DELETE FROM user_movie_state WHERE tenant_id = :tenant_id AND user_id IN :user_ids"
    ).bindparams(bindparam("user_ids", expanding=True))
    connection.execute(delete_state, {"tenant_id": tenant_id, "user_ids": user_ids})
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
        state_rows = [
            {
                **row,
                "watched_at": datetime.fromtimestamp(_fixture_int(row["timestamp"]), UTC),
            }
            for row in rating_rows
        ]
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, watched_at, rating,
                    rating_updated_at, state_version, updated_at
                ) VALUES (
                    :tenant_id, :user_id, :movie_id, :watched_at, :rating,
                    :watched_at, 1, :watched_at
                )
                """),
            state_rows,
        )
        insert_seed_event = text("""
            INSERT INTO user_feedback_events (
                tenant_id, event_id, actor_user_id, user_id, movie_id, action,
                old_value, new_value, state_version, outcome, created_at
            ) VALUES (
                :tenant_id, :event_id, 'seed:demo-personas', :user_id, :movie_id,
                'rating_imported', NULL, :new_value, 1, 'backfilled', :created_at
            )
            ON CONFLICT (tenant_id, event_id) DO NOTHING
            """).bindparams(bindparam("new_value", type_=JSON))
        connection.execute(
            insert_seed_event,
            [
                {
                    "tenant_id": str(row["tenant_id"]),
                    "event_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"movielens-demo:{row['tenant_id']}:{row['user_id']}:{row['movie_id']}",
                        )
                    ),
                    "user_id": _fixture_int(row["user_id"]),
                    "movie_id": _fixture_int(row["movie_id"]),
                    "new_value": {
                        "rating": _fixture_float(row["rating"]),
                        "watched_at": _fixture_datetime(row["watched_at"]).isoformat(),
                    },
                    "created_at": _fixture_datetime(row["watched_at"]),
                }
                for row in state_rows
            ],
        )


def _fixture_int(value: object) -> int:
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"fixture value is not integer-compatible: {value!r}")


def _fixture_float(value: object) -> float:
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"fixture value is not numeric: {value!r}")


def _fixture_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"fixture value is not a datetime: {value!r}")


def main() -> None:
    # The self-contained demo may need to add its compact global movie catalog.
    # That bootstrap is intentionally owner-only; admin_user remains limited to
    # tenant-scoped materialization and cannot mutate movies or links.
    engine = create_engine(Settings().database_url, future=True)
    try:
        result = seed_demo_personas(engine)
    finally:
        engine.dispose()
    print(
        f"Seeded {result.persona_count} personas, {result.persona_rating_count} persona ratings, "
        f"and {result.background_rating_count} background ratings into tenant {result.tenant_id!r}."
    )


def _release_year_from_title(title: str) -> int | None:
    if len(title) >= 6 and title.endswith(")") and title[-5:-1].isdigit():
        return int(title[-5:-1])
    return None


def _sort_title(title: str) -> str:
    value = title[:-7] if _release_year_from_title(title) is not None else title
    normalized = " ".join(value.casefold().split())
    for article in ("the ", "an ", "a "):
        if normalized.startswith(article):
            return f"{normalized[len(article):]}, {article.strip()}"
    return normalized


if __name__ == "__main__":
    main()
