"""WO-5 step two on the rented GPU: the approved sweep, one run at a time, each run packaged.

``infra/gpu/sweep.sh`` prepares the pod (the checks it shares with the timing kit),
then hands over to ``run-sweep``, which:

1. reads ``docs/experiments/sasrec/wo5-sweep-plan.json`` (the order) and each
   step's cells file (the cell, its projection, what it is checked against);
2. runs v1's calibration first; the contingent second seed only when the first
   lands outside ±1% of 0.5091713455 as scored on the pod; stops on a second miss;
3. then the six cells, cheapest projected first, each in its own process
   (``train-cell``) under a timeout of twice its projected hours, never past the
   sweep's hard deadline;
4. after every run, finished or not, writes one bundle
   ``out/<seq>-<step>-<run id>.tgz`` holding everything non-negotiable 12 asks
   for, its ``.sha256`` beside it, and one line in ``out/INDEX``;
5. stops at the first run that is not clean (failed, killed, a check missed),
   writing ``out/HALT`` with the reason, and at a ``STOP`` file the coordinator
   may drop into ``out/``.

What it does not do is stop the pod: ``sweep.sh`` does that once the Mac has
acknowledged the last bundle (or 30 minutes pass), and an independent watchdog
does it at the hard limit whatever this process is doing.

Subcommands::

    plan                      print the order, projections and kill limits
    run-sweep --root DIR ...  the sweep (sweep.sh starts it)
    train-cell CELLS ...      one training run (run-sweep starts it)

Every run trains through ``src.training.sasrec.run_once`` unchanged, logging to a
SQLite MLflow store on the pod's disk; the Mac imports each run into the shared
store with its original run id (``src.training.wo5_harvest``).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.models.candidates.sasrec import SASRecConfig
from src.training import protocol_manifest

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RESULT_KIND = "wo5-sweep-run"
CELLS_DIR = protocol_manifest.REPO_ROOT / "docs" / "experiments" / "sasrec"
PLAN_PATH = CELLS_DIR / "wo5-sweep-plan.json"

# The pod's layout under the sweep root. No directory is called ``artifacts``:
# MLflow 3.15 refuses a store path that contains one.
OUT_DIRNAME = "out"
WORK_DIRNAME = "work"
STORE_DIRNAME = "mlstore"
STORE_DB = "mlflow.db"
STORE_FILES = "files"
SETUP_ENV = Path("setup") / "env"
INDEX_NAME = "INDEX"
HALT_NAME = "HALT"
STOP_NAME = "STOP"
RESULT_NAME = "RESULT"
PHASE_NAME = "PHASE"
CHILD_RESULT = "result.json"
RUNNER_RESULT = "runner.json"
CONSOLE_LOG = "console.log"
MODEL_EXPORT = "model-export"

ROLE_CALIBRATION = "calibration"
ROLE_CONTINGENT = "calibration-contingent"
ROLE_CELL = "cell"
ROLES = (ROLE_CALIBRATION, ROLE_CONTINGENT, ROLE_CELL)

FINISHED = "FINISHED"
FAILED = "FAILED"
KILLED = "KILLED"
HARD_LIMIT = "HARD-LIMIT"
PROTOCOL_MISMATCH = "PROTOCOL-MISMATCH"
CHECK_FAILED = "CHECK-FAILED"
STATUSES = (FINISHED, FAILED, KILLED, HARD_LIMIT, PROTOCOL_MISMATCH, CHECK_FAILED)
PASS = "PASS"
MISS = "MISS"

# Exit codes of train-cell and run-sweep.
EXIT_PROTOCOL_MISMATCH = 3
EXIT_HALT = 10

WARM_RECALL = "warm_recall_at_k_candidates"
COLD_RECALL = "cold_recall_at_k_candidates"
# Metrics logged once per pass by run_once and log_training_report.
PER_PASS_METRICS = (
    "train_loss",
    "epoch_warm_recall_at_k_candidates",
    "epoch_train_seconds",
    "early_stopping_probe_recall_at_k_candidates",
    "epoch_peak_rss_bytes",
)
# Cold users are popularity-routed, so their recall is the same number in every
# run of the full protocol; ADR 0020 calls any difference a defect.
COLD_RECALL_TOLERANCE = 1e-9
# A child gets this long to exit after SIGTERM before SIGKILL.
KILL_GRACE_SECONDS = 60


# --- the plan ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """A step's projected hours: passes x hours per pass + a fixed per-run overhead."""

    hours_per_pass: float
    passes: int
    overhead_hours: float
    basis: str = ""

    @property
    def projected_hours(self) -> float:
        return self.passes * self.hours_per_pass + self.overhead_hours

    @property
    def kill_hours(self) -> float:
        """Owner condition 4: a run past twice its projected time is killed."""
        return 2 * self.projected_hours


@dataclass(frozen=True)
class Step:
    name: str
    role: str
    cells_file: Path
    projection: Projection
    expected_protocol_hash: str
    expected: Mapping[str, float]


@dataclass(frozen=True)
class CalibrationCheck:
    metric: str
    target: float
    relative_tolerance: float

    def verdict(self, value: float | None) -> str:
        if value is None:
            return MISS
        return PASS if abs(value / self.target - 1.0) <= self.relative_tolerance else MISS

    @property
    def band(self) -> tuple[float, float]:
        return (
            self.target * (1 - self.relative_tolerance),
            self.target * (1 + self.relative_tolerance),
        )


@dataclass(frozen=True)
class SweepPlan:
    steps: tuple[Step, ...]
    calibration: CalibrationCheck
    hard_limit_hours: float
    experiment: str
    rate_usd_per_hour_max: float

    def first(self, role: str) -> Step | None:
        return next((step for step in self.steps if step.role == role), None)

    @property
    def cells(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.role == ROLE_CELL)


def load_plan(path: Path = PLAN_PATH, cells_dir: Path | None = None) -> SweepPlan:
    """Read and check the plan: one calibration first, cells cheapest-first, each file sane."""
    document = json.loads(path.read_text())
    directory = cells_dir or path.parent
    steps: list[Step] = []
    for raw in document["steps"]:
        role = str(raw["role"])
        if role not in ROLES:
            raise ValueError(f"step {raw.get('name')!r} has unknown role {role!r}")
        cells_file = directory / str(raw["cells_file"])
        spec = json.loads(cells_file.read_text())
        if len(spec.get("cells", [])) != 1:
            raise ValueError(f"{cells_file.name} must hold exactly one cell")
        block = spec["projection"]
        projection = Projection(
            hours_per_pass=float(block["hours_per_pass"]),
            passes=int(block["passes_ceiling"]),
            overhead_hours=float(block["overhead_hours"]),
            basis=str(block.get("hours_per_pass_basis", "")),
        )
        for key, value in (
            ("projected_hours", projection.projected_hours),
            ("kill_hours", projection.kill_hours),
        ):
            if abs(float(block[key]) - value) > 1e-3:
                raise ValueError(f"{cells_file.name}: {key} {block[key]} != computed {value:.3f}")
        cell = spec["cells"][0]
        if int(cell.get("epochs", 0)) != projection.passes:
            raise ValueError(f"{cells_file.name}: epochs is not the projection's pass ceiling")
        steps.append(
            Step(
                name=str(raw["name"]),
                role=role,
                cells_file=cells_file,
                projection=projection,
                expected_protocol_hash=str(spec["expected_protocol_hash"]),
                expected=dict(spec.get("expected", {})),
            )
        )
    roles = [step.role for step in steps]
    if roles.count(ROLE_CALIBRATION) != 1 or roles[0] != ROLE_CALIBRATION:
        raise ValueError("the plan starts with exactly one calibration")
    if roles.count(ROLE_CONTINGENT) > 1:
        raise ValueError("at most one contingent calibration")
    if ROLE_CONTINGENT in roles and roles.index(ROLE_CONTINGENT) != 1:
        raise ValueError("the contingent calibration comes straight after the calibration")
    cells = [step for step in steps if step.role == ROLE_CELL]
    hours = [step.projection.projected_hours for step in cells]
    if hours != sorted(hours):
        raise ValueError("cells must run from the cheapest projected to the most expensive")
    if len({step.name for step in steps}) != len(steps):
        raise ValueError("step names must be unique")
    check = document["calibration"]
    return SweepPlan(
        steps=tuple(steps),
        calibration=CalibrationCheck(
            metric=str(check["metric"]),
            target=float(check["target"]),
            relative_tolerance=float(check["relative_tolerance"]),
        ),
        hard_limit_hours=float(document["hard_limit_hours"]),
        experiment=str(document["experiment"]),
        rate_usd_per_hour_max=float(document["rate_usd_per_hour_max"]),
    )


def plan_table(plan: SweepPlan) -> str:
    lines = [
        f"{'step':<22} {'role':<23} {'h/pass':>7} {'passes':>6} {'proj h':>7} {'kill h':>7}",
    ]
    total = 0.0
    for step in plan.steps:
        projection = step.projection
        lines.append(
            f"{step.name:<22} {step.role:<23} {projection.hours_per_pass:>7.3f} "
            f"{projection.passes:>6} {projection.projected_hours:>7.2f} "
            f"{projection.kill_hours:>7.2f}"
        )
        if step.role != ROLE_CONTINGENT:
            total += projection.projected_hours
    contingent = plan.first(ROLE_CONTINGENT)
    lines.append(
        f"projected without the contingent seed: {total:.2f} h"
        + (
            f"; with it: {total + contingent.projection.projected_hours:.2f} h"
            if contingent
            else ""
        )
    )
    lines.append(
        f"hard limit {plan.hard_limit_hours:g} h = "
        f"${plan.hard_limit_hours * plan.rate_usd_per_hour_max:.2f} "
        f"at ${plan.rate_usd_per_hour_max:.2f}/h"
    )
    return "\n".join(lines)


# --- what to run next ----------------------------------------------------------------------


@dataclass
class Outcome:
    seq: int
    step_name: str
    status: str
    run_id: str | None = None
    warm_recall: float | None = None
    verdict: str | None = None
    problems: list[str] = field(default_factory=list)
    bundle: str | None = None
    sha256: str | None = None

    @property
    def index_status(self) -> str:
        if self.status == FINISHED and self.verdict is not None:
            return f"{FINISHED}-CAL-{self.verdict}"
        return self.status

    def index_line(self, step_label: str) -> str:
        return (
            f"{self.seq:02d} {step_label} {self.run_id or '-'} {self.bundle} "
            f"{self.sha256} {self.index_status}"
        )


@dataclass(frozen=True)
class Terminal:
    kind: str  # "DONE" or "HALT"
    reason: str


def next_action(plan: SweepPlan, outcomes: Sequence[Outcome]) -> Step | Terminal:
    """The next step to run, or why the sweep ends. A pure function of what has run."""
    for outcome in outcomes:
        if outcome.status != FINISHED:
            return Terminal("HALT", f"{outcome.step_name} ended {outcome.status}")
        if outcome.problems:
            return Terminal(
                "HALT", f"{outcome.step_name} failed its checks: {'; '.join(outcome.problems)}"
            )
    done = {outcome.step_name: outcome for outcome in outcomes}
    calibration = plan.first(ROLE_CALIBRATION)
    assert calibration is not None
    if calibration.name not in done:
        return calibration
    if done[calibration.name].verdict == MISS:
        contingent = plan.first(ROLE_CONTINGENT)
        if contingent is None:
            return Terminal("HALT", "the calibration missed and the plan has no contingent seed")
        if contingent.name not in done:
            return contingent
        if done[contingent.name].verdict == MISS:
            return Terminal("HALT", "second calibration miss: both seeds outside ±1% (stop)")
    for step in plan.cells:
        if step.name not in done:
            return step
    return Terminal("DONE", "every planned step finished clean")


def step_label(step_name: str) -> str:
    """The short name a step carries in INDEX and in its bundle's file name."""
    return step_name.removeprefix("cell-")


def parse_index(text: str) -> list[dict[str, str]]:
    """Bundle lines of an INDEX, in order: ``seq cell run_id tgz sha256 status``."""
    entries = []
    for line in text.splitlines():
        parts = line.split()
        # A bundle line: numeric seq, a .tgz, a 64-hex digest. The DONE/HALT line is not.
        if (
            len(parts) == 6
            and parts[0].isdigit()
            and parts[3].endswith(".tgz")
            and len(parts[4]) == 64
        ):
            seq, cell, run_id, tgz, sha256, status = parts
            entries.append(
                {
                    "seq": seq,
                    "cell": cell,
                    "run_id": run_id,
                    "tgz": tgz,
                    "sha256": sha256,
                    "status": status,
                }
            )
    return entries


def index_terminal(text: str) -> str | None:
    """``DONE ...`` or ``HALT ...`` if the INDEX has its last line, else None."""
    for line in text.splitlines():
        if line.startswith(("DONE", "HALT")):
            return line.strip()
    return None


def outcomes_from_index(plan: SweepPlan, text: str) -> list[Outcome]:
    """What an earlier start of this sweep already ran, so a restart resumes after it."""
    by_label = {step_label(step.name): step for step in plan.steps}
    outcomes = []
    for entry in parse_index(text):
        step = by_label.get(entry["cell"])
        if step is None:
            continue
        status = entry["status"]
        verdict = None
        if status.startswith(f"{FINISHED}-CAL-"):
            verdict = status.rsplit("-", 1)[1]
            status = FINISHED
        outcomes.append(
            Outcome(
                seq=int(entry["seq"]),
                step_name=step.name,
                status=status,
                run_id=None if entry["run_id"] == "-" else entry["run_id"],
                verdict=verdict,
                bundle=entry["tgz"],
                sha256=entry["sha256"],
            )
        )
    return outcomes


# --- overrides for rehearsals ----------------------------------------------------------------


@dataclass(frozen=True)
class Overrides:
    """What a rehearsal changes. Any of them makes the run not a result: the checks against
    v1's full-data numbers are skipped and every output names the overrides."""

    sample_fraction: float | None = None
    device: str | None = None
    epochs: int | None = None
    tiny: bool = False
    # Scales every projected and kill time, so a test can exercise the limits in seconds.
    hours_scale: float | None = None
    # Forces calibration verdicts in order (e.g. MISS then PASS), for a rehearsal.
    calibration_verdicts: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return any(
            (
                self.sample_fraction is not None,
                self.device is not None,
                self.epochs is not None,
                self.tiny,
                self.hours_scale is not None,
                bool(self.calibration_verdicts),
            )
        )

    def apply(self, config: SASRecConfig) -> SASRecConfig:
        changes: dict[str, Any] = {}
        if self.device is not None:
            changes["device"] = self.device
        if self.epochs is not None:
            changes["epochs"] = self.epochs
            if config.early_stopping and self.epochs < config.early_stopping_min_epochs:
                changes["early_stopping"] = False
        if self.tiny:
            changes.update(
                hidden_dim=16,
                num_heads=2,
                feedforward_dim=64,
                num_blocks=min(config.num_blocks, 2),
                max_sequence_length=min(config.max_sequence_length, 20),
                negative_count=min(config.negative_count, 64),
            )
        return dataclasses.replace(config, **changes) if changes else config

    def child_args(self) -> list[str]:
        args: list[str] = []
        if self.sample_fraction is not None:
            args += ["--sample-fraction", str(self.sample_fraction)]
        if self.device is not None:
            args += ["--device", self.device]
        if self.epochs is not None:
            args += ["--epochs", str(self.epochs)]
        if self.tiny:
            args.append("--tiny")
        return args

    def as_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["calibration_verdicts"] = list(self.calibration_verdicts)
        return out


# --- one training run (train-cell) -------------------------------------------------------------


def store_uri(store: Path) -> str:
    return f"sqlite:///{(store / STORE_DB).resolve()}"


def ensure_experiment(tracking_uri: str, name: str, artifact_root: Path) -> str:
    """The experiment the runs log to, created with its artifacts under the store."""
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    existing = client.get_experiment_by_name(name)
    if existing is not None:
        return str(existing.experiment_id)
    artifact_root.mkdir(parents=True, exist_ok=True)
    return str(client.create_experiment(name, artifact_location=artifact_root.resolve().as_uri()))


def _utc(epoch_ms: int | None = None) -> str:
    moment = (
        dt.datetime.now(dt.UTC)
        if epoch_ms is None
        else dt.datetime.fromtimestamp(epoch_ms / 1000, dt.UTC)
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_run(tracking_uri: str, experiment: str, started_ms: int) -> str | None:
    """The run a child started, found by time when the child could not say (it died)."""
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    found = client.get_experiment_by_name(experiment)
    if found is None:
        return None
    runs = client.search_runs(
        [found.experiment_id],
        filter_string=f"attributes.start_time >= {started_ms}",
        order_by=["attributes.start_time DESC"],
        max_results=1,
    )
    return str(runs[0].info.run_id) if runs else None


def run_document(tracking_uri: str, run_id: str) -> dict[str, Any]:
    """Every metric, param and tag of a run, and each per-pass series, read from the store."""
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    per_pass = {
        key: [
            {"pass": point.step, "value": point.value}
            for point in sorted(client.get_metric_history(run_id, key), key=lambda p: p.step)
        ]
        for key in PER_PASS_METRICS
        if key in run.data.metrics
    }
    return {
        "run_id": run_id,
        "run_name": run.info.run_name,
        "experiment_id": str(run.info.experiment_id),
        "mlflow_status": run.info.status,
        "start_utc": _utc(run.info.start_time),
        "end_utc": _utc(run.info.end_time) if run.info.end_time else None,
        "artifact_uri": run.info.artifact_uri,
        "metrics": dict(run.data.metrics),
        "params": dict(run.data.params),
        "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.log-model")},
        "per_pass": per_pass,
    }


def train_cell(
    cells_file: Path,
    *,
    run_dir: Path,
    store: Path,
    experiment: str,
    overrides: Overrides,
    input_dir: Path | None,
    run_kind: str,
    ratings: Any = None,
) -> dict[str, Any]:
    """Train one cell through ``run_once`` and write ``result.json``; return it.

    Before anything trains, the protocol hash this machine's data gives is checked
    against the cells file's: a pod reading different data stops in a minute
    rather than after hours. A rehearsal's overrides skip the check, and say so.
    """
    import mlflow
    import pandas as pd

    from src.config import Settings
    from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES
    from src.training import sasrec as sasrec_training
    from src.training.candidate_data import INPUT_DIR_ENV_VAR, load_inputs
    from src.training.sasrec_sweep import parse_grid
    from src.training.sasrec_timing import device_info, git_info, host_info, software_info

    run_dir.mkdir(parents=True, exist_ok=True)
    spec = json.loads(cells_file.read_text())
    sample_fraction, cells = parse_grid(spec)
    label, config = cells[0]
    config = overrides.apply(config)
    config.validate()
    if overrides.sample_fraction is not None:
        sample_fraction = overrides.sample_fraction
    expected_hash = str(spec.get("expected_protocol_hash", ""))
    tracking_uri = store_uri(store)
    store.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri)
    ensure_experiment(tracking_uri, experiment, store / STORE_FILES)
    os.environ[sasrec_training.EXPERIMENT_ENV_VAR] = experiment

    document: dict[str, Any] = {
        "kind": RESULT_KIND,
        "schema_version": SCHEMA_VERSION,
        "status": FAILED,
        "run_kind": run_kind,
        "label": label,
        "cells_file": cells_file.name,
        "cells_file_sha256": file_sha256(cells_file),
        "config": config.as_params(),
        "sample_fraction": sample_fraction,
        "overrides": overrides.as_dict(),
        "seed": config.seed,
        "git": git_info(),
        "device": device_info(config.device),
        "software": software_info(),
        "host": host_info(),
        "window_utc": {"start": _utc(), "end": None},
    }
    result_path = run_dir / CHILD_RESULT

    def write() -> None:
        result_path.write_text(json.dumps(document, indent=2, sort_keys=True, default=str) + "\n")

    if ratings is None:
        raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
        directory = input_dir or (Path(raw) if raw else None)
        ratings, _movies = load_inputs(Settings(), input_dir=directory)
    assert isinstance(ratings, pd.DataFrame)
    split, train_frame, _cohort = sasrec_training.evaluation_frames(ratings, sample_fraction)
    # Exactly the protocol run_once builds: its model routes at COLD_START_THRESHOLD.
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(COLD_START_THRESHOLD),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    document["protocol"] = {
        "semantic_hash": protocol.semantic_hash,
        "expected_semantic_hash": expected_hash,
        "matches_expected": protocol.semantic_hash == expected_hash,
        "checked": not overrides.active,
        "manifest": protocol.to_dict(),
    }
    document["data"] = {
        "n_ratings_read": len(ratings),
        "n_train_rows": len(split.train),
        "n_fitted_rows": len(train_frame),
        "raw_data_revision": protocol.raw_data_revision,
        "input_dir": str(input_dir or os.environ.get(INPUT_DIR_ENV_VAR, "")),
    }
    if not overrides.active and protocol.semantic_hash != expected_hash:
        document["status"] = PROTOCOL_MISMATCH
        document["window_utc"]["end"] = _utc()
        write()
        raise ProtocolMismatchError(
            f"protocol {protocol.semantic_hash} != expected {expected_hash}"
        )
    write()

    started_ms = int(time.time() * 1000)
    artifact_root = run_dir / MODEL_EXPORT
    try:
        sasrec_training.run_once(
            ratings,
            config,
            sample_fraction=sample_fraction,
            run_label=label,
            artifact_root=artifact_root,
        )
    except BaseException as error:
        document["error"] = f"{type(error).__name__}: {error}"
        document["run_id"] = find_run(tracking_uri, experiment, started_ms)
        document["window_utc"]["end"] = _utc()
        write()
        raise
    finished = mlflow.last_active_run()
    if finished is None:
        raise RuntimeError("run_once returned without an MLflow run")
    run_id = str(finished.info.run_id)
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    tags = {
        "wo5_step": label,
        "wo5_cells_file": cells_file.name,
        "wo5_cells_file_sha256": document["cells_file_sha256"],
        "run_kind": run_kind,
        "device_name": str(document["device"].get("name")),
        "trained_on": config.device,
        "scored_on": "pod-cpu",
        "rescore_of_record": "false",
    }
    nvidia = document["device"].get("nvidia_smi") or {}
    if nvidia:
        tags["gpu_driver"] = str(nvidia.get("driver_version"))
        tags["gpu_driver_cuda"] = str(nvidia.get("driver_cuda_version"))
    tags["torch_cuda"] = str(document["software"].get("torch_cuda"))
    if document["git"].get("commit"):
        tags["git_commit"] = str(document["git"]["commit"])
    if overrides.active:
        tags["wo5_overrides"] = json.dumps(overrides.as_dict(), sort_keys=True)
    for key, value in tags.items():
        client.set_tag(run_id, key, value)
    document.update(run_document(tracking_uri, run_id))
    document["status"] = FINISHED
    model_dir = artifact_root / run_id
    archive = model_dir / "sasrec-model.zip"
    document["model"] = {
        "directory": f"{MODEL_EXPORT}/{run_id}",
        "archive": "sasrec-model.zip",
        "archive_sha256": file_sha256(archive) if archive.is_file() else None,
        "manifest_model_sha256": document["tags"].get("sasrec_artifact_sha256"),
        "weights_sha256": document["tags"].get("sasrec_weights_sha256"),
    }
    document["per_user_file"] = "per_user_recall.json"
    document["window_utc"]["end"] = _utc()
    write()
    return document


class ProtocolMismatchError(RuntimeError):
    """The data on this machine does not give the protocol the cells file pins."""


# --- packaging a run ------------------------------------------------------------------------


def snapshot_store(db: Path, run_id: str, destination: Path) -> dict[str, int]:
    """A copy of the SQLite store holding one run and nothing else, for the Mac's import.

    The live store keeps every run; the copy drops the rows of all the others (and
    VACUUMs), so a bundle carries one run's tens of thousands of per-step rows, not
    the whole sweep's.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        source.close()
    counts: dict[str, int] = {}
    try:
        tables = [
            row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        for table in tables:
            columns = {row[1] for row in target.execute(f'PRAGMA table_info("{table}")')}
            for column in ("run_uuid", "run_id"):
                if column in columns:
                    cursor = target.execute(
                        f'DELETE FROM "{table}" WHERE "{column}" != ?', (run_id,)
                    )
                    counts[table] = cursor.rowcount
                    break
            else:
                if table == "inputs" and "destination_id" in columns:
                    cursor = target.execute(
                        'DELETE FROM "inputs" WHERE destination_id != ?', (run_id,)
                    )
                    counts[table] = cursor.rowcount
        target.commit()
        target.execute("VACUUM")
    finally:
        target.close()
    return counts


def _artifact_dir(artifact_uri: str) -> Path | None:
    if artifact_uri.startswith("file://"):
        return Path(artifact_uri[len("file://") :])
    path = Path(artifact_uri)
    return path if path.is_absolute() else None


def write_manifest(directory: Path) -> None:
    lines = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        rel = path.relative_to(directory).as_posix()
        if rel == "MANIFEST.sha256":
            continue
        lines.append(f"{file_sha256(path)}  ./{rel}")
    (directory / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


def package(
    *,
    root: Path,
    seq: int,
    step: Step,
    outcome: Outcome,
    run_dir: Path,
    runner: Mapping[str, Any],
    tracking_uri: str,
) -> tuple[str, str]:
    """Write ``out/<seq>-<step>-<run id>.tgz`` and its ``.sha256``; return (name, sha256)."""
    out = root / OUT_DIRNAME
    name = f"{seq:02d}-{step_label(step.name)}-{outcome.run_id or 'norun'}"
    staging = out / ".staging" / name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    (staging / "cells").mkdir()
    shutil.copy2(step.cells_file, staging / "cells" / step.cells_file.name)
    (staging / "logs").mkdir()
    for log_name in (CONSOLE_LOG, "gpu-utilization.csv"):
        if (run_dir / log_name).is_file():
            shutil.copy2(run_dir / log_name, staging / "logs" / log_name)
    if (run_dir / CHILD_RESULT).is_file():
        shutil.copy2(run_dir / CHILD_RESULT, staging / CHILD_RESULT)
    env = root / SETUP_ENV
    if env.is_dir():
        shutil.copytree(env, staging / "env")
    if outcome.run_id:
        model_dir = run_dir / MODEL_EXPORT / outcome.run_id
        if model_dir.is_dir():
            shutil.copytree(model_dir, staging / "model")
        db = root / STORE_DIRNAME / STORE_DB
        if db.is_file():
            store_info: dict[str, Any] = {"run_id": outcome.run_id, "db": f"mlflow/{STORE_DB}"}
            store_info["deleted_rows"] = snapshot_store(
                db, outcome.run_id, staging / "mlflow" / STORE_DB
            )
            try:
                from mlflow.tracking import MlflowClient

                info = MlflowClient(tracking_uri=tracking_uri).get_run(outcome.run_id).info
                source = _artifact_dir(info.artifact_uri)
                store_info["artifact_uri"] = info.artifact_uri
                if source is not None and source.is_dir():
                    # Kept at the same relative place as on the pod: files/<run id>/artifacts.
                    relative = Path(STORE_FILES) / outcome.run_id / "artifacts"
                    shutil.copytree(source, staging / "mlflow" / relative)
                    store_info["artifact_root_on_pod"] = str(source.parent.parent)
                    store_info["artifact_root_in_bundle"] = f"mlflow/{STORE_FILES}"
            except Exception as error:  # noqa: BLE001 -- the bundle still ships what exists
                store_info["error"] = f"{type(error).__name__}: {error}"
            (staging / "mlflow" / "STORE.json").write_text(json.dumps(store_info, indent=2) + "\n")
    (staging / RUNNER_RESULT).write_text(
        json.dumps(dict(runner), indent=2, sort_keys=True, default=str) + "\n"
    )
    write_manifest(staging)
    tgz = out / f"{name}.tgz"
    partial = out / f".{name}.tgz.part"
    with tarfile.open(partial, "w:gz") as archive:
        archive.add(staging, arcname=name)
    partial.rename(tgz)
    digest = file_sha256(tgz)
    (out / f"{name}.tgz.sha256").write_text(f"{digest}  {name}.tgz\n")
    shutil.rmtree(staging)
    return f"{name}.tgz", digest


# --- the sweep ----------------------------------------------------------------------------


ChildCommand = Callable[[Step, Path], list[str]]


def default_child_command(
    *, python: str, root: Path, experiment: str, overrides: Overrides, run_kind: str
) -> ChildCommand:
    def command(step: Step, run_dir: Path) -> list[str]:
        return [
            python,
            "-m",
            "src.training.wo5_sweep",
            "train-cell",
            str(step.cells_file),
            "--run-dir",
            str(run_dir),
            "--store",
            str(root / STORE_DIRNAME),
            "--experiment",
            experiment,
            "--run-kind",
            run_kind,
            *overrides.child_args(),
        ]

    return command


def _run_child(
    command: list[str], *, console: Path, timeout: float, cwd: Path | None
) -> tuple[int | None, bool]:
    """Run one child in its own process group, its output to ``console`` and to stdout.

    Returns (exit code, timed out). On timeout the group gets SIGTERM, then SIGKILL.
    """
    with console.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
        assert process.stdout is not None
        stream = process.stdout

        def pump() -> None:
            for line in iter(stream.readline, b""):
                log.write(line)
                log.flush()
                sys.stdout.buffer.write(line)
                sys.stdout.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=max(1.0, timeout))
            reader.join(timeout=30)
            return code, False
        except subprocess.TimeoutExpired:
            for sig, grace in ((signal.SIGTERM, KILL_GRACE_SECONDS), (signal.SIGKILL, 30)):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    process.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
            reader.join(timeout=30)
            return process.returncode, True


def _start_gpu_sampler(path: Path) -> subprocess.Popen[bytes] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    handle = path.open("wb")
    return subprocess.Popen(
        [
            "nvidia-smi",
            "--query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,power.draw,"
            "clocks.sm,temperature.gpu",
            "--format=csv",
            "-l",
            "10",
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def checks_for(
    step: Step, result: Mapping[str, Any] | None, overrides: Overrides
) -> tuple[list[str], dict[str, Any]]:
    """The predeclared checks of a finished run; problems, and what was checked."""
    if overrides.active:
        return [], {"skipped": "a rehearsal's overrides make every full-data check meaningless"}
    if result is None:
        return ["no result.json"], {}
    metrics = result.get("metrics", {})
    problems: list[str] = []
    checked: dict[str, Any] = {}
    protocol = result.get("protocol", {}).get("semantic_hash")
    run_protocol = result.get("params", {}).get("evaluation_protocol_hash")
    checked["protocol_hash"] = protocol
    if protocol != step.expected_protocol_hash:
        problems.append(f"protocol {protocol} != {step.expected_protocol_hash}")
    if run_protocol is not None and run_protocol != step.expected_protocol_hash:
        problems.append(f"the run logged protocol {run_protocol}")
    for key, metric in (("n_warm_users", "n_warm_users"), ("n_cold_users", "n_cold_users")):
        if key in step.expected:
            checked[key] = metrics.get(metric)
            if metrics.get(metric) != float(step.expected[key]):
                problems.append(f"{metric} {metrics.get(metric)} != {step.expected[key]}")
    if "cold_recall_at_k_candidates" in step.expected:
        value = metrics.get(COLD_RECALL)
        checked["cold_recall"] = value
        target = float(step.expected["cold_recall_at_k_candidates"])
        if value is None or abs(float(value) - target) > COLD_RECALL_TOLERANCE:
            problems.append(f"cold recall {value} != {target} (popularity-routed; a defect)")
    return problems, checked


def run_sweep(
    plan: SweepPlan,
    *,
    root: Path,
    deadline_epoch: float,
    child_command: ChildCommand,
    overrides: Overrides,
    experiment: str,
    cwd: Path | None = None,
    clock: Callable[[], float] = time.time,
) -> Terminal:
    """Run the plan to its end, a halt, a STOP file or the deadline; return how it ended."""
    out = root / OUT_DIRNAME
    work = root / WORK_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    index = out / INDEX_NAME
    tracking_uri = store_uri(root / STORE_DIRNAME)
    scale = overrides.hours_scale or 1.0
    outcomes = outcomes_from_index(plan, index.read_text()) if index.is_file() else []
    if outcomes:
        logger.info("resuming after %d run(s) already in %s", len(outcomes), index)
    forced = list(overrides.calibration_verdicts)
    for outcome in outcomes:
        if outcome.verdict is not None and forced:
            forced.pop(0)
    seq = max((outcome.seq for outcome in outcomes), default=0)

    def finish(terminal: Terminal) -> Terminal:
        (out / RESULT_NAME).write_text(f"{terminal.kind} {terminal.reason}\n")
        if terminal.kind == "HALT":
            (out / HALT_NAME).write_text(f"{_utc()} {terminal.reason}\n")
        logger.info("sweep %s: %s", terminal.kind, terminal.reason)
        return terminal

    while True:
        action = next_action(plan, outcomes)
        if isinstance(action, Terminal):
            return finish(action)
        if (out / STOP_NAME).exists():
            return finish(Terminal("HALT", f"STOP file found before {action.name}"))
        step = action
        projected = step.projection.projected_hours * 3600 * scale
        kill = step.projection.kill_hours * 3600 * scale
        remaining = deadline_epoch - clock()
        if remaining < projected:
            return finish(
                Terminal(
                    "HALT",
                    f"hard limit: {remaining / 3600:.2f} h left, {step.name} is projected at "
                    f"{projected / 3600:.2f} h",
                )
            )
        timeout = min(kill, remaining)
        seq += 1
        run_dir = work / f"{seq:02d}-{step_label(step.name)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        started = clock()
        started_ms = int(time.time() * 1000)
        (root / PHASE_NAME).write_text(
            f"step {seq:02d} {step.name} started {_utc()} timeout_h {timeout / 3600:.2f}\n"
        )
        logger.info(
            "step %02d %s: projected %.2f h, killed past %.2f h (timeout %.2f h)",
            seq,
            step.name,
            projected / 3600,
            kill / 3600,
            timeout / 3600,
        )
        sampler = _start_gpu_sampler(run_dir / "gpu-utilization.csv")
        try:
            code, timed_out = _run_child(
                child_command(step, run_dir),
                console=run_dir / CONSOLE_LOG,
                timeout=timeout,
                cwd=cwd,
            )
        finally:
            if sampler is not None:
                sampler.terminate()
                sampler.wait(timeout=30)
        elapsed = clock() - started
        result_path = run_dir / CHILD_RESULT
        result = json.loads(result_path.read_text()) if result_path.is_file() else None
        run_id = (result or {}).get("run_id")
        if not run_id and (root / STORE_DIRNAME / STORE_DB).is_file():
            # The child died before it could say which run it started.
            run_id = find_run(tracking_uri, experiment, started_ms)
        if timed_out:
            status = HARD_LIMIT if timeout < kill else KILLED
        elif code == 0 and result is not None and result.get("status") == FINISHED:
            status = FINISHED
        elif code == EXIT_PROTOCOL_MISMATCH:
            status = PROTOCOL_MISMATCH
        else:
            status = FAILED
        if run_id and status != FINISHED:
            _mark_terminated(tracking_uri, run_id, "KILLED" if timed_out else "FAILED")
        outcome = Outcome(seq=seq, step_name=step.name, status=status, run_id=run_id)
        checked: dict[str, Any] = {}
        if status == FINISHED:
            problems, checked = checks_for(step, result, overrides)
            outcome.problems = problems
            if problems:
                outcome.status = CHECK_FAILED
            outcome.warm_recall = (result or {}).get("metrics", {}).get(plan.calibration.metric)
            if step.role in (ROLE_CALIBRATION, ROLE_CONTINGENT):
                outcome.verdict = (
                    forced.pop(0) if forced else plan.calibration.verdict(outcome.warm_recall)
                )
        runner = {
            "kind": "wo5-sweep-runner",
            "seq": seq,
            "step": step.name,
            "role": step.role,
            "status": outcome.status,
            "exit_code": code,
            "timed_out": timed_out,
            "elapsed_seconds": round(elapsed, 1),
            "projected_hours": step.projection.projected_hours,
            "kill_hours": step.projection.kill_hours,
            "hours_scale": scale,
            "timeout_seconds": round(timeout, 1),
            "deadline_utc": _utc(int(deadline_epoch * 1000)),
            "projection_basis": step.projection.basis,
            "run_id": run_id,
            "warm_recall_scored_on_pod": outcome.warm_recall,
            "calibration": (
                {
                    "verdict": outcome.verdict,
                    "forced": bool(overrides.calibration_verdicts),
                    "target": plan.calibration.target,
                    "band": list(plan.calibration.band),
                }
                if outcome.verdict
                else None
            ),
            "checks": checked,
            "problems": outcome.problems,
            "overrides": overrides.as_dict(),
            "finished_utc": _utc(),
        }
        bundle, digest = package(
            root=root,
            seq=seq,
            step=step,
            outcome=outcome,
            run_dir=run_dir,
            runner=runner,
            tracking_uri=tracking_uri,
        )
        outcome.bundle, outcome.sha256 = bundle, digest
        with index.open("a") as handle:
            handle.write(outcome.index_line(step_label(step.name)) + "\n")
        logger.info("INDEX: %s", outcome.index_line(step_label(step.name)))
        outcomes.append(outcome)


def _mark_terminated(tracking_uri: str, run_id: str, status: str) -> None:
    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri)
        if client.get_run(run_id).info.status == "RUNNING":
            client.set_terminated(run_id, status)
    except Exception:  # noqa: BLE001 -- the bundle records the state either way
        logger.exception("could not mark run %s %s", run_id, status)


# --- command line ---------------------------------------------------------------------------


def _overrides(args: argparse.Namespace) -> Overrides:
    verdicts = tuple(
        part.strip().upper()
        for part in (getattr(args, "calibration_verdicts", "") or "").split(",")
        if part.strip()
    )
    if any(verdict not in (PASS, MISS) for verdict in verdicts):
        raise SystemExit("--calibration-verdicts takes PASS and MISS, comma-separated")
    return Overrides(
        sample_fraction=args.sample_fraction,
        device=args.device,
        epochs=args.epochs,
        tiny=args.tiny,
        hours_scale=getattr(args, "hours_scale", None),
        calibration_verdicts=verdicts,
    )


def _add_override_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample-fraction", type=float, help="rehearsal: a user subsample")
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), help="rehearsal: the device")
    parser.add_argument("--epochs", type=int, help="rehearsal: passes")
    parser.add_argument("--tiny", action="store_true", help="rehearsal: a tiny encoder")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("plan", help="print the order, projections and kill limits")
    show.add_argument("--plan", type=Path, default=PLAN_PATH)

    sweep = commands.add_parser("run-sweep", help="run the plan (sweep.sh starts this)")
    sweep.add_argument("--plan", type=Path, default=PLAN_PATH)
    sweep.add_argument("--root", type=Path, required=True)
    sweep.add_argument("--deadline-epoch", type=float, required=True)
    sweep.add_argument("--run-kind", default="rented-gpu")
    sweep.add_argument("--experiment", help="default: the plan's")
    sweep.add_argument("--hours-scale", type=float, help="rehearsal: scale every time limit")
    sweep.add_argument("--calibration-verdicts", help="rehearsal: e.g. MISS,PASS")
    _add_override_flags(sweep)

    cell = commands.add_parser("train-cell", help="train one cells file (run-sweep starts this)")
    cell.add_argument("cells", type=Path)
    cell.add_argument("--run-dir", type=Path, required=True)
    cell.add_argument("--store", type=Path, required=True)
    cell.add_argument("--experiment", required=True)
    cell.add_argument("--run-kind", default="rented-gpu")
    cell.add_argument("--input-dir", type=Path)
    _add_override_flags(cell)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    if args.command == "plan":
        print(plan_table(load_plan(args.plan)))
        return 0
    if args.command == "train-cell":
        try:
            train_cell(
                args.cells,
                run_dir=args.run_dir,
                store=args.store,
                experiment=args.experiment,
                overrides=_overrides(args),
                input_dir=args.input_dir,
                run_kind=args.run_kind,
            )
        except ProtocolMismatchError as error:
            logger.error("%s", error)
            return EXIT_PROTOCOL_MISMATCH
        return 0
    plan = load_plan(args.plan)
    overrides = _overrides(args)
    experiment = args.experiment or plan.experiment
    terminal = run_sweep(
        plan,
        root=args.root,
        deadline_epoch=args.deadline_epoch,
        child_command=default_child_command(
            python=sys.executable,
            root=args.root,
            experiment=experiment,
            overrides=overrides,
            run_kind=args.run_kind,
        ),
        overrides=overrides,
        experiment=experiment,
    )
    return 0 if terminal.kind == "DONE" else EXIT_HALT


if __name__ == "__main__":
    raise SystemExit(main())
