"""Sample how long an ``fdatasync`` costs on the filesystem Postgres commits to.

The gate's tail cannot currently be attributed. Cold traffic — the popularity
fallback, which never reaches the model sidecar or the feature server — carries
the same p99 as warm traffic on the runners that breach, so whatever is slow
lives in the path both share: auth, pgBouncer, the per-request transaction, and
the COMMIT at the end of it. That COMMIT is one ``fdatasync`` per request
(``synchronous_commit`` is on), which makes the storage under Postgres's data
directory a suspect the evidence tree says nothing about today.

So this samples the suspect directly. Every ``--interval`` it rewrites the same
8 KiB block in a probe file *on Postgres's own data volume* and times the
``fdatasync`` that follows. The timestamp recorded is the one at which the sync
returned, matching how k6 stamps a request, so `summarize.py` can put a slow
second's latency next to that second's sync cost and see whether they move
together.

On start it also runs a short burst of back-to-back operations and writes their
percentile summary as the first line, so a window has a *pre-window* baseline:
a device that is already syncing in tens of milliseconds before any load
arrives is a different finding from one that degrades once the service starts
writing.

**The continuous sampler is opt-in, and this is why.** Four ``fdatasync`` calls
a second is a rounding error next to the ~55 the service issues — in *volume*.
It is not a rounding error in *effect*: each one is a device cache flush, and a
flush is not per-file. Measured on a Docker Desktop host (2026-08-26, identical
stack, three 60-second windows):

    probe off, Postgres timing counters on   p50 6.87  p95 10.57  p99  29.64
    probe on, sampling every 250 ms          p50 7.33  p95 47.71  p99 124.74

p95 moved by a factor of 4.5 and the gate failed. The probe was measuring
itself. So ``run_gate.sh`` runs ``--once`` by default — the burst alone, before
the window opens, which costs the measurement nothing — and the continuous
sampler only when ``LOAD_FSYNC_PROBE=on`` asks for it, on the understanding
that the window it produces is diagnostic and not a gate verdict. Whether the
same effect appears on a runner with a real block device underneath it is
exactly what turning it on once will answer.

Runs inside a throwaway container that mounts Postgres's volume (see
``run_gate.sh``), as uid 999, so the probe file lands on the same filesystem as
the WAL rather than on whatever the runner's working directory happens to be.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType
from typing import IO, Any

BLOCK_BYTES = 8192
BASELINE_OPS = 200
INTERVAL_S = 0.25
# A safety stop only: the caller stops the container when the window ends.
MAX_SECONDS = 900.0

_stopping = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample fdatasync latency on Postgres's volume.")
    parser.add_argument("--path", required=True, type=Path, help="Probe file; removed on exit.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=INTERVAL_S)
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    parser.add_argument("--baseline-ops", type=int, default=BASELINE_OPS)
    parser.add_argument("--block-bytes", type=int, default=BLOCK_BYTES)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the baseline burst and exit, without sampling during the window.",
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        descriptor = os.open(str(args.path), os.O_CREAT | os.O_WRONLY, 0o600)
    except OSError as error:
        # Never fail the gate over its own instrumentation: record why the
        # evidence is missing and let the window run without it.
        args.output.write_text(json.dumps({"available": False, "reason": str(error)}) + "\n")
        print(f"[fsync-probe] cannot open {args.path}: {error}", file=sys.stderr)
        return 0

    payload = b"\0" * max(args.block_bytes, 1)
    try:
        with args.output.open("w", buffering=1) as stream:
            baseline = _burst(descriptor, payload, max(args.baseline_ops, 1))
            baseline.update({"path": str(args.path), "block_bytes": len(payload)})
            stream.write(json.dumps(baseline) + "\n")
            if not args.once:
                _sample_loop(descriptor, payload, stream, args)
    finally:
        os.close(descriptor)
        args.path.unlink(missing_ok=True)
    return 0


def _stop(signum: int, frame: FrameType | None) -> None:
    global _stopping
    _stopping = True


def _sample_loop(
    descriptor: int, payload: bytes, stream: IO[str], args: argparse.Namespace
) -> None:
    deadline = time.time() + args.max_seconds
    while not _stopping and time.time() < deadline:
        # A signal lands here and returns early, so a stop is honoured within
        # one interval rather than after the whole window.
        time.sleep(args.interval)
        if _stopping:
            break
        stream.write(json.dumps(_sample(descriptor, payload)) + "\n")


def _sample(descriptor: int, payload: bytes) -> dict[str, float]:
    """One timed sync of one rewritten block.

    Only the ``fdatasync`` is timed: the write itself lands in the page cache,
    and it is the trip to the device that a COMMIT waits on.
    """
    os.pwrite(descriptor, payload, 0)
    started = time.perf_counter()
    _sync(descriptor)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    # Stamped when the sync returned, so a stall is attributed to the second it
    # finished in — the same convention k6 uses for a request.
    return {"ts": round(time.time(), 3), "fdatasync_ms": round(elapsed_ms, 3)}


def _sync(descriptor: int) -> None:
    """Flush the block the way a COMMIT does.

    The probe always runs inside a Linux container against Postgres's own
    volume, which is the measurement that matters. The `fsync` branch exists
    only so the module still runs if somebody executes it directly on a Mac,
    where there is no `fdatasync` at all — a different call with a different
    cost, so a sample taken there is a sanity check and not evidence.
    """
    if sys.platform == "linux":
        os.fdatasync(descriptor)
    else:
        os.fsync(descriptor)


def _burst(descriptor: int, payload: bytes, operations: int) -> dict[str, Any]:
    durations = [_sample(descriptor, payload)["fdatasync_ms"] for _ in range(operations)]
    summary = summarize(durations)
    summary["kind"] = "baseline"
    return summary


def summarize(durations_ms: list[float]) -> dict[str, Any]:
    """Percentile summary of a set of sync durations.

    Nearest-rank, matching ``summarize.py`` — this is a shape to read, not a
    threshold anything is compared against.
    """
    ordered = sorted(durations_ms)
    if not ordered:
        return {"ops": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    return {
        "ops": len(ordered),
        "p50_ms": round(_percentile(ordered, 0.50), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "p99_ms": round(_percentile(ordered, 0.99), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _percentile(ordered: list[float], fraction: float) -> float:
    rank = math.ceil(fraction * len(ordered))
    return ordered[min(max(rank, 1), len(ordered)) - 1]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
