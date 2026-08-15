"""Tenant-scoped read path for the first end-to-end serving slice.

The Phase 2 models remain offline modules. This service provides the first
online baseline by ranking movies directly from the interaction table, using
the request-bound SQLAlchemy connection created by ``AuthMiddleware``. That
connection has ``SET LOCAL app.tenant_id`` applied, so both the popularity
counts and the selected user's history are constrained by Postgres RLS.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class RecommendedMovie:
    movie_id: int
    title: str
    genres: list[str]
    tmdb_id: str | None
    interaction_count: int
    score: float = 0.0
    reason: str = "Popular with viewers in this tenant"


@dataclass(frozen=True)
class HistoryMovie:
    movie_id: int
    title: str
    genres: list[str]
    rating: float
    timestamp: int


@dataclass(frozen=True)
class DemoPersona:
    user_id: int
    slug: str
    display_name: str
    description: str


@dataclass(frozen=True)
class CatalogMovie:
    movie_id: int
    title: str
    genres: list[str]
    rating: float | None


class UnknownDemoPersonaError(ValueError):
    """The requested user is not writable through the demo surface."""


class UnknownMovieError(ValueError):
    """The requested movie does not exist in the shared catalog."""


class RecommendationService:
    """Read recommendations and history through an RLS-scoped connection."""

    def popular_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
    ) -> list[RecommendedMovie]:
        """Return popular unseen movies for a user.

        This is deliberately the first online policy: it is deterministic,
        has an explicit cold-start behavior, and exercises the same tenant and
        catalog boundaries the learned serving path will inherit.
        """
        rows = connection.execute(
            text("""
                SELECT
                    m."movieId" AS movie_id,
                    m.title,
                    m.genres,
                    l."tmdbId" AS tmdb_id,
                    COUNT(r."movieId") AS interaction_count
                FROM ratings AS r
                JOIN movies AS m ON m."movieId" = r."movieId"
                LEFT JOIN links AS l ON l."movieId" = m."movieId"
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM ratings AS seen
                    WHERE seen."userId" = :user_id
                      AND seen."movieId" = r."movieId"
                )
                GROUP BY m."movieId", m.title, m.genres, l."tmdbId"
                ORDER BY interaction_count DESC, m."movieId" ASC
                LIMIT :limit
                """),
            {"user_id": user_id, "limit": limit},
        )
        return [
            RecommendedMovie(
                movie_id=int(row.movie_id),
                title=str(row.title),
                genres=_split_genres(str(row.genres)),
                tmdb_id=str(row.tmdb_id) if row.tmdb_id is not None else None,
                interaction_count=int(row.interaction_count),
                score=float(row.interaction_count),
            )
            for row in rows
        ]

    def personalized_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
    ) -> tuple[str, list[RecommendedMovie]]:
        """Rank unseen popular candidates using positive/negative genre feedback."""
        candidates = self.popular_for_user(connection, user_id=user_id, limit=max(50, limit * 10))
        history = self.recent_history(connection, user_id=user_id, limit=100)
        if not history:
            return "popularity", candidates[:limit]

        genre_weights: dict[str, float] = {}
        for movie in history:
            preference = movie.rating - 3.0
            for genre in movie.genres:
                genre_weights[genre] = genre_weights.get(genre, 0.0) + preference

        personalized = [
            RecommendedMovie(
                movie_id=movie.movie_id,
                title=movie.title,
                genres=movie.genres,
                tmdb_id=movie.tmdb_id,
                interaction_count=movie.interaction_count,
                score=float(movie.interaction_count)
                + sum(genre_weights.get(genre, 0.0) for genre in movie.genres),
                reason=_affinity_reason(movie.genres, genre_weights),
            )
            for movie in candidates
        ]
        personalized.sort(key=lambda movie: (-movie.score, movie.movie_id))
        return "genre-affinity", personalized[:limit]

    def recent_history(
        self,
        connection: Connection,
        *,
        user_id: int,
        limit: int,
    ) -> list[HistoryMovie]:
        """Return a user's most recent tenant-scoped interactions."""
        rows = connection.execute(
            text("""
                SELECT
                    m."movieId" AS movie_id,
                    m.title,
                    m.genres,
                    r.rating,
                    r.timestamp
                FROM ratings AS r
                JOIN movies AS m ON m."movieId" = r."movieId"
                WHERE r."userId" = :user_id
                ORDER BY r.timestamp DESC, m."movieId" ASC
                LIMIT :limit
                """),
            {"user_id": user_id, "limit": limit},
        )
        return [
            HistoryMovie(
                movie_id=int(row.movie_id),
                title=str(row.title),
                genres=_split_genres(str(row.genres)),
                rating=float(row.rating),
                timestamp=int(row.timestamp),
            )
            for row in rows
        ]

    def list_demo_personas(self, connection: Connection) -> list[DemoPersona]:
        """Return the named personas visible to the request tenant."""
        rows = connection.execute(text("""
                SELECT user_id, slug, display_name, description
                FROM demo_personas
                WHERE synthetic IS TRUE
                ORDER BY sort_order ASC, user_id ASC
                """))
        return [
            DemoPersona(
                user_id=int(row.user_id),
                slug=str(row.slug),
                display_name=str(row.display_name),
                description=str(row.description),
            )
            for row in rows
        ]

    def catalog_for_user(self, connection: Connection, *, user_id: int) -> list[CatalogMovie]:
        """Return the compact catalog and this tenant user's current ratings."""
        self._require_demo_persona(connection, user_id=user_id)
        rows = connection.execute(
            text("""
                SELECT m."movieId" AS movie_id, m.title, m.genres, current.rating
                FROM movies AS m
                LEFT JOIN ratings AS current
                  ON current."movieId" = m."movieId"
                 AND current."userId" = :user_id
                ORDER BY m."movieId" ASC
                LIMIT 100
                """),
            {"user_id": user_id},
        )
        return [
            CatalogMovie(
                movie_id=int(row.movie_id),
                title=str(row.title),
                genres=_split_genres(str(row.genres)),
                rating=float(row.rating) if row.rating is not None else None,
            )
            for row in rows
        ]

    def rate_movie(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        user_id: int,
        movie_id: int,
        rating: float,
        timestamp: int,
    ) -> None:
        """Replace one demo persona rating inside the request's RLS scope."""
        self._require_demo_persona(connection, user_id=user_id)
        exists = connection.execute(
            text('SELECT 1 FROM movies WHERE "movieId" = :movie_id'),
            {"movie_id": movie_id},
        ).scalar_one_or_none()
        if exists is None:
            raise UnknownMovieError(f"movie {movie_id} does not exist")
        connection.execute(
            text('DELETE FROM ratings WHERE "userId" = :user_id AND "movieId" = :movie_id'),
            {"user_id": user_id, "movie_id": movie_id},
        )
        connection.execute(
            text("""
                INSERT INTO ratings (tenant_id, "userId", "movieId", rating, timestamp)
                VALUES (:tenant_id, :user_id, :movie_id, :rating, :timestamp)
                """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "movie_id": movie_id,
                "rating": rating,
                "timestamp": timestamp,
            },
        )

    def reset_ratings(self, connection: Connection, *, user_id: int) -> int:
        """Clear only this demo persona's ratings in the active tenant."""
        self._require_demo_persona(connection, user_id=user_id)
        result = connection.execute(
            text('DELETE FROM ratings WHERE "userId" = :user_id'),
            {"user_id": user_id},
        )
        return int(result.rowcount)

    def _require_demo_persona(self, connection: Connection, *, user_id: int) -> None:
        exists = connection.execute(
            text("SELECT 1 FROM demo_personas WHERE user_id = :user_id AND synthetic IS TRUE"),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if exists is None:
            raise UnknownDemoPersonaError(f"user {user_id} is not a writable demo persona")


def _split_genres(value: str) -> list[str]:
    if not value or value == "(no genres listed)":
        return []
    return value.split("|")


def _affinity_reason(genres: list[str], weights: dict[str, float]) -> str:
    matched = sorted(genres, key=lambda genre: weights.get(genre, 0.0), reverse=True)
    if matched and weights.get(matched[0], 0.0) > 0:
        return f"Matches your {matched[0]} ratings"
    return "Popular unseen movie in this tenant"
