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
a gate pass — k6's exit status is still the verdict — it only decides whether a
breached window is allowed one honest repeat.
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

# Only the tagged scenario traffic is the measurement. Warm-up and readiness
# requests carry their own endpoint tags and are excluded here exactly as they
# are excluded from the thresholds.
MEASURED_ENDPOINT = "recommendations"
LATENCY_METRIC = "http_req_duration"
RAW_METRICS_NAMES = ("raw-metrics.json.gz", "raw-metrics.json")
HOST_CPU_NAME = "host-cpu.jsonl"
SUMMARY_NAME = "summary.json"
SLOWEST_SECONDS = 10
SLO_MS = 100.0

# --- the re-measure rule ----------------------------------------------------
# A breached window is re-measured at most once, and only when the seconds that
# breached line up with the host being preempted. The constants are the rule:
#
#   re-measure  <=>  p99 > SLO  AND  at least RE_MEASURE_MIN_SECONDS of the ten
#                    slowest seconds recorded steal >= RE_MEASURE_STEAL_PCT
#
# 10% steal in a one-second window on a 4-vCPU runner is roughly "we lost more
# than a third of a core to somebody else for that second" — far outside what a
# quiet runner shows, and enough to move a p99 built from ~55 samples. Requiring
# three such seconds keeps a single blip from buying a rerun. Both numbers are
# provisional until several CI runs have recorded steal; the artifact carries
# the raw per-second values so they can be re-derived rather than guessed again.
RE_MEASURE_STEAL_PCT = 10.0
RE_MEASURE_MIN_SECONDS = 3
RE_MEASURE_RULE = (
    f"p99 > {SLO_MS:.0f} ms AND >= {RE_MEASURE_MIN_SECONDS} of the {SLOWEST_SECONDS} "
    f"slowest seconds at >= {RE_MEASURE_STEAL_PCT:.0f}% CPU steal"
)

_NANOSECOND_TIME = re.compile(r"^(.*\.\d{1,6})\d*([+-]\d{2}:\d{2})$")


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
            "traffic": self.traffic,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize one k6 measurement window.")
    parser.add_argument("window", type=Path, help="Directory holding one window's output.")
    parser.add_argument(
        "--final",
        action="store_true",
        help="This window is the verdict; never ask for another measurement.",
    )
    args = parser.parse_args(argv)
    window: Path = args.window

    raw = _find_raw_metrics(window)
    if raw is None:
        print(f"[load-summary] no k6 sample stream under {window}; skipping the breakdown")
        _write_decision(window, _unavailable_decision("no k6 sample stream"))
        return 0

    steal = _read_host_cpu(window / HOST_CPU_NAME)
    buckets = _bucket_by_second(raw, steal)
    if not buckets:
        print(f"[load-summary] {raw.name} holds no {MEASURED_ENDPOINT}-tagged latency samples")
        _write_decision(window, _unavailable_decision("no measured latency samples"))
        return 0

    (window / "per-second.txt").write_text(f"{_render_table(buckets)}\n")
    (window / "per-second.json").write_text(
        json.dumps([bucket.as_dict() for bucket in buckets], indent=2) + "\n"
    )

    slowest = sorted(buckets, key=lambda bucket: bucket.p99, reverse=True)[:SLOWEST_SECONDS]
    decision = _decide(window, buckets, slowest, final=args.final)
    _write_decision(window, decision)
    _report(window, buckets, slowest, decision)
    # The shell wrapper greps this line rather than parsing JSON in sh.
    print(f"REMEASURE={'yes' if decision['remeasure'] else 'no'}")
    return 0


def _report(
    window: Path,
    buckets: list[SecondBucket],
    slowest: list[SecondBucket],
    decision: dict[str, Any],
) -> None:
    total = sum(bucket.count for bucket in buckets)
    over_slo = sum(bucket.over_slo for bucket in buckets)
    print(
        f"\n[load-summary] {window.name}: {total} measured requests across {len(buckets)} "
        f"seconds; {over_slo} over {SLO_MS:.0f} ms ({_share(over_slo, total)}). "
        f"Full table: {window / 'per-second.txt'}"
    )
    print(f"[load-summary] slowest {len(slowest)} seconds by p99, with host CPU steal:")
    print(_render_table(sorted(slowest, key=lambda bucket: bucket.second)))
    print("[load-summary] opening second of the measured window:")
    print(_render_table(buckets[:1]))
    print(f"[load-summary] {decision['label']}")


def _decide(
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

    measured = [bucket for bucket in slowest if bucket.steal_pct is not None]
    high = [
        bucket
        for bucket in measured
        if bucket.steal_pct is not None and bucket.steal_pct >= RE_MEASURE_STEAL_PCT
    ]
    max_steal = max((bucket.steal_pct or 0.0 for bucket in buckets), default=0.0)
    available = bool(measured)

    remeasure = not final and breached and available and len(high) >= RE_MEASURE_MIN_SECONDS
    if final:
        label = f"final window: p99 {p99:.2f} ms — this verdict stands regardless of steal"
    elif not breached:
        label = f"within the SLO at p99 {p99:.2f} ms; no re-measurement considered"
    elif not available:
        label = (
            f"p99 {p99:.2f} ms breached and no host CPU accounting was captured, so the "
            "breach is treated as the service's and fails immediately"
        )
    elif remeasure:
        label = (
            f"re-measured after hypervisor steal: {len(high)} of the {len(measured)} slowest "
            f"seconds at or above {RE_MEASURE_STEAL_PCT:.0f}% steal (peak {max_steal:.1f}%) "
            f"while p99 was {p99:.2f} ms"
        )
    else:
        label = (
            f"not re-measured: p99 {p99:.2f} ms breached with only {len(high)} of the "
            f"{len(measured)} slowest seconds at or above {RE_MEASURE_STEAL_PCT:.0f}% steal "
            f"(peak {max_steal:.1f}%) — a low-steal breach is the service's"
        )
    return {
        "window": window.name,
        "rule": RE_MEASURE_RULE,
        "p99_ms": round(p99, 2),
        "p99_source": p99_source,
        "slo_ms": SLO_MS,
        "p99_breached": breached,
        "steal_available": available,
        "steal_peak_pct": round(max_steal, 2),
        "slowest_seconds_considered": len(measured),
        "slowest_seconds_above_threshold": len(high),
        "steal_threshold_pct": RE_MEASURE_STEAL_PCT,
        "min_seconds_above_threshold": RE_MEASURE_MIN_SECONDS,
        "remeasure": remeasure,
        "label": label,
    }


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


def _bucket_by_second(raw: Path, steal: dict[int, dict[str, float]]) -> list[SecondBucket]:
    samples: list[tuple[float, float, str]] = []
    opener: Callable[..., IO[str]] = gzip.open if raw.suffix == ".gz" else open
    with opener(raw, "rt") as stream:
        for line in stream:
            sample = _read_sample(line)
            if sample is not None:
                samples.append(sample)
    if not samples:
        return []

    start = min(sample[0] for sample in samples)
    values: defaultdict[int, list[float]] = defaultdict(list)
    traffic: defaultdict[int, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for timestamp, value, class_name in samples:
        second = int(timestamp - start)
        values[second].append(value)
        traffic[second][class_name] += 1

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
            )
        )
    return buckets


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _read_sample(line: str) -> tuple[float, float, str] | None:
    """Return (epoch seconds, latency ms, traffic class) for one measured point."""
    text = line.strip()
    # Cheap prefilter: the stream is mostly metrics this never looks at, and
    # json.loads on every line is the slow way to discover that.
    if not text or LATENCY_METRIC not in text or MEASURED_ENDPOINT not in text:
        return None
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    if record.get("type") != "Point" or record.get("metric") != LATENCY_METRIC:
        return None
    data = record.get("data") or {}
    tags = data.get("tags") or {}
    if tags.get("endpoint") != MEASURED_ENDPOINT:
        return None
    try:
        timestamp = _parse_time(str(data["time"]))
        value = float(data["value"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp, value, str(tags.get("traffic", "untagged"))


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


def _render_table(buckets: list[SecondBucket]) -> str:
    header = (
        f"{'sec':>4} {'reqs':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>9} "
        f"{'>slo':>5} {'steal%':>7} {'runq':>5}  traffic"
    )
    lines = [header, "-" * len(header)]
    for bucket in buckets:
        traffic = " ".join(f"{name}={count}" for name, count in bucket.traffic.items())
        steal = "n/a" if bucket.steal_pct is None else f"{bucket.steal_pct:.1f}"
        run_queue = "n/a" if bucket.run_queue is None else f"{bucket.run_queue:.0f}"
        lines.append(
            f"{bucket.second:>4} {bucket.count:>5} {bucket.p50:>8.2f} {bucket.p95:>8.2f} "
            f"{bucket.p99:>8.2f} {bucket.maximum:>9.2f} {bucket.over_slo:>5} "
            f"{steal:>7} {run_queue:>5}  {traffic}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
