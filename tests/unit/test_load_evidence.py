"""The two evidence collectors the load gate runs around each window.

Neither needs a live device or a live database: the probe's percentile summary
is arithmetic, and the export's SQL is behind a callable the tests stub. What
is worth pinning is the shaping — an export that silently changes shape breaks
`summarize.py` a week later, in a job that only runs when something is already
wrong.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from synthetic.load import probe_disk_fsync, server_side

# --- probe_disk_fsync -------------------------------------------------------


def test_summary_reports_nearest_rank_percentiles() -> None:
    summary = probe_disk_fsync.summarize([float(value) for value in range(1, 101)])
    assert summary["ops"] == 100
    assert summary["p50_ms"] == 50.0
    assert summary["p95_ms"] == 95.0
    assert summary["p99_ms"] == 99.0
    assert summary["max_ms"] == 100.0


def test_summary_of_a_single_sample_is_that_sample() -> None:
    summary = probe_disk_fsync.summarize([7.5])
    assert summary["p50_ms"] == summary["p99_ms"] == summary["max_ms"] == 7.5


def test_summary_of_nothing_reports_nothing_rather_than_zero() -> None:
    # Zero milliseconds and "no measurement" are different findings.
    summary = probe_disk_fsync.summarize([])
    assert summary["ops"] == 0
    assert summary["p99_ms"] is None


def test_probe_writes_a_baseline_then_samples_and_removes_its_file(tmp_path: Path) -> None:
    probe_file = tmp_path / "probe"
    output = tmp_path / "disk-fsync.jsonl"
    probe_disk_fsync.main(
        [
            "--path",
            str(probe_file),
            "--output",
            str(output),
            "--interval",
            "0.02",
            "--max-seconds",
            "0.2",
            "--baseline-ops",
            "5",
        ]
    )
    records = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
    assert records[0]["kind"] == "baseline"
    assert records[0]["ops"] == 5
    assert records[0]["block_bytes"] == probe_disk_fsync.BLOCK_BYTES
    samples = records[1:]
    assert samples, "the probe recorded no samples"
    assert all("ts" in sample and "fdatasync_ms" in sample for sample in samples)
    # The probe cleans up after itself: a file left behind inside Postgres's
    # data directory would outlive the run that created it.
    assert not probe_file.exists()


def test_once_writes_the_burst_and_stops(tmp_path: Path) -> None:
    # The default shape in the gate: a burst before the window opens and no
    # sampling inside it, because sampling inside it moves the measurement.
    output = tmp_path / "disk-fsync.jsonl"
    probe_disk_fsync.main(
        [
            "--path",
            str(tmp_path / "probe"),
            "--output",
            str(output),
            "--baseline-ops",
            "5",
            "--once",
        ]
    )
    records = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["kind"] == "baseline"


def test_probe_records_why_it_could_not_run(tmp_path: Path) -> None:
    output = tmp_path / "disk-fsync.jsonl"
    assert (
        probe_disk_fsync.main(
            ["--path", str(tmp_path / "missing" / "probe"), "--output", str(output)]
        )
        == 0
    )
    record = json.loads(output.read_text().splitlines()[0])
    assert record["available"] is False
    assert record["reason"]


# --- server_side ------------------------------------------------------------


def _stub_fetch(
    *,
    rows: list[dict[str, Any]] | None = None,
    failing: set[str] | None = None,
) -> server_side.Fetch:
    failing = failing or set()

    def fetch(sql: str, params: Any) -> list[dict[str, Any]]:
        if "recommendation_audits" in sql:
            if "audits" in failing:
                raise RuntimeError("permission denied for table recommendation_audits")
            return list(rows or [])
        if "pg_stat_wal" in sql:
            if "wal" in failing:
                raise RuntimeError('relation "pg_stat_wal" does not exist')
            return [
                {
                    "wal_records": 1200,
                    "wal_bytes": Decimal("98765"),
                    "wal_write": 300,
                    "wal_sync": 300,
                    "wal_write_time": 12.5,
                    "wal_sync_time": 880.25,
                }
            ]
        if "pg_stat_bgwriter" in sql:
            return [{"checkpoints_timed": 3, "checkpoints_req": 0}]
        return [{"xact_commit": 4000, "blk_write_time": 17.0, "datname": params["database"]}]

    return fetch


def _row(created_at: Any, policy: str = "popularity") -> dict[str, Any]:
    return {
        "created_at": created_at,
        "latency_ms": 8.25,
        "candidate_latency_ms": 1.0,
        "feature_latency_ms": 2.0,
        "ranker_latency_ms": 3.0,
        "model_latency_ms": 4.0,
        "policy": policy,
        "fallback_reason": None,
        "user_id": 900000101,
        "outcome": "success",
        "http_status": 200,
    }


def test_export_shapes_rows_for_the_summarizer() -> None:
    since = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    payload = server_side.collect(
        _stub_fetch(rows=[_row(datetime(2026, 8, 26, 12, 0, 30, tzinfo=UTC))]),
        since=since,
        database="movielens",
    )
    (row,) = payload["rows"]
    assert row["created_at"] == "2026-08-26T12:00:30+00:00"
    assert row["latency_ms"] == 8.25
    assert row["model_latency_ms"] == 4.0
    assert row["policy"] == "popularity"
    assert row["fallback_reason"] is None
    assert row["http_status"] == 200
    assert payload["since"] == since.isoformat()
    assert json.loads(json.dumps(payload))  # the export has to survive a round trip


def test_naive_timestamps_are_read_as_utc() -> None:
    payload = server_side.collect(
        _stub_fetch(rows=[_row(datetime(2026, 8, 26, 12, 0, 30))]),
        since=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )
    assert payload["rows"][0]["created_at"].endswith("+00:00")


def test_numeric_counters_become_json_safe() -> None:
    payload = server_side.collect(_stub_fetch(), since=None, snapshot_only=True)
    assert payload["stats"]["wal"]["wal_bytes"] == 98765.0
    assert payload["stats"]["wal"]["wal_sync_time"] == 880.25
    assert payload["stats"]["database"]["xact_commit"] == 4000


def test_snapshot_only_takes_counters_without_rows() -> None:
    payload = server_side.collect(
        _stub_fetch(rows=[_row(datetime(2026, 8, 26, 12, 0, 30, tzinfo=UTC))]),
        since=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        snapshot_only=True,
    )
    assert payload["rows"] == []
    assert payload["snapshot_only"] is True
    assert payload["stats"]["wal"]["wal_sync"] == 300


def test_a_failing_query_is_recorded_not_raised() -> None:
    payload = server_side.collect(
        _stub_fetch(failing={"wal", "audits"}),
        since=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )
    assert payload["rows"] == []
    assert "permission denied" in payload["rows_unavailable"]
    assert "does not exist" in payload["stats"]["wal"]["unavailable"]
    # The sections that did work are still there — a partial export is the
    # point, since this file only ever runs when something already went wrong.
    assert payload["stats"]["bgwriter"]["checkpoints_timed"] == 3


def test_since_accepts_the_shell_wrapper_s_timestamp_format() -> None:
    parsed = server_side._parse_since("2026-08-26T12:00:00Z")
    assert parsed == datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
