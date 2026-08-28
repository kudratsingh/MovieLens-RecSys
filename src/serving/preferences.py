"""Per-persona presentation preferences, on the RLS-bound request connection.

One row per (tenant, user), holding choices about *what a viewer is shown* —
never about what the recommender learns.  The distinction is the whole reason
this module is separate from ``feedback.py``: a watched title is a signal, a
dismissal is an exclusion, and a preference is neither.  Nothing in
``src/serving/orchestration.py`` or ``recommendations.py`` reads this table, and
nothing should start: turning a presentation preference into a serving filter
would make the audit's exclusion digest disagree with what the API actually
sent (ADR 0012's 2026-08-28 note).

The write path is deliberately the same shape as a movie-state mutation —
absolute full-object PUT, optimistic ``expected_revision``, a revision that only
moves on a real change — so a client that already knows how to write state does
not need a second set of rules.  It carries no idempotency-key replay log,
because it does not need one: setting the whole object twice is the same request
twice, and the second is reported as ``no_change`` rather than applied again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Connection, text

from src.serving.feedback import (
    StateRevisionConflictError,
    as_utc_datetime,
    require_demo_persona,
)

#: What a persona that has never written a preference is shown. Named rather
#: than inlined so the API default, the migration's server default, and the
#: frontend's fallback can all be checked against one value.
DEFAULT_FEATURE_WATCHLISTED_TITLES = True

# SQLite (the unit-test dialect) has no boolean parameter literal that Postgres
# also accepts, so the default is spelled once here from the same constant the
# API answers with rather than written twice as ``1`` and ``TRUE``.
_DEFAULT_LITERAL = "TRUE" if DEFAULT_FEATURE_WATCHLISTED_TITLES else "FALSE"


@dataclass(frozen=True)
class UserPreferences:
    tenant_id: str
    user_id: int
    feature_watchlisted_titles: bool
    revision: int
    #: ``None`` for a persona with no stored row. A default is not an edit, and
    #: inventing "now" as its timestamp would let a client show a change nobody
    #: made.
    updated_at: datetime | None


@dataclass(frozen=True)
class PreferencesMutationResult:
    outcome: Literal["changed", "no_change"]
    preferences: UserPreferences


_COLUMNS = "tenant_id, user_id, feature_watchlisted_titles, revision, updated_at"


class PreferencesService:
    """Read and write one persona's presentation preferences."""

    def get(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        user_id: int,
    ) -> UserPreferences:
        require_demo_persona(connection, user_id=user_id)
        row = self._row(connection, user_id=user_id)
        if row is not None:
            return row
        return UserPreferences(
            tenant_id=tenant_id,
            user_id=user_id,
            feature_watchlisted_titles=DEFAULT_FEATURE_WATCHLISTED_TITLES,
            revision=0,
            updated_at=None,
        )

    def set(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        actor_user_id: str,
        user_id: int,
        feature_watchlisted_titles: bool,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> PreferencesMutationResult:
        require_demo_persona(connection, user_id=user_id)
        event_time = as_utc_datetime(now) or datetime.now(UTC)

        # Materialize the row at revision 0 before locking it, exactly as the
        # feedback path does: a first write and a tenth write then take the
        # same code, and ``expected_revision=0`` means the same thing on both.
        connection.execute(
            text(f"""
                INSERT INTO user_preferences (
                    tenant_id, user_id, feature_watchlisted_titles, revision,
                    updated_by_actor, updated_at
                ) VALUES (
                    :tenant_id, :user_id, {_DEFAULT_LITERAL}, 0, '', :updated_at
                )
                ON CONFLICT (tenant_id, user_id) DO NOTHING
                """),
            {"tenant_id": tenant_id, "user_id": user_id, "updated_at": event_time},
        )
        current = self._row(connection, user_id=user_id, lock=True)
        if current is None:  # pragma: no cover - an RLS/constraint invariant
            raise RuntimeError("preferences row was not visible after insert")
        if expected_revision is not None and current.revision != expected_revision:
            # Same sentence shape as the movie-state conflict, so a client can
            # tell a lost race from a refused transition by the same rule.
            raise StateRevisionConflictError(
                f"state revision {expected_revision} is stale; current revision is "
                f"{current.revision}"
            )

        changed = current.feature_watchlisted_titles != feature_watchlisted_titles
        if not changed:
            return PreferencesMutationResult(outcome="no_change", preferences=current)

        connection.execute(
            text("""
                UPDATE user_preferences
                SET feature_watchlisted_titles = :feature_watchlisted_titles,
                    revision = :revision,
                    updated_by_actor = :actor,
                    updated_at = :updated_at
                WHERE user_id = :user_id
                """),
            {
                "feature_watchlisted_titles": feature_watchlisted_titles,
                "revision": current.revision + 1,
                "actor": actor_user_id,
                "updated_at": event_time,
                "user_id": user_id,
            },
        )
        return PreferencesMutationResult(
            outcome="changed",
            preferences=UserPreferences(
                tenant_id=current.tenant_id,
                user_id=user_id,
                feature_watchlisted_titles=feature_watchlisted_titles,
                revision=current.revision + 1,
                updated_at=event_time,
            ),
        )

    def _row(
        self,
        connection: Connection,
        *,
        user_id: int,
        lock: bool = False,
    ) -> UserPreferences | None:
        suffix = " FOR UPDATE" if lock and connection.dialect.name == "postgresql" else ""
        row = connection.execute(
            text(f"SELECT {_COLUMNS} FROM user_preferences " f"WHERE user_id = :user_id{suffix}"),
            {"user_id": user_id},
        ).one_or_none()
        if row is None:
            return None
        return UserPreferences(
            tenant_id=str(row.tenant_id),
            user_id=int(row.user_id),
            feature_watchlisted_titles=bool(row.feature_watchlisted_titles),
            revision=int(row.revision),
            updated_at=as_utc_datetime(row.updated_at),
        )
