"""Time SASRec optimizer steps on any device, bounded by steps or by seconds (WO-5).

WO-5's first step on rented hardware is a timing, not a training run: how long one
optimizer step of ADR 0020's cells takes on a ``cuda`` card, so the sweep can be
priced from a measurement instead of an assumed speed-up. The WO-5 preparation
timed cell A on the CPU and on ``mps`` with a throwaway script — 2,000 steps, the
first 20 excluded as warm-up, the steady mean multiplied by the steps in a pass
(``docs/model-planning/experiments/wo5-cell-repricing.md``). This module is that
method, committed, with a second way to bound it: by seconds of steady-state
stepping, because a rented card is billed by the second and its speed is the very
thing nobody knows yet.

Subcommands::

    run <cells.json> --out DIR      time each cell in a cells file, one JSON per cell
    summarize SESSION_DIR           fold a session's results into summary.json
    record SESSION_DIR              log a session to MLflow as a tagged timing run
    prepare-inputs / verify-inputs  fetch-side and read-side checks of ratings/movies

What a timing does not do: evaluate, export, or log to MLflow. The fit is
interrupted after the last timed step, exactly as the WO-4 script did, so nothing
it produces can be mistaken for a model. Every output says so in
``result_status``, and ``record`` tags its MLflow run ``timing_only`` and
``not_a_result_of_record``.

The fitted frame is ``split.train`` without ADR 0011's synthetic cold-start
cohort, as in the WO-4 timing: the cohort's users each carry one timestamp, so
they add no training example and no step, and the cohort file is not on a rented
machine. The protocol hash recorded here is therefore the no-cohort hash, and a
cells file can pin it so a pod reading different data fails rather than timing it.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import gc
import hashlib
import json
import logging
import math
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

import src.models.candidates.sasrec as sasrec
from src.config import Settings
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES
from src.models.candidates.sasrec import (
    DEVICES,
    LEGACY_TRAINING_OBJECTIVE,
    SASRecConfig,
    SASRecModel,
    SASRecTrainingStep,
)
from src.training import protocol_manifest
from src.training.candidate_data import (
    INPUT_DIR_ENV_VAR,
    load_inputs,
    sample_and_split,
    sealed_partition_params,
)
from src.training.sasrec import SUBSAMPLE_SEED, peak_rss_bytes
from src.training.sasrec_sweep import parse_grid

logger = logging.getLogger(__name__)

TIMING_KIND = "sasrec-step-timing"
SUMMARY_KIND = "sasrec-timing-session-summary"
SCHEMA_VERSION = 1
RESULT_STATUS = "timing only - not a result of record"
# WO-4's choice, kept so a GPU step and the CPU/mps steps it is compared with
# exclude the same warm-up.
DEFAULT_WARMUP_STEPS = 20
INPUT_FILES = ("ratings.csv", "movies.csv")
INPUT_MANIFEST_PATH = (
    protocol_manifest.REPO_ROOT / "docs" / "experiments" / "sasrec" / "ml-25m-input-sha256.json"
)
# A timing session directory, as infra/gpu/bootstrap.sh lays it out.
RESULTS_DIRNAME = "results"
FACTS_FILENAME = "env/bootstrap-facts.env"
SUMMARY_FILENAME = "summary.json"
GPU_SMOKE_FILENAME = "gpu-smoke.json"
# Below this many steady steps the percentiles say little; the output warns.
MIN_STEADY_STEPS = 10
_HASH_CHUNK = 8 * 1024 * 1024
_ARCHIVE_DIRNAME = "ml-25m"


class BudgetSpentError(Exception):
    """Raised from ``on_step`` to end a timed fit; never escapes ``time_cell``."""


class TimingCheckError(RuntimeError):
    """A pre-declared check failed; the output is written, the exit code is not 0."""


# --- the budget and the clock ------------------------------------------------------


@dataclass(frozen=True)
class TimingBudget:
    """How long to time: a step count or seconds of steady stepping, never both.

    ``warmup_steps`` is at least 1 because the first step's stamp carries the
    whole fit setup (the example store, the encoder's move to the device), so it
    can never be a steady-state step.
    """

    timing_seconds: float | None = None
    steps: int | None = None
    warmup_steps: int = DEFAULT_WARMUP_STEPS

    @property
    def mode(self) -> str:
        return "seconds" if self.timing_seconds is not None else "steps"

    def validate(self) -> None:
        if (self.timing_seconds is None) == (self.steps is None):
            raise ValueError("a timing budget sets exactly one of timing_seconds and steps")
        if self.warmup_steps < 1:
            raise ValueError("warmup_steps must be at least 1: the first step carries setup")
        if self.timing_seconds is not None and not self.timing_seconds > 0:
            raise ValueError("timing_seconds must be positive")
        if self.steps is not None and self.steps <= self.warmup_steps:
            raise ValueError("steps must exceed warmup_steps, or nothing steady is timed")

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> TimingBudget:
        """Read a cells file's ``timing`` block (WO-4's ``steps`` form, or ``timing_seconds``)."""
        block = spec.get("timing")
        if not isinstance(block, Mapping):
            raise ValueError("cells file has no 'timing' block")
        seconds = block.get("timing_seconds")
        steps = block.get("steps")
        budget = cls(
            timing_seconds=None if seconds is None else float(seconds),
            steps=None if steps is None else int(steps),
            warmup_steps=int(block.get("warmup_steps_excluded", DEFAULT_WARMUP_STEPS)),
        )
        budget.validate()
        return budget

    def overridden(
        self,
        *,
        timing_seconds: float | None = None,
        steps: int | None = None,
        warmup_steps: int | None = None,
    ) -> TimingBudget:
        """A command-line bound replaces the file's bound of either kind."""
        if timing_seconds is not None and steps is not None:
            raise ValueError("override timing_seconds or steps, not both")
        budget = self
        if timing_seconds is not None:
            budget = dataclasses.replace(budget, timing_seconds=timing_seconds, steps=None)
        if steps is not None:
            budget = dataclasses.replace(budget, steps=steps, timing_seconds=None)
        if warmup_steps is not None:
            budget = dataclasses.replace(budget, warmup_steps=warmup_steps)
        budget.validate()
        return budget

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "timing_seconds": self.timing_seconds,
            "steps": self.steps,
            "warmup_steps_excluded": self.warmup_steps,
        }


@dataclass
class StepClock:
    """Stamps every optimizer step and says when the budget is spent.

    ``start`` is called just before ``fit``; ``tick`` after each step. Steady
    time starts at the end of the last warm-up step, so in seconds mode the
    budget is steady stepping only — data loading, the example store and the
    warm-up steps are all outside it.

    No device synchronization is added. Every step already ends in a host read
    (the loss and the gradient norm are ``.item()`` calls), so consecutive
    stamps measure throughput as a real run sees it, with the next batch's host
    work free to overlap the optimizer's last kernels.
    """

    budget: TimingBudget
    clock: Callable[[], float] = time.perf_counter
    started_at: float | None = None
    stamps: list[float] = field(default_factory=list)

    def start(self) -> None:
        self.started_at = self.clock()

    @property
    def steady_started_at(self) -> float | None:
        warmup = self.budget.warmup_steps
        return self.stamps[warmup - 1] if len(self.stamps) >= warmup else None

    def tick(self) -> bool:
        """Record one step; True once the budget is spent."""
        if self.started_at is None:
            raise RuntimeError("StepClock.start() was not called")
        now = self.clock()
        self.stamps.append(now)
        if self.budget.steps is not None:
            return len(self.stamps) >= self.budget.steps
        steady_start = self.steady_started_at
        assert self.budget.timing_seconds is not None
        return steady_start is not None and now - steady_start >= self.budget.timing_seconds

    @property
    def step_seconds(self) -> list[float]:
        """Each step's duration; the first includes everything since ``start``."""
        if self.started_at is None or not self.stamps:
            return []
        return [self.stamps[0] - self.started_at] + [
            later - earlier for earlier, later in zip(self.stamps, self.stamps[1:])
        ]

    @property
    def steady_seconds(self) -> list[float]:
        return self.step_seconds[self.budget.warmup_steps :]


def step_statistics(steady: Sequence[float]) -> dict[str, float | int | None]:
    """Mean, median, p10/p90 and the two halves, as the WO-4 timing reported them."""
    if not steady:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "p10": None,
            "p90": None,
            "min": None,
            "max": None,
            "first_half_mean": None,
            "second_half_mean": None,
        }
    deciles = statistics.quantiles(steady, n=10) if len(steady) >= 2 else [steady[0]] * 9
    half = len(steady) // 2
    return {
        "n": len(steady),
        "mean": statistics.fmean(steady),
        "median": statistics.median(steady),
        "p10": deciles[0],
        "p90": deciles[-1],
        "min": min(steady),
        "max": max(steady),
        "first_half_mean": statistics.fmean(steady[:half]) if half else None,
        "second_half_mean": statistics.fmean(steady[half:]),
    }


def steps_per_pass(n_targets: int, batch_size: int) -> int:
    """Optimizer steps in one pass of the strict-prefix trainer: ceil(examples / batch)."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return -(-n_targets // batch_size)


def extrapolation(
    mean_step_seconds: float | None,
    *,
    n_targets: int,
    config: SASRecConfig,
    expected_steps_per_pass: int | None,
) -> dict[str, Any]:
    """Seconds per full pass from the steady mean, the WO-4 method."""
    steps = steps_per_pass(n_targets, config.batch_size)
    basis = "ceil(training examples / examples per step)"
    if config.training_objective != LEGACY_TRAINING_OBJECTIVE:
        # All-positions steps pack whole windows, so a pass holds at least this
        # many steps; the strict-prefix count is exact.
        basis += "; a lower bound under an all-positions objective"
    seconds = None if mean_step_seconds is None else mean_step_seconds * steps
    return {
        "steps_per_pass": steps,
        "basis": basis,
        "expected_steps_per_pass": expected_steps_per_pass,
        "matches_expected": (
            None if expected_steps_per_pass is None else steps == expected_steps_per_pass
        ),
        "seconds_per_pass": seconds,
        "hours_per_pass": None if seconds is None else seconds / 3600,
        "hours_3_passes": None if seconds is None else 3 * seconds / 3600,
        "hours_5_passes": None if seconds is None else 5 * seconds / 3600,
    }


def speedups(references: Mapping[str, Any], mean_step_seconds: float | None) -> dict[str, Any]:
    """Each reference step time from the cells file over the measured one."""
    out: dict[str, Any] = {}
    for name, reference in references.items():
        if not isinstance(reference, Mapping) or "step_seconds" not in reference:
            continue
        value = float(reference["step_seconds"])
        out[name] = {
            **dict(reference),
            "speedup": None if not mean_step_seconds else value / mean_step_seconds,
        }
    return out


# --- what machine this is ----------------------------------------------------------


def _run_text(command: Sequence[str], timeout: float = 15.0) -> str | None:
    """stdout of a diagnostic command, or None when it is missing or fails."""
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _cpu_model() -> str | None:
    if sys.platform == "darwin":
        return _run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def _nvidia_smi() -> dict[str, str] | None:
    text = _run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,pci.bus_id",
            "--format=csv,noheader",
        ]
    )
    if not text:
        return None
    name, driver, memory, bus = (part.strip() for part in text.splitlines()[0].split(",", 3))
    header = _run_text(["nvidia-smi"]) or ""
    match = re.search(r"CUDA Version:\s*([0-9.]+)", header)
    return {
        "name": name,
        "driver_version": driver,
        "memory_total": memory,
        "pci_bus_id": bus,
        "driver_cuda_version": match.group(1) if match else "unknown",
    }


def device_info(device: str) -> dict[str, Any]:
    """The accelerator a timing ran on, as torch and the driver describe it."""
    info: dict[str, Any] = {"type": device}
    if device == "cuda" and torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        info.update(
            {
                "name": torch.cuda.get_device_name(0),
                "capability": list(torch.cuda.get_device_capability(0)),
                "total_memory_bytes": int(props.total_memory),
                "multiprocessor_count": int(props.multi_processor_count),
                "device_count": torch.cuda.device_count(),
                "nvidia_smi": _nvidia_smi(),
            }
        )
    elif device == "mps":
        info.update(
            {
                "name": f"Apple GPU via mps ({_cpu_model() or 'unknown chip'})",
                "available": bool(torch.backends.mps.is_available()),
                "macos": platform.mac_ver()[0] or None,
            }
        )
    else:
        info["name"] = _cpu_model() or "unknown CPU"
    return info


def software_info() -> dict[str, Any]:
    # Typed as Any: torch's stubs leave these two untyped in some releases
    # and not in others, and a version-specific ignore would fail on the other one.
    cudnn_backend: Any = torch.backends.cudnn
    cudnn = cudnn_backend.version() if cudnn_backend.is_available() else None
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cudnn": cudnn,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        # The memo's projections assume plain FP32 on the GPU, matching the CPU:
        # no TF32 matmuls, no autocast. Recorded so a timing that ran otherwise
        # is visible as such.
        "float32_matmul_precision": torch.get_float32_matmul_precision(),
        "cuda_matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
        "cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32),
        "cuda_matmul_fp32_precision": getattr(torch.backends.cuda.matmul, "fp32_precision", None),
        "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
        "torch_num_threads": torch.get_num_threads(),
    }


def host_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
    }


def git_info(root: Path = protocol_manifest.REPO_ROOT) -> dict[str, Any]:
    """The commit, and whether the tree differs from it, untracked files included.

    Untracked files count: a cells file or a module that was never committed
    changes what ran as surely as an edit does.
    """
    commit = _run_text(["git", "-C", str(root), "rev-parse", "HEAD"])
    status = _run_text(["git", "-C", str(root), "status", "--porcelain"])
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the inputs -------------------------------------------------------------------


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_input_manifest(path: Path = INPUT_MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != set(INPUT_FILES):
        raise ValueError(f"{path} must list exactly {list(INPUT_FILES)}")
    return dict(manifest)


def verify_inputs(
    input_dir: Path, manifest_path: Path = INPUT_MANIFEST_PATH
) -> dict[str, dict[str, Any]]:
    """SHA-256 of ``ratings.csv`` and ``movies.csv`` against the committed manifest.

    Those are the only two files ``load_inputs`` reads. The manifest was written
    from the copies every recorded run read, so a match ties a rented machine's
    data to the runs of record byte for byte.
    """
    manifest = load_input_manifest(manifest_path)
    out: dict[str, dict[str, Any]] = {}
    for name in INPUT_FILES:
        path = input_dir / name
        expected = str(manifest["files"][name]["sha256"])
        if not path.is_file():
            out[name] = {"sha256": None, "expected": expected, "matches_manifest": False}
            continue
        actual = file_digest(path)
        out[name] = {
            "sha256": actual,
            "expected": expected,
            "matches_manifest": actual == expected,
            "bytes": path.stat().st_size,
        }
    return out


def published_md5(text: str) -> str:
    """The one 32-hex-digit token in a published ``.md5`` file, whatever its layout."""
    tokens = re.findall(r"\b[0-9a-fA-F]{32}\b", text)
    if len(set(token.lower() for token in tokens)) != 1:
        raise ValueError("expected exactly one MD5 digest in the published checksum file")
    return str(tokens[0]).lower()


def prepare_inputs(
    archive: Path,
    published_md5_text: str,
    destination: Path,
    manifest_path: Path = INPUT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Check the downloaded zip against GroupLens's MD5, extract the two CSVs, verify them.

    Three checks. The MD5 must equal both what GroupLens publishes beside the
    zip and the value the manifest pins (the zip fetched once on 2026-10-07);
    the SHA-256s are this repository's own record of the bytes every run read,
    so the files themselves decide even if both MD5s were somehow wrong.
    """
    expected_md5 = published_md5(published_md5_text)
    actual_md5 = file_digest(archive, "md5")
    if actual_md5 != expected_md5:
        raise TimingCheckError(
            f"{archive} has MD5 {actual_md5}; GroupLens publishes {expected_md5}"
        )
    pinned = load_input_manifest(manifest_path).get("zip", {}).get("md5")
    if pinned is not None and actual_md5 != pinned:
        # GroupLens re-published the archive. The SHA-256s below would still
        # decide, but a moved archive is worth saying out loud.
        raise TimingCheckError(f"{archive} has MD5 {actual_md5}; the manifest pins {pinned}")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in INPUT_FILES:
            member = f"{_ARCHIVE_DIRNAME}/{name}"
            with bundle.open(member) as source, (destination / name).open("wb") as target:
                shutil.copyfileobj(source, target, length=_HASH_CHUNK)
    checks = verify_inputs(destination, manifest_path)
    failed = [name for name, check in checks.items() if not check["matches_manifest"]]
    if failed:
        raise TimingCheckError(f"SHA-256 mismatch against {manifest_path.name}: {failed}")
    return {"zip_md5": actual_md5, "published_md5": expected_md5, "files": checks}


# --- one timed cell -----------------------------------------------------------------

_BUILDERS = (
    "build_user_history",
    "build_strict_prefix_example_store",
    "build_all_position_training_data",
    "build_overlapping_all_position_training_data",
)


@contextlib.contextmanager
def _timed_builders(timings: dict[str, float]) -> Iterator[None]:
    """Time ``fit``'s data builders by wrapping the names it calls, then put them back."""
    originals = {name: getattr(sasrec, name) for name in _BUILDERS}

    def wrap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            result = original(*args, **kwargs)
            timings[f"{name}_seconds"] = time.perf_counter() - started
            return result

        return timed

    try:
        for name, original in originals.items():
            setattr(sasrec, name, wrap(name, original))
        yield
    finally:
        for name, original in originals.items():
            setattr(sasrec, name, original)


def _reset_device_peaks(device: str) -> None:
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()


def _synchronize(device: str) -> None:
    if device == "cuda" and torch.cuda.is_available():
        torch.cuda.synchronize()
    elif device == "mps" and torch.backends.mps.is_available():
        torch.mps.synchronize()


def _mps_allocated() -> int:
    return int(torch.mps.driver_allocated_memory()) if torch.backends.mps.is_available() else 0


def _device_memory(device: str, mps_peak_sampled: int) -> dict[str, Any]:
    if device == "cuda" and torch.cuda.is_available():
        return {
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "total_bytes": int(torch.cuda.get_device_properties(0).total_memory),
        }
    if device == "mps":
        return {
            # mps keeps no peak counter; this is the driver's allocation, read
            # after every step, and the largest value seen.
            "peak_driver_allocated_bytes_sampled": mps_peak_sampled,
            "recommended_max_bytes": int(torch.mps.recommended_max_memory()),
        }
    return {}


@dataclass(frozen=True)
class CellTiming:
    """What ``time_cell`` measured, and whether its pre-declared checks held."""

    document: dict[str, Any]
    failed_checks: list[str]


def time_cell(
    ratings: pd.DataFrame,
    config: SASRecConfig,
    budget: TimingBudget,
    *,
    sample_fraction: float = 1.0,
    expected_protocol_hash: str | None = None,
    expected_steps_per_pass: int | None = None,
    references: Mapping[str, Any] | None = None,
) -> CellTiming:
    """Fit ``config`` until the budget is spent and report how long each step took.

    ``ratings`` is the full frame; ``sample_fraction`` below 1 subsamples users
    and cuts at the full frame's boundaries exactly as ``run_once`` does (O-25),
    so a smoke on a subsample is held to the same sealed boundary as a full run.
    """
    config.validate()
    budget.validate()
    started_utc = _utc_now()
    seconds: dict[str, float] = {}

    t = time.perf_counter()
    sample, split = sample_and_split(
        ratings, sample_fraction=sample_fraction, sample_seed=SUBSAMPLE_SEED
    )
    partition = sealed_partition_params(ratings, split, split.train)
    seconds["split"] = time.perf_counter() - t

    t = time.perf_counter()
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=split.train,
        learned_routing_policy=protocol_manifest.routing_policy_value(COLD_START_THRESHOLD),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    protocol_hash = protocol.semantic_hash
    seconds["protocol_hash"] = time.perf_counter() - t

    device = config.device
    _reset_device_peaks(device)
    clock = StepClock(budget)
    losses: list[float] = []
    mps_peak = 0

    def on_step(step: SASRecTrainingStep) -> None:
        nonlocal mps_peak
        losses.append(step.loss)
        if device == "mps":
            mps_peak = max(mps_peak, _mps_allocated())
        if clock.tick():
            raise BudgetSpentError

    builder_seconds: dict[str, float] = {}
    model = SASRecModel(config=config, cold_start_threshold=COLD_START_THRESHOLD)
    with _timed_builders(builder_seconds):
        clock.start()
        try:
            model.fit(split.train, on_step=on_step, retrieval_backend="torch")
            stopped_by = "fit-finished"
        except BudgetSpentError:
            stopped_by = budget.mode
    _synchronize(device)
    seconds.update(builder_seconds)
    step_seconds = clock.step_seconds
    assert clock.started_at is not None
    seconds["stepping_wall"] = (clock.stamps[-1] - clock.started_at) if clock.stamps else 0.0
    if len(step_seconds) >= 2:
        # WO-4's definition: everything before the first step ended, less one
        # ordinary step (the second), so the two timings read alike.
        seconds["fit_setup_before_first_step"] = step_seconds[0] - step_seconds[1]
    steady = clock.steady_seconds
    stats = step_statistics(steady)
    training_stats = model._training_stats
    n_targets = training_stats.n_targets if training_stats is not None else 0
    mean = stats["mean"]
    mean_value = float(mean) if mean is not None else None
    usage = resource.getrusage(resource.RUSAGE_SELF)

    finite = bool(losses) and all(math.isfinite(value) for value in losses)
    window = min(20, len(losses))
    checks: dict[str, bool | None] = {
        "all_losses_finite": finite,
        "steady_steps_timed": len(steady) > 0,
        "protocol_hash_matches_expected": (
            None if expected_protocol_hash is None else protocol_hash == expected_protocol_hash
        ),
    }
    extrapolated = extrapolation(
        mean_value,
        n_targets=n_targets,
        config=config,
        expected_steps_per_pass=expected_steps_per_pass,
    )
    checks["steps_per_pass_matches_expected"] = extrapolated["matches_expected"]
    warnings: list[str] = []
    if 0 < len(steady) < MIN_STEADY_STEPS:
        warnings.append(f"only {len(steady)} steady steps; the percentiles are rough")
    if stopped_by == "fit-finished":
        warnings.append("the fit ended before the budget; every step it took is timed")

    document: dict[str, Any] = {
        "kind": TIMING_KIND,
        "schema_version": SCHEMA_VERSION,
        "result_status": RESULT_STATUS,
        "window_utc": {"start": started_utc, "end": _utc_now()},
        "config": config.as_params(),
        "budget": budget.as_dict(),
        "stopped_by": stopped_by,
        "device": device_info(device),
        "software": software_info(),
        "host": host_info(),
        "git": git_info(),
        "data": {
            "sample_fraction": sample_fraction,
            "subsample_seed": SUBSAMPLE_SEED if sample_fraction != 1.0 else None,
            "n_ratings_read": len(ratings),
            "n_ratings_in_sample": len(sample),
            "n_train_rows": len(split.train),
            "raw_data_revision": protocol.raw_data_revision,
        },
        "protocol": {
            "semantic_hash": protocol_hash,
            "fitted_frame": "split.train, without ADR 0011's cohort (it adds no training example)",
            "expected_semantic_hash": expected_protocol_hash,
        },
        "partition": partition,
        "seconds": seconds,
        "steps": {
            "completed": len(clock.stamps),
            "warmup_excluded": budget.warmup_steps,
            "steady": len(steady),
            "first_step_seconds_including_setup": step_seconds[0] if step_seconds else None,
        },
        "step_seconds_steady": stats,
        "losses": {
            "first": losses[0] if losses else None,
            "last": losses[-1] if losses else None,
            "first_20_mean": statistics.fmean(losses[:window]) if window else None,
            "last_20_mean": statistics.fmean(losses[-window:]) if window else None,
            "all_finite": finite,
        },
        "training_examples": {
            "n_targets": n_targets,
            "n_truncated_sequences": (
                training_stats.n_truncated_sequences if training_stats is not None else 0
            ),
            "example_store_bytes": model._training_example_bytes,
            "n_items": len(model._index_to_item),
        },
        "extrapolation": extrapolated,
        "references": speedups(references or {}, mean_value),
        "memory": {
            "peak_rss_bytes": peak_rss_bytes(),
            "device": _device_memory(device, mps_peak),
        },
        "process_cpu_seconds": {"user": usage.ru_utime, "system": usage.ru_stime},
        "checks": checks,
        "warnings": warnings,
        "step_seconds_all": [round(value, 6) for value in step_seconds],
    }
    failed = [name for name, passed in checks.items() if passed is False]
    return CellTiming(document=document, failed_checks=failed)


# --- the run subcommand -------------------------------------------------------------


@dataclass(frozen=True)
class Overrides:
    """Command-line changes to a cells file, each recorded in the output."""

    device: str | None = None
    sample_fraction: float | None = None
    timing_seconds: float | None = None
    steps: int | None = None
    warmup_steps: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in dataclasses.asdict(self).items() if value is not None}


def _relative(path: Path) -> str:
    with contextlib.suppress(ValueError):
        return str(path.resolve().relative_to(protocol_manifest.REPO_ROOT))
    return str(path)


def run_cells(
    cells_path: Path,
    out_dir: Path,
    *,
    ratings: pd.DataFrame | None = None,
    input_dir: Path | None = None,
    labels: Sequence[str] = (),
    overrides: Overrides = Overrides(),
    run_kind: str = "timing",
) -> list[Path]:
    """Time each selected cell of a cells file; write one JSON per cell into ``out_dir``.

    The file is ``<label>.json``, or ``<label>__<run_kind>-<device>.json`` when
    anything was overridden.

    Returns the written paths. Raises ``TimingCheckError`` after writing every
    output when any cell's pre-declared check failed.
    """
    raw = cells_path.read_bytes()
    spec = json.loads(raw)
    if spec.get("hold"):
        raise TimingCheckError(f"cells file {cells_path} is on hold: {spec['hold']}")
    sample_fraction, cells = parse_grid(spec)
    timing = spec.get("timing", {})
    budget = TimingBudget.from_spec(spec).overridden(
        timing_seconds=overrides.timing_seconds,
        steps=overrides.steps,
        warmup_steps=overrides.warmup_steps,
    )
    if labels:
        unknown = set(labels) - {label for label, _ in cells}
        if unknown:
            raise ValueError(f"no cells labelled {sorted(unknown)} in {cells_path}")
        cells = [(label, config) for label, config in cells if label in labels]
    if overrides.sample_fraction is not None:
        sample_fraction = overrides.sample_fraction
    # A pinned hash or step count describes the file's data as written; on a
    # subsample it no longer applies and is not checked.
    data_as_written = overrides.sample_fraction is None
    expected_hash = timing.get("expected_protocol_hash") if data_as_written else None
    expected_steps = timing.get("expected_steps_per_pass") if data_as_written else None

    load_seconds = 0.0
    input_checks: dict[str, dict[str, Any]] | None = None
    input_hash_seconds = 0.0
    if ratings is None:
        if input_dir is None:
            raw_dir = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
            input_dir = Path(raw_dir) if raw_dir else None
        if input_dir is not None:
            t = time.perf_counter()
            input_checks = verify_inputs(input_dir)
            input_hash_seconds = time.perf_counter() - t
            moved = [name for name, check in input_checks.items() if not check["matches_manifest"]]
            if moved:
                # Refused before any stepping: minutes of billed GPU time on the
                # wrong data would only produce a timing nobody can use.
                raise TimingCheckError(
                    f"{input_dir}: {moved} do not match {INPUT_MANIFEST_PATH.name}"
                )
        t = time.perf_counter()
        ratings, _movies = load_inputs(Settings(), input_dir=input_dir)
        load_seconds = time.perf_counter() - t

    # Reference step times describe the cell as written, on the full catalog;
    # against an overridden device or a subsample they would read as speed-ups
    # that were never measured, so they are dropped.
    references = timing.get("references") if data_as_written and not overrides.device else None
    inputs_ok = (
        None
        if input_checks is None
        else all(check["matches_manifest"] for check in input_checks.values())
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    failures: list[str] = []
    for label, config in cells:
        if overrides.device is not None:
            config = dataclasses.replace(config, device=overrides.device)
        logger.info(
            "Timing %s on %s (%s %s, warm-up %d)",
            label,
            config.device,
            budget.mode,
            budget.timing_seconds if budget.steps is None else budget.steps,
            budget.warmup_steps,
        )
        result = time_cell(
            ratings,
            config,
            budget,
            sample_fraction=sample_fraction,
            expected_protocol_hash=expected_hash,
            expected_steps_per_pass=expected_steps,
            references=references,
        )
        # An overridden run must not be filed under the name of the run as
        # written: a local mps smoke of the "...-cuda" cell says so in its name.
        output_label = label if not overrides.as_dict() else f"{label}__{run_kind}-{config.device}"
        document = {
            "label": label,
            "output_label": output_label,
            "cell": timing.get("cell", label),
            "run_kind": run_kind,
            **result.document,
            "cells_file": {
                "path": _relative(cells_path),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "overrides": overrides.as_dict(),
        }
        document["seconds"] = {
            "load": load_seconds,
            "input_hash": input_hash_seconds,
            **document["seconds"],
        }
        document["data"]["inputs"] = input_checks
        document["checks"]["inputs_match_manifest"] = inputs_ok
        failed = [*result.failed_checks, *(["inputs_match_manifest"] if inputs_ok is False else [])]
        path = out_dir / f"{output_label}.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(path)
        stats = document["step_seconds_steady"]
        logger.info(
            "%s: %d steps (%d steady), mean %.4f s/step, pass %.2f h -> %s",
            label,
            document["steps"]["completed"],
            document["steps"]["steady"],
            stats["mean"] or float("nan"),
            document["extrapolation"]["hours_per_pass"] or float("nan"),
            path,
        )
        if failed:
            failures.append(f"{label}: {', '.join(failed)}")
        # One cell's tensors must not sit in the next cell's device memory.
        gc.collect()
        if config.device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()
    if failures:
        raise TimingCheckError("pre-declared checks failed -- " + "; ".join(failures))
    return written


# --- a session: summarize and record ------------------------------------------------


def read_facts(path: Path) -> dict[str, str]:
    """``KEY=value`` lines from the bootstrap's facts file; blank and # lines skipped."""
    facts: dict[str, str] = {}
    if not path.is_file():
        return facts
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        facts[key.strip()] = value.strip()
    return facts


def _load_results(session_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    results_dir = session_dir / RESULTS_DIRNAME
    timings: list[dict[str, Any]] = []
    smoke: dict[str, Any] | None = None
    for path in sorted(results_dir.glob("*.json")) if results_dir.is_dir() else []:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("kind") == TIMING_KIND:
            timings.append(document)
        elif path.name == GPU_SMOKE_FILENAME:
            smoke = document
    return timings, smoke


def summarize_session(session_dir: Path) -> dict[str, Any]:
    """One document for a session: the bootstrap's facts, the smoke, each cell's timing."""
    timings, smoke = _load_results(session_dir)
    facts = read_facts(session_dir / FACTS_FILENAME)
    cells = []
    for document in timings:
        stats = document["step_seconds_steady"]
        cells.append(
            {
                "label": document["label"],
                "output_label": document.get("output_label", document["label"]),
                "cell": document["cell"],
                "device": document["config"]["device"],
                "device_name": document["device"].get("name"),
                "sample_fraction": document["data"]["sample_fraction"],
                "steps_completed": document["steps"]["completed"],
                "steady_steps": document["steps"]["steady"],
                "mean_step_seconds": stats["mean"],
                "median_step_seconds": stats["median"],
                "p10_step_seconds": stats["p10"],
                "p90_step_seconds": stats["p90"],
                "steps_per_pass": document["extrapolation"]["steps_per_pass"],
                "hours_per_pass": document["extrapolation"]["hours_per_pass"],
                "hours_3_passes": document["extrapolation"]["hours_3_passes"],
                "hours_5_passes": document["extrapolation"]["hours_5_passes"],
                "peak_rss_bytes": document["memory"]["peak_rss_bytes"],
                "device_memory": document["memory"]["device"],
                "speedups": {
                    name: reference["speedup"]
                    for name, reference in document.get("references", {}).items()
                },
                "checks": document["checks"],
                "warnings": document.get("warnings", []),
            }
        )
    order = {"0b": 0, "A": 1, "B": 2}
    cells.sort(key=lambda cell: (order.get(str(cell["cell"]), 9), str(cell["label"])))
    run_kinds = sorted({str(document.get("run_kind")) for document in timings})
    commits = sorted({str(document["git"]["commit"]) for document in timings})
    failed = [
        f"{cell['label']}:{name}"
        for cell in cells
        for name, passed in cell["checks"].items()
        if passed is False
    ]
    smoke_passed = None if smoke is None else bool(smoke.get("passed"))
    return {
        "kind": SUMMARY_KIND,
        "schema_version": SCHEMA_VERSION,
        "result_status": RESULT_STATUS,
        "run_kind": run_kinds[0] if len(run_kinds) == 1 else ",".join(run_kinds),
        "git_commits": commits,
        "facts": facts,
        "gpu_smoke": (
            None
            if smoke is None
            else {
                "passed": smoke_passed,
                "device": smoke.get("device"),
                "failed_checks": [
                    check["name"] for check in smoke.get("checks", []) if not check.get("passed")
                ],
            }
        ),
        "cells": cells,
        "failed_checks": failed,
        # The bootstrap writes STATUS only when it packages, so a session it
        # marked FAILED never reads as passed here, whatever did finish.
        "session_status": facts.get("STATUS"),
        "passed": (
            not failed
            and smoke_passed is not False
            and bool(cells)
            and facts.get("STATUS") != "FAILED"
        ),
    }


def summary_table(summary: Mapping[str, Any]) -> str:
    lines = [
        f"{'cell':<5} {'device':<6} {'steps':>7} {'mean s/step':>12} {'p10-p90':>17} "
        f"{'h/pass':>8} {'x cpu':>7} {'x mps':>7}"
    ]
    for cell in summary["cells"]:
        speed = cell["speedups"]

        def fmt(value: Any, spec: str) -> str:
            return "-" if value is None else format(value, spec)

        spread = fmt(cell["p10_step_seconds"], ".4f") + "-" + fmt(cell["p90_step_seconds"], ".4f")
        lines.append(
            f"{cell['cell']:<5} {cell['device']:<6} {cell['steps_completed']:>7} "
            f"{fmt(cell['mean_step_seconds'], '.4f'):>12} {spread:>17} "
            f"{fmt(cell['hours_per_pass'], '.3f'):>8} "
            f"{fmt(speed.get('cpu'), '.2f'):>7} {fmt(speed.get('mps'), '.2f'):>7}"
        )
    smoke = summary.get("gpu_smoke")
    lines.append(f"gpu smoke: {'-' if smoke is None else ('PASS' if smoke['passed'] else 'FAIL')}")
    lines.append(f"overall: {'PASS' if summary['passed'] else 'FAIL'}")
    return "\n".join(lines)


def _flat_numbers(prefix: str, value: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(value, bool):
        out[prefix] = float(value)
    elif isinstance(value, int | float) and math.isfinite(float(value)):
        out[prefix] = float(value)
    elif isinstance(value, Mapping):
        for key, inner in value.items():
            out.update(_flat_numbers(f"{prefix}_{key}", inner))
    return out


def _mlflow_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-./ ]", "_", value)


def record_session(
    session_dir: Path,
    *,
    tracking_uri: str,
    experiment: str,
    provider: str,
    instance: str,
    rate_usd_per_hour: str,
    label: str,
    tarball: Path | None = None,
) -> dict[str, Any]:
    """Log one session as one MLflow run, tagged so no gate or reader takes it for a model.

    The run carries no model and none of the metric names a gate reads
    (``warm_recall_at_k_candidates`` and the like), and its ``model_type`` is
    not ``sasrec``. Everything the session produced is attached as artifacts.
    """
    import mlflow
    from mlflow.artifacts import list_artifacts
    from mlflow.tracking import MlflowClient

    summary = summarize_session(session_dir)
    summary_path = session_dir / SUMMARY_FILENAME
    # The pod's own summary is covered by its MANIFEST.sha256 and is left as it
    # came; one is written only for a session that never got as far.
    if not summary_path.is_file():
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    facts = summary["facts"]
    first = summary["cells"][0] if summary["cells"] else {}
    nvidia: dict[str, Any] = {}
    timings, _smoke = _load_results(session_dir)
    if timings:
        nvidia = timings[0]["device"].get("nvidia_smi") or {}
    software = timings[0]["software"] if timings else {}
    tags = {
        "timing_only": "true",
        "not_a_result_of_record": "true",
        "result_status": RESULT_STATUS,
        "run_kind": label,
        "work_order": "WO-5",
        "wo5_step": "step-one-timing",
        "model_type": "sasrec-timing",
        "provider": provider,
        "instance": instance,
        "rate_usd_per_hour": rate_usd_per_hour,
        "gpu": str(first.get("device_name") or "none"),
        "gpu_driver": str(nvidia.get("driver_version", "none")),
        "driver_cuda_version": str(nvidia.get("driver_cuda_version", "none")),
        "torch_version": str(software.get("torch", "unknown")),
        "torch_cuda": str(software.get("torch_cuda")),
        "git_commit": ",".join(summary["git_commits"]),
        "pod_id": facts.get("RUNPOD_POD_ID", "none"),
        "torch_wheel_variant": facts.get("TORCH_WHEEL_VARIANT", "none"),
        "session_passed": str(summary["passed"]).lower(),
        "mlflow.note.content": (
            "WO-5 step one: step timings of ADR 0020 cells. Timing only - not a result of "
            "record. No model was trained to completion, evaluated or exported."
        ),
    }
    if tarball is not None:
        tags["tarball"] = tarball.name
        sha_file = tarball.with_name(tarball.name + ".sha256")
        if sha_file.is_file():
            tags["tarball_sha256"] = sha_file.read_text(encoding="utf-8").split()[0]
    params: dict[str, Any] = {}
    metrics: dict[str, float] = {}
    for cell in summary["cells"]:
        key = _mlflow_key(f"cell{cell['cell']}")
        params[f"{key}_label"] = cell["label"]
        params[f"{key}_device"] = cell["device"]
        params[f"{key}_sample_fraction"] = cell["sample_fraction"]
        for name in (
            "steps_completed",
            "steady_steps",
            "mean_step_seconds",
            "median_step_seconds",
            "p10_step_seconds",
            "p90_step_seconds",
            "steps_per_pass",
            "hours_per_pass",
            "hours_3_passes",
            "hours_5_passes",
            "peak_rss_bytes",
        ):
            metrics.update(_flat_numbers(f"{key}_{name}", cell[name]))
        metrics.update(_flat_numbers(f"{key}_device_memory", cell["device_memory"]))
        metrics.update(_flat_numbers(f"{key}_speedup_vs", cell["speedups"]))
    if summary["gpu_smoke"] is not None:
        metrics["gpu_smoke_passed"] = float(bool(summary["gpu_smoke"]["passed"]))
    for key, value in facts.items():
        if key.endswith("_SECONDS"):
            with contextlib.suppress(ValueError):
                metrics[_mlflow_key(f"bootstrap_{key.lower()}")] = float(value)

    mlflow.set_tracking_uri(tracking_uri)
    experiment_id = mlflow.set_experiment(experiment).experiment_id
    client = MlflowClient(tracking_uri=tracking_uri)
    run_id = _recorded_run(client, experiment_id, tags.get("tarball_sha256"))
    reused = run_id is not None
    if run_id is None:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        with mlflow.start_run(run_name=f"wo5-timing-{label}-{stamp}") as run:
            mlflow.set_tags(tags)
            mlflow.log_params({_mlflow_key(k): str(v) for k, v in params.items()})
            if metrics:
                mlflow.log_metrics(metrics)
            mlflow.log_artifacts(str(session_dir), artifact_path="timing-session")
            if tarball is not None:
                mlflow.log_artifact(str(tarball), artifact_path="bundle")
                sha_file = tarball.with_name(tarball.name + ".sha256")
                if sha_file.is_file():
                    mlflow.log_artifact(str(sha_file), artifact_path="bundle")
            run_id = run.info.run_id

    fetched = client.get_run(run_id)
    # Listed through the run's artifact location rather than
    # ``MlflowClient.list_artifacts``: a 3.x client asks a 2.x server for
    # logged models there, and the shared store's server is 2.13.
    listed = {
        item.path
        for folder in ("timing-session", "bundle")
        for item in list_artifacts(artifact_uri=f"{fetched.info.artifact_uri}/{folder}")
    }
    problems = [
        f"tag {name} is {fetched.data.tags.get(name)!r}"
        for name in ("timing_only", "not_a_result_of_record")
        if fetched.data.tags.get(name) != "true"
    ]
    expected = ["timing-session/results", f"timing-session/{SUMMARY_FILENAME}"]
    if tarball is not None:
        expected.append(f"bundle/{tarball.name}")
    problems += [f"artifact {path} is missing" for path in expected if path not in listed]
    problems += [
        f"metric {name} is missing" for name in metrics if name not in fetched.data.metrics
    ]
    if problems:
        raise TimingCheckError(f"run {run_id} did not read back as written: {problems}")
    return {
        "run_id": run_id,
        "reused_existing_run": reused,
        "experiment_id": experiment_id,
        "experiment": experiment,
        "tracking_uri": tracking_uri,
        "run_name": fetched.info.run_name,
        "artifacts": sorted(listed),
        "metrics_logged": len(fetched.data.metrics),
        "summary_passed": summary["passed"],
    }


def _recorded_run(client: Any, experiment_id: str, tarball_sha256: str | None) -> str | None:
    """The run already recorded for this tarball, so a second pull does not log a twin.

    A pull that died after its run was created (a dropped connection during the
    read-back, say) is rerun as it was; the rerun finds that run by the tarball's
    SHA-256 and verifies it instead of logging the same session twice.
    """
    if tarball_sha256 is None:
        return None
    runs = client.search_runs(
        experiment_ids=[experiment_id],
        filter_string=f"tags.tarball_sha256 = '{tarball_sha256}' and tags.timing_only = 'true'",
        max_results=2,
    )
    if len(runs) > 1:
        raise TimingCheckError(f"more than one run already records tarball {tarball_sha256}")
    return str(runs[0].info.run_id) if runs else None


# --- command line -------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.training.sasrec_timing")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="time the cells of a cells file")
    run.add_argument("cells", type=Path)
    run.add_argument("--out", type=Path, required=True, help="directory for <label>.json")
    run.add_argument("--input-dir", type=Path, help=f"CSV directory (default ${INPUT_DIR_ENV_VAR})")
    run.add_argument("--label", action="append", default=[], help="time only this cell")
    run.add_argument("--device", choices=DEVICES)
    run.add_argument("--sample-fraction", type=float)
    run.add_argument("--timing-seconds", type=float)
    run.add_argument("--steps", type=int)
    run.add_argument("--warmup-steps", type=int)
    run.add_argument("--run-kind", default="timing", help="e.g. rented-gpu or local-smoke")

    summarize = commands.add_parser("summarize", help="write SESSION_DIR/summary.json")
    summarize.add_argument("session_dir", type=Path)

    record = commands.add_parser("record", help="log a session to MLflow as a timing run")
    record.add_argument("session_dir", type=Path)
    record.add_argument("--tracking-uri", default="http://localhost:5001")
    record.add_argument("--experiment", default="phase-a-sasrec")
    record.add_argument("--provider", required=True)
    record.add_argument("--instance", required=True)
    record.add_argument("--rate-usd-per-hour", required=True)
    record.add_argument("--label", required=True, help="rented-gpu or local-smoke")
    record.add_argument("--tarball", type=Path)
    record.add_argument("--out", type=Path, help="also write the outcome JSON here")

    prepare = commands.add_parser("prepare-inputs", help="check the zip's MD5, extract, verify")
    prepare.add_argument("--zip", type=Path, required=True)
    prepare.add_argument("--published-md5-file", type=Path, required=True)
    prepare.add_argument("--dest", type=Path, required=True)

    verify = commands.add_parser("verify-inputs", help="SHA-256 of the CSVs against the manifest")
    verify.add_argument("--dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            written = run_cells(
                args.cells,
                args.out,
                input_dir=args.input_dir,
                labels=args.label,
                overrides=Overrides(
                    device=args.device,
                    sample_fraction=args.sample_fraction,
                    timing_seconds=args.timing_seconds,
                    steps=args.steps,
                    warmup_steps=args.warmup_steps,
                ),
                run_kind=args.run_kind,
            )
            print("\n".join(str(path) for path in written))
        elif args.command == "summarize":
            summary = summarize_session(args.session_dir)
            path = args.session_dir / SUMMARY_FILENAME
            path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print(summary_table(summary))
            return 0 if summary["passed"] else 1
        elif args.command == "record":
            outcome = record_session(
                args.session_dir,
                tracking_uri=args.tracking_uri,
                experiment=args.experiment,
                provider=args.provider,
                instance=args.instance,
                rate_usd_per_hour=args.rate_usd_per_hour,
                label=args.label,
                tarball=args.tarball,
            )
            text = json.dumps(outcome, indent=2)
            if args.out is not None:
                # MLflow prints its own run links to stdout, so a caller that
                # wants the outcome as a file asks for it here.
                args.out.write_text(text + "\n", encoding="utf-8")
            print(text)
        elif args.command == "prepare-inputs":
            outcome = prepare_inputs(
                args.zip, args.published_md5_file.read_text(encoding="utf-8"), args.dest
            )
            print(json.dumps(outcome, indent=2))
        else:
            checks = verify_inputs(args.dir)
            print(json.dumps(checks, indent=2))
            return 0 if all(check["matches_manifest"] for check in checks.values()) else 1
    except TimingCheckError as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
