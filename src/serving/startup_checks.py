"""
Fail-loud startup assertions per ADR 0007 §decision + ADR 0008 §risks.

Three checks, run in ``lifespan`` before FastAPI accepts requests:

  1. **DB role doesn't have BYPASSRLS.** Non-negotiable — the app_user
     connection this app queries with must have RLS applied, not
     bypassed. A misconfigured DB_USER pointing at ``recsys`` or
     ``admin_user`` would silently defeat every ADR 0008 protection.

  2. **pgBouncer is in transaction pool mode.** Session mode would
     preserve ``SET LOCAL app.tenant_id`` across the connection's
     lifetime and cross-request-leak. Verified by connecting to
     pgBouncer's admin console and reading both ``SHOW POOLS`` (which
     reports the mode each live pool actually runs in, including a
     per-database override) and ``SHOW CONFIG`` (which reports the
     configured default, and answers even when no pool exists yet).

  3. **dev_auth_bypass is off in non-dev.** Settings.__init__ already
     asserts this at construction — this second check is redundant
     but zero-cost and defends against a future refactor that pulls
     the bypass flag out of Settings without keeping the assertion.

All three raise ``StartupCheckError`` on failure; ``lifespan`` lets
that bubble up so the app exits with a non-zero code and the
orchestrator sees a failed boot rather than a silently-broken app.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg2
from sqlalchemy import Engine, text

from src.config import Settings

logger = logging.getLogger(__name__)


class StartupCheckError(RuntimeError):
    """Any startup assertion failure. FastAPI's lifespan propagates
    this out; the process exits non-zero."""


def run_startup_checks(
    *,
    settings: Settings,
    app_engine: Engine,
) -> None:
    """Run every startup check. Raises on the first failure.

    Takes only the app engine: the serving process holds no BYPASSRLS engine
    any more, and the one assertion that ever needed an engine is the one that
    proves *this* engine's role cannot bypass RLS.
    """
    _check_app_engine_not_bypassrls(app_engine)
    _check_pgbouncer_transaction_mode(settings)
    _check_dev_bypass_only_in_dev(settings)
    logger.info("Startup checks passed.")


def _check_app_engine_not_bypassrls(engine: Engine) -> None:
    """The engine the app queries with must belong to a role that has
    ``rolbypassrls = false``. Bypassing RLS would defeat every ADR 0008
    isolation guarantee — a hostile query, a bug in a WHERE clause, or
    a missing SET LOCAL would all leak silently.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolname, rolbypassrls, rolsuper "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    if row.rolbypassrls or row.rolsuper:
        raise StartupCheckError(
            f"connected DB role '{row.rolname}' has BYPASSRLS={row.rolbypassrls} "
            f"SUPERUSER={row.rolsuper}; app_user must have neither. "
            f"Check settings.app_user_* and the DB migrations."
        )
    logger.info("Startup check: DB role '%s' has RLS applied (ok).", row.rolname)


def _check_pgbouncer_transaction_mode(settings: Settings) -> None:
    """Connect to pgBouncer's admin console (special ``pgbouncer``
    database) and assert transaction pool mode two ways: ``SHOW POOLS``
    for the mode each live pool is actually running in, and ``SHOW
    CONFIG`` for the configured default. Session mode would preserve
    SET LOCAL across transactions on the same connection and defeat
    isolation.

    Both queries run, always. ``SHOW POOLS`` alone is silent when no
    pool exists yet, and ``SHOW CONFIG`` alone cannot see a per-database
    ``pool_mode=session`` override in the ``[databases]`` section — so
    neither one is sufficient on its own.

    Uses psycopg2 directly because pgBouncer's admin protocol doesn't
    play well with SQLAlchemy's connection introspection (the
    'pgbouncer' pseudo-database doesn't accept prepared statements).
    """
    # The admin role is listed in pgbouncer.ini's admin_users; the dev
    # credentials live in infra/pgbouncer/userlist.txt and Settings
    # refuses the default password outside dev.
    # autocommit=True is required — pgBouncer's admin protocol
    # rejects BEGIN, which psycopg2 emits by default for query batches.
    conn = psycopg2.connect(
        host=settings.app_user_db_host,
        port=settings.app_user_db_port,
        user=settings.pgbouncer_admin_user,
        password=settings.pgbouncer_admin_password.get_secret_value(),
        dbname="pgbouncer",
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        _assert_live_pools_are_transaction_mode(cur)
        _assert_configured_pool_mode_is_transaction(cur)
    finally:
        conn.close()
    logger.info("Startup check: pgBouncer pool_mode = transaction (ok).")


def _assert_live_pools_are_transaction_mode(cur: Any) -> None:
    """Every pool pgBouncer currently holds must report transaction mode.
    This is what catches a per-database override that the global config
    value would not reveal.
    """
    cur.execute("SHOW POOLS")
    rows = cur.fetchall()
    cols = [d.name for d in cur.description]
    # 1.24+ ships 'pool_mode' as an explicit column. Older versions report
    # only per-mode counters, in which case there is nothing per-pool to
    # read here and the SHOW CONFIG assertion carries the check alone.
    if "pool_mode" not in cols:
        logger.info("pgBouncer SHOW POOLS has no pool_mode column; relying on SHOW CONFIG.")
        return
    mode_col = cols.index("pool_mode")
    db_col = cols.index("database")
    for row in rows:
        db_name = row[db_col]
        mode = row[mode_col]
        # The pseudo-'pgbouncer' database is the admin console itself; it
        # always reports statement mode and isn't a pool we care about.
        if db_name == "pgbouncer":
            continue
        if mode and mode != "transaction":
            raise StartupCheckError(
                f"pgBouncer pool_mode is {mode!r} on database {db_name!r}; "
                f"only 'transaction' is safe under ADR 0008. "
                f"Check infra/pgbouncer/pgbouncer.ini."
            )


def _assert_configured_pool_mode_is_transaction(cur: Any) -> None:
    """The pooler's configured default must be transaction mode, whether or
    not a pool happens to exist yet. A missing entry is a failure, not a
    pass — an admin console that cannot answer the question has not
    answered it.
    """
    cur.execute("SHOW CONFIG")
    config_rows = cur.fetchall()
    config_cols = [d.name for d in cur.description]
    key_col = config_cols.index("key")
    val_col = config_cols.index("value")
    for row in config_rows:
        if row[key_col] == "pool_mode":
            if row[val_col] != "transaction":
                raise StartupCheckError(
                    f"pgBouncer pool_mode = {row[val_col]!r}; "
                    f"only 'transaction' is safe under ADR 0008."
                )
            return
    raise StartupCheckError("pgBouncer SHOW CONFIG returned no pool_mode entry")


def _check_dev_bypass_only_in_dev(settings: Settings) -> None:
    """Redundant with Settings.__init__ but zero-cost, and defends
    against a future refactor that removes the constructor check.
    """
    if settings.dev_auth_bypass and settings.environment != "dev":
        raise StartupCheckError(
            f"dev_auth_bypass=True with environment={settings.environment!r} is "
            f"an unauthenticated production path. Non-negotiable #10."
        )
    if settings.dev_auth_bypass:
        logger.warning(
            "Startup check: dev_auth_bypass=True (dev only; every request "
            "will be treated as tenant=%s user=%s)",
            settings.dev_bypass_tenant,
            settings.dev_bypass_user,
        )


# Regex kept for future expansion (parsing a comma-separated
# ignore_startup_parameters list from SHOW CONFIG if we ever want to
# assert it explicitly).
_TRANSACTION_MODE_RE = re.compile(r"^transaction$")
