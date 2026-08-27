"""The load gate's breakdown, including the storage and server-side evidence.

The point of these is narrow: the new evidence has to *appear* when it is
there, has to say "n/a" when it is not, and must never move the re-measure
verdict in either case. That last one is the one worth pinning — the whole
value of ADR 0010's rule is that nothing quietly widened it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthetic.load import summarize

BASE = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
BASE_EPOCH = int(BASE.timestamp())


def _k6_point(offset_s: float, value_ms: float, traffic: str = "warm") -> str:
    when = datetime.fromtimestamp(BASE.timestamp() + offset_s, tz=UTC)
    return json.dumps(
        {
            "type": "Point",
            "metric": "http_req_duration",
            "data": {
                "time": when.isoformat().replace("+00:00", "Z"),
                "value": value_ms,
                "tags": {"endpoint": "recommendations", "traffic": traffic},
            },
        }
    )


def _audit_row(offset_s: float, latency_ms: float, policy: str) -> dict[str, object]:
    when = datetime.fromtimestamp(BASE.timestamp() + offset_s, tz=UTC)
    return {
        "created_at": when.isoformat(),
        "latency_ms": latency_ms,
        "candidate_latency_ms": 1.5,
        "feature_latency_ms": 2.5,
        "ranker_latency_ms": 0.5,
        "model_latency_ms": 4.0,
        "policy": policy,
        "fallback_reason": None,
        "user_id": 900000101,
        "outcome": "success",
        "http_status": 200,
    }


def _write_window(
    tmp_path: Path,
    *,
    values: dict[float, list[tuple[float, str]]],
    p99: float,
    steal_pct: float = 0.0,
) -> Path:
    window = tmp_path / "window-1"
    window.mkdir(parents=True)
    lines = [
        _k6_point(second, value, traffic)
        for second, entries in values.items()
        for value, traffic in entries
    ]
    (window / "raw-metrics.json").write_text("\n".join(lines) + "\n")
    (window / "summary.json").write_text(json.dumps({"latency_ms": {"p99": p99}}))
    (window / "host-cpu.jsonl").write_text(
        "\n".join(
            json.dumps({"at": BASE_EPOCH + second, "steal_pct": steal_pct, "procs_running": 2.0})
            for second in range(4)
        )
        + "\n"
    )
    return window


def _add_fsync(window: Path, per_second: dict[int, list[float]]) -> None:
    lines = [json.dumps({"kind": "baseline", "ops": 200, "p50_ms": 0.4, "p99_ms": 0.9})]
    lines.extend(
        json.dumps({"ts": BASE_EPOCH + second + 0.5, "fdatasync_ms": value})
        for second, values in per_second.items()
        for value in values
    )
    (window / "disk-fsync.jsonl").write_text("\n".join(lines) + "\n")


def _add_server_side(window: Path, rows: list[dict[str, object]]) -> None:
    (window / "server-side-before.json").write_text(
        json.dumps(
            {
                "rows": [],
                "snapshot_only": True,
                "stats": {
                    "wal": {"wal_sync": 100.0, "wal_sync_time": 500.0, "wal_records": 10.0},
                    "bgwriter": {"checkpoints_req": 1.0},
                    "database": {"xact_commit": 5.0, "blk_write_time": 2.0},
                },
            }
        )
    )
    (window / "server-side.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "snapshot_only": False,
                "stats": {
                    "wal": {"wal_sync": 400.0, "wal_sync_time": 1500.0, "wal_records": 60.0},
                    "bgwriter": {"checkpoints_req": 2.0},
                    "database": {"xact_commit": 305.0, "blk_write_time": 9.0},
                },
            }
        )
    )


@pytest.fixture
def window(tmp_path: Path) -> Path:
    return _write_window(
        tmp_path,
        values={
            0: [(5.0, "warm"), (6.0, "cold"), (40.0, "warm")],
            1: [(5.0, "warm"), (7.0, "cold")],
            2: [(6.0, "warm"), (8.0, "cold")],
        },
        p99=40.0,
    )


def test_per_second_table_joins_fsync_and_server_columns(window: Path, capsys) -> None:
    _add_fsync(window, {0: [1.0, 22.0], 1: [1.2], 2: [0.9]})
    _add_server_side(
        window,
        [
            _audit_row(0.1, 20.0, "item-item-cosine+lightgbm"),
            _audit_row(0.2, 3.0, "popularity"),
            _audit_row(1.1, 4.0, "item-item-cosine+lightgbm"),
            _audit_row(2.1, 5.0, "popularity"),
        ],
    )
    summarize.main([str(window), "--k6-exit", "0"])
    capsys.readouterr()

    rows = json.loads((window / "per-second.json").read_text())
    assert rows[0]["fsync_p99_ms"] == 22.0
    assert rows[0]["server_p99_ms"] == 20.0
    assert rows[1]["fsync_p99_ms"] == 1.2
    header = (window / "per-second.txt").read_text().splitlines()[0]
    assert "srv_p99" in header
    assert "fsync" in header


def test_breakdown_reports_server_side_against_k6(window: Path, capsys) -> None:
    _add_fsync(window, {0: [1.0, 22.0], 1: [1.2], 2: [0.9]})
    _add_server_side(
        window,
        [
            _audit_row(0.1, 20.0, "item-item-cosine+lightgbm"),
            _audit_row(0.2, 3.0, "popularity"),
            _audit_row(1.1, 4.0, "item-item-cosine+lightgbm"),
            _audit_row(2.1, 5.0, "popularity"),
        ],
    )
    summarize.main([str(window), "--k6-exit", "0"])
    printed = capsys.readouterr().out

    assert "server-side vs k6" in printed
    assert "k6 client" in printed
    assert "server handler" in printed
    assert "implied outside the handler" in printed
    # Both traffic classes and both serving policies get their own line, which
    # is what makes "the cold path has the same tail" readable at a glance.
    assert "item-item-cosine+lightgbm" in printed
    assert "popularity" in printed
    assert "cand p99" in printed
    # The counter deltas are the window's, not the server's lifetime totals.
    assert "WAL syncs" in printed
    assert "300" in printed
    assert "pre-window baseline" in printed


def test_evidence_fields_land_in_the_decision(window: Path, capsys) -> None:
    _add_fsync(window, {0: [1.0, 22.0], 1: [1.2], 2: [0.9]})
    _add_server_side(window, [_audit_row(0.1, 20.0, "item-item-cosine+lightgbm")])
    summarize.main([str(window), "--k6-exit", "0"])
    capsys.readouterr()

    decision = json.loads((window / "decision.json").read_text())
    assert decision["fsync_probe_p99_ms"] == 22.0
    assert decision["fsync_probe_max_ms"] == 22.0
    assert decision["fsync_probe_p50_ms"] == 1.0
    assert decision["slowest_seconds_with_fsync_p99_over_10ms"] == 1
    assert decision["handler_p99_ms"] == 20.0
    assert decision["outside_handler_p99_ms"] == pytest.approx(20.0, abs=0.01)


def test_baseline_only_probe_output_reports_the_burst(window: Path, capsys) -> None:
    # What every default run produces: the pre-window burst, no in-window
    # sampling, and a table whose fsync column is honestly empty.
    (window / "disk-fsync.jsonl").write_text(
        json.dumps({"kind": "baseline", "ops": 200, "p50_ms": 0.4, "p99_ms": 0.9, "max_ms": 1.2})
        + "\n"
    )
    summarize.main([str(window), "--k6-exit", "0"])
    printed = capsys.readouterr().out

    assert "pre-window baseline (200 back-to-back ops)" in printed
    assert "during the window: n/a (baseline only; continuous sampling off)" in printed
    rows = json.loads((window / "per-second.json").read_text())
    assert rows[0]["fsync_p99_ms"] is None
    decision = json.loads((window / "decision.json").read_text())
    assert decision["fsync_probe_p99_ms"] is None
    assert decision["fsync_probe_baseline"]["p99_ms"] == 0.9


def test_missing_evidence_degrades_to_not_available(window: Path, capsys) -> None:
    summarize.main([str(window), "--k6-exit", "0"])
    printed = capsys.readouterr().out

    rows = json.loads((window / "per-second.json").read_text())
    assert rows[0]["fsync_p99_ms"] is None
    assert rows[0]["server_p99_ms"] is None
    assert "n/a (no disk-fsync.jsonl)" in printed
    assert "n/a (no server-side-before.json)" in printed

    decision = json.loads((window / "decision.json").read_text())
    assert decision["fsync_probe_p99_ms"] is None
    assert decision["handler_p99_ms"] is None
    assert decision["outside_handler_p99_ms"] is None
    assert decision["slowest_seconds_with_fsync_p99_over_10ms"] == 0


def test_unreadable_evidence_is_reported_rather_than_raised(window: Path, capsys) -> None:
    (window / "server-side.json").write_text("{ this is not json")
    (window / "disk-fsync.jsonl").write_text("not json either\n")
    summarize.main([str(window), "--k6-exit", "0"])
    printed = capsys.readouterr().out

    assert "GATE=pass" in printed
    assert "server handler: n/a" in printed


def test_evidence_never_moves_the_remeasure_verdict(tmp_path: Path, capsys) -> None:
    # A breach with enough steal under it is re-measured; adding a slow disk
    # to the same window must not change that either way.
    def build(directory: Path) -> Path:
        return _write_window(
            directory,
            values={
                0: [(150.0, "warm"), (150.0, "cold")],
                1: [(150.0, "warm"), (150.0, "cold")],
                2: [(150.0, "warm"), (150.0, "cold")],
            },
            p99=150.0,
            steal_pct=25.0,
        )

    bare = build(tmp_path / "bare")
    summarize.main([str(bare), "--k6-exit", "99"])
    capsys.readouterr()
    bare_decision = json.loads((bare / "decision.json").read_text())

    rich = build(tmp_path / "rich")
    _add_fsync(rich, {0: [90.0], 1: [80.0], 2: [95.0]})
    _add_server_side(rich, [_audit_row(0.1, 140.0, "popularity")])
    summarize.main([str(rich), "--k6-exit", "99"])
    capsys.readouterr()
    rich_decision = json.loads((rich / "decision.json").read_text())

    assert bare_decision["remeasure"] is True
    assert rich_decision["remeasure"] == bare_decision["remeasure"]
    assert rich_decision["label"] == bare_decision["label"]
    assert rich_decision["p99_ms"] == bare_decision["p99_ms"]


def test_explicit_evidence_paths_override_the_window_defaults(
    window: Path, tmp_path: Path, capsys
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _add_fsync(elsewhere, {0: [33.0]})
    summarize.main(
        [
            str(window),
            "--k6-exit",
            "0",
            "--disk-fsync",
            str(elsewhere / "disk-fsync.jsonl"),
        ]
    )
    capsys.readouterr()
    decision = json.loads((window / "decision.json").read_text())
    assert decision["fsync_probe_max_ms"] == 33.0


def test_a_window_with_no_samples_still_carries_the_probe_evidence(tmp_path: Path, capsys) -> None:
    window = tmp_path / "window-1"
    window.mkdir()
    (window / "raw-metrics.json").write_text("")
    _add_fsync(window, {0: [12.0]})
    summarize.main([str(window), "--k6-exit", "0"])
    capsys.readouterr()

    decision = json.loads((window / "decision.json").read_text())
    assert decision["remeasure"] is False
    assert decision["fsync_probe_max_ms"] == 12.0
    assert decision["handler_p99_ms"] is None
