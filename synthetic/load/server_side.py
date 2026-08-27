"""Export what the server itself recorded while the load gate was measuring.

k6 measures from outside: it sees the whole trip, including auth resolution,
the transaction the middleware opens, the RLS ``SET LOCAL``, the audit INSERT,
and the COMMIT that ends it. The audit row measures from inside: its
``latency_ms`` is timed around the handler alone. Neither number alone can say
where a tail lives, but the *difference* between them can — a p99 that is 40 ms
on the client and 8 ms in the handler puts 32 ms somewhere outside the handler,
which is the shared path every traffic class runs through, cold included.

So this pulls back the audit rows for one window plus the Postgres counters
that describe what the storage under them was doing: WAL syncs and the time
spent in them, checkpoints, and block read/write time. Taken once before the
window and once after, the difference is the window's own cost.

``--snapshot-only`` takes the counters without the rows, which is how the
before-side is captured.

This is an *evidence export*, not a serving path: it connects with the admin
(BYPASSRLS) role because it deliberately reads every tenant's audit rows for a
window that only synthetic load traffic wrote. Nothing here is reachable from
the API, and no result is returned to a caller — it writes a file the gate's
artifact carries. Tenant-scoped reads of the same table remain the RLS-bound
``GET /users/{id}/audits`` path.

Writes JSON to stdout by default so a Make/shell caller can redirect it into
the window directory; logs go to stderr so that redirect stays clean.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

from src.config import Settings

# One row per recommendation request. `created_at` defaults to now() — the
# transaction's start — so bucketing rows by it lines them up with the second
# the request was being served in, which is what the per-second table needs.
AUDIT_SQL = """
    SELECT
        created_at,
        latency_ms,
        candidate_latency_ms,
        feature_latency_ms,
        ranker_latency_ms,
        model_latency_ms,
        policy,
        fallback_reason,
        user_id,
        outcome,
        http_status
    FROM recommendation_audits
    WHERE created_at >= :since
    ORDER BY created_at
"""

# wal_write_time / wal_sync_time are only populated with track_wal_io_timing on
# (set in docker-compose.yml); without it they stay at zero and the counts
# still tell you how many syncs the window cost.
WAL_SQL = """
    SELECT wal_records, wal_bytes, wal_write, wal_sync, wal_write_time, wal_sync_time
    FROM pg_stat_wal
"""

BGWRITER_SQL = """
    SELECT
        checkpoints_timed,
        checkpoints_req,
        checkpoint_write_time,
        checkpoint_sync_time,
        buffers_checkpoint,
        buffers_backend
    FROM pg_stat_bgwriter
"""

# blk_read_time / blk_write_time need track_io_timing on, same as above.
DATABASE_SQL = """
    SELECT xact_commit, blks_read, blks_hit, blk_read_time, blk_write_time
    FROM pg_stat_database
    WHERE datname = :database
"""

AUDIT_FLOAT_COLUMNS = (
    "latency_ms",
    "candidate_latency_ms",
    "feature_latency_ms",
    "ranker_latency_ms",
    "model_latency_ms",
)

# Every query goes through this so the shaping can be exercised without a live
# database: the tests hand `collect` a stub that returns canned rows.
Fetch = Callable[[str, Mapping[str, Any]], list[Mapping[str, Any]]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export one load window's server-side evidence.")
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp; audit rows at or after it are exported.",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Take the Postgres counters without the audit rows (the before-side).",
    )
    parser.add_argument("--output", type=Path, default=None, help="Defaults to stdout.")
    args = parser.parse_args(argv)

    since = _parse_since(args.since)
    settings = Settings()
    engine = create_engine(settings.admin_user_database_url, future=True)
    try:
        payload = collect(
            _engine_fetch(engine),
            since=since,
            snapshot_only=bool(args.snapshot_only),
            database=settings.admin_user_db_name,
        )
    finally:
        engine.dispose()

    rendered = json.dumps(payload, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered)
    print(
        f"[server-side] {len(payload['rows'])} audit rows, "
        f"snapshot_only={payload['snapshot_only']}",
        file=sys.stderr,
    )
    return 0


def collect(
    fetch: Fetch,
    *,
    since: datetime | None,
    snapshot_only: bool = False,
    database: str = "movielens",
) -> dict[str, Any]:
    """Assemble one export. Nothing here raises: missing evidence is recorded.

    A counter view that does not exist on this Postgres, or a permission the
    role turns out not to have, must not fail the load gate — the gate's
    verdict is k6's, and this is the material that explains it.
    """
    payload: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "since": since.isoformat() if since is not None else None,
        "snapshot_only": snapshot_only,
        "rows": [],
        "stats": {},
    }
    if not snapshot_only and since is not None:
        rows, note = _try(lambda: [_shape_audit(row) for row in fetch(AUDIT_SQL, {"since": since})])
        payload["rows"] = rows or []
        if note is not None:
            payload["rows_unavailable"] = note
    payload["stats"] = {
        "wal": _stat(fetch, WAL_SQL, {}),
        "bgwriter": _stat(fetch, BGWRITER_SQL, {}),
        "database": _stat(fetch, DATABASE_SQL, {"database": database}),
    }
    return payload


def _stat(fetch: Fetch, sql: str, params: Mapping[str, Any]) -> dict[str, Any]:
    rows, note = _try(lambda: list(fetch(sql, params)))
    if note is not None:
        return {"unavailable": note}
    if not rows:
        return {"unavailable": "no row"}
    return {key: _number(value) for key, value in dict(rows[0]).items()}


def _try(action: Callable[[], list[Any]]) -> tuple[list[Any] | None, str | None]:
    try:
        return action(), None
    except Exception as error:
        # Broad on purpose: this file exists to explain a failure, so any
        # reason it cannot is itself evidence rather than a reason to stop.
        return None, f"{type(error).__name__}: {error}"


def _shape_audit(row: Mapping[str, Any]) -> dict[str, Any]:
    shaped: dict[str, Any] = {"created_at": _iso_utc(row.get("created_at"))}
    for column in AUDIT_FLOAT_COLUMNS:
        shaped[column] = _optional_float(row.get(column))
    shaped["policy"] = _optional_str(row.get("policy"))
    shaped["fallback_reason"] = _optional_str(row.get("fallback_reason"))
    shaped["user_id"] = _optional_int(row.get("user_id"))
    shaped["outcome"] = _optional_str(row.get("outcome"))
    shaped["http_status"] = _optional_int(row.get("http_status"))
    return shaped


def _iso_utc(value: Any) -> str | None:
    if isinstance(value, datetime):
        # Postgres hands back an aware timestamp; a naive one can only come
        # from a stub, and treating it as UTC keeps the export comparable.
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat()
    return str(value) if value is not None else None


def _number(value: Any) -> Any:
    if isinstance(value, Decimal):
        # pg_stat_wal.wal_bytes is numeric, which json cannot serialize.
        return float(value)
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text_value = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    parsed = datetime.fromisoformat(text_value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _engine_fetch(engine: Engine) -> Fetch:
    def fetch(sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        with engine.connect() as connection:
            result = connection.execute(text(sql), dict(params))
            return [dict(mapping) for mapping in result.mappings()]

    return fetch


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
