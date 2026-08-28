from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, text

from src.serving.feedback import StateRevisionConflictError
from src.serving.preferences import (
    DEFAULT_FEATURE_WATCHLISTED_TITLES,
    PreferencesService,
)
from src.serving.recommendations import UnknownDemoPersonaError
from tests.unit.test_serving_recommendations import _connection

TENANT = "demo"
ACTOR = "oidc-actor"
USER = 900000101
START = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _preferences_connection() -> Connection:
    """The shared serving fixture plus the table this migration adds.

    Declared here rather than in the shared helper because nothing else reads
    it: preferences are presentation state and no serving path consumes them,
    which is a property worth keeping visible in the test setup too.
    """
    connection = _connection()
    connection.execute(
        text(
            "CREATE TABLE user_preferences ("
            "tenant_id TEXT NOT NULL, user_id INTEGER NOT NULL, "
            "feature_watchlisted_titles BOOLEAN NOT NULL DEFAULT TRUE, "
            "revision INTEGER NOT NULL DEFAULT 0, "
            "updated_by_actor TEXT NOT NULL DEFAULT '', "
            "updated_at DATETIME NOT NULL, PRIMARY KEY (tenant_id, user_id))"
        )
    )
    return connection


def _set(
    service: PreferencesService,
    connection: Connection,
    *,
    value: bool,
    minute: int,
    expected_revision: int | None = None,
    user_id: int = USER,
):
    return service.set(
        connection,
        tenant_id=TENANT,
        actor_user_id=ACTOR,
        user_id=user_id,
        feature_watchlisted_titles=value,
        expected_revision=expected_revision,
        now=START + timedelta(minutes=minute),
    )


def test_an_untouched_persona_reads_the_documented_default() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        preferences = service.get(connection, tenant_id=TENANT, user_id=USER)
    finally:
        connection.close()

    assert preferences.feature_watchlisted_titles is DEFAULT_FEATURE_WATCHLISTED_TITLES
    assert preferences.feature_watchlisted_titles is True
    assert preferences.revision == 0
    # A default is not an edit, so there is no time at which it was made.
    assert preferences.updated_at is None


def test_a_write_moves_the_revision_and_is_readable_back() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        written = _set(service, connection, value=False, minute=0, expected_revision=0)
        read_back = service.get(connection, tenant_id=TENANT, user_id=USER)
    finally:
        connection.close()

    assert written.outcome == "changed"
    assert written.preferences.feature_watchlisted_titles is False
    assert written.preferences.revision == 1
    assert read_back.feature_watchlisted_titles is False
    assert read_back.revision == 1
    assert read_back.updated_at == START


def test_writing_the_same_object_twice_is_the_same_request_twice() -> None:
    """The full-object PUT is its own idempotency: no key, no replay log.

    A repeat has to be reported as a repeat rather than applied again, because
    a revision that moved on an unchanged value would make every client's next
    write stale for no reason.
    """
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        first = _set(service, connection, value=False, minute=0)
        second = _set(service, connection, value=False, minute=1)
    finally:
        connection.close()

    assert first.outcome == "changed"
    assert second.outcome == "no_change"
    assert second.preferences.revision == first.preferences.revision
    assert second.preferences.updated_at == START


def test_a_stale_revision_is_refused_rather_than_overwritten() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        _set(service, connection, value=False, minute=0, expected_revision=0)
        with pytest.raises(StateRevisionConflictError) as conflict:
            _set(service, connection, value=True, minute=1, expected_revision=0)
        survivor = service.get(connection, tenant_id=TENANT, user_id=USER)
    finally:
        connection.close()

    # The message shape is the movie-state one on purpose: a client tells a lost
    # race from a refused transition by the same rule on both endpoints.
    assert str(conflict.value).startswith("state revision 0 is stale")
    assert survivor.feature_watchlisted_titles is False
    assert survivor.revision == 1


def test_a_write_with_no_expectation_still_lands() -> None:
    """The revision is optional so a caller with no rendered value can write."""
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        result = _set(service, connection, value=False, minute=0)
    finally:
        connection.close()

    assert result.outcome == "changed"
    assert result.preferences.revision == 1


def test_the_actor_that_changed_it_is_recorded() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        _set(service, connection, value=False, minute=0)
        actor = connection.execute(
            text("SELECT updated_by_actor FROM user_preferences WHERE user_id = :user_id"),
            {"user_id": USER},
        ).scalar_one()
    finally:
        connection.close()

    # The OIDC subject that made the change, not the persona it was made for.
    assert actor == ACTOR


def test_both_operations_refuse_a_user_that_is_not_a_writable_persona() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        with pytest.raises(UnknownDemoPersonaError):
            service.get(connection, tenant_id=TENANT, user_id=424242)
        with pytest.raises(UnknownDemoPersonaError):
            _set(service, connection, value=False, minute=0, user_id=424242)
        rows = connection.execute(text("SELECT COUNT(*) FROM user_preferences")).scalar_one()
    finally:
        connection.close()

    # The guard runs before the row is materialized, so a refused write leaves
    # nothing behind for the next reader to find.
    assert rows == 0


def test_each_persona_holds_its_own_setting() -> None:
    connection = _preferences_connection()
    service = PreferencesService()
    try:
        _set(service, connection, value=False, minute=0)
        other = service.get(connection, tenant_id=TENANT, user_id=900000102)
    finally:
        connection.close()

    assert other.feature_watchlisted_titles is True
    assert other.revision == 0
