"""Durable tenant-scoped feedback projection and Library reads.

The request connection is already protected by ``SET LOCAL app.tenant_id`` and
forced RLS.  This service adds the product-level state machine, optimistic
revision checks, idempotency keys, and append-only mutation evidence required by
ADR 0012.  Imported MovieLens ``ratings`` are intentionally never mutated here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import JSON, Connection, bindparam, text

from src.serving.recommendations import UnknownDemoPersonaError, UnknownMovieError
from src.serving.sql import escape_like

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
LibrarySort = Literal["recent", "title", "rating", "release", "tmdb"]


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
class LibraryQuery:
    """One Library view: which rows, in what order, and where the page starts.

    ``limit`` and ``cursor`` are deliberately not part of what identifies the
    view — paging deeper into the same collection, or asking for a different
    page size, is the same query — which is why the fingerprint a cursor is
    bound to is built from the other six fields alone.
    """

    tab: LibraryTab = "rated"
    sort: LibrarySort = "recent"
    q: str | None = None
    genre: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 24
    cursor: str | None = None


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
    # The TMDB crowd average, from the same snapshot and the same SQL the
    # ``tmdb`` sort orders by, so a row's mark and its position cannot disagree
    # about which titles have a score. The vote count stays off the row: a
    # compact list mark has no room for it, and an average shown without its
    # count is the one thing the detail view's score copy refuses to do.
    tmdb_rating: float | None
    state: MovieState


@dataclass(frozen=True)
class LibraryCounts:
    rated: int
    watchlist: int
    history: int


@dataclass(frozen=True)
class LibraryPage:
    # The normalized query this page answers. The response echoes it rather
    # than the raw parameters, so what a client is told it asked for is exactly
    # what the fingerprint — and therefore the cursor — was built from.
    query: LibraryQuery
    items: list[LibraryMovie]
    counts: LibraryCounts
    # Rows matching the tab condition and the filters, ignoring cursor and
    # limit. Exact, never an estimate: this counts one viewer's own bounded
    # rows rather than claiming breadth about the shared catalog, and it is the
    # number the Seen spotlight's position readout would otherwise invent.
    matched: int
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

_LIBRARY_FROM = """
    FROM user_movie_state AS s
    JOIN movies AS m ON m."movieId" = s.movie_id
    LEFT JOIN movie_catalog_metadata AS cm ON cm.movie_id = s.movie_id
"""

# The TMDB score, chosen by dialect the way ``FOR UPDATE`` and the advisory
# lock already are. Both spellings answer NULL for a title with no snapshot
# row, no details, no ``tmdb_rating``, a non-numeric payload, or no votes: an
# average nobody voted for is not a score, and saying so once in SQL is what
# stops the row's mark, the ``tmdb`` ordering and the detail view from
# disagreeing about which titles have one.
_TMDB_SCORE_POSTGRESQL = """
    CASE
      WHEN jsonb_typeof(cm.details -> 'tmdb_rating' -> 'average') = 'number'
       AND jsonb_typeof(cm.details -> 'tmdb_rating' -> 'count') = 'number'
      THEN CASE
             WHEN (cm.details -> 'tmdb_rating' -> 'count')::text::numeric > 0
             THEN (cm.details -> 'tmdb_rating' -> 'average')::text::numeric
           END
    END
"""
# The two ``jsonb_typeof`` guards cannot raise and the casts only run beneath
# them, so a hand-written row that put a string where a number belongs reads as
# "no score" instead of erroring the whole page — the same tolerance
# ``CatalogService`` applies in Python, for the same reason. ``json_valid`` is
# SQLite's half of that bargain.
_TMDB_SCORE_SQLITE = """
    CASE
      WHEN cm.details IS NOT NULL
       AND json_valid(cm.details)
       AND json_type(cm.details, '$.tmdb_rating.average') IN ('integer', 'real')
       AND json_type(cm.details, '$.tmdb_rating.count') IN ('integer', 'real')
       AND json_extract(cm.details, '$.tmdb_rating.count') > 0
      THEN json_extract(cm.details, '$.tmdb_rating.average')
    END
"""

_KeyKind = Literal["text", "number"]


@dataclass(frozen=True)
class _SortKey:
    """One term of a sort's key vector, ahead of the ``movie_id`` tie-break.

    ``expression`` is always a non-null scalar: nulls are mapped to a sentinel
    below every real value rather than spelled as ``NULLS LAST``, which is what
    keeps the keyset predicate a two- or three-term comparison instead of a
    per-dialect null dance.
    """

    expression: str
    descending: bool
    kind: _KeyKind


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
        query: LibraryQuery,
    ) -> LibraryPage:
        self._require_demo_persona(connection, user_id=user_id)
        normalized = _normalize_library_query(query)
        if normalized.sort == "rating" and normalized.tab == "watchlist":
            # A watchlisted title cannot carry a star value — a rating implies
            # watched, and watched clears the watchlist — so this is refused
            # for a product reason rather than by the enum.
            raise InvalidLibraryCursorError(
                "rating sort is available only on the Rated and Seen tabs"
            )
        dialect = connection.dialect.name
        fingerprint = _query_fingerprint(normalized)
        keys = _sort_keys(tab=normalized.tab, sort=normalized.sort, dialect=dialect)
        cursor = (
            _decode_cursor(normalized.cursor, fingerprint=fingerprint, keys=keys)
            if normalized.cursor
            else None
        )

        where, filters = _row_set(user_id=user_id, query=normalized)
        score = _tmdb_score_sql(dialect)
        projection = ", ".join(
            f"{key.expression} AS cursor_key_{index}" for index, key in enumerate(keys)
        )
        order = ", ".join(
            f"cursor_key_{index} {'DESC' if key.descending else 'ASC'}"
            for index, key in enumerate(keys)
        )
        keyset = "" if cursor is None else f"WHERE {_keyset_condition(keys)}"
        page_parameters: dict[str, object] = {**filters, "limit": normalized.limit + 1}
        if cursor is not None:
            page_parameters.update(_cursor_parameters(cursor))

        # The row set is computed once in a derived table so the sort keys —
        # the TMDB score in particular — are spelled once instead of repeated
        # across the projection, the keyset predicate and the ordering.
        rows = list(
            connection.execute(
                text(f"""
                    SELECT * FROM (
                        SELECT {_ALIASED_STATE_COLUMNS}, m.title, m.genres,
                               cm.release_year, cm.poster_url,
                               {score} AS tmdb_rating,
                               {projection}
                        {_LIBRARY_FROM}
                        WHERE {where}
                    ) AS matches
                    {keyset}
                    ORDER BY {order}, movie_id ASC
                    LIMIT :limit
                    """),
                page_parameters,
            )
        )
        has_more = len(rows) > normalized.limit
        page_rows = rows[: normalized.limit]
        items = [
            LibraryMovie(
                movie_id=int(row.movie_id),
                title=str(row.title),
                genres=_split_genres(str(row.genres)),
                release_year=(int(row.release_year) if row.release_year is not None else None),
                poster_url=(str(row.poster_url) if row.poster_url is not None else None),
                tmdb_rating=(float(row.tmdb_rating) if row.tmdb_rating is not None else None),
                state=_row_to_state(row),
            )
            for row in page_rows
        ]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(
                fingerprint=fingerprint,
                values=[
                    _cursor_key(getattr(last, f"cursor_key_{index}"), kind=key.kind)
                    for index, key in enumerate(keys)
                ],
                movie_id=int(last.movie_id),
            )
        return LibraryPage(
            query=normalized,
            items=items,
            counts=self._library_counts(connection, user_id=user_id),
            matched=self._library_matched(connection, where=where, filters=filters),
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

    def _library_matched(
        self,
        connection: Connection,
        *,
        where: str,
        filters: dict[str, object],
    ) -> int:
        """Count the filtered rows, on every page rather than only the first.

        The counts above are the three whole-tab totals the tabs print and stay
        unfiltered; this is the filtered set the page is a window onto, so a
        position readout stays true after the window has been extended.
        """
        return int(
            connection.execute(
                text(f"SELECT COUNT(*) AS matched {_LIBRARY_FROM} WHERE {where}"),
                filters,
            ).scalar_one()
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


def _cursor_key(value: object, *, kind: _KeyKind) -> str | float:
    """Encode one key so it survives the decoder's own type check.

    The decoder validates a cursor's key vector against the types the sort
    declares, so a driver handing back an int where a timestamp was expected
    would otherwise mint a cursor this endpoint immediately rejects.
    """
    encoded = _cursor_value(value)
    return str(encoded) if kind == "text" else float(encoded)


def _encode_cursor(*, fingerprint: str, values: list[str | float], movie_id: int) -> str:
    raw = json.dumps(
        {"v": 2, "f": fingerprint, "k": values, "id": movie_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    fingerprint: str,
    keys: tuple[_SortKey, ...],
) -> tuple[list[str | float], int]:
    """Read a cursor, or refuse it.

    Version 1 cursors are rejected rather than translated: their key was a
    single value under a different sort vocabulary, and a link somebody kept
    from before this view existed is exactly as stale as one from another
    query. Both come back as the same 400, and the client restarts from the top.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise ValueError
        # The reuse rejection, and the whole point of the fingerprint.
        if payload.get("f") != fingerprint:
            raise ValueError
        movie_id = payload.get("id")
        if isinstance(movie_id, bool) or not isinstance(movie_id, int) or movie_id < 1:
            raise ValueError
        values = payload.get("k")
        if not isinstance(values, list) or len(values) != len(keys):
            raise ValueError
        decoded = [
            _decoded_key(value, kind=key.kind) for key, value in zip(keys, values, strict=True)
        ]
        return decoded, movie_id
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidLibraryCursorError("library cursor is invalid for this query") from exc


def _decoded_key(value: object, *, kind: _KeyKind) -> str | float:
    if isinstance(value, bool):
        raise ValueError("a boolean is not a sort key")
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError("expected a text sort key")
        return value
    if not isinstance(value, (int, float)):
        raise ValueError("expected a numeric sort key")
    return float(value)


def _normalize_library_query(query: LibraryQuery) -> LibraryQuery:
    """Normalize once, before anything else touches the parameters.

    Whitespace runs in ``q`` collapse, so "  the   thing " and "the thing" are
    the same query, the same fingerprint and therefore the same cursor. The
    genre is compared against the fixed MovieLens vocabulary and so keeps its
    case.
    """
    text_query = " ".join(query.q.split()) if query.q else None
    genre = query.genre.strip() if query.genre else None
    if query.year_from is not None and query.year_to is not None:
        if query.year_from > query.year_to:
            raise ValueError("year_from must be less than or equal to year_to")
    return replace(query, q=text_query or None, genre=genre or None)


def _query_fingerprint(query: LibraryQuery) -> str:
    payload = json.dumps(
        {
            "tab": query.tab,
            "sort": query.sort,
            "q": query.q.lower() if query.q else None,
            "genre": query.genre,
            "year_from": query.year_from,
            "year_to": query.year_to,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _row_set(*, user_id: int, query: LibraryQuery) -> tuple[str, dict[str, object]]:
    """The tab condition and the filters, as SQL and its parameters.

    A year bound is the one filter that can hide a row the tab contains: the
    release year comes from the left-joined snapshot, so a title it has never
    covered has none and cannot satisfy "between 1990 and 1999". Unfiltered,
    that row is still listed with a null year and no poster.
    """
    where = ["s.user_id = :user_id", _TAB_CONDITION[query.tab]]
    parameters: dict[str, object] = {"user_id": user_id}
    if query.q:
        where.append("lower(m.title) LIKE :query ESCAPE '\\'")
        parameters["query"] = f"%{escape_like(query.q.lower())}%"
    if query.genre:
        where.append("('|' || m.genres || '|') LIKE :genre ESCAPE '\\'")
        parameters["genre"] = f"%|{escape_like(query.genre)}|%"
    if query.year_from is not None:
        where.append("cm.release_year >= :year_from")
        parameters["year_from"] = query.year_from
    if query.year_to is not None:
        where.append("cm.release_year <= :year_to")
        parameters["year_to"] = query.year_to
    return " AND ".join(where), parameters


def _tmdb_score_sql(dialect: str) -> str:
    return _TMDB_SCORE_POSTGRESQL if dialect == "postgresql" else _TMDB_SCORE_SQLITE


def _sort_keys(*, tab: LibraryTab, sort: LibrarySort, dialect: str) -> tuple[_SortKey, ...]:
    if sort == "recent":
        # The tab condition guarantees the column, so no sentinel is reachable.
        return (_SortKey(_RECENT_COLUMN[tab], descending=True, kind="text"),)
    if sort == "title":
        return (_SortKey("lower(m.title)", descending=False, kind="text"),)
    if sort == "rating":
        # Watched date breaks ties, so equally-rated titles have one order
        # rather than an arbitrary one by id. Both tabs this sort is offered on
        # guarantee one of the two timestamps.
        return (
            _SortKey("COALESCE(s.rating, -1)", descending=True, kind="number"),
            _SortKey("COALESCE(s.watched_at, s.rating_updated_at)", descending=True, kind="text"),
        )
    if sort == "release":
        return (_SortKey("COALESCE(cm.release_year, -1)", descending=True, kind="number"),)
    return (_SortKey(f"COALESCE({_tmdb_score_sql(dialect)}, -1)", descending=True, kind="number"),)


def _keyset_condition(keys: tuple[_SortKey, ...]) -> str:
    """The keyset predicate for a page that starts after the cursor's row.

    Read it as a lexicographic "strictly after": each term fixes the keys
    before it and advances the next one, and the last term falls through to the
    ``movie_id`` tie-break that makes every one of these sorts total.
    """
    terms = []
    for index, key in enumerate(keys):
        comparison = "<" if key.descending else ">"
        terms.append(
            " AND ".join(
                [
                    *(f"cursor_key_{before} = :cursor_key_{before}" for before in range(index)),
                    f"cursor_key_{index} {comparison} :cursor_key_{index}",
                ]
            )
        )
    terms.append(
        " AND ".join(
            [
                *(f"cursor_key_{index} = :cursor_key_{index}" for index in range(len(keys))),
                "movie_id > :cursor_movie_id",
            ]
        )
    )
    return "(" + " OR ".join(f"({term})" for term in terms) + ")"


def _cursor_parameters(cursor: tuple[list[str | float], int]) -> dict[str, object]:
    values, movie_id = cursor
    parameters: dict[str, object] = {"cursor_movie_id": movie_id}
    for index, value in enumerate(values):
        parameters[f"cursor_key_{index}"] = value
    return parameters
