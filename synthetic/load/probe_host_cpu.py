"""Sample the machine's CPU accounting once a second while the gate measures.

The load gate's p99 cannot tell "the service got slower" apart from "the VM the
service runs on was not scheduled". `/proc/stat` can: `steal` is time the
hypervisor gave to somebody else while this kernel had work to run, and
`procs_running` is how deep the run queue was. Sampled per second and joined
against the per-second latency buckets, they turn an unexplained tail into
either evidence of preemption or evidence against it.

On a Linux CI runner this reads `/proc/stat` directly. On a Docker Desktop host
there is no `/proc`, so `--container` falls back to reading the same file from
inside a running container, which reports the Linux VM's kernel — the layer
whose preemption we care about there.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")
# /proc/stat's aggregate line, in order, after the "cpu" label.
CPU_FIELDS = (
    "user",
    "nice",
    "system",
    "idle",
    "iowait",
    "irq",
    "softirq",
    "steal",
    "guest",
    "guest_nice",
)


@dataclass(frozen=True)
class CpuSample:
    at: float
    totals: dict[str, int]
    procs_running: int
    procs_blocked: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=1.0)
    # A safety stop only: the caller kills this process when the run ends.
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument(
        "--container",
        default="",
        help="Read /proc/stat from inside this container when the host has none.",
    )
    args = parser.parse_args(argv)

    reader = _make_reader(args.container)
    if reader is None:
        args.output.write_text(
            json.dumps({"available": False, "reason": "no readable /proc/stat"}) + "\n"
        )
        print("[cpu-probe] no readable /proc/stat; steal correlation will be unavailable")
        return 0

    previous = _read_sample(reader)
    deadline = time.time() + args.max_seconds
    with args.output.open("w", buffering=1) as stream:
        while time.time() < deadline:
            time.sleep(args.interval)
            current = _read_sample(reader)
            if current is None or previous is None:
                previous = current
                continue
            record = _delta(previous, current)
            if record is not None:
                stream.write(json.dumps(record) + "\n")
            previous = current
    return 0


def _make_reader(container: str) -> Callable[[], str] | None:
    """Return a callable producing raw /proc/stat text, or None."""
    if PROC_STAT.is_file():
        return PROC_STAT.read_text
    if not container:
        return None

    def read_from_container() -> str:
        result = subprocess.run(
            ["docker", "exec", container, "cat", "/proc/stat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout

    try:
        if read_from_container().startswith("cpu"):
            return read_from_container
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _read_sample(reader: Callable[[], str]) -> CpuSample | None:
    try:
        text = reader()
    except (OSError, subprocess.SubprocessError):
        return None
    totals: dict[str, int] = {}
    procs_running = 0
    procs_blocked = 0
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            for name, raw in zip(CPU_FIELDS, parts[1:], strict=False):
                totals[name] = int(raw)
        elif parts[0] == "procs_running":
            procs_running = int(parts[1])
        elif parts[0] == "procs_blocked":
            procs_blocked = int(parts[1])
    if not totals:
        return None
    return CpuSample(time.time(), totals, procs_running, procs_blocked)


def _delta(previous: CpuSample, current: CpuSample) -> dict[str, float] | None:
    total = sum(
        current.totals.get(name, 0) - previous.totals.get(name, 0)
        for name in CPU_FIELDS
        # guest time is already counted inside user/nice, so adding it again
        # would inflate the denominator and understate every percentage.
        if name not in ("guest", "guest_nice")
    )
    if total <= 0:
        return None
    record: dict[str, float] = {"at": round(current.at, 3)}
    for name in CPU_FIELDS:
        if name in ("guest", "guest_nice"):
            continue
        jiffies = current.totals.get(name, 0) - previous.totals.get(name, 0)
        record[f"{name}_pct"] = round(100.0 * jiffies / total, 2)
    record["procs_running"] = float(current.procs_running)
    record["procs_blocked"] = float(current.procs_blocked)
    return record


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
