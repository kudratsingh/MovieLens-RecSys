"""Benchmark the exported SASRec encoder at its request-time boundary.

This is deliberately narrower than the authenticated service load gate. It
measures one history-to-vector call through ``encode_movie_history`` so the
Transformer's own latency can be held to ADR 0016's 15 ms p99 budget before it
is integrated into the model sidecar.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch

from src.models.candidates.sasrec_artifact import SASRecArtifactManifest, load_sasrec

DEFAULT_ITERATIONS = 10_000
DEFAULT_WARMUP_ITERATIONS = 500
ENCODER_P99_BUDGET_MS = 15.0


@dataclass(frozen=True)
class EncoderLatencySummary:
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True)
class EncoderLatencyReport:
    manifest_path: str
    artifact_sha256: str
    iterations: int
    warmup_iterations: int
    history_length: int
    summary: EncoderLatencySummary
    p99_budget_ms: float
    passed: bool
    platform: str
    machine: str
    python_version: str
    torch_version: str
    torch_num_threads: int
    torch_num_interop_threads: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def summarize_latency_ns(samples_ns: list[int]) -> EncoderLatencySummary:
    """Summarize positive nanosecond samples in milliseconds."""
    if not samples_ns:
        raise ValueError("at least one latency sample is required")
    if any(type(sample) is not int or sample <= 0 for sample in samples_ns):
        raise ValueError("latency samples must be positive integer nanoseconds")
    samples_ms = np.asarray(samples_ns, dtype=np.float64) / 1_000_000.0
    return EncoderLatencySummary(
        mean_ms=statistics.fmean(float(value) for value in samples_ms),
        p50_ms=float(np.quantile(samples_ms, 0.50, method="linear")),
        p95_ms=float(np.quantile(samples_ms, 0.95, method="linear")),
        p99_ms=float(np.quantile(samples_ms, 0.99, method="linear")),
        max_ms=float(np.max(samples_ms)),
    )


def benchmark_encoder(
    manifest_path: Path,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    warmup_iterations: int = DEFAULT_WARMUP_ITERATIONS,
    history_length: int | None = None,
    p99_budget_ms: float = ENCODER_P99_BUDGET_MS,
) -> EncoderLatencyReport:
    """Load the checksum-pinned model and measure its public encoder seam."""
    if type(iterations) is not int or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if type(warmup_iterations) is not int or warmup_iterations < 0:
        raise ValueError("warmup_iterations must be a non-negative integer")
    if history_length is not None and (type(history_length) is not int or history_length <= 0):
        raise ValueError("history_length must be a positive integer")
    if (
        not isinstance(p99_budget_ms, (int, float))
        or isinstance(p99_budget_ms, bool)
        or not math.isfinite(float(p99_budget_ms))
        or p99_budget_ms <= 0
    ):
        raise ValueError("p99_budget_ms must be a finite positive number")

    manifest = SASRecArtifactManifest.load(manifest_path)
    model = load_sasrec(manifest_path)
    resolved_history_length = history_length or manifest.max_sequence_length
    item_ids = [
        model._index_to_item[(index % len(model._index_to_item)) + 1]
        for index in range(resolved_history_length)
    ]

    for _ in range(warmup_iterations):
        model.encode_movie_history(item_ids)

    samples_ns: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        model.encode_movie_history(item_ids)
        elapsed = time.perf_counter_ns() - started
        samples_ns.append(max(1, elapsed))

    summary = summarize_latency_ns(samples_ns)
    return EncoderLatencyReport(
        manifest_path=str(manifest_path.resolve()),
        artifact_sha256=manifest.model_sha256,
        iterations=iterations,
        warmup_iterations=warmup_iterations,
        history_length=resolved_history_length,
        summary=summary,
        p99_budget_ms=float(p99_budget_ms),
        passed=summary.p99_ms < p99_budget_ms,
        platform=platform.platform(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        torch_version=torch.__version__,
        torch_num_threads=torch.get_num_threads(),
        torch_num_interop_threads=torch.get_num_interop_threads(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.evaluation.sasrec_latency",
        description="Measure the checksum-pinned SASRec history encoder against its p99 budget.",
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup-iterations", type=int, default=DEFAULT_WARMUP_ITERATIONS)
    parser.add_argument("--history-length", type=int, default=None)
    parser.add_argument("--p99-budget-ms", type=float, default=ENCODER_P99_BUDGET_MS)
    args = parser.parse_args(argv)

    # Pin the same single-thread topology used for the full training run. Do it
    # before model construction so the report means one stable runtime shape.
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        # PyTorch refuses to change this after parallel work starts. The report
        # records the actual value, so a caller cannot mistake that run for the
        # requested topology.
        pass

    try:
        report = benchmark_encoder(
            args.manifest,
            iterations=args.iterations,
            warmup_iterations=args.warmup_iterations,
            history_length=args.history_length,
            p99_budget_ms=args.p99_budget_ms,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "reason": str(error)}, sort_keys=True))
        return 2

    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
