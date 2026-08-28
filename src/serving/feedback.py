"""Durable tenant-scoped feedback projection and Library reads.

The request connection is already protected by ``SET LOCAL app.tenant_id`` and
forced RLS.  This service adds the product-level state machine, optimistic
revision checks, idempotency keys, and append-only mutation evidence required by
ADR 0012.  Imported MovieLens ``ratings`` are intentionally never mutated here.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import JSON, Connection, bindparam, text

from src.serving.recommendations import UnknownDemoPersonaError, UnknownMovieError

FeedbackAction = Literal[
    "watched_set",
    "history_removed",
    "rating_set",
    "rating_deleted",
    "watchlist_set",
    "watchlist_deleted",
    "dismissal_set",
    "dismissal_deleted",
]
LibraryTab = Literal["rated", "watchlist", "history"]
LibrarySort = Literal["recent", "title", "rating"]


class StateRevisionConflictError(ValueError):
    """The caller mutated a stale state revision."""


class IdempotencyConflictError(ValueError):
    """An idempotency key was already used for a different mutation."""


class InvalidStateTransitionError(ValueError):
    """The requested product states would contradict one another."""


class InvalidLibraryCursorError(ValueError):
    """A Library cursor is malformed or belongs to another query."""


@dataclass(frozen=True)
class MovieState:
    tenant_id: str
    user_id: int
    movie_id: int
    watched_at: datetime | None
    rating: float | None
    rating_updated_at: datetime | None
    watchlisted_at: datetime | None
    dismissed_at: datetime | None
    state_version: int
    updated_at: datetime


@dataclass(frozen=True)
class MutationResult:
    request_id: UUID
    replayed: bool
    outcome: Literal["changed", "no_change"]
    state: MovieState


@dataclass(frozen=True)
class LibraryMovie:
    movie_id: int
    title: str
    genres: list[str]
    # Artwork and the structured year come from the shared
    # ``movie_catalog_metadata`` snapshot, so a Library row can be rendered at
    # the same poster density as the rest of the product without a TMDB call
    # per row. Both are None for a title the snapshot has never covered.
    release_year: int | None
    poster_url: str | None
    state: MovieState


@dataclass(frozen=True)
class LibraryCounts:
    rated: int
    watchlist: int
    history: int


@dataclass(frozen=True)
class LibraryPage:
    items: list[LibraryMovie]
    counts: LibraryCounts
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class TasteGenre:
    genre: str
    rated_count: int
    average_rating: float


@dataclass(frozen=True)
class TasteSummary:
    source: str
    generated_at: datetime
    rating_count: int
    average_rating: float | None
    top_genres: list[TasteGenre]
    explanation: str


_STATE_COLUMN_NAMES = (
    "tenant_id",
    "user_id",
    "movie_id",
    "watched_at",
    "rating",
    "rating_updated_at",
    "watchlisted_at",
    "dismissed_at",
    "state_version",
    "updated_at",
)
_STATE_COLUMNS = ", ".join(_STATE_COLUMN_NAMES)
# The Library query joins a table that also has a ``movie_id`` and a
# ``release_year``, so its projection has to name the state table explicitly.
_ALIASED_STATE_COLUMNS = ", ".join(f"s.{column}" for column in _STATE_COLUMN_NAMES)

# Keyed on the movie set alone. A recommended title can carry a state row whose
# product flags are all null — adding a movie to the watchlist and taking it off
# again leaves the row behind at revision 2 — and that row is exactly what a
# client needs in order to send a correct ``expected_revision`` on its first
# press, so filtering on the flags here would hide the case this read exists for.
_STATES_FOR_MOVIES = text(
    f"SELECT {_STATE_COLUMNS} FROM user_movie_state "
    "WHERE user_id = :user_id AND movie_id IN :movie_ids"
).bindparams(bindparam("movie_ids", expanding=True))

_INSERT_EVENT = text("""
    INSERT INTO user_feedback_events (
        tenant_id, event_id, actor_user_id, user_id, movie_id, action,
        old_value, new_value, state_version, outcome, created_at
    ) VALUES (
        :tenant_id, :event_id, :actor_user_id, :user_id, :movie_id, :action,
        :old_value, :new_value, :state_version, :outcome, :created_at
    )
    """).bindparams(
    bindparam("old_value", type_=JSON),
    bindparam("new_value", type_=JSON),
)

_TAB_CONDITION: dict[LibraryTab, str] = {
    "rated": "s.rating IS NOT NULL",
    "watchlist": "s.watchlisted_at IS NOT NULL",
    "history": "s.watched_at IS NOT NULL",
}

_RECENT_COLUMN: dict[LibraryTab, str] = {
    "rated": "s.rating_updated_at",
    "watchlist": "s.watchlisted_at",
    "history": "s.watched_at",
}


def require_demo_persona(connection: Connection, *, user_id: int) -> None:
    """Authorize a ``/users/{id}`` target as a writable demo persona.

    Module level rather than a method because it is not about feedback: every
    persona-scoped resource asks the same question, and a second copy of the
    query is how two of them end up disagreeing about which ids are writable.
    RLS has already bounded the read to the request tenant, so a persona from
    another tenant is invisible here and fails closed as unknown.
    """
    exists = connection.execute(
        text("SELECT 1 FROM demo_personas WHERE user_id = :user_id AND synthetic IS TRUE"),
        {"user_id": user_id},
    ).scalar_one_or_none()
    if exists is None:
        raise UnknownDemoPersonaError(f"user {user_id} is not a writable demo persona")


class FeedbackService:
    """Own current-state transitions and append exactly one event per request ID."""

    def require_persona(self, connection: Connection, *, user_id: int) -> None:
        """Authorize a target before a bulk compatibility operation."""
        self._require_demo_persona(connection, user_id=user_id)

    def mutate(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        actor_user_id: str,
        user_id: int,
        movie_id: int,
        action: FeedbackAction,
        request_id: UUID,
        rating: float | None = None,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> MutationResult:
        self._require_demo_persona(connection, user_id=user_id)
        self._require_movie(connection, movie_id=movie_id)
        if connection.dialect.name == "postgresql":
            # Serialize retries sharing an idempotency key before checking the
            # event log. Under READ COMMITTED, a waiter sees the first
            # transaction's committed event and replays it rather than racing
            # into the unique key at event insert time.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"{tenant_id}:{request_id}"},
            )
        replay = self._find_replay(
            connection,
            tenant_id=tenant_id,
            request_id=request_id,
            user_id=user_id,
            movie_id=movie_id,
            action=action,
            rating=rating,
        )
        if replay is not None:
            return replay

        event_time = _ensure_utc(now or datetime.now(UTC))
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, state_version, updated_at
                ) VALUES (:tenant_id, :user_id, :movie_id, 0, :updated_at)
                ON CONFLICT (tenant_id, user_id, movie_id) DO NOTHING
                """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "movie_id": movie_id,
                "updated_at": event_time,
            },
        )
        state = self._get_state(connection, user_id=user_id, movie_id=movie_id, lock=True)
        if state is None:  # pragma: no cover - an RLS/constraint invariant, not a product path
            raise RuntimeError("state row was not visible after insert")
        if expected_revision is not None and state.state_version != expected_revision:
            raise StateRevisionConflictError(
                f"state revision {expected_revision} is stale; current revision is "
                f"{state.state_version}"
            )

        candidate = self._transition(state, action=action, rating=rating, now=event_time)
        changed = _meaningful_state(candidate) != _meaningful_state(state)
        canonical = replace(
            candidate if changed else state,
            state_version=state.state_version + 1 if changed else state.state_version,
            updated_at=event_time if changed else state.updated_at,
        )
        if changed:
            connection.execute(
                text("""
                    UPDATE user_movie_state
                    SET watched_at = :watched_at,
                        rating = :rating,
                        rating_updated_at = :rating_updated_at,
                        watchlisted_at = :watchlisted_at,
                        dismissed_at = :dismissed_at,
                        state_version = :state_version,
                        updated_at = :updated_at
                    WHERE user_id = :user_id AND movie_id = :movie_id
                    """),
                _state_parameters(canonical),
            )

        outcome: Literal["changed", "no_change"] = "changed" if changed else "no_change"
        connection.execute(
            _INSERT_EVENT,
            {
                "tenant_id": tenant_id,
                "event_id": str(request_id),
                "actor_user_id": actor_user_id,
                "user_id": user_id,
                "movie_id": movie_id,
                "action": action,
                "old_value": _state_snapshot(state),
                "new_value": _state_snapshot(canonical),
                "state_version": canonical.state_version,
                "outcome": outcome,
                "created_at": event_time,
            },
        )
        return MutationResult(
            request_id=request_id,
            replayed=False,
            outcome=outcome,
            state=canonical,
        )

    def get_state(
        self,
        connection: Connection,
        *,
        user_id: int,
        movie_id: int,
    ) -> MovieState | None:
        self._require_demo_persona(connection, user_id=user_id)
        return self._get_state(connection, user_id=user_id, movie_id=movie_id)

    def states_for_movies(
        self,
        connection: Connection,
        *,
        user_id: int,
        movie_ids: Sequence[int],
    ) -> dict[int, MovieState]:
        """Batch-read one user's current state for a set of movies.

        This is the overlay a recommendation response carries so a client can
        show what it already knows about a ranked title — and, more importantly,
        can address its first write to the revision the row is actually at. The
        persona check the mutation paths run is deliberately not repeated: the
        caller has already been authorized for this user, RLS bounds the read to
        the request tenant, and a second round trip on the recommendation path
        would be paid on every request to re-prove what the handler knows.
        """
        if not movie_ids:
            return {}
        rows = connection.execute(
            _STATES_FOR_MOVIES,
            {"user_id": user_id, "movie_ids": list(dict.fromkeys(movie_ids))},
        )
        return {int(row.movie_id): _row_to_state(row) for row in rows}

    def library(
        self,
        connection: Connection,
        *,
        user_id: int,
        tab: LibraryTab,
        sort: LibrarySort,
        limit: int,
        cursor: str | None,
        query: str | None,
    ) -> LibraryPage:
        self._require_demo_persona(connection, user_id=user_id)
        if sort == "rating" and tab != "rated":
            raise InvalidLibraryCursorError("rating sort is available only on the Rated tab")
        decoded = _decode_cursor(cursor, tab=tab, sort=sort, query=query) if cursor else None
        where = ["s.user_id = :user_id", _TAB_CONDITION[tab]]
        parameters: dict[str, object] = {"user_id": user_id, "limit": limit + 1}
        normalized_query = query.strip().lower() if query else ""
        if normalized_query:
            where.append("lower(m.title) LIKE :query")
            parameters["query"] = f"%{normalized_query}%"

        if sort == "recent":
            sort_expression = _RECENT_COLUMN[tab]
            order = f"{sort_expression} DESC, s.movie_id ASC"
            if decoded is not None:
                where.append(
                    f"({sort_expression} < :cursor_value OR "
                    f"({sort_expression} = :cursor_value AND s.movie_id > :cursor_movie_id))"
                )
                parameters["cursor_value"] = decoded[0]
                parameters["cursor_movie_id"] = decoded[1]
        elif sort == "title":
            sort_expression = "lower(m.title)"
            order = f"{sort_expression} ASC, s.movie_id ASC"
            if decoded is not None:
                where.append(
                    f"({sort_expression} > :cursor_value OR "
                    f"({sort_expression} = :cursor_value AND s.movie_id > :cursor_movie_id))"
                )
                parameters["cursor_value"] = decoded[0]
                parameters["cursor_movie_id"] = decoded[1]
        else:
            sort_expression = "s.rating"
            order = "s.rating DESC, s.movie_id ASC"
            if decoded is not None:
                where.append(
                    "(s.rating < :cursor_value OR "
                    "(s.rating = :cursor_value AND s.movie_id > :cursor_movie_id))"
                )
                parameters["cursor_value"] = float(decoded[0])
                parameters["cursor_movie_id"] = decoded[1]

        rows = list(
            connection.execute(
                text(f"""
                    SELECT {_ALIASED_STATE_COLUMNS}, m.title, m.genres,
                           cm.release_year, cm.poster_url,
                           {sort_expression} AS cursor_value
                    FROM user_movie_state AS s
                    JOIN movies AS m ON m."movieId" = s.movie_id
                    LEFT JOIN movie_catalog_metadata AS cm ON cm.movie_id = s.movie_id
                    WHERE {' AND '.join(where)}
                    ORDER BY {order}
                    LIMIT :limit
                    """),
                parameters,
            )
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [
            LibraryMovie(
                movie_id=int(row.movie_id),
                title=str(row.title),
                genres=_split_genres(str(row.genres)),
                release_year=(int(row.release_year) if row.release_year is not None else None),
                poster_url=(str(row.poster_url) if row.poster_url is not None else None),
                state=_row_to_state(row),
            )
            for row in page_rows
        ]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(
                tab=tab,
                sort=sort,
                query=normalized_query,
                value=_cursor_value(last.cursor_value),
                movie_id=int(last.movie_id),
            )
        return LibraryPage(
            items=items,
            counts=self._library_counts(connection, user_id=user_id),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def taste_summary(self, connection: Connection, *, user_id: int) -> TasteSummary:
        self._require_demo_persona(connection, user_id=user_id)
        rows = connection.execute(
            text("""
                SELECT s.rating, m.genres
                FROM user_movie_state AS s
                JOIN movies AS m ON m."movieId" = s.movie_id
                WHERE s.user_id = :user_id AND s.rating IS NOT NULL
                ORDER BY s.movie_id ASC
                """),
            {"user_id": user_id},
        )
        ratings: list[float] = []
        genre_ratings: dict[str, list[float]] = {}
        for row in rows:
            rating = float(row.rating)
            ratings.append(rating)
            for genre in _split_genres(str(row.genres)):
                genre_ratings.setdefault(genre, []).append(rating)
        top_genres = sorted(
            (
                TasteGenre(
                    genre=genre,
                    rated_count=len(values),
                    average_rating=round(sum(values) / len(values), 2),
                )
                for genre, values in genre_ratings.items()
            ),
            key=lambda item: (-item.rated_count, -item.average_rating, item.genre),
        )[:5]
        return TasteSummary(
            source="live-ratings-v1",
            generated_at=datetime.now(UTC),
            rating_count=len(ratings),
            average_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
            top_genres=top_genres,
            explanation=(
                "Based on ratings in this persona's live library. "
                "This summary is not a deployed-model explanation."
            ),
        )

    def _transition(
        self,
        state: MovieState,
        *,
        action: FeedbackAction,
        rating: float | None,
        now: datetime,
    ) -> MovieState:
        if action == "watched_set":
            return replace(state, watched_at=state.watched_at or now, watchlisted_at=None)
        if action == "history_removed":
            return replace(state, watched_at=None, rating=None, rating_updated_at=None)
        if action == "rating_set":
            if rating is None:
                raise InvalidStateTransitionError("rating_set requires a rating")
            return replace(
                state,
                watched_at=state.watched_at or now,
                rating=rating,
                rating_updated_at=(state.rating_updated_at if state.rating == rating else now),
                watchlisted_at=None,
            )
        if action == "rating_deleted":
            return replace(state, rating=None, rating_updated_at=None)
        if action == "watchlist_set":
            if state.watched_at is not None:
                raise InvalidStateTransitionError(
                    "a watched movie cannot be added to the watchlist"
                )
            if state.dismissed_at is not None:
                raise InvalidStateTransitionError(
                    "undo dismissal before adding this movie to watchlist"
                )
            return replace(state, watchlisted_at=state.watchlisted_at or now)
        if action == "watchlist_deleted":
            return replace(state, watchlisted_at=None)
        if action == "dismissal_set":
            return replace(state, dismissed_at=state.dismissed_at or now, watchlisted_at=None)
        if action == "dismissal_deleted":
            return replace(state, dismissed_at=None)
        raise AssertionError(f"unsupported feedback action: {action}")

    def _find_replay(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        request_id: UUID,
        user_id: int,
        movie_id: int,
        action: FeedbackAction,
        rating: float | None,
    ) -> MutationResult | None:
        row = connection.execute(
            text("""
                SELECT user_id, movie_id, action, outcome, new_value
                FROM user_feedback_events
                WHERE event_id = :event_id
                """),
            {"event_id": str(request_id)},
        ).one_or_none()
        if row is None:
            return None
        if int(row.user_id) != user_id or int(row.movie_id) != movie_id or row.action != action:
            raise IdempotencyConflictError("idempotency key was already used for another mutation")
        raw_outcome = str(row.outcome)
        if raw_outcome == "changed":
            replay_outcome: Literal["changed", "no_change"] = "changed"
        elif raw_outcome == "no_change":
            replay_outcome = "no_change"
        else:
            raise RuntimeError(f"invalid feedback event outcome: {raw_outcome!r}")
        snapshot = _json_object(row.new_value)
        if action == "rating_set" and _optional_float(snapshot.get("rating")) != rating:
            raise IdempotencyConflictError(
                "idempotency key was already used with a different rating"
            )
        return MutationResult(
            request_id=request_id,
            replayed=True,
            outcome=replay_outcome,
            state=_snapshot_to_state(snapshot, tenant_id=tenant_id),
        )

    def _get_state(
        self,
        connection: Connection,
        *,
        user_id: int,
        movie_id: int,
        lock: bool = False,
    ) -> MovieState | None:
        suffix = " FOR UPDATE" if lock and connection.dialect.name == "postgresql" else ""
        row = connection.execute(
            text(
                f"SELECT {_STATE_COLUMNS} FROM user_movie_state "
                f"WHERE user_id = :user_id AND movie_id = :movie_id{suffix}"
            ),
            {"user_id": user_id, "movie_id": movie_id},
        ).one_or_none()
        return _row_to_state(row) if row is not None else None

    def _library_counts(self, connection: Connection, *, user_id: int) -> LibraryCounts:
        row = connection.execute(
            text("""
                SELECT
                    SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS rated,
                    SUM(CASE WHEN watchlisted_at IS NOT NULL THEN 1 ELSE 0 END) AS watchlist,
                    SUM(CASE WHEN watched_at IS NOT NULL THEN 1 ELSE 0 END) AS history
                FROM user_movie_state
                WHERE user_id = :user_id
                """),
            {"user_id": user_id},
        ).one()
        return LibraryCounts(
            rated=int(row.rated or 0),
            watchlist=int(row.watchlist or 0),
            history=int(row.history or 0),
        )

    @staticmethod
    def _require_demo_persona(connection: Connection, *, user_id: int) -> None:
        require_demo_persona(connection, user_id=user_id)

    @staticmethod
    def _require_movie(connection: Connection, *, movie_id: int) -> None:
        exists = connection.execute(
            text('SELECT 1 FROM movies WHERE "movieId" = :movie_id'),
            {"movie_id": movie_id},
        ).scalar_one_or_none()
        if exists is None:
            raise UnknownMovieError(f"movie {movie_id} does not exist")


def _row_to_state(row: object) -> MovieState:
    return MovieState(
        tenant_id=str(getattr(row, "tenant_id")),
        user_id=int(getattr(row, "user_id")),
        movie_id=int(getattr(row, "movie_id")),
        watched_at=as_utc_datetime(getattr(row, "watched_at")),
        rating=(float(getattr(row, "rating")) if getattr(row, "rating") is not None else None),
        rating_updated_at=as_utc_datetime(getattr(row, "rating_updated_at")),
        watchlisted_at=as_utc_datetime(getattr(row, "watchlisted_at")),
        dismissed_at=as_utc_datetime(getattr(row, "dismissed_at")),
        state_version=int(getattr(row, "state_version")),
        updated_at=as_utc_datetime(getattr(row, "updated_at")) or datetime.now(UTC),
    )


def _state_parameters(state: MovieState) -> dict[str, object]:
    return {
        "tenant_id": state.tenant_id,
        "user_id": state.user_id,
        "movie_id": state.movie_id,
        "watched_at": state.watched_at,
        "rating": state.rating,
        "rating_updated_at": state.rating_updated_at,
        "watchlisted_at": state.watchlisted_at,
        "dismissed_at": state.dismissed_at,
        "state_version": state.state_version,
        "updated_at": state.updated_at,
    }


def _meaningful_state(state: MovieState) -> tuple[object, ...]:
    return (
        state.watched_at,
        state.rating,
        state.rating_updated_at,
        state.watchlisted_at,
        state.dismissed_at,
    )


def _state_snapshot(state: MovieState) -> dict[str, object]:
    snapshot = asdict(state)
    for key in (
        "watched_at",
        "rating_updated_at",
        "watchlisted_at",
        "dismissed_at",
        "updated_at",
    ):
        value = snapshot[key]
        snapshot[key] = value.isoformat() if isinstance(value, datetime) else None
    return snapshot


def _snapshot_to_state(snapshot: dict[str, object], *, tenant_id: str) -> MovieState:
    return MovieState(
        tenant_id=tenant_id,
        user_id=_required_int(snapshot["user_id"]),
        movie_id=_required_int(snapshot["movie_id"]),
        watched_at=as_utc_datetime(snapshot.get("watched_at")),
        rating=_optional_float(snapshot.get("rating")),
        rating_updated_at=as_utc_datetime(snapshot.get("rating_updated_at")),
        watchlisted_at=as_utc_datetime(snapshot.get("watchlisted_at")),
        dismissed_at=as_utc_datetime(snapshot.get("dismissed_at")),
        state_version=_required_int(snapshot["state_version"]),
        updated_at=as_utc_datetime(snapshot.get("updated_at")) or datetime.now(UTC),
    )


def _json_object(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("feedback event snapshot is not a JSON object")
    return {str(key): item for key, item in decoded.items()}


def _required_int(value: object) -> int:
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"expected an integer-compatible value, got {value!r}")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"expected a numeric value, got {value!r}")


def as_utc_datetime(value: object) -> datetime | None:
    """Whatever the driver or a JSON snapshot handed us, as aware UTC.

    Public because it is not about feedback: SQLite hands a ``DATETIME`` column
    back as text where psycopg2 hands back a ``datetime``, and every module that
    reads a timestamp off a raw row has to survive both. One coercion, so two
    modules cannot disagree about what a naive value means.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, str):
        return _ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise TypeError(f"unsupported datetime value: {value!r}")


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _split_genres(value: str) -> list[str]:
    return [] if not value or value == "(no genres listed)" else value.split("|")


def _cursor_value(value: object) -> str | float:
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return float(value) if isinstance(value, (int, float)) else str(value)


def _encode_cursor(
    *,
    tab: LibraryTab,
    sort: LibrarySort,
    query: str,
    value: str | float,
    movie_id: int,
) -> str:
    raw = json.dumps(
        {"v": 1, "tab": tab, "sort": sort, "q": query, "value": value, "movie_id": movie_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    tab: LibraryTab,
    sort: LibrarySort,
    query: str | None,
) -> tuple[str | float, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        expected_query = query.strip().lower() if query else ""
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("tab") != tab
            or payload.get("sort") != sort
            or payload.get("q") != expected_query
            or not isinstance(payload.get("movie_id"), int)
            or not isinstance(payload.get("value"), (str, int, float))
        ):
            raise ValueError
        return payload["value"], int(payload["movie_id"])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidLibraryCursorError("invalid or query-mismatched library cursor") from exc
