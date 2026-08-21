"""Stable, local movie-catalog and movie-detail read paths.

Browse and detail never call TMDB. Grid metadata is read from the shared
``movie_catalog_metadata`` snapshot and tenant-owned movie state is overlaid
through the request's RLS-scoped connection.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import Connection, bindparam, text

from src.serving.feedback import MovieState
from src.serving.recommendations import UnknownDemoPersonaError, UnknownMovieError

CatalogSort = Literal["title", "newest", "popular"]
MetadataSource = Literal["reviewed-fixture", "tmdb-snapshot", "movielens"]
MetadataStatus = Literal["complete", "partial", "unavailable"]


class InvalidCatalogCursorError(ValueError):
    """The opaque cursor is malformed or belongs to another query."""


@dataclass(frozen=True)
class CatalogQuery:
    search: str | None = None
    genre: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    sort: CatalogSort = "title"
    limit: int = 24
    cursor: str | None = None


@dataclass(frozen=True)
class CatalogMovie:
    movie_id: int
    title: str
    genres: list[str]
    tmdb_id: str | None
    release_year: int | None
    poster_url: str | None
    overview: str | None
    metadata_source: MetadataSource
    source_status: MetadataStatus
    state: MovieState | None
    interaction_count: int


@dataclass(frozen=True)
class CatalogPage:
    items: list[CatalogMovie]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class LocalMovieMetadata:
    poster_url: str | None
    overview: str | None
    release_year: int | None
    metadata_source: MetadataSource


class CatalogService:
    """Query the persisted shared catalog with a tenant-owned state overlay."""

    def list_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        query: CatalogQuery,
    ) -> CatalogPage:
        self._require_demo_persona(connection, user_id=user_id)
        normalized = _normalize_query(query)
        fingerprint = _query_fingerprint(normalized)
        cursor = _decode_cursor(normalized.cursor, expected_fingerprint=fingerprint)

        order_sql, cursor_sql, cursor_params = _sort_sql(normalized.sort, cursor)
        rows = connection.execute(
            text(f"""
                SELECT
                    m."movieId" AS movie_id,
                    m.title,
                    m.genres,
                    l."tmdbId" AS tmdb_id,
                    cm.sort_title,
                    cm.release_year,
                    cm.poster_url,
                    cm.overview,
                    cm.metadata_source,
                    cm.source_status,
                    current.tenant_id AS state_tenant_id,
                    current.user_id AS state_user_id,
                    current.movie_id AS state_movie_id,
                    current.watched_at AS state_watched_at,
                    current.rating AS state_rating,
                    current.rating_updated_at AS state_rating_updated_at,
                    current.watchlisted_at AS state_watchlisted_at,
                    current.dismissed_at AS state_dismissed_at,
                    current.state_version AS state_version,
                    current.updated_at AS state_updated_at,
                    COALESCE(popularity.interaction_count, 0) AS interaction_count
                FROM movie_catalog_metadata AS cm
                JOIN movies AS m ON m."movieId" = cm.movie_id
                LEFT JOIN links AS l ON l."movieId" = m."movieId"
                LEFT JOIN user_movie_state AS current
                  ON current.movie_id = m."movieId" AND current.user_id = :user_id
                LEFT JOIN (
                    SELECT "movieId", COUNT(*) AS interaction_count
                    FROM ratings
                    GROUP BY "movieId"
                ) AS popularity ON popularity."movieId" = m."movieId"
                WHERE cm.visible = TRUE
                  AND (:search IS NULL OR LOWER(m.title) LIKE :search ESCAPE '\\')
                  AND (:genre IS NULL OR ('|' || m.genres || '|') LIKE :genre ESCAPE '\\')
                  AND (:year_from IS NULL OR cm.release_year >= :year_from)
                  AND (:year_to IS NULL OR cm.release_year <= :year_to)
                  {cursor_sql}
                ORDER BY {order_sql}
                LIMIT :fetch_limit
                """),
            {
                "user_id": user_id,
                "search": (
                    f"%{_escape_like(normalized.search.lower())}%" if normalized.search else None
                ),
                "genre": (f"%|{_escape_like(normalized.genre)}|%" if normalized.genre else None),
                "year_from": normalized.year_from,
                "year_to": normalized.year_to,
                "fetch_limit": normalized.limit + 1,
                **cursor_params,
            },
        ).all()
        has_more = len(rows) > normalized.limit
        page_rows = rows[: normalized.limit]
        items = [_catalog_movie(row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            sort_value: str | int
            if normalized.sort == "title":
                sort_value = str(last.sort_title)
            elif normalized.sort == "newest":
                sort_value = int(last.release_year or 0)
            else:
                sort_value = int(last.interaction_count)
            next_cursor = _encode_cursor(
                fingerprint=fingerprint,
                sort_value=sort_value,
                movie_id=int(last.movie_id),
            )
        return CatalogPage(items=items, next_cursor=next_cursor, has_more=has_more)

    def get_for_user(
        self,
        connection: Connection,
        *,
        user_id: int,
        movie_id: int,
    ) -> CatalogMovie:
        self._require_demo_persona(connection, user_id=user_id)
        row = connection.execute(
            text("""
                SELECT
                    m."movieId" AS movie_id,
                    m.title,
                    m.genres,
                    l."tmdbId" AS tmdb_id,
                    cm.release_year,
                    cm.poster_url,
                    cm.overview,
                    cm.metadata_source,
                    cm.source_status,
                    current.tenant_id AS state_tenant_id,
                    current.user_id AS state_user_id,
                    current.movie_id AS state_movie_id,
                    current.watched_at AS state_watched_at,
                    current.rating AS state_rating,
                    current.rating_updated_at AS state_rating_updated_at,
                    current.watchlisted_at AS state_watchlisted_at,
                    current.dismissed_at AS state_dismissed_at,
                    current.state_version AS state_version,
                    current.updated_at AS state_updated_at,
                    COALESCE(popularity.interaction_count, 0) AS interaction_count
                FROM movie_catalog_metadata AS cm
                JOIN movies AS m ON m."movieId" = cm.movie_id
                LEFT JOIN links AS l ON l."movieId" = m."movieId"
                LEFT JOIN user_movie_state AS current
                  ON current.movie_id = m."movieId" AND current.user_id = :user_id
                LEFT JOIN (
                    SELECT "movieId", COUNT(*) AS interaction_count
                    FROM ratings
                    GROUP BY "movieId"
                ) AS popularity ON popularity."movieId" = m."movieId"
                WHERE cm.visible = TRUE AND m."movieId" = :movie_id
                """),
            {"user_id": user_id, "movie_id": movie_id},
        ).one_or_none()
        if row is None:
            raise UnknownMovieError(f"movie {movie_id} is not in the visible catalog")
        return _catalog_movie(row)

    def metadata_for_movies(
        self,
        connection: Connection,
        *,
        movie_ids: list[int],
    ) -> dict[int, LocalMovieMetadata]:
        """Batch-read persisted metadata for recommendation hydration."""
        if not movie_ids:
            return {}
        query = text("""
            SELECT movie_id, poster_url, overview, release_year, metadata_source
            FROM movie_catalog_metadata
            WHERE movie_id IN :movie_ids
            """).bindparams(bindparam("movie_ids", expanding=True))
        rows = connection.execute(query, {"movie_ids": list(dict.fromkeys(movie_ids))})
        return {
            int(row.movie_id): LocalMovieMetadata(
                poster_url=str(row.poster_url) if row.poster_url is not None else None,
                overview=str(row.overview) if row.overview is not None else None,
                release_year=int(row.release_year) if row.release_year is not None else None,
                metadata_source=cast(MetadataSource, str(row.metadata_source)),
            )
            for row in rows
        }

    @staticmethod
    def _require_demo_persona(connection: Connection, *, user_id: int) -> None:
        exists = connection.execute(
            text("SELECT 1 FROM demo_personas WHERE user_id = :user_id AND synthetic IS TRUE"),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if exists is None:
            raise UnknownDemoPersonaError(f"user {user_id} is not a writable demo persona")


def _normalize_query(query: CatalogQuery) -> CatalogQuery:
    search = " ".join(query.search.split()).strip() if query.search else None
    genre = query.genre.strip() if query.genre else None
    if query.year_from is not None and query.year_to is not None:
        if query.year_from > query.year_to:
            raise ValueError("year_from must be less than or equal to year_to")
    return CatalogQuery(
        search=search or None,
        genre=genre or None,
        year_from=query.year_from,
        year_to=query.year_to,
        sort=query.sort,
        limit=query.limit,
        cursor=query.cursor,
    )


def _query_fingerprint(query: CatalogQuery) -> str:
    payload = json.dumps(
        {
            "search": query.search.lower() if query.search else None,
            "genre": query.genre,
            "year_from": query.year_from,
            "year_to": query.year_to,
            "sort": query.sort,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _encode_cursor(*, fingerprint: str, sort_value: str | int, movie_id: int) -> str:
    raw = json.dumps(
        {"v": 1, "q": fingerprint, "s": sort_value, "id": movie_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    value: str | None,
    *,
    expected_fingerprint: str,
) -> tuple[str | int, int] | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("q") != expected_fingerprint
            or not isinstance(payload.get("id"), int)
            or payload["id"] < 1
            or isinstance(payload.get("s"), bool)
            or not isinstance(payload.get("s"), (str, int))
            or (isinstance(payload.get("s"), str) and not payload["s"])
            or (isinstance(payload.get("s"), int) and payload["s"] < 0)
        ):
            raise ValueError
        return payload["s"], payload["id"]
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidCatalogCursorError("catalog cursor is invalid for this query") from exc


def _sort_sql(
    sort: CatalogSort,
    cursor: tuple[str | int, int] | None,
) -> tuple[str, str, dict[str, object]]:
    if sort == "title":
        if cursor is not None and not isinstance(cursor[0], str):
            raise InvalidCatalogCursorError("catalog cursor has the wrong sort value")
        return (
            'cm.sort_title ASC, m."movieId" ASC',
            (
                ""
                if cursor is None
                else (
                    "AND (cm.sort_title > :cursor_value OR "
                    '(cm.sort_title = :cursor_value AND m."movieId" > :cursor_id))'
                )
            ),
            {} if cursor is None else {"cursor_value": cursor[0], "cursor_id": cursor[1]},
        )
    column = (
        "COALESCE(cm.release_year, 0)"
        if sort == "newest"
        else ("COALESCE(popularity.interaction_count, 0)")
    )
    if cursor is not None and (not isinstance(cursor[0], int) or isinstance(cursor[0], bool)):
        raise InvalidCatalogCursorError("catalog cursor has the wrong sort value")
    return (
        f'{column} DESC, m."movieId" ASC',
        (
            ""
            if cursor is None
            else (
                f"AND ({column} < :cursor_value OR "
                f'({column} = :cursor_value AND m."movieId" > :cursor_id))'
            )
        ),
        {} if cursor is None else {"cursor_value": cursor[0], "cursor_id": cursor[1]},
    )


def _catalog_movie(row: object) -> CatalogMovie:
    return CatalogMovie(
        movie_id=int(row.movie_id),  # type: ignore[attr-defined]
        title=str(row.title),  # type: ignore[attr-defined]
        genres=_split_genres(str(row.genres)),  # type: ignore[attr-defined]
        tmdb_id=(
            str(row.tmdb_id) if row.tmdb_id is not None else None  # type: ignore[attr-defined]
        ),
        release_year=(
            int(row.release_year)  # type: ignore[attr-defined]
            if row.release_year is not None  # type: ignore[attr-defined]
            else None
        ),
        poster_url=(
            str(row.poster_url) if row.poster_url is not None else None  # type: ignore[attr-defined]
        ),
        overview=(
            str(row.overview) if row.overview is not None else None  # type: ignore[attr-defined]
        ),
        metadata_source=cast(
            MetadataSource,
            str(row.metadata_source),  # type: ignore[attr-defined]
        ),
        source_status=cast(
            MetadataStatus,
            str(row.source_status),  # type: ignore[attr-defined]
        ),
        state=_movie_state(row),
        interaction_count=int(row.interaction_count),  # type: ignore[attr-defined]
    )


def _movie_state(row: object) -> MovieState | None:
    tenant_id = getattr(row, "state_tenant_id")
    if tenant_id is None:
        return None
    return MovieState(
        tenant_id=str(tenant_id),
        user_id=int(getattr(row, "state_user_id")),
        movie_id=int(getattr(row, "state_movie_id")),
        watched_at=_as_datetime(getattr(row, "state_watched_at")),
        rating=(
            float(getattr(row, "state_rating"))
            if getattr(row, "state_rating") is not None
            else None
        ),
        rating_updated_at=_as_datetime(getattr(row, "state_rating_updated_at")),
        watchlisted_at=_as_datetime(getattr(row, "state_watchlisted_at")),
        dismissed_at=_as_datetime(getattr(row, "state_dismissed_at")),
        state_version=int(getattr(row, "state_version")),
        updated_at=_as_datetime(getattr(row, "state_updated_at")) or datetime.now(UTC),
    )


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    raise TypeError(f"unsupported datetime value: {value!r}")


def _split_genres(value: str) -> list[str]:
    return [] if not value or value == "(no genres listed)" else value.split("|")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
