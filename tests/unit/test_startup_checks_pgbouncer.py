"""The pgBouncer transaction-pool-mode startup check (ADR 0008 §risks).

Session mode preserves ``SET LOCAL app.tenant_id`` past the transaction that
set it, so the next request served on that pooled connection reads another
tenant's rows — the highest-severity bug class this project has. The check that
refuses to boot in that case is worth its own tests, and both halves of it need
one: ``SHOW POOLS`` sees a per-database override that the global config value
hides, and ``SHOW CONFIG`` answers even when no pool exists yet.

The admin console is faked rather than reached: these tests are about the
decision the check makes from what pgBouncer reports, and a live pooler is
already exercised by ``tests/tenant_isolation/``.
"""

from __future__ import annotations

from typing import Any

import psycopg2
import pytest

from src.config import Settings
from src.serving import startup_checks
from src.serving.startup_checks import StartupCheckError, _check_pgbouncer_transaction_mode

# What pgBouncer 1.24 reports for a healthy dev stack: the app pool plus the
# admin console's own pseudo-database, which always reads "statement".
_HEALTHY_POOLS = (
    ["database", "user", "pool_mode", "cl_active"],
    [
        ["movielens_app", "app_user", "transaction", 1],
        ["pgbouncer", "pgbouncer_admin", "statement", 1],
    ],
)
_HEALTHY_CONFIG = (
    ["key", "value", "default", "changeable"],
    [
        ["max_client_conn", "100", "100", "yes"],
        ["pool_mode", "transaction", "session", "yes"],
    ],
)


class _FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCursor:
    """Answers exactly the two admin queries the check runs."""

    def __init__(self, responses: dict[str, tuple[list[str], list[list[Any]]]]) -> None:
        self._responses = responses
        self._columns: list[str] = []
        self._rows: list[list[Any]] = []
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if sql not in self._responses:
            raise AssertionError(f"unexpected admin query: {sql!r}")
        self._columns, self._rows = self._responses[sql]

    def fetchall(self) -> list[list[Any]]:
        return self._rows

    @property
    def description(self) -> list[_FakeColumn]:
        return [_FakeColumn(name) for name in self._columns]


class _FakeConnection:
    def __init__(self, responses: dict[str, tuple[list[str], list[list[Any]]]]) -> None:
        self.cursor_obj = _FakeCursor(responses)
        self.autocommit = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for var in ("ENVIRONMENT", "MODEL_SERVER_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return Settings(
        _env_file=None,
        app_user_db_host="pgbouncer.internal",
        app_user_db_port=6432,
        pgbouncer_admin_user="pooler_admin",
        pgbouncer_admin_password="pooler-secret",
    )


def _install_fake_console(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pools: tuple[list[str], list[list[Any]]],
    config: tuple[list[str], list[list[Any]]],
) -> tuple[dict[str, Any], _FakeConnection]:
    """Replace psycopg2.connect with a fake admin console. Returns the kwargs
    it was called with and the connection it handed back."""
    captured: dict[str, Any] = {}
    connection = _FakeConnection({"SHOW POOLS": pools, "SHOW CONFIG": config})

    def fake_connect(**kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    return captured, connection


def test_passes_when_pools_and_config_both_report_transaction(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, connection = _install_fake_console(monkeypatch, pools=_HEALTHY_POOLS, config=_HEALTHY_CONFIG)

    _check_pgbouncer_transaction_mode(settings)

    assert connection.cursor_obj.executed == ["SHOW POOLS", "SHOW CONFIG"]
    assert connection.autocommit is True
    assert connection.closed is True


def test_connects_with_the_configured_admin_credentials(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The credential used to be hardcoded to the dev literals, which meant a
    # production pooler had to keep the published password to boot at all.
    captured, _ = _install_fake_console(monkeypatch, pools=_HEALTHY_POOLS, config=_HEALTHY_CONFIG)

    _check_pgbouncer_transaction_mode(settings)

    assert captured == {
        "host": "pgbouncer.internal",
        "port": 6432,
        "user": "pooler_admin",
        "password": "pooler-secret",
        "dbname": "pgbouncer",
    }


def test_refuses_a_session_mode_pool(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    pools = (
        _HEALTHY_POOLS[0],
        [
            ["movielens_app", "app_user", "session", 1],
            ["pgbouncer", "pgbouncer_admin", "statement", 1],
        ],
    )
    _, connection = _install_fake_console(monkeypatch, pools=pools, config=_HEALTHY_CONFIG)

    with pytest.raises(StartupCheckError, match="movielens_app"):
        _check_pgbouncer_transaction_mode(settings)

    assert connection.closed is True


def test_refuses_session_mode_config_even_when_no_app_pool_exists(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The case the unconditional SHOW CONFIG closes: with only the admin
    # console's own pool open, SHOW POOLS has nothing to object to and the
    # check used to pass a session-mode pooler.
    pools = (_HEALTHY_POOLS[0], [["pgbouncer", "pooler_admin", "statement", 1]])
    config = (
        _HEALTHY_CONFIG[0],
        [["pool_mode", "session", "session", "yes"]],
    )
    _install_fake_console(monkeypatch, pools=pools, config=config)

    with pytest.raises(StartupCheckError, match="session"):
        _check_pgbouncer_transaction_mode(settings)


def test_refuses_a_console_that_reports_no_pool_mode_at_all(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An admin console that cannot answer the question has not answered it.
    config = (_HEALTHY_CONFIG[0], [["max_client_conn", "100", "100", "yes"]])
    _install_fake_console(monkeypatch, pools=_HEALTHY_POOLS, config=config)

    with pytest.raises(StartupCheckError, match="no pool_mode entry"):
        _check_pgbouncer_transaction_mode(settings)


def test_older_pgbouncer_without_a_pool_mode_column_falls_back_to_config(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-1.24 consoles report per-mode counters instead of a pool_mode
    # column; there is nothing per-pool to read, and SHOW CONFIG decides.
    pools = (["database", "user", "cl_active", "sv_used"], [["movielens_app", "app_user", 1, 1]])
    _install_fake_console(monkeypatch, pools=pools, config=_HEALTHY_CONFIG)

    _check_pgbouncer_transaction_mode(settings)


def test_older_pgbouncer_still_refuses_session_mode(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    pools = (["database", "user", "cl_active", "sv_used"], [["movielens_app", "app_user", 1, 1]])
    config = (_HEALTHY_CONFIG[0], [["pool_mode", "session", "session", "yes"]])
    _install_fake_console(monkeypatch, pools=pools, config=config)

    with pytest.raises(StartupCheckError, match="session"):
        _check_pgbouncer_transaction_mode(settings)


def test_check_is_wired_into_run_startup_checks() -> None:
    # Cheap guard against the check being dropped from the boot sequence in a
    # refactor: a startup check nobody calls protects nothing.
    called_names = startup_checks.run_startup_checks.__code__.co_names
    assert "_check_pgbouncer_transaction_mode" in called_names
