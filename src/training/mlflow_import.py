"""Import MLflow runs from a local store into the shared tracking server, keeping their run ids.

Non-negotiable 12 says a run lives in the one shared store (``localhost:5001``), and
that a store that dies with its worktree, or with a rented pod's disk, leaves only
numbers in a doc. MLflow's own client cannot carry a run across stores with its id
(``create_run`` always mints a new one), so this writes the rows itself:

* **Sources** are MLflow *file stores* (``<root>/<exp id>/<run id>/{meta.yaml,
  params/, metrics/, tags/, artifacts/}``) or *SQLite stores* (any schema with the
  classic ``runs``/``params``/``tags``/``metrics``/``latest_metrics`` tables, which
  is every MLflow from 2.x to 3.15). A SQLite run's artifacts are read from its
  ``artifact_uri``; ``--remap`` points that at wherever the store was unpacked.
* **The target** is the dev stack's MLflow 2.13 on Postgres (the ``mlflow``
  database), serving artifacts from the Docker volume
  ``movielens-coldstart_mlflow_artifacts`` through its proxy. Experiments are
  matched by name; a name the target has never seen gets a new experiment.

Modes, mutually exclusive:

``--dry-run`` (default)
    Read-only end to end: parses every source, opens one READ ONLY transaction on
    the target, lists the artifact volume through a read-only mount, and prints the
    plan and the verification it would run.
``--execute``
    For each run: copy its artifacts into the volume and check every copied byte by
    SHA-256 inside the volume, then insert runs, params, tags, metrics and
    latest_metrics in ONE transaction. A run that already exists is skipped whole,
    never half-imported. Requires ``--pg-dump``, a fresh dump of the target database.
``--verify``
    Read-only: reads each run back through the REST API and compares it with the
    source (info, params, latest metrics, tags, warm recall, history row counts,
    the artifact listing), and downloads ``per_user_recall.json`` to compare SHA-256.

The connection string comes from ``--dsn``, else ``MLFLOW_IMPORT_DSN``, else the
dev stack's Postgres settings (``Settings``) with the database ``mlflow`` — never a
literal in this file. Every imported run is tagged ``imported_from`` and
``import_date``. History: the 2026-10-05 and 2026-10-07 imports ran an earlier,
uncommitted copy of this tool (``docs/model-planning/experiments/run-ledger-2026-10-05..07.md``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DSN_ENV_VAR = "MLFLOW_IMPORT_DSN"
DEFAULT_TRACKING_URI = "http://localhost:5001"
DEFAULT_VOLUME = "movielens-coldstart_mlflow_artifacts"
DEFAULT_DOCKER_IMAGE = "alpine:3"
DEFAULT_DOCKER_PLATFORM = "linux/arm64"
EXPECTED_ALEMBIC = "867495a8f9d4"
PER_USER_ARTIFACT = "per_user_recall.json"
# Candidate-stage runs log the first; ranker and baseline runs the second.
WARM_RECALL_KEYS = ("warm_recall_at_k_candidates", "warm_recall_at_k")

RUN_STATUS = {1: "RUNNING", 2: "SCHEDULED", 3: "FINISHED", 4: "FAILED", 5: "KILLED"}
SOURCE_TYPE = {1: "NOTEBOOK", 2: "JOB", 3: "PROJECT", 4: "LOCAL", 1000: "UNKNOWN"}
# MLflow 2.13's own API caps. The params column is wider (varchar 8000), so a value
# between the two fits but is one the 2.13 API would refuse; it is warned on.
APP_CAPS = {("params", "value"): 6000, ("tags", "value"): 5000}
RUN_DIR_KNOWN = {"meta.yaml", "params", "metrics", "tags", "artifacts"}
SQL_MAX_FLOAT = 1.7976931348623157e308


def default_dsn() -> str:
    """``MLFLOW_IMPORT_DSN``, else the dev stack's Postgres with the database ``mlflow``."""
    import os

    explicit = os.environ.get(DSN_ENV_VAR, "").strip()
    if explicit:
        return explicit
    from src.config import Settings

    settings = Settings()
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/mlflow"
    )


def mask(dsn: str) -> str:
    return re.sub(r"(://[^:/@]+:)[^@]+@", r"\1***@", dsn)


def ignored(name: str) -> bool:
    """Finder litter and AppleDouble shadows; never MLflow data."""
    return name == ".DS_Store" or name.startswith("._")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- the parsed sources --------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    key: str
    root: Path
    label: str | None = None
    kind: str = "file"  # "file" or "sqlite"
    db: Path | None = None
    # (prefix on the machine that wrote the store, prefix here): a SQLite run's
    # artifact_uri is an absolute path on that machine.
    remap: tuple[str, str] | None = None

    @property
    def imported_from(self) -> str:
        return self.label if self.label is not None else self.root.name


@dataclass
class MetricPoint:
    key: str
    value: float  # as the SQL store keeps it (NaN -> 0 with is_nan, inf -> +/-max)
    raw: float
    timestamp: int
    step: int
    is_nan: bool

    def pk(self) -> tuple[str, int, int, float, bool]:
        return (self.key, self.timestamp, self.step, self.value, self.is_nan)


@dataclass
class ArtifactFile:
    rel: str
    size: int
    sha256: str
    path: Path


@dataclass
class SourceExperiment:
    source: Source
    exp_id: str
    name: str | None
    creation_time: int | None
    directory: Path
    run_dirs: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class RunRecord:
    source: Source
    exp: SourceExperiment
    run_dir: Path
    run_id: str
    import_date: str
    run_name: str | None = None
    status: str | None = None
    source_type: str | None = None
    start_time: int | None = None
    end_time: int | None = None
    deleted_time: int | None = None
    lifecycle_stage: str = "active"
    user_id: str | None = None
    source_name: str | None = None
    entry_point_name: str | None = None
    source_version: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    metric_points: list[MetricPoint] = field(default_factory=list)
    metric_lines_read: int = 0
    duplicate_metric_points: int = 0
    latest: dict[str, MetricPoint] = field(default_factory=dict)
    artifacts: list[ArtifactFile] = field(default_factory=list)
    artifact_dir: Path | None = None
    parse_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    synthesized_tags: dict[str, str] = field(default_factory=dict)
    action: str = "import"  # import | skip-existing | blocked | excluded
    block_reasons: list[str] = field(default_factory=list)
    target_exp_id: int | None = None
    target_exp_name: str | None = None
    target_exp_new: bool = False

    @property
    def artifact_uri(self) -> str:
        return f"mlflow-artifacts:/{self.target_exp_id}/{self.run_id}/artifacts"

    def import_tags(self) -> dict[str, str]:
        tags = dict(self.tags)
        tags.update(self.synthesized_tags)
        tags["imported_from"] = self.source.imported_from
        tags["import_date"] = self.import_date
        return tags


def _sql_value(raw: float) -> tuple[float, bool]:
    if math.isnan(raw):
        return 0.0, True
    if math.isinf(raw):
        return (SQL_MAX_FLOAT if raw > 0 else -SQL_MAX_FLOAT), False
    return raw, False


def _add_point(run: RunRecord, point: MetricPoint, seen: set[tuple[Any, ...]]) -> None:
    run.metric_lines_read += 1
    if point.pk() in seen:
        # The same (key, timestamp, step, value) twice would violate metric_pk;
        # MLflow 2.13's own _log_metrics drops such duplicates too.
        run.duplicate_metric_points += 1
        return
    seen.add(point.pk())
    run.metric_points.append(point)
    # latest_metrics exactly as 2.13 picks it: the greatest (step, timestamp, value).
    current = run.latest.get(point.key)
    if current is None or (point.step, point.timestamp, point.value) > (
        current.step,
        current.timestamp,
        current.value,
    ):
        run.latest[point.key] = point


def read_artifacts(run: RunRecord, base: Path) -> None:
    run.artifact_dir = base
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            run.warnings.append(f"artifact symlink not copied: {path.relative_to(base)}")
            continue
        if path.is_dir():
            continue
        rel = path.relative_to(base).as_posix()
        if ignored(path.name):
            run.warnings.append(f"ignored artifact {rel}")
            continue
        run.artifacts.append(ArtifactFile(rel, path.stat().st_size, sha256_file(path), path))


def _read_kv_dir(run: RunRecord, sub: str) -> dict[str, str]:
    out: dict[str, str] = {}
    base = run.run_dir / sub
    if not base.exists():
        run.warnings.append(f"no {sub}/ directory (treated as empty)")
        return out
    for path in sorted(base.rglob("*")):
        if path.is_dir() or ignored(path.name):
            continue
        key = path.relative_to(base).as_posix()
        try:
            out[key] = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            run.parse_errors.append(f"{sub}/{key}: {error}")
    return out


def _read_file_metrics(run: RunRecord) -> None:
    base = run.run_dir / "metrics"
    if not base.exists():
        run.warnings.append("no metrics/ directory (treated as empty)")
        return
    seen: set[tuple[Any, ...]] = set()
    for path in sorted(base.rglob("*")):
        if path.is_dir() or ignored(path.name):
            continue
        key = path.relative_to(base).as_posix()
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            run.parse_errors.append(f"metrics/{key}: {error}")
            continue
        points = 0
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                run.warnings.append(f"metrics/{key}:{number} blank line skipped")
                continue
            parts = line.strip().split(" ")
            if len(parts) not in (2, 3, 5):
                run.parse_errors.append(f"metrics/{key}:{number} has {len(parts)} fields")
                continue
            try:
                stamp, raw = int(parts[0]), float(parts[1])
                step = int(parts[2]) if len(parts) >= 3 else 0
            except ValueError as error:
                run.parse_errors.append(f"metrics/{key}:{number} {error}")
                continue
            value, is_nan = _sql_value(raw)
            _add_point(run, MetricPoint(key, value, raw, stamp, step, is_nan), seen)
            points += 1
        if points == 0:
            run.parse_errors.append(f"metrics/{key}: no data points")


def _int_or_none(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _finish_run(run: RunRecord, tag_name: str | None) -> None:
    if tag_name is None and run.run_name:
        # 2.13's create_run stores the name as runs.name and the mlflow.runName tag.
        run.synthesized_tags["mlflow.runName"] = run.run_name
    elif tag_name is not None and run.run_name and tag_name != run.run_name:
        run.parse_errors.append(f"run name {run.run_name!r} != tag mlflow.runName {tag_name!r}")
    for key in ("imported_from", "import_date"):
        if key in run.tags:
            run.parse_errors.append(f"source already has tag {key!r}; refusing to overwrite it")
    if run.status == "RUNNING" and run.end_time is None:
        run.warnings.append("status RUNNING with no end_time: it will show as running forever")


def parse_file_run(
    source: Source, exp: SourceExperiment, run_dir: Path, import_date: str
) -> RunRecord:
    import yaml

    run = RunRecord(
        source=source, exp=exp, run_dir=run_dir, run_id=run_dir.name, import_date=import_date
    )
    try:
        meta = yaml.safe_load((run_dir / "meta.yaml").read_text("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("meta.yaml is not a mapping")
    except Exception as error:  # noqa: BLE001 -- any failure makes the run unparseable
        run.parse_errors.append(f"meta.yaml: {error}")
        return run
    for key in ("run_id", "run_uuid"):
        if meta.get(key) and meta[key] != run_dir.name:
            run.parse_errors.append(f"meta.yaml {key} does not match the directory")
    if not re.fullmatch(r"[0-9a-f]{32}", run_dir.name):
        run.parse_errors.append(f"run directory {run_dir.name!r} is not a 32-hex run id")
    status = meta.get("status", 1)
    run.status = RUN_STATUS.get(status) if isinstance(status, int) else str(status)
    kind = meta.get("source_type")
    run.source_type = (
        "UNKNOWN"
        if kind is None
        else (SOURCE_TYPE.get(kind) if isinstance(kind, int) else str(kind))
    )
    try:
        run.start_time = _int_or_none(meta.get("start_time"))
        run.end_time = _int_or_none(meta.get("end_time"))
        run.deleted_time = _int_or_none(meta.get("deleted_time"))
    except (TypeError, ValueError) as error:
        run.parse_errors.append(f"meta.yaml time: {error}")
    run.lifecycle_stage = str(meta.get("lifecycle_stage") or "active")
    run.user_id = _str_or_none(meta.get("user_id"))
    run.source_name = _str_or_none(meta.get("source_name"))
    run.entry_point_name = _str_or_none(meta.get("entry_point_name"))
    run.source_version = _str_or_none(meta.get("source_version"))
    for name in sorted(p.name for p in run_dir.iterdir() if p.name not in RUN_DIR_KNOWN):
        run.warnings.append(f"run dir entry not imported: {name}")
    run.params = _read_kv_dir(run, "params")
    run.tags = _read_kv_dir(run, "tags")
    _read_file_metrics(run)
    read_artifacts(run, run_dir / "artifacts")
    tag_name = run.tags.get("mlflow.runName")
    run.run_name = _str_or_none(meta.get("run_name")) or tag_name
    if not run.run_name:
        run.parse_errors.append("no run name in meta.yaml or the mlflow.runName tag")
    _finish_run(run, tag_name)
    return run


def scan_file_source(
    source: Source, import_date: str
) -> tuple[list[SourceExperiment], list[RunRecord], list[str]]:
    import yaml

    exps: list[SourceExperiment] = []
    runs: list[RunRecord] = []
    notes: list[str] = []
    if not source.root.is_dir():
        return exps, runs, [f"store root missing: {source.root}"]
    for directory in sorted(source.root.iterdir()):
        if not directory.is_dir() or directory.name == ".trash":
            continue
        exp = SourceExperiment(source, directory.name, None, None, directory)
        try:
            meta = yaml.safe_load((directory / "meta.yaml").read_text("utf-8"))
            exp.name = str(meta["name"])
            exp.creation_time = meta.get("creation_time")
        except Exception as error:  # noqa: BLE001
            exp.problems.append(f"experiment meta.yaml unreadable: {error}")
        for child in sorted(directory.iterdir()):
            if child.is_dir() and (child / "meta.yaml").exists():
                exp.run_dirs.append(child)
        exps.append(exp)
        runs.extend(parse_file_run(source, exp, run_dir, import_date) for run_dir in exp.run_dirs)
    return exps, runs, notes


def remap_path(path: Path, remap: tuple[str, str] | None) -> Path:
    if remap is None:
        return path
    old, new = remap
    text = str(path)
    if text == old or text.startswith(old.rstrip("/") + "/"):
        return Path(new + text[len(old.rstrip("/")) :])
    return path


def scan_sqlite_source(
    source: Source, import_date: str
) -> tuple[list[SourceExperiment], list[RunRecord], list[str]]:
    """Read a SQLite store read-only into the same records the file-store reader makes.

    Artifacts come from each run's artifact_uri (remapped when the store moved); a
    run whose artifact directory is missing is blocked rather than imported bare.
    """
    exps: list[SourceExperiment] = []
    runs: list[RunRecord] = []
    notes: list[str] = []
    db = source.db
    if db is None or not db.is_file():
        return exps, runs, [f"sqlite store missing: {db}"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        revision = con.execute("SELECT version_num FROM alembic_version").fetchone()
        notes.append(f"sqlite alembic revision {revision[0] if revision else '?'}")
        by_id: dict[str, SourceExperiment] = {}
        for eid, name, ctime in con.execute(
            "SELECT experiment_id, name, creation_time FROM experiments ORDER BY experiment_id"
        ).fetchall():
            found = SourceExperiment(source, str(eid), name, ctime, source.root)
            by_id[str(eid)] = found
            exps.append(found)
        columns = (
            "run_uuid, name, source_type, source_name, entry_point_name, user_id, status, "
            "start_time, end_time, source_version, lifecycle_stage, artifact_uri, "
            "experiment_id, deleted_time"
        )
        names = [column.strip() for column in columns.split(",")]
        for row in con.execute(f"SELECT {columns} FROM runs ORDER BY start_time").fetchall():
            meta = dict(zip(names, row, strict=True))
            exp = by_id.get(str(meta["experiment_id"]))
            if exp is None:
                notes.append(f"run {meta['run_uuid']} points at a missing experiment")
                continue
            uri = str(meta["artifact_uri"] or "")
            art_dir = remap_path(
                Path(uri[len("file://") :] if uri.startswith("file://") else uri), source.remap
            )
            run = RunRecord(
                source=source,
                exp=exp,
                run_dir=art_dir.parent,
                run_id=str(meta["run_uuid"]),
                import_date=import_date,
            )
            exp.run_dirs.append(art_dir.parent)
            if not re.fullmatch(r"[0-9a-f]{32}", run.run_id):
                run.parse_errors.append(f"run id {run.run_id!r} is not 32-hex")
            if not uri or not art_dir.is_absolute():
                run.parse_errors.append(f"artifact_uri {uri!r} is not a local absolute path")
            elif art_dir.name != "artifacts" or not art_dir.is_dir():
                run.parse_errors.append(f"artifact directory {art_dir} is missing")
            run.run_name = meta["name"]
            run.status = meta["status"]
            run.source_type = meta["source_type"] or "UNKNOWN"
            run.start_time = meta["start_time"]
            run.end_time = meta["end_time"]
            run.deleted_time = meta["deleted_time"]
            run.lifecycle_stage = meta["lifecycle_stage"] or "active"
            run.user_id = meta["user_id"]
            run.source_name = meta["source_name"]
            run.entry_point_name = meta["entry_point_name"]
            run.source_version = meta["source_version"]
            rid = run.run_id
            run.params = dict(
                con.execute("SELECT key, value FROM params WHERE run_uuid = ?", (rid,)).fetchall()
            )
            run.tags = dict(
                con.execute("SELECT key, value FROM tags WHERE run_uuid = ?", (rid,)).fetchall()
            )
            seen: set[tuple[Any, ...]] = set()
            for key, value, stamp, step, is_nan in con.execute(
                "SELECT key, value, timestamp, step, is_nan FROM metrics WHERE run_uuid = ? "
                "ORDER BY key, timestamp, step",
                (rid,),
            ).fetchall():
                flag = bool(is_nan)
                raw = float("nan") if flag else float(value)
                _add_point(
                    run, MetricPoint(key, float(value), raw, int(stamp), int(step), flag), seen
                )
            stored = {
                key: (float(value), int(step))
                for key, value, step in con.execute(
                    "SELECT key, value, step FROM latest_metrics WHERE run_uuid = ?", (rid,)
                ).fetchall()
            }
            if stored != {key: (m.value, m.step) for key, m in run.latest.items()}:
                run.parse_errors.append("recomputed latest_metrics differ from the store's table")
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "inputs" in tables:
                n_inputs = con.execute(
                    "SELECT count(*) FROM inputs WHERE destination_id = ?", (rid,)
                ).fetchone()[0]
                if n_inputs:
                    run.warnings.append(f"{n_inputs} dataset input(s) not imported")
            _finish_run(run, run.tags.get("mlflow.runName"))
            if art_dir.is_dir():
                read_artifacts(run, art_dir)
            runs.append(run)
    finally:
        con.close()
    return exps, runs, notes


def scan(
    source: Source, import_date: str
) -> tuple[list[SourceExperiment], list[RunRecord], list[str]]:
    if source.kind == "sqlite":
        return scan_sqlite_source(source, import_date)
    return scan_file_source(source, import_date)


# --- the target, read-only ----------------------------------------------------------------


@dataclass
class Target:
    alembic: str
    read_only: str
    experiments: dict[str, dict[str, Any]]  # name -> row
    run_ids: dict[str, tuple[int, str]]  # run_uuid -> (experiment_id, name)
    col_limits: dict[tuple[str, str], int | None]
    not_null: set[tuple[str, str]]
    checks: dict[str, set[str]]
    seq_last: int
    seq_called: bool
    server_version: str = ""


def connect_readonly(dsn: str) -> Any:
    import psycopg2

    conn = psycopg2.connect(
        dsn, options="-c default_transaction_read_only=on", application_name="mlflow-import-ro"
    )
    conn.set_session(readonly=True, autocommit=False)
    return conn


def load_target(conn: Any) -> Target:
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        read_only = cur.fetchone()[0]
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]
        cur.execute("SELECT version_num FROM alembic_version")
        alembic = cur.fetchone()[0]
        cur.execute(
            "SELECT experiment_id, name, artifact_location, lifecycle_stage FROM experiments"
        )
        experiments = {
            row[1]: {
                "experiment_id": row[0],
                "name": row[1],
                "artifact_location": row[2],
                "lifecycle_stage": row[3],
            }
            for row in cur.fetchall()
        }
        cur.execute("SELECT run_uuid, experiment_id, name FROM runs")
        run_ids = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        cur.execute("""SELECT table_name, column_name, character_maximum_length, is_nullable
                 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('runs','params','metrics','latest_metrics','tags',
                                     'experiments')""")
        limits: dict[tuple[str, str], int | None] = {}
        not_null: set[tuple[str, str]] = set()
        for table, column, length, nullable in cur.fetchall():
            limits[(table, column)] = length
            if nullable == "NO":
                not_null.add((table, column))
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'runs'::regclass AND contype = 'c'"
        )
        checks: dict[str, set[str]] = {}
        for (definition,) in cur.fetchall():
            match = re.match(r"CHECK \(\(?\(?(\w+)\)?::text = ANY", definition)
            if match:
                checks[match.group(1)] = set(
                    re.findall(r"'([^']+)'::character varying", definition)
                )
        cur.execute("SELECT last_value, is_called FROM experiments_experiment_id_seq")
        seq_last, seq_called = cur.fetchone()
    conn.rollback()
    return Target(
        alembic=alembic,
        read_only=read_only,
        experiments=experiments,
        run_ids=run_ids,
        col_limits=limits,
        not_null=not_null,
        checks=checks,
        seq_last=int(seq_last),
        seq_called=bool(seq_called),
        server_version=str(server_version),
    )


# --- planning --------------------------------------------------------------------------------


@dataclass
class Plan:
    exps: list[SourceExperiment]
    runs: list[RunRecord]
    notes: dict[str, list[str]]
    target: Target
    new_experiments: dict[str, int]
    over_app_cap: list[str]


def build_plan(
    target: Target,
    sources: Sequence[Source],
    *,
    import_date: str,
    only_runs: set[str] | None = None,
    exclude: set[str] | None = None,
) -> Plan:
    """Decide, for every run in every source, import / skip-existing / blocked / excluded."""
    exps: list[SourceExperiment] = []
    runs: list[RunRecord] = []
    notes: dict[str, list[str]] = {}
    for source in sources:
        found_exps, found_runs, found_notes = scan(source, import_date)
        exps += found_exps
        runs += found_runs
        notes[source.key] = found_notes
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.run_id] = counts.get(run.run_id, 0) + 1
    over_app_cap: list[str] = []
    first_new = target.seq_last + 1 if target.seq_called else target.seq_last
    pending_new: dict[str, int] = {}

    def check_len(run: RunRecord, table: str, column: str, value: str | None, label: str) -> None:
        if value is None:
            if (table, column) in target.not_null:
                run.block_reasons.append(f"{label}: NULL into NOT NULL {table}.{column}")
            return
        limit = target.col_limits.get((table, column))
        if limit is not None and len(value) > limit:
            run.block_reasons.append(f"{label}: {len(value)} chars > {table}.{column}({limit})")
            return
        cap = APP_CAPS.get((table, column))
        if cap is not None and len(value) > cap:
            over_app_cap.append(f"{run.run_id} {label}: {len(value)} chars > 2.13 cap {cap}")
        if column == "key" and len(value) > 250:
            run.block_reasons.append(f"{label}: key longer than MLflow's 250-character cap")

    for run in runs:
        excluded = (only_runs is not None and run.run_id not in only_runs) or (
            run.run_id in (exclude or set())
        )
        if run.exp.name is None:
            run.block_reasons.append("source experiment has no readable name")
        elif run.exp.name in target.experiments:
            row = target.experiments[run.exp.name]
            run.target_exp_id = int(row["experiment_id"])
            run.target_exp_name = run.exp.name
            if row["lifecycle_stage"] != "active":
                run.block_reasons.append(f"target experiment {run.exp.name!r} is not active")
        else:
            run.target_exp_name = run.exp.name
            run.target_exp_new = True
            if not excluded:
                pending_new.setdefault(run.exp.name, first_new + len(pending_new))
            run.target_exp_id = pending_new.get(run.exp.name)
        run.block_reasons.extend(f"parse: {error}" for error in run.parse_errors)
        if counts[run.run_id] > 1:
            run.block_reasons.append(f"run id appears in {counts[run.run_id]} source stores")
        for column, value in (
            ("run_uuid", run.run_id),
            ("name", run.run_name),
            ("source_type", run.source_type),
            ("source_name", run.source_name),
            ("entry_point_name", run.entry_point_name),
            ("user_id", run.user_id),
            ("status", run.status),
            ("source_version", run.source_version),
            ("lifecycle_stage", run.lifecycle_stage),
        ):
            check_len(run, "runs", column, value, f"runs.{column}")
            allowed = target.checks.get(column)
            if value is not None and allowed and value not in allowed:
                run.block_reasons.append(f"runs.{column}={value!r} violates its CHECK")
        for key, value in run.params.items():
            check_len(run, "params", "key", key, f"param key {key!r}")
            check_len(run, "params", "value", value, f"param {key!r}")
        for key, value in run.import_tags().items():
            check_len(run, "tags", "key", key, f"tag key {key!r}")
            check_len(run, "tags", "value", value, f"tag {key!r}")
        for key in run.latest:
            check_len(run, "metrics", "key", key, f"metric key {key!r}")
        if excluded:
            run.action = "excluded"
        elif run.block_reasons:
            run.action = "blocked"
        elif run.run_id in target.run_ids:
            run.action = "skip-existing"
        else:
            run.action = "import"
    # Only an experiment some imported run lands in is created.
    needed = [
        name
        for name in pending_new
        if any(r.action == "import" and r.target_exp_name == name for r in runs)
    ]
    new_experiments = {name: first_new + index for index, name in enumerate(needed)}
    for run in runs:
        if run.target_exp_new:
            run.target_exp_id = new_experiments.get(run.target_exp_name or "")
    return Plan(exps, runs, notes, target, new_experiments, over_app_cap)


def expected_counts(run: RunRecord) -> dict[str, Any]:
    key, value = warm_recall(run)
    return {
        "params": len(run.params),
        "latest_metrics": len(run.latest),
        "metric_points": len(run.metric_points),
        "tags": len(run.import_tags()),
        "artifact_files": len(run.artifacts),
        "artifact_bytes": sum(a.size for a in run.artifacts),
        "warm_recall_key": key,
        "warm_recall": value,
    }


def warm_recall(run: RunRecord) -> tuple[str | None, float | None]:
    for key in WARM_RECALL_KEYS:
        if key in run.latest:
            return key, run.latest[key].raw
    return None, None


def plan_document(plan: Plan) -> dict[str, Any]:
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "new_experiments": plan.new_experiments,
        "notes": plan.notes,
        "over_app_cap": plan.over_app_cap,
        "runs": [
            {
                "run_id": run.run_id,
                "run_name": run.run_name,
                "source": run.source.key,
                "source_experiment": [run.exp.exp_id, run.exp.name],
                "target_experiment_id": run.target_exp_id,
                "target_experiment_new": run.target_exp_new,
                "action": run.action,
                "block_reasons": run.block_reasons,
                "status": run.status,
                "counts": expected_counts(run),
                "warnings": run.warnings,
                "artifacts": [
                    {"path": a.rel, "size": a.size, "sha256": a.sha256} for a in run.artifacts
                ],
            }
            for run in plan.runs
        ],
    }


def print_report(plan: Plan, dsn: str) -> None:
    target = plan.target
    print(f"target {mask(dsn)} (Postgres {target.server_version}), read-only={target.read_only}")
    if target.alembic != EXPECTED_ALEMBIC:
        print(f"  alembic {target.alembic} (EXPECTED {EXPECTED_ALEMBIC}: a different MLflow)")
    for key, notes in plan.notes.items():
        for note in notes:
            print(f"  {key}: {note}")
    if plan.new_experiments:
        print(f"  WOULD CREATE experiments {plan.new_experiments}")
    for run in plan.runs:
        counts = expected_counts(run)
        print(
            f"  {run.action:<13} {run.source.key:<16} {run.run_id} -> exp {run.target_exp_id} "
            f"p{counts['params']} m{counts['latest_metrics']}/{counts['metric_points']} "
            f"t{counts['tags']} a{counts['artifact_files']} {run.run_name} "
            f"{counts['warm_recall_key'] or ''}={counts['warm_recall']}"
        )
        for reason in run.block_reasons:
            print(f"      blocked: {reason}")
        for warning in run.warnings:
            print(f"      warning: {warning}")
    for line in plan.over_app_cap:
        print(f"  over the 2.13 API cap (the column still fits): {line}")


# --- execute (the only code that writes) ---------------------------------------------------


@dataclass(frozen=True)
class DockerTarget:
    volume: str = DEFAULT_VOLUME
    image: str = DEFAULT_DOCKER_IMAGE
    platform: str = DEFAULT_DOCKER_PLATFORM


def _docker(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def check_pg_dump(path: Path, max_age_hours: float) -> list[str]:
    """Problems with the dump the import insists on first; empty when it is usable."""
    if not path.exists():
        return [f"{path} does not exist"]
    problems = []
    if path.stat().st_size == 0:
        problems.append(f"{path} is empty")
    age = (time.time() - path.stat().st_mtime) / 3600
    if age > max_age_hours:
        problems.append(f"{path} is {age:.1f} h old (> {max_age_hours} h); take a fresh dump")
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        else:
            text = path.read_text("utf-8", errors="replace")
    except OSError as error:
        return problems + [f"cannot read {path}: {error}"]
    if "PostgreSQL database dump" not in text[:4096]:
        problems.append(f"{path} does not look like a plain pg_dump")
    for table in ("public.runs", "public.experiments", "public.metrics", "public.alembic_version"):
        if f"COPY {table} " not in text:
            problems.append(f"{path} has no COPY {table} section")
    if "complete" not in text[-512:]:
        problems.append(f"{path} lacks pg_dump's completion marker (truncated?)")
    return problems


def copy_artifacts(docker: DockerTarget, run: RunRecord) -> tuple[str, str]:
    """Copy a run's artifacts into the volume and prove every byte by SHA-256 in place.

    Idempotent: a destination that already holds exactly these files is accepted;
    anything else already there is an error.
    """
    if not run.artifacts or run.artifact_dir is None:
        return "no-artifacts", ""
    dest = f"/m/{run.target_exp_id}/{run.run_id}/artifacts"
    run_root = f"/m/{run.target_exp_id}/{run.run_id}"
    script = (
        "set -eu\n"
        f"dest={shlex.quote(dest)}\n"
        'if [ -d "$dest" ] && [ -n "$(ls -A "$dest")" ]; then\n'
        "  echo STATE=EXISTS\n"
        "else\n"
        '  mkdir -p "$dest"\n'
        '  cp -R /src/. "$dest"/\n'
        f"  chown -R 0:0 {shlex.quote(run_root)}\n"
        f"  chmod -R u=rwX,go=rX {shlex.quote(run_root)}\n"
        "  echo STATE=COPIED\n"
        "fi\n"
        'cd "$dest" && find . -type f -exec sha256sum {} +\n'
    )
    done = _docker(
        [
            "run",
            "--rm",
            "--platform",
            docker.platform,
            "--mount",
            f"type=volume,src={docker.volume},dst=/m",
            "--mount",
            f"type=bind,src={run.artifact_dir},dst=/src,readonly",
            docker.image,
            "sh",
            "-c",
            script,
        ]
    )
    if done.returncode != 0:
        return "error", done.stderr.strip()
    state = (
        "COPIED"
        if "STATE=COPIED" in done.stdout
        else ("EXISTS" if "STATE=EXISTS" in done.stdout else "?")
    )
    got: dict[str, str] = {}
    for line in done.stdout.splitlines():
        if line.startswith("STATE="):
            continue
        digest, _, rel = line.partition("  ")
        got[rel[2:] if rel.startswith("./") else rel] = digest
    want = {a.rel: a.sha256 for a in run.artifacts if not ignored(Path(a.rel).name)}
    got = {rel: digest for rel, digest in got.items() if not ignored(Path(rel).name)}
    if got != want:
        missing = sorted(set(want) - set(got))[:5]
        extra = sorted(set(got) - set(want))[:5]
        differ = sorted(k for k in set(want) & set(got) if want[k] != got[k])[:5]
        return "error", f"{state}: sha256 mismatch: missing {missing} extra {extra} differ {differ}"
    return state.lower(), f"{len(got)} files sha256-verified in the volume"


def create_experiment(conn: Any, name: str, creation_time: int | None) -> int:
    """As SqlAlchemyStore.create_experiment does: insert, then point at the artifact proxy."""
    now = int(time.time() * 1000)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT experiment_id FROM experiments WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                conn.rollback()
                return int(row[0])
            cur.execute(
                "INSERT INTO experiments (name, artifact_location, lifecycle_stage, "
                "creation_time, last_update_time) VALUES (%s, NULL, 'active', %s, %s) "
                "RETURNING experiment_id",
                (name, creation_time or now, now),
            )
            eid = int(cur.fetchone()[0])
            cur.execute(
                "UPDATE experiments SET artifact_location = %s WHERE experiment_id = %s",
                (f"mlflow-artifacts:/{eid}", eid),
            )
        conn.commit()
        return eid
    except Exception:
        conn.rollback()
        raise


def insert_run(conn: Any, run: RunRecord) -> str:
    """Insert one run in one transaction; returns ``inserted`` or ``conflict``."""
    import psycopg2.extras

    tags = run.import_tags()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (run_uuid, name, source_type, source_name, entry_point_name, "
                "user_id, status, start_time, end_time, source_version, lifecycle_stage, "
                "artifact_uri, experiment_id, deleted_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_uuid) DO NOTHING RETURNING run_uuid",
                (
                    run.run_id,
                    run.run_name,
                    run.source_type,
                    run.source_name,
                    run.entry_point_name,
                    run.user_id,
                    run.status,
                    run.start_time,
                    run.end_time,
                    run.source_version,
                    run.lifecycle_stage,
                    run.artifact_uri,
                    run.target_exp_id,
                    run.deleted_time,
                ),
            )
            if cur.fetchone() is None:
                conn.rollback()
                return "conflict"
            if run.params:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO params (key, value, run_uuid) VALUES %s",
                    [(k, v, run.run_id) for k, v in run.params.items()],
                )
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO tags (key, value, run_uuid) VALUES %s",
                [(k, v, run.run_id) for k, v in tags.items()],
            )
            if run.metric_points:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO metrics (key, value, timestamp, step, is_nan, run_uuid) VALUES %s",
                    [
                        (m.key, m.value, m.timestamp, m.step, m.is_nan, run.run_id)
                        for m in run.metric_points
                    ],
                    page_size=1000,
                )
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO latest_metrics (key, value, timestamp, step, is_nan, run_uuid) "
                    "VALUES %s",
                    [
                        (m.key, m.value, m.timestamp, m.step, m.is_nan, run.run_id)
                        for m in run.latest.values()
                    ],
                )
            cur.execute(
                "SELECT (SELECT count(*) FROM params WHERE run_uuid = %(r)s), "
                "(SELECT count(*) FROM tags WHERE run_uuid = %(r)s), "
                "(SELECT count(*) FROM metrics WHERE run_uuid = %(r)s), "
                "(SELECT count(*) FROM latest_metrics WHERE run_uuid = %(r)s)",
                {"r": run.run_id},
            )
            got = tuple(cur.fetchone())
            want = (len(run.params), len(tags), len(run.metric_points), len(run.latest))
            if got != want:
                raise RuntimeError(f"row counts inside the transaction {got} != planned {want}")
        conn.commit()
        return "inserted"
    except Exception:
        conn.rollback()
        raise


def execute(
    plan: Plan,
    *,
    dsn: str,
    pg_dump: Path,
    docker: DockerTarget,
    max_dump_age_hours: float = 2.0,
    allow_blocked: bool = False,
    log_dir: Path | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Import every ``import`` run; stop at the first failure. Returns (exit code, results)."""
    import psycopg2

    problems = check_pg_dump(pg_dump.expanduser(), max_dump_age_hours)
    if problems:
        print("REFUSING TO EXECUTE -- the pg_dump precondition failed:")
        for problem in problems:
            print(f"  {problem}")
        return 2, []
    blocked = [run for run in plan.runs if run.action == "blocked"]
    if blocked and not allow_blocked:
        print(f"REFUSING TO EXECUTE -- {len(blocked)} run(s) blocked (see the report)")
        return 2, []
    to_import = [run for run in plan.runs if run.action == "import"]
    conn = psycopg2.connect(dsn, application_name="mlflow-import-rw")
    conn.set_session(readonly=False, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SET lock_timeout = '10s'")
        cur.execute("SET statement_timeout = '300s'")
    conn.commit()
    results: list[dict[str, Any]] = []
    created: dict[str, int] = {}
    code = 0
    try:
        for name in plan.new_experiments:
            created[name] = create_experiment(
                conn, name, next((e.creation_time for e in plan.exps if e.name == name), None)
            )
            print(f"  experiment {name!r}: id {created[name]}")
        for run in to_import:
            if run.target_exp_new:
                run.target_exp_id = created[run.target_exp_name or ""]
            state, detail = copy_artifacts(docker, run)
            if state == "error":
                print(f"  FAIL artifacts {run.run_id}: {detail}")
                results.append({"run_id": run.run_id, "result": "artifact-error", "detail": detail})
                code = 1
                break
            outcome = insert_run(conn, run)
            results.append(
                {
                    "run_id": run.run_id,
                    "result": outcome,
                    "artifacts": state,
                    "experiment_id": run.target_exp_id,
                }
            )
            print(f"  {outcome:<9} {run.run_id} exp {run.target_exp_id} artifacts {state} {detail}")
    except Exception as error:  # noqa: BLE001 -- reported and returned, never half-done
        print(f"  STOPPED: {type(error).__name__}: {error}")
        code = 1
    finally:
        conn.close()
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        (log_dir / f"import-execute-{stamp}.json").write_text(
            json.dumps(
                {"dsn": mask(dsn), "volume": docker.volume, "results": results, "created": created},
                indent=2,
            )
        )
    return code, results


# --- verify (read-only) ----------------------------------------------------------------------


def _feq(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


def verify(
    plan: Plan,
    *,
    dsn: str,
    tracking_uri: str,
    run_ids: Sequence[str],
    download_per_user: bool = True,
    scratch: Path | None = None,
) -> tuple[int, list[str]]:
    """Read each run back through the REST API and compare it with its source."""
    from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    by_id = {run.run_id: run for run in plan.runs}
    conn = connect_readonly(dsn)
    target = load_target(conn)
    lines: list[str] = []
    failures = 0
    try:
        for rid in run_ids:
            run = by_id.get(rid)
            if run is None:
                lines.append(f"FAIL {rid}: not in any source store")
                failures += 1
                continue
            row = target.experiments.get(run.exp.name or "")
            if row is None:
                lines.append(f"FAIL {rid}: the target has no experiment {run.exp.name!r}")
                failures += 1
                continue
            exp_id = row["experiment_id"]
            errors: list[str] = []
            try:
                got = client.get_run(rid)
            except Exception as error:  # noqa: BLE001
                lines.append(f"FAIL {rid}: get_run: {str(error)[:160]}")
                failures += 1
                continue
            info, data = got.info, got.data
            for label, server, source in (
                ("run_name", info.run_name, run.run_name),
                ("status", info.status, run.status),
                ("start_time", info.start_time, run.start_time),
                ("end_time", info.end_time, run.end_time),
                ("experiment_id", str(info.experiment_id), str(exp_id)),
                ("artifact_uri", info.artifact_uri, f"mlflow-artifacts:/{exp_id}/{rid}/artifacts"),
                ("lifecycle_stage", info.lifecycle_stage, run.lifecycle_stage),
            ):
                if server != source:
                    errors.append(f"{label}: server {server!r} != source {source!r}")
            if data.params != run.params:
                errors.append(f"params differ ({len(data.params)} vs {len(run.params)})")
            want = {key: point.raw for key, point in run.latest.items()}
            if set(data.metrics) != set(want) or any(
                not _feq(data.metrics[k], want[k]) for k in want
            ):
                errors.append(f"latest metrics differ ({len(data.metrics)} vs {len(want)})")
            if data.tags != run.import_tags():
                errors.append("tags differ")
            key, value = warm_recall(run)
            if key and value is not None:
                served = data.metrics.get(key)
                if served is None or not _feq(served, value):
                    errors.append(f"{key}: server {served} != source {value}")
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM metrics WHERE run_uuid = %s", (rid,))
                n_history = int(cur.fetchone()[0])
            conn.rollback()
            if n_history != len(run.metric_points):
                errors.append(f"history rows {n_history} != source {len(run.metric_points)}")
            listed: dict[str, int] = {}
            try:
                # Walk the run's own artifact repository: the 3.x client's
                # list_artifacts goes through an endpoint a 2.13 server lacks.
                repo = get_artifact_repository(info.artifact_uri, tracking_uri=tracking_uri)
                stack: list[str | None] = [None]
                while stack:
                    path = stack.pop()
                    for item in repo.list_artifacts(path):
                        if item.is_dir:
                            stack.append(item.path)
                        else:
                            listed[item.path] = int(item.file_size or 0)
            except Exception as error:  # noqa: BLE001
                errors.append(f"list_artifacts: {str(error)[:120]}")
            if listed != {a.rel: a.size for a in run.artifacts}:
                errors.append(
                    f"artifacts differ: server {len(listed)} / source {len(run.artifacts)}"
                )
            source_file = next((a for a in run.artifacts if a.rel == PER_USER_ARTIFACT), None)
            if download_per_user and source_file is not None:
                with tempfile.TemporaryDirectory(dir=scratch) as tmp:
                    local = client.download_artifacts(rid, PER_USER_ARTIFACT, tmp)
                    digest = sha256_file(Path(local))
                if digest != source_file.sha256:
                    errors.append(f"{PER_USER_ARTIFACT} sha256 {digest} != {source_file.sha256}")
            status = "PASS" if not errors else "FAIL"
            failures += bool(errors)
            lines.append(
                f"{status} {rid} exp {exp_id} params {len(data.params)} "
                f"metrics {len(data.metrics)} history {n_history} artifacts {len(listed)}"
            )
            lines.extend(f"     {error}" for error in errors)
    finally:
        conn.close()
    return failures, lines


# --- command line -------------------------------------------------------------------------


def parse_sources(
    items: Sequence[str], labels: Sequence[str], remaps: Sequence[str]
) -> list[Source]:
    """``KEY=PATH`` (a file-store root, or a ``.db`` file), ``KEY=TEXT`` labels,
    ``KEY=OLD=NEW`` artifact-path remaps."""
    label_of = dict(item.split("=", 1) for item in labels)
    remap_of: dict[str, tuple[str, str]] = {}
    for item in remaps:
        key, old, new = item.split("=", 2)
        remap_of[key] = (old, new)
    sources: list[Source] = []
    for item in items:
        key, _, raw = item.partition("=")
        path = Path(raw).expanduser().resolve()
        if path.suffix == ".db":
            sources.append(
                Source(key, path.parent, label_of.get(key), "sqlite", path, remap_of.get(key))
            )
        else:
            sources.append(Source(key, path, label_of.get(key), "file", None, remap_of.get(key)))
    unknown = (set(label_of) | set(remap_of)) - {source.key for source in sources}
    if unknown:
        raise ValueError(f"--source-label/--remap for unknown store(s): {sorted(unknown)}")
    return sources


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run")
    mode.add_argument("--execute", dest="mode", action="store_const", const="execute")
    mode.add_argument("--verify", dest="mode", action="store_const", const="verify")
    parser.set_defaults(mode="dry-run")
    parser.add_argument("--source", action="append", required=True, metavar="KEY=PATH")
    parser.add_argument("--source-label", action="append", default=[], metavar="KEY=TEXT")
    parser.add_argument("--remap", action="append", default=[], metavar="KEY=OLD=NEW")
    parser.add_argument("--import-date", required=True)
    parser.add_argument("--only-run", action="append", default=[], help="import just these ids")
    parser.add_argument("--exclude-run", action="append", default=[])
    parser.add_argument("--dsn", help=f"default: ${DSN_ENV_VAR}, else the dev stack's settings")
    parser.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    parser.add_argument("--artifacts-volume", default=DEFAULT_VOLUME)
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--docker-platform", default=DEFAULT_DOCKER_PLATFORM)
    parser.add_argument("--pg-dump", type=Path, help="--execute: a fresh plain pg_dump (.sql[.gz])")
    parser.add_argument("--max-dump-age-hours", type=float, default=2.0)
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--run-id", action="append", default=[], help="--verify: these runs")
    args = parser.parse_args(list(argv) if argv is not None else None)

    dsn = args.dsn or default_dsn()
    sources = parse_sources(args.source, args.source_label, args.remap)
    conn = connect_readonly(dsn)
    try:
        target = load_target(conn)
    finally:
        conn.close()
    if target.read_only != "on":
        raise SystemExit("could not open a read-only session; refusing to continue")
    plan = build_plan(
        target,
        sources,
        import_date=args.import_date,
        only_runs=set(args.only_run) or None,
        exclude=set(args.exclude_run),
    )
    if args.mode == "verify":
        ids = list(args.run_id) or [r.run_id for r in plan.runs if r.action != "excluded"]
        failures, lines = verify(plan, dsn=dsn, tracking_uri=args.tracking_uri, run_ids=ids)
        print("\n".join(lines))
        print(f"VERIFY: {'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
        return 0 if not failures else 1
    print_report(plan, dsn)
    if args.plan_json:
        args.plan_json.write_text(json.dumps(plan_document(plan), indent=2, default=str))
    if args.mode == "dry-run":
        print("DRY RUN: nothing was written.")
        return 0
    if args.pg_dump is None:
        parser.error("--execute requires --pg-dump (take a fresh pg_dump of the mlflow database)")
    code, _results = execute(
        plan,
        dsn=dsn,
        pg_dump=args.pg_dump,
        docker=DockerTarget(args.artifacts_volume, args.docker_image, args.docker_platform),
        max_dump_age_hours=args.max_dump_age_hours,
        allow_blocked=args.allow_blocked,
        log_dir=args.log_dir,
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
