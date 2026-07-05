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
     pgBouncer's admin console and reading ``SHOW POOLS``.

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
    admin_engine: Engine,
) -> None:
    """Run all Bundle #1b startup checks. Raises on the first failure."""
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
    database) and run ``SHOW POOLS`` — every entry's mode field must
    read 'transaction'. Session mode would preserve SET LOCAL across
    transactions on the same connection and defeat isolation.

    Uses psycopg2 directly because pgBouncer's admin protocol doesn't
    play well with SQLAlchemy's connection introspection (the
    'pgbouncer' pseudo-database doesn't accept prepared statements).
    """
    # pgbouncer_admin is defined in infra/pgbouncer/userlist.txt +
    # pgbouncer.ini's admin_users list; trust auth in dev.
    # autocommit=True is required — pgBouncer's admin protocol
    # rejects BEGIN, which psycopg2 emits by default for query batches.
    conn = psycopg2.connect(
        host=settings.app_user_db_host,
        port=settings.app_user_db_port,
        user="pgbouncer_admin",
        password="pgbouncer_admin",
        dbname="pgbouncer",
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SHOW POOLS")
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
        # Column names differ across pgBouncer versions; find the one
        # that names a pool mode.
        mode_col = None
        for candidate in ("pool_mode", "sv_used"):
            if candidate in cols:
                mode_col = cols.index(candidate)
                break
        # 1.24+ ships 'pool_mode' as an explicit column. Older versions
        # split into per-mode counters. Fall back to a config query
        # if the column isn't there.
        if mode_col is not None:
            db_col = cols.index("database")
            for row in rows:
                db_name = row[db_col]
                mode = row[mode_col]
                # The pseudo-'pgbouncer' database is the admin console
                # itself; it always reports statement mode and isn't a
                # pool we care about. Skip it.
                if db_name == "pgbouncer":
                    continue
                if mode and mode != "transaction":
                    raise StartupCheckError(
                        f"pgBouncer pool_mode is {mode!r} on database {db_name!r}; "
                        f"only 'transaction' is safe under ADR 0008. "
                        f"Check infra/pgbouncer/pgbouncer.ini."
                    )
        else:
            cur.execute("SHOW CONFIG")
            config_rows = cur.fetchall()
            config_cols = [d.name for d in cur.description]
            key_col = config_cols.index("key")
            val_col = config_cols.index("value")
            found = False
            for row in config_rows:
                if row[key_col] == "pool_mode":
                    found = True
                    if row[val_col] != "transaction":
                        raise StartupCheckError(
                            f"pgBouncer pool_mode = {row[val_col]!r}; "
                            f"only 'transaction' is safe under ADR 0008."
                        )
                    break
            if not found:
                raise StartupCheckError(
                    "pgBouncer SHOW CONFIG returned no pool_mode entry"
                )
    finally:
        conn.close()
    logger.info("Startup check: pgBouncer pool_mode = transaction (ok).")


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
