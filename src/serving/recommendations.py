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


@dataclass(frozen=True)
class HistoryMovie:
    movie_id: int
    title: str
    genres: list[str]
    rating: float
    timestamp: int


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
            )
            for row in rows
        ]

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


def _split_genres(value: str) -> list[str]:
    if not value or value == "(no genres listed)":
        return []
    return value.split("|")
