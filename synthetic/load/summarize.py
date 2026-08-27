"""Turn one k6 window into a per-second latency table and a validity verdict.

The threshold summary answers "did the gate pass". It cannot answer "when did
it go wrong", which is the question every load-gate failure actually raises. A
tail in the opening seconds is a cold cache; a tail smeared across the middle is
contention; a tail that tracks one traffic class is a serving regression. Those
need different fixes and the aggregate cannot tell them apart.

So this reads back k6's own sample stream, buckets the measured requests by
second, and joins each second against `/proc/stat` deltas taken during the same
window (see `probe_host_cpu.py`). CPU steal is the discriminator the aggregate
lacks: it is time the hypervisor spent elsewhere while this kernel had runnable
work, so a slow second with high steal is a second the machine was not given,
not a second the service was slow.

That join is also what the re-measure rule reads. See `RE_MEASURE_RULE` below:
a breach with preemption underneath it is a measurement to redo once; a breach
without preemption is the service's and fails immediately. Nothing here can make
a gate pass — it only decides whether a breached window is allowed one honest
repeat, and turns k6's own result into the wrapper's exit code.

Two further joins are *informational* and feed nothing the rule reads. Steal
answers "was the CPU taken away"; it cannot answer "was the disk". So the
per-second table also carries `fsync` — that second's `fdatasync` cost on
Postgres's own volume (`probe_disk_fsync.py`, opt-in because the sampler that
fills this column perturbs the commits it measures; the pre-window burst that
runs by default costs nothing) — and `srv_p99`, the p99 of the audit rows the
server wrote in the same second (`server_side.py`). Since the
audit's `latency_ms` is timed around the handler alone, k6 minus handler is the
share spent outside it: auth, the transaction, the audit insert, and the COMMIT
every traffic class pays, including the cold path that never reaches a model.
All three are optional — an older evidence directory summarizes exactly as it
did before, with `n/a` where a file is missing.

Two workloads share this tool:

`recommendations`
    The pinned p99 gate. Measured samples are the ones tagged
    `endpoint=recommendations`; the verdict is k6's exit status and nothing
    else, exactly as it was before page workloads existed.

`pages`
    The page-shaped workloads in `pages.js`. Measured samples are everything
    carrying a `page` tag, the breakdown gains a per-(page, step) table, and the
    verdict is read from the verdict object that script writes into
    `summary.json`. That split matters: a correctness breach there is
    deterministic and always fails, while the per-step latency budgets can be
    reported rather than enforced while they are still new (`--advisory-latency`).
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

# Only the tagged scenario traffic is the measurement. Warm-up, readiness,
# snapshot and teardown requests carry their own endpoint tags and are excluded
# here exactly as they are excluded from the thresholds.
MEASURED_ENDPOINT = "recommendations"
LATENCY_METRIC = "http_req_duration"
RAW_METRICS_NAMES = ("raw-metrics.json.gz", "raw-metrics.json")
HOST_CPU_NAME = "host-cpu.jsonl"
DISK_FSYNC_NAME = "disk-fsync.jsonl"
SERVER_SIDE_NAME = "server-side.json"
SERVER_SIDE_BEFORE_NAME = "server-side-before.json"
SUMMARY_NAME = "summary.json"
SLOWEST_SECONDS = 10
SLO_MS = 100.0
# What counts as a slow second on the storage side, for the informational
# count in decision.json. A commit costs one fdatasync; ten milliseconds of it
# is already a tenth of the whole request budget, so a second spent above that
# is worth being able to point at. It gates nothing.
FSYNC_SLOW_MS = 10.0
WORKLOAD_RECOMMENDATIONS = "recommendations"
WORKLOAD_PAGES = "pages"
# k6 returns this when a declared threshold was breached. Any other non-zero
# status is the script or the stack failing, which no verdict object describes
# and which therefore always fails the gate.
K6_THRESHOLD_EXIT = 99

# --- the re-measure rule ----------------------------------------------------
# A breached window is re-measured at most once, and only when the seconds that
# breached line up with the host being preempted. The constants are the rule:
#
#   re-measure  <=>  latency breached  AND  at least RE_MEASURE_MIN_SECONDS of
#                    the ten slowest seconds recorded steal >= RE_MEASURE_STEAL_PCT
#
# 10% steal in a one-second window on a 4-vCPU runner is roughly "we lost more
# than a third of a core to somebody else for that second" — far outside what a
# quiet runner shows, and enough to move a p99 built from ~55 samples. Requiring
# three such seconds keeps a single blip from buying a rerun. Both numbers are
# provisional until several CI runs have recorded steal; the artifact carries
# the raw per-second values so they can be re-derived rather than guessed again.
#
# A *correctness* breach is never re-measured. Preemption cannot make an API
# return the wrong body, so there is nothing to redo.
RE_MEASURE_STEAL_PCT = 10.0
RE_MEASURE_MIN_SECONDS = 3
RE_MEASURE_RULE = (
    f"latency breached AND >= {RE_MEASURE_MIN_SECONDS} of the {SLOWEST_SECONDS} "
    f"slowest seconds at >= {RE_MEASURE_STEAL_PCT:.0f}% CPU steal"
)

_NANOSECOND_TIME = re.compile(r"^(.*\.\d{1,6})\d*([+-]\d{2}:\d{2})$")


@dataclass(frozen=True)
class Sample:
    """One measured request: when it finished, how long it took, and whose it was."""

    timestamp: float
    value: float
    # The column the per-second table groups by. Recommendation traffic groups
    # by traffic class (warm/cold/mixed); page traffic groups by route, which
    # is what makes a smeared tail attributable to a page at a glance.
    class_name: str
    page: str
    step: str


@dataclass(frozen=True)
class SecondBucket:
    second: int
    epoch: int
    count: int
    p50: float
    p95: float
    p99: float
    maximum: float
    over_slo: int
    traffic: dict[str, int]
    steal_pct: float | None
    run_queue: float | None
    # Both optional: they exist only when the newer probes ran.
    server_p99: float | None = None
    fsync_p99: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "second": self.second,
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
            "max_ms": round(self.maximum, 2),
            f"over_{int(SLO_MS)}ms": self.over_slo,
            "steal_pct": self.steal_pct,
            "run_queue": self.run_queue,
            "server_p99_ms": _rounded(self.server_p99),
            "fsync_p99_ms": _rounded(self.fsync_p99),
            "traffic": self.traffic,
        }


@dataclass(frozen=True)
class StepStats:
    page: str
    step: str
    count: int
    p50: float
    p95: float
    p99: float
    maximum: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "step": self.step,
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
            "max_ms": round(self.maximum, 2),
        }


@dataclass(frozen=True)
class ServerRow:
    """One `recommendation_audits` row, reduced to what a window needs."""

    epoch: float
    latency_ms: float
    candidate_ms: float
    feature_ms: float
    ranker_ms: float
    model_ms: float
    policy: str


@dataclass(frozen=True)
class ServerSide:
    """One `server_side.py` export. `note` says why it is empty when it is."""

    rows: list[ServerRow]
    stats: dict[str, Any]
    note: str


@dataclass(frozen=True)
class DiskFsync:
    """Per-second `fdatasync` durations plus the pre-window baseline burst."""

    per_second: dict[int, list[float]]
    all_samples: list[float]
    baseline: dict[str, Any]
    note: str

    @property
    def available(self) -> bool:
        return bool(self.all_samples)


@dataclass(frozen=True)
class Percentiles:
    label: str
    count: int | None
    p50: float
    p95: float
    p99: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
        }


@dataclass(frozen=True)
class Comparison:
    """What the client measured, what the server measured, and the gap."""

    client: Percentiles
    handler: Percentiles | None
    outside: Percentiles | None
    by_class: list[Percentiles]
    by_policy: list[Percentiles]
    stages: dict[str, dict[str, float]]
    note: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize one k6 measurement window.")
    parser.add_argument("window", type=Path, help="Directory holding one window's output.")
    parser.add_argument(
        "--workload",
        choices=(WORKLOAD_RECOMMENDATIONS, WORKLOAD_PAGES),
        default=WORKLOAD_RECOMMENDATIONS,
        help="Which script produced this window.",
    )
    parser.add_argument(
        "--k6-exit",
        type=int,
        default=None,
        help="k6's exit status for this window; required to emit a GATE verdict.",
    )
    parser.add_argument(
        "--advisory-latency",
        action="store_true",
        help="Report the page latency budgets instead of failing on them (pages only).",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="This window is the verdict; never ask for another measurement.",
    )
    # All three default to the window directory's own names, so an evidence
    # tree captured before these probes existed summarizes unchanged.
    parser.add_argument("--disk-fsync", type=Path, default=None)
    parser.add_argument("--server-side", type=Path, default=None)
    parser.add_argument("--server-side-before", type=Path, default=None)
    args = parser.parse_args(argv)
    window: Path = args.window
    workload: str = args.workload

    fsync = _read_disk_fsync(args.disk_fsync or window / DISK_FSYNC_NAME)
    server = _read_server_side(args.server_side or window / SERVER_SIDE_NAME)
    server_before = _read_server_side(args.server_side_before or window / SERVER_SIDE_BEFORE_NAME)

    raw = _find_raw_metrics(window)
    if raw is None:
        print(f"[load-summary] no k6 sample stream under {window}; skipping the breakdown")
        decision = _unavailable_decision("no k6 sample stream")
        _attach_evidence(decision, fsync=fsync, slowest=[], comparison=None)
        _write_decision(window, decision)
        return _emit(decision, _gate(window, workload, args))

    steal = _read_host_cpu(window / HOST_CPU_NAME)
    samples = _read_samples(raw, workload)
    buckets = _bucket_by_second(samples, steal, fsync=fsync, server=server)
    if not buckets:
        print(f"[load-summary] {raw.name} holds no {workload}-tagged latency samples")
        decision = _unavailable_decision("no measured latency samples")
        _attach_evidence(decision, fsync=fsync, slowest=[], comparison=None)
        _write_decision(window, decision)
        return _emit(decision, _gate(window, workload, args))

    (window / "per-second.txt").write_text(f"{_render_table(buckets)}\n")
    (window / "per-second.json").write_text(
        json.dumps([bucket.as_dict() for bucket in buckets], indent=2) + "\n"
    )
    steps = _step_stats(samples) if workload == WORKLOAD_PAGES else []
    if steps:
        (window / "steps.txt").write_text(f"{_render_steps(window, steps)}\n")
        (window / "steps.json").write_text(
            json.dumps([step.as_dict() for step in steps], indent=2) + "\n"
        )

    slowest = sorted(buckets, key=lambda bucket: bucket.p99, reverse=True)[:SLOWEST_SECONDS]
    comparison = _compare(samples, server, buckets)
    decision = _decide(window, workload, buckets, slowest, args)
    _attach_evidence(decision, fsync=fsync, slowest=slowest, comparison=comparison)
    _write_decision(window, decision)
    _report(window, buckets, slowest, steps, decision, comparison, fsync, server_before, server)
    return _emit(decision, _gate(window, workload, args))


def _emit(decision: dict[str, Any], gate: str | None) -> int:
    # The shell wrapper greps these lines rather than parsing JSON in sh.
    print(f"REMEASURE={'yes' if decision['remeasure'] else 'no'}")
    if gate is not None:
        print(f"GATE={gate}")
    return 0


def _report(
    window: Path,
    buckets: list[SecondBucket],
    slowest: list[SecondBucket],
    steps: list[StepStats],
    decision: dict[str, Any],
    comparison: Comparison | None,
    fsync: DiskFsync,
    server_before: ServerSide,
    server_after: ServerSide,
) -> None:
    total = sum(bucket.count for bucket in buckets)
    over_slo = sum(bucket.over_slo for bucket in buckets)
    print(
        f"\n[load-summary] {window.name}: {total} measured requests across {len(buckets)} "
        f"seconds; {over_slo} over {SLO_MS:.0f} ms ({_share(over_slo, total)}). "
        f"Full table: {window / 'per-second.txt'}"
    )
    if steps:
        print("[load-summary] per-step latency against budget:")
        print(_render_steps(window, steps))
    print(f"[load-summary] slowest {len(slowest)} seconds by p99, with host CPU steal:")
    print(_render_table(sorted(slowest, key=lambda bucket: bucket.second)))
    print("[load-summary] opening second of the measured window:")
    print(_render_table(buckets[:1]))
    print("\n[load-summary] server-side vs k6:")
    print(_render_comparison(comparison))
    print("\n[load-summary] fdatasync on Postgres's volume:")
    print(_render_fsync(fsync))
    print("\n[load-summary] Postgres counters across the window:")
    print(_render_stat_deltas(server_before, server_after))
    print(f"\n[load-summary] {decision['label']}")


# --- verdicts ---------------------------------------------------------------


def _decide(
    window: Path,
    workload: str,
    buckets: list[SecondBucket],
    slowest: list[SecondBucket],
    args: argparse.Namespace,
) -> dict[str, Any]:
    if workload == WORKLOAD_PAGES:
        return _decide_pages(window, buckets, slowest, final=bool(args.final))
    return _decide_recommendations(window, buckets, slowest, final=bool(args.final))


def _decide_recommendations(
    window: Path,
    buckets: list[SecondBucket],
    slowest: list[SecondBucket],
    *,
    final: bool,
) -> dict[str, Any]:
    summary = _read_summary(window)
    p99 = _summary_p99(summary)
    if p99 is None:
        p99 = max((bucket.p99 for bucket in buckets), default=0.0)
        p99_source = "per-second buckets (k6 summary unavailable)"
    else:
        p99_source = "k6 summary"
    breached = p99 > SLO_MS

    steal = _steal_evidence(buckets, slowest)
    remeasure = (
        not final and breached and steal["available"] and steal["high"] >= RE_MEASURE_MIN_SECONDS
    )
    if final:
        label = f"final window: p99 {p99:.2f} ms — this verdict stands regardless of steal"
    elif not breached:
        label = f"within the SLO at p99 {p99:.2f} ms; no re-measurement considered"
    elif not steal["available"]:
        label = (
            f"p99 {p99:.2f} ms breached and no host CPU accounting was captured, so the "
            "breach is treated as the service's and fails immediately"
        )
    elif remeasure:
        label = (
            f"re-measured after hypervisor steal: {steal['high']} of the {steal['measured']} "
            f"slowest seconds at or above {RE_MEASURE_STEAL_PCT:.0f}% steal "
            f"(peak {steal['peak']:.1f}%) while p99 was {p99:.2f} ms"
        )
    else:
        label = (
            f"not re-measured: p99 {p99:.2f} ms breached with only {steal['high']} of the "
            f"{steal['measured']} slowest seconds at or above {RE_MEASURE_STEAL_PCT:.0f}% steal "
            f"(peak {steal['peak']:.1f}%) — a low-steal breach is the service's"
        )
    return {
        "window": window.name,
        "workload": WORKLOAD_RECOMMENDATIONS,
        "rule": RE_MEASURE_RULE,
        "p99_ms": round(p99, 2),
        "p99_source": p99_source,
        "slo_ms": SLO_MS,
        "p99_breached": breached,
        "steal_available": steal["available"],
        "steal_peak_pct": round(steal["peak"], 2),
        "slowest_seconds_considered": steal["measured"],
        "slowest_seconds_above_threshold": steal["high"],
        "steal_threshold_pct": RE_MEASURE_STEAL_PCT,
        "min_seconds_above_threshold": RE_MEASURE_MIN_SECONDS,
        "remeasure": remeasure,
        "label": label,
    }


def _decide_pages(
    window: Path,
    buckets: list[SecondBucket],
    slowest: list[SecondBucket],
    *,
    final: bool,
) -> dict[str, Any]:
    verdict = _read_verdict(window)
    correctness_ok = bool(verdict.get("correctness_ok", True))
    latency_ok = bool(verdict.get("latency_ok", True))
    breached_latency = [str(item) for item in verdict.get("breached_latency", [])]
    breached_correctness = [str(item) for item in verdict.get("breached_correctness", [])]

    steal = _steal_evidence(buckets, slowest)
    # Only a latency breach can be a measurement problem. A wrong body is not.
    remeasure = (
        not final
        and correctness_ok
        and not latency_ok
        and steal["available"]
        and steal["high"] >= RE_MEASURE_MIN_SECONDS
    )
    if not correctness_ok:
        label = (
            f"correctness breached ({len(breached_correctness)} thresholds); never re-measured "
            "— preemption cannot make the API return a wrong body"
        )
    elif latency_ok:
        label = "all page budgets met; no re-measurement considered"
    elif final:
        label = (
            f"final window: {len(breached_latency)} page budgets breached — this verdict "
            "stands regardless of steal"
        )
    elif not steal["available"]:
        label = (
            f"{len(breached_latency)} page budgets breached and no host CPU accounting was "
            "captured, so the breach is treated as the service's"
        )
    elif remeasure:
        label = (
            f"re-measured after hypervisor steal: {steal['high']} of the {steal['measured']} "
            f"slowest seconds at or above {RE_MEASURE_STEAL_PCT:.0f}% steal "
            f"(peak {steal['peak']:.1f}%) while {len(breached_latency)} budgets breached"
        )
    else:
        label = (
            f"not re-measured: {len(breached_latency)} page budgets breached with only "
            f"{steal['high']} of the {steal['measured']} slowest seconds at or above "
            f"{RE_MEASURE_STEAL_PCT:.0f}% steal (peak {steal['peak']:.1f}%)"
        )
    return {
        "window": window.name,
        "workload": WORKLOAD_PAGES,
        "rule": RE_MEASURE_RULE,
        "correctness_ok": correctness_ok,
        "latency_ok": latency_ok,
        "breached_correctness": breached_correctness,
        "breached_latency": breached_latency,
        "steal_available": steal["available"],
        "steal_peak_pct": round(steal["peak"], 2),
        "slowest_seconds_considered": steal["measured"],
        "slowest_seconds_above_threshold": steal["high"],
        "steal_threshold_pct": RE_MEASURE_STEAL_PCT,
        "min_seconds_above_threshold": RE_MEASURE_MIN_SECONDS,
        "remeasure": remeasure,
        "label": label,
    }


def _steal_evidence(buckets: list[SecondBucket], slowest: list[SecondBucket]) -> dict[str, Any]:
    measured = [bucket for bucket in slowest if bucket.steal_pct is not None]
    high = [
        bucket
        for bucket in measured
        if bucket.steal_pct is not None and bucket.steal_pct >= RE_MEASURE_STEAL_PCT
    ]
    return {
        "available": bool(measured),
        "measured": len(measured),
        "high": len(high),
        "peak": max((bucket.steal_pct or 0.0 for bucket in buckets), default=0.0),
    }


def _gate(window: Path, workload: str, args: argparse.Namespace) -> str | None:
    """The wrapper's exit code, as a word.

    For the recommendation gate this is k6's exit status and nothing else, which
    is the contract ADR 0010 pinned. For page workloads it splits: a correctness
    breach always fails, a latency breach fails unless the caller asked for it
    to be advisory, and any k6 failure that is *not* a threshold breach (a
    script error, a setup failure, a teardown that had to repair state) fails
    whatever the verdict object says.
    """
    if args.k6_exit is None:
        return None
    exit_status = int(args.k6_exit)
    if workload != WORKLOAD_PAGES:
        return "pass" if exit_status == 0 else "fail"

    verdict = _read_verdict(window)
    if exit_status not in (0, K6_THRESHOLD_EXIT):
        print(
            f"[load-summary] k6 exited {exit_status}, which is not a threshold breach: "
            "the run itself failed. See k6-stdout.txt."
        )
        return "fail"
    if not bool(verdict.get("correctness_ok", exit_status == 0)):
        return "fail"
    if bool(verdict.get("latency_ok", exit_status == 0)):
        return "pass"
    if args.advisory_latency:
        print(
            "[load-summary] ADVISORY: page latency budgets breached and reported, not enforced. "
            "See ADR 0010 for what it takes to promote these budgets to enforced."
        )
        return "pass"
    return "fail"


def _unavailable_decision(reason: str) -> dict[str, Any]:
    return {
        "rule": RE_MEASURE_RULE,
        "remeasure": False,
        "label": f"no re-measurement considered: {reason}",
    }


def _write_decision(window: Path, decision: dict[str, Any]) -> None:
    (window / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")


def _read_summary(window: Path) -> dict[str, Any]:
    path = window / SUMMARY_NAME
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_verdict(window: Path) -> dict[str, Any]:
    verdict = _read_summary(window).get("verdict")
    return verdict if isinstance(verdict, dict) else {}


def _summary_p99(summary: dict[str, Any]) -> float | None:
    latency = summary.get("latency_ms")
    if not isinstance(latency, dict):
        return None
    value = latency.get("p99")
    return float(value) if isinstance(value, int | float) else None


def _share(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.2f}%" if whole else "n/a"


def _find_raw_metrics(window: Path) -> Path | None:
    for name in RAW_METRICS_NAMES:
        candidate = window / name
        if candidate.is_file():
            return candidate
    return None


def _read_host_cpu(path: Path) -> dict[int, dict[str, float]]:
    """Map whole-second epoch -> that second's CPU accounting deltas."""
    if not path.is_file():
        return {}
    samples: dict[int, dict[str, float]] = {}
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        at = record.get("at")
        if not isinstance(at, int | float):
            continue
        samples[int(at)] = record
    return samples


def _read_disk_fsync(path: Path) -> DiskFsync:
    """Read `probe_disk_fsync.py`'s stream: one baseline line, then samples."""
    if not path.is_file():
        return DiskFsync({}, [], {}, f"no {path.name}")
    per_second: defaultdict[int, list[float]] = defaultdict(list)
    all_samples: list[float] = []
    baseline: dict[str, Any] = {}
    note = ""
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("kind") == "baseline":
            baseline = record
            continue
        if record.get("available") is False:
            note = str(record.get("reason", "probe unavailable"))
            continue
        at = record.get("ts")
        duration = record.get("fdatasync_ms")
        if not isinstance(at, int | float) or not isinstance(duration, int | float):
            continue
        per_second[int(at)].append(float(duration))
        all_samples.append(float(duration))
    if not all_samples and not note:
        # The common case, not a fault: LOAD_FSYNC_PROBE defaults to off, so a
        # normal artifact carries the burst and nothing else.
        note = "baseline only; continuous sampling off" if baseline else "no samples in probe file"
    return DiskFsync(dict(per_second), all_samples, baseline, note)


def _read_server_side(path: Path) -> ServerSide:
    """Read one `server_side.py` export, tolerating every way it can be absent."""
    if not path.is_file():
        return ServerSide([], {}, f"no {path.name}")
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return ServerSide([], {}, f"{path.name} unreadable: {error}")
    if not isinstance(loaded, dict):
        return ServerSide([], {}, f"{path.name} is not an object")
    stats = loaded.get("stats")
    rows = [
        row for row in (_server_row(entry) for entry in loaded.get("rows") or []) if row is not None
    ]
    note = str(loaded.get("rows_unavailable", "")) if not rows else ""
    return ServerSide(rows, stats if isinstance(stats, dict) else {}, note)


def _server_row(entry: Any) -> ServerRow | None:
    if not isinstance(entry, dict):
        return None
    try:
        epoch = _parse_time(str(entry["created_at"]))
        latency = float(entry["latency_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    return ServerRow(
        epoch=epoch,
        latency_ms=latency,
        candidate_ms=_float_or_zero(entry.get("candidate_latency_ms")),
        feature_ms=_float_or_zero(entry.get("feature_latency_ms")),
        ranker_ms=_float_or_zero(entry.get("ranker_latency_ms")),
        model_ms=_float_or_zero(entry.get("model_latency_ms")),
        policy=str(entry.get("policy") or "unknown"),
    )


def _server_by_second(server: ServerSide) -> dict[int, list[float]]:
    grouped: defaultdict[int, list[float]] = defaultdict(list)
    for row in server.rows:
        grouped[int(row.epoch)].append(row.latency_ms)
    return dict(grouped)


# --- server-side vs client-side ---------------------------------------------


def _compare(
    samples: list[Sample], server: ServerSide, buckets: list[SecondBucket]
) -> Comparison | None:
    """Line the two measurements up over the same seconds.

    The audit export is bounded by the *window's* start, which includes k6's
    warm-up; the client samples are only the measured requests. Clipping the
    rows to the measured seconds is what makes the two columns comparable
    rather than merely adjacent.
    """
    if not samples or not buckets:
        return None
    client = _percentiles("k6 client", [sample.value for sample in samples])
    first, last = buckets[0].epoch, buckets[-1].epoch + 1
    rows = [row for row in server.rows if first <= row.epoch < last]

    by_class: list[Percentiles] = []
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for sample in samples:
        grouped[sample.class_name].append(sample.value)
    for name in sorted(grouped):
        by_class.append(_percentiles(name, grouped[name]))

    if not rows:
        note = server.note or (
            "no audit rows inside the measured seconds" if server.rows else "no server-side export"
        )
        return Comparison(client, None, None, by_class, [], {}, note)

    handler = _percentiles("server handler", [row.latency_ms for row in rows])
    outside = Percentiles(
        label="implied outside the handler",
        count=None,
        p50=client.p50 - handler.p50,
        p95=client.p95 - handler.p95,
        p99=client.p99 - handler.p99,
    )
    by_policy: list[Percentiles] = []
    stages: dict[str, dict[str, float]] = {}
    policies: defaultdict[str, list[ServerRow]] = defaultdict(list)
    for row in rows:
        policies[row.policy].append(row)
    for policy in sorted(policies):
        grouped_rows = policies[policy]
        by_policy.append(_percentiles(policy, [row.latency_ms for row in grouped_rows]))
        stages[policy] = {
            "candidate": _percentile(sorted(row.candidate_ms for row in grouped_rows), 0.99),
            "feature": _percentile(sorted(row.feature_ms for row in grouped_rows), 0.99),
            "ranker": _percentile(sorted(row.ranker_ms for row in grouped_rows), 0.99),
            "model": _percentile(sorted(row.model_ms for row in grouped_rows), 0.99),
        }
    return Comparison(client, handler, outside, by_class, by_policy, stages, "")


def _percentiles(label: str, values: list[float]) -> Percentiles:
    ordered = sorted(values)
    return Percentiles(
        label=label,
        count=len(ordered),
        p50=_percentile(ordered, 0.50),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
    )


def _attach_evidence(
    decision: dict[str, Any],
    *,
    fsync: DiskFsync,
    slowest: list[SecondBucket],
    comparison: Comparison | None,
) -> None:
    """Add the informational fields — and only those.

    `remeasure` and `label` are produced by `_decide` and are never touched
    here: this evidence exists to explain a verdict, not to change one.
    """
    ordered = sorted(fsync.all_samples)
    decision["fsync_probe_p50_ms"] = _rounded(_percentile(ordered, 0.50)) if ordered else None
    decision["fsync_probe_p99_ms"] = _rounded(_percentile(ordered, 0.99)) if ordered else None
    decision["fsync_probe_max_ms"] = _rounded(ordered[-1]) if ordered else None
    decision["fsync_probe_baseline"] = fsync.baseline or None
    decision["slowest_seconds_with_fsync_p99_over_10ms"] = sum(
        1 for bucket in slowest if bucket.fsync_p99 is not None and bucket.fsync_p99 > FSYNC_SLOW_MS
    )
    handler = comparison.handler if comparison is not None else None
    outside = comparison.outside if comparison is not None else None
    decision["handler_p99_ms"] = _rounded(handler.p99) if handler is not None else None
    decision["outside_handler_p99_ms"] = _rounded(outside.p99) if outside is not None else None


# --- reading k6's sample stream ---------------------------------------------


def _read_samples(raw: Path, workload: str) -> list[Sample]:
    samples: list[Sample] = []
    opener: Callable[..., IO[str]] = gzip.open if raw.suffix == ".gz" else open
    with opener(raw, "rt") as stream:
        for line in stream:
            sample = _read_sample(line, workload)
            if sample is not None:
                samples.append(sample)
    return samples


def _read_sample(line: str, workload: str) -> Sample | None:
    """Return one measured point, or None if this line is not one."""
    text = line.strip()
    # Cheap prefilter: the stream is mostly metrics this never looks at, and
    # json.loads on every line is the slow way to discover that.
    if not text or LATENCY_METRIC not in text:
        return None
    if workload == WORKLOAD_RECOMMENDATIONS and MEASURED_ENDPOINT not in text:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    if record.get("type") != "Point" or record.get("metric") != LATENCY_METRIC:
        return None
    data = record.get("data") or {}
    tags = data.get("tags") or {}
    page = str(tags.get("page", ""))
    if workload == WORKLOAD_PAGES:
        if not page:
            return None
        class_name = page
    else:
        if tags.get("endpoint") != MEASURED_ENDPOINT:
            return None
        class_name = str(tags.get("traffic", "untagged"))
    try:
        timestamp = _parse_time(str(data["time"]))
        value = float(data["value"])
    except (KeyError, TypeError, ValueError):
        return None
    return Sample(
        timestamp=timestamp,
        value=value,
        class_name=class_name,
        page=page,
        step=str(tags.get("step", "")),
    )


def _bucket_by_second(
    samples: list[Sample],
    steal: dict[int, dict[str, float]],
    *,
    fsync: DiskFsync | None = None,
    server: ServerSide | None = None,
) -> list[SecondBucket]:
    if not samples:
        return []
    fsync_seconds = fsync.per_second if fsync is not None else {}
    server_seconds = _server_by_second(server) if server is not None else {}

    start = min(sample.timestamp for sample in samples)
    values: defaultdict[int, list[float]] = defaultdict(list)
    traffic: defaultdict[int, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sample in samples:
        second = int(sample.timestamp - start)
        values[second].append(sample.value)
        traffic[second][sample.class_name] += 1

    buckets: list[SecondBucket] = []
    for second in sorted(values):
        ordered = sorted(values[second])
        epoch = int(start) + second
        host = steal.get(epoch) or steal.get(epoch + 1) or {}
        buckets.append(
            SecondBucket(
                second=second,
                epoch=epoch,
                count=len(ordered),
                p50=_percentile(ordered, 0.50),
                p95=_percentile(ordered, 0.95),
                p99=_percentile(ordered, 0.99),
                maximum=ordered[-1],
                over_slo=sum(1 for value in ordered if value > SLO_MS),
                traffic=dict(sorted(traffic[second].items())),
                steal_pct=_optional_float(host.get("steal_pct")),
                run_queue=_optional_float(host.get("procs_running")),
                server_p99=_p99_of(server_seconds.get(epoch)),
                fsync_p99=_p99_of(fsync_seconds.get(epoch)),
            )
        )
    return buckets


def _p99_of(values: list[float] | None) -> float | None:
    return _percentile(sorted(values), 0.99) if values else None


def _step_stats(samples: list[Sample]) -> list[StepStats]:
    grouped: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    for sample in samples:
        if sample.step:
            grouped[(sample.page, sample.step)].append(sample.value)
    stats: list[StepStats] = []
    for (page, step), values in sorted(grouped.items()):
        ordered = sorted(values)
        stats.append(
            StepStats(
                page=page,
                step=step,
                count=len(ordered),
                p50=_percentile(ordered, 0.50),
                p95=_percentile(ordered, 0.95),
                p99=_percentile(ordered, 0.99),
                maximum=ordered[-1],
            )
        )
    return stats


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _float_or_zero(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _parse_time(raw: str) -> float:
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    # k6 writes RFC3339 with nanoseconds; Python's ISO parser stops at
    # microseconds, so the extra digits are trimmed rather than rejected.
    match = _NANOSECOND_TIME.match(text)
    if match:
        text = f"{match.group(1)}{match.group(2)}"
    return datetime.fromisoformat(text).timestamp()


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Deliberately not k6's interpolated definition: this is a diagnostic view of
    where the run went slow, and the verdict still comes from k6's thresholds.
    A per-second p99 over ~55 samples is a shape, not a contract.
    """
    if not ordered:
        return 0.0
    rank = math.ceil(fraction * len(ordered))
    return ordered[min(max(rank, 1), len(ordered)) - 1]


# --- rendering --------------------------------------------------------------


def _render_table(buckets: list[SecondBucket]) -> str:
    header = (
        f"{'sec':>4} {'reqs':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>9} "
        f"{'>slo':>5} {'steal%':>7} {'runq':>5} {'srv_p99':>8} {'fsync':>8}  traffic"
    )
    lines = [header, "-" * len(header)]
    for bucket in buckets:
        traffic = " ".join(f"{name}={count}" for name, count in bucket.traffic.items())
        steal = "n/a" if bucket.steal_pct is None else f"{bucket.steal_pct:.1f}"
        run_queue = "n/a" if bucket.run_queue is None else f"{bucket.run_queue:.0f}"
        server = "n/a" if bucket.server_p99 is None else f"{bucket.server_p99:.2f}"
        fsync = "n/a" if bucket.fsync_p99 is None else f"{bucket.fsync_p99:.2f}"
        lines.append(
            f"{bucket.second:>4} {bucket.count:>5} {bucket.p50:>8.2f} {bucket.p95:>8.2f} "
            f"{bucket.p99:>8.2f} {bucket.maximum:>9.2f} {bucket.over_slo:>5} "
            f"{steal:>7} {run_queue:>5} {server:>8} {fsync:>8}  {traffic}"
        )
    return "\n".join(lines)


def _render_comparison(comparison: Comparison | None) -> str:
    """k6's view, the handler's view, and what the difference implies.

    `implied outside the handler` is a subtraction of percentiles, not a
    measured quantity: the p99 request on the client is not necessarily the p99
    request in the handler. It is still the right first cut, because a large
    gap can only come from work the handler's timer does not cover — auth,
    opening the transaction, the audit insert, and the COMMIT.
    """
    if comparison is None:
        return "  n/a (no measured samples)"
    header = f"  {'where':<40} {'n':>6} {'p50':>9} {'p95':>9} {'p99':>9}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    lines.append(_comparison_row(comparison.client))
    if comparison.handler is not None and comparison.outside is not None:
        lines.append(_comparison_row(comparison.handler))
        lines.append(_comparison_row(comparison.outside))
    else:
        lines.append(f"  server handler: n/a ({comparison.note or 'unavailable'})")

    lines.append("")
    lines.append(f"  {'k6 traffic class':<40} {'n':>6} {'p50':>9} {'p95':>9} {'p99':>9}")
    lines.append("  " + "-" * (len(header) - 2))
    lines.extend(_comparison_row(entry) for entry in comparison.by_class)

    if comparison.by_policy:
        stage_header = (
            f"  {'server policy':<40} {'n':>6} {'p50':>9} {'p95':>9} {'p99':>9}"
            f" {'cand p99':>9} {'feat p99':>9} {'rank p99':>9} {'model p99':>10}"
        )
        lines.append("")
        lines.append(stage_header)
        lines.append("  " + "-" * (len(stage_header) - 2))
        for entry in comparison.by_policy:
            stages = comparison.stages.get(entry.label, {})
            lines.append(
                f"{_comparison_row(entry)}"
                f" {stages.get('candidate', 0.0):>9.2f} {stages.get('feature', 0.0):>9.2f}"
                f" {stages.get('ranker', 0.0):>9.2f} {stages.get('model', 0.0):>10.2f}"
            )
    return "\n".join(lines)


def _comparison_row(entry: Percentiles) -> str:
    count = "-" if entry.count is None else str(entry.count)
    return f"  {entry.label:<40} {count:>6} {entry.p50:>9.2f} {entry.p95:>9.2f} {entry.p99:>9.2f}"


def _render_fsync(fsync: DiskFsync) -> str:
    """The pre-window burst and, when it was collected, the in-window sampling.

    The two halves are independent because the in-window half is opt-in: a
    continuous sampler on the same volume perturbs the very commits the gate is
    timing (see `probe_disk_fsync.py`), so the default artifact carries the
    burst alone.
    """
    lines = []
    if fsync.baseline:
        baseline = fsync.baseline
        lines.append(
            f"  pre-window baseline ({baseline.get('ops', 0)} back-to-back ops): "
            f"p50 {_render_ms(baseline.get('p50_ms'))}, "
            f"p95 {_render_ms(baseline.get('p95_ms'))}, "
            f"p99 {_render_ms(baseline.get('p99_ms'))}, max {_render_ms(baseline.get('max_ms'))}"
        )
    else:
        lines.append("  pre-window baseline: n/a")
    if fsync.available:
        ordered = sorted(fsync.all_samples)
        lines.append(
            f"  during the window ({len(ordered)} samples): "
            f"p50 {_percentile(ordered, 0.50):.2f} ms, p95 {_percentile(ordered, 0.95):.2f} ms, "
            f"p99 {_percentile(ordered, 0.99):.2f} ms, max {ordered[-1]:.2f} ms"
        )
    else:
        lines.append(f"  during the window: n/a ({fsync.note or 'no probe output'})")
    return "\n".join(lines)


def _render_ms(value: Any) -> str:
    return f"{float(value):.2f} ms" if isinstance(value, int | float) else "n/a"


# The counters worth reading for a latency tail, in the order they explain one:
# how much WAL the window produced, how many syncs that cost and how long they
# took, whether a checkpoint landed inside the window, and how much time the
# backends themselves spent in IO.
STAT_DELTA_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("wal", "wal_records", "WAL records"),
    ("wal", "wal_bytes", "WAL bytes"),
    ("wal", "wal_write", "WAL writes"),
    ("wal", "wal_sync", "WAL syncs"),
    ("wal", "wal_write_time", "WAL write time (ms)"),
    ("wal", "wal_sync_time", "WAL sync time (ms)"),
    ("bgwriter", "checkpoints_timed", "checkpoints (timed)"),
    ("bgwriter", "checkpoints_req", "checkpoints (requested)"),
    ("bgwriter", "checkpoint_write_time", "checkpoint write time (ms)"),
    ("bgwriter", "checkpoint_sync_time", "checkpoint sync time (ms)"),
    ("bgwriter", "buffers_checkpoint", "buffers written at checkpoint"),
    ("bgwriter", "buffers_backend", "buffers written by backends"),
    ("database", "xact_commit", "transactions committed"),
    ("database", "blks_read", "blocks read"),
    ("database", "blk_read_time", "block read time (ms)"),
    ("database", "blk_write_time", "block write time (ms)"),
)


def _render_stat_deltas(before: ServerSide, after: ServerSide) -> str:
    """Differences between the two snapshots.

    Postgres's counters are cumulative since the last stats reset, so a single
    reading says nothing about one minute of traffic. Only the pair does.
    """
    if not before.stats or not after.stats:
        missing = before.note or after.note or "snapshot missing"
        return f"  n/a ({missing})"
    lines = []
    for section, field, label in STAT_DELTA_FIELDS:
        start = _optional_float((before.stats.get(section) or {}).get(field))
        end = _optional_float((after.stats.get(section) or {}).get(field))
        if start is None or end is None:
            lines.append(f"  {label:<30} n/a")
            continue
        delta = end - start
        rendered = f"{delta:,.2f}" if field.endswith("time") else f"{delta:,.0f}"
        lines.append(f"  {label:<30} {rendered:>16}")
    return "\n".join(lines)


def _render_steps(window: Path, steps: list[StepStats]) -> str:
    budgets = _read_step_budgets(window)
    header = (
        f"{'page':<11} {'step':<30} {'reqs':>5} {'p50':>8} {'p95':>8} {'p99':>8} "
        f"{'max':>9}  {'budget p95/p99':>15}  verdict"
    )
    lines = [header, "-" * len(header)]
    for step in steps:
        budget = budgets.get(f"{step.page}:{step.step}")
        if budget is None:
            rendered_budget = "-"
            verdict = "unbudgeted"
        else:
            rendered_budget = f"{budget[0]:.0f}/{budget[1]:.0f}"
            over = []
            if step.p95 >= budget[0]:
                over.append("p95")
            if step.p99 >= budget[1]:
                over.append("p99")
            verdict = "ok" if not over else f"OVER {'+'.join(over)}"
        lines.append(
            f"{step.page:<11} {step.step:<30} {step.count:>5} {step.p50:>8.2f} "
            f"{step.p95:>8.2f} {step.p99:>8.2f} {step.maximum:>9.2f}  "
            f"{rendered_budget:>15}  {verdict}"
        )
    return "\n".join(lines)


def _read_step_budgets(window: Path) -> dict[str, tuple[float, float]]:
    """The budgets the script recorded next to its measurements.

    Read back from `summary.json` rather than re-parsed out of the JavaScript,
    so the table can never disagree with the thresholds k6 actually applied.
    """
    steps = _read_summary(window).get("steps")
    if not isinstance(steps, dict):
        return {}
    budgets: dict[str, tuple[float, float]] = {}
    for key, entry in steps.items():
        if not isinstance(entry, dict):
            continue
        budget = entry.get("budget")
        if not isinstance(budget, dict):
            continue
        p95 = budget.get("p95")
        p99 = budget.get("p99")
        if isinstance(p95, int | float) and isinstance(p99, int | float):
            budgets[str(key)] = (float(p95), float(p99))
    return budgets


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
