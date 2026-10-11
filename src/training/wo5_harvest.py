"""WO-5 step two, the Mac's half: bring every sweep run home, record it, re-score it, back it up.

``infra/gpu/harvest.sh`` runs this unattended (a launchd LaunchAgent under
``caffeinate -i``) while the pod runs ``infra/gpu/sweep.sh``. Every ``--interval``
seconds it reads the pod's ``out/INDEX`` over SSH and, for each bundle it has not
finished, in order and one at a time:

1. **fetch** the ``.tgz`` and its ``.sha256`` (scp; nothing on the pod is changed);
2. **verify** the SHA-256 against both the ``.sha256`` and the INDEX line, unpack into
   ``artifacts/wo5-sweep/<seq>-<cell>/``, check the bundle's own MANIFEST.sha256 and
   refuse anything credential-shaped (the pod's RunPod key above all);
3. **import** the run into the shared MLflow store with its original run id
   (``src.training.mlflow_import``, after a fresh pg_dump) and **verify** it back
   through the REST API;
4. **re-score** it on this Mac's CPU from its saved weights (owner condition 5,
   ADR 0020 D6), under the same protocol hash, as a second run tagged
   ``rescored_from=<run id>``, ``rescore_of_record=true``; for the calibration, the
   D6 check (warm recall@500 within ±1% of 0.5091713455) — PASS or MISS;
5. **back up** the bundle, its ``.sha256`` and a fresh ``pg_dump`` of mlflow into the
   backup repository (every file under 100 MB, split if not; secret-scanned), one
   commit per bundle, pushed;
6. **acknowledge** it by appending ``<seq> <run id> <sha256> OK <utc>`` to the pod's
   ``out/HARVESTED`` — the line ``sweep.sh`` waits for before the pod stops itself.

A stage that fails is retried on the next poll; nothing is skipped past. When the
INDEX ends in DONE or HALT and every bundle is acknowledged, it writes its last
summary line, removes its own LaunchAgent (when it is one) and exits.

Progress for a person, with no agent involved: ``harvest-status.json`` (last poll,
each bundle's stages), ``harvest-summary.log`` (one line per bundle) and
``harvest.log`` (everything), all in the destination directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.training import protocol_manifest
from src.training.wo5_sweep import (
    CHILD_RESULT,
    RUNNER_RESULT,
    CalibrationCheck,
    file_sha256,
    index_terminal,
    load_plan,
    parse_index,
)

logger = logging.getLogger(__name__)

REPO_ROOT = protocol_manifest.REPO_ROOT
DEFAULT_DEST = REPO_ROOT / "artifacts" / "wo5-sweep"
DEFAULT_REMOTE_ROOT = "/workspace/wo5-sweep"
DEFAULT_LABEL = "io.movielens.wo5-harvest"
LAUNCHD_ENV_VAR = "WO5_HARVEST_LAUNCHD_LABEL"
STATE_FILE = "harvest-state.json"
STATUS_FILE = "harvest-status.json"
SUMMARY_LOG = "harvest-summary.log"
HARVEST_LOG = "harvest.log"
ACK_FILE = "HARVESTED"
# GitHub refuses files over 100 MB; parts stay well under it.
BACKUP_PART_BYTES = 95 * 1024 * 1024
STAGES = ("fetched", "verified", "imported", "rescored", "backed_up", "acked")
# Tries at an ACK, after the sweep has ended, before it is recorded as unsent.
ACK_ATTEMPTS = 3
# The RunPod key format first: the pod's own key is the credential that must never ride along.
CREDENTIAL_PATTERNS = (
    re.compile(rb"rpa_[A-Za-z0-9]{20,}"),
    re.compile(rb"(?i)(api[_-]?key|secret|passw(or)?d|bearer|token)[ \t]*[:=][ \t]*\S{8,}"),
)
# Any training entry point except this harvester's own processes.
TRAINING_PROCESS = re.compile(r"-m src\.training\.(?!wo5_harvest)")


class HarvestError(RuntimeError):
    """A stage failed; the bundle is retried on the next poll."""


# --- where the bundles come from ------------------------------------------------------------


class Transport(Protocol):
    def read_text(self, relative: str) -> str | None: ...
    def fetch(self, relative: str, local: Path) -> None: ...
    def append_line(self, relative: str, line: str) -> None: ...


@dataclass(frozen=True)
class SshTransport:
    """The pod over SSH on its exposed TCP port. Reads and copies; writes only ACK lines."""

    host: str
    port: int
    key: Path
    user: str = "root"
    remote_root: str = DEFAULT_REMOTE_ROOT
    ssh_bin: str = "ssh"
    scp_bin: str = "scp"
    timeout: float = 600.0

    def _options(self, port_flag: str) -> list[str]:
        return [
            port_flag,
            str(self.port),
            "-i",
            str(self.key),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=20",
            "-o",
            "ServerAliveInterval=15",
        ]

    def _ssh(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.ssh_bin, *self._options("-p"), f"{self.user}@{self.host}", command],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

    def _path(self, relative: str) -> str:
        return f"{self.remote_root.rstrip('/')}/{relative}"

    def read_text(self, relative: str) -> str | None:
        path = shlex.quote(self._path(relative))
        done = self._ssh(f"if [ -f {path} ]; then cat {path}; else echo __WO5_ABSENT__; fi")
        if done.returncode != 0:
            raise HarvestError(f"ssh read {relative}: {done.stderr.strip()[:300]}")
        return None if done.stdout.strip() == "__WO5_ABSENT__" else done.stdout

    def fetch(self, relative: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(local.name + ".part")
        done = subprocess.run(
            [
                self.scp_bin,
                *self._options("-P"),
                f"{self.user}@{self.host}:{self._path(relative)}",
                str(partial),
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout * 6,
            check=False,
        )
        if done.returncode != 0:
            raise HarvestError(f"scp {relative}: {done.stderr.strip()[:300]}")
        partial.rename(local)

    def append_line(self, relative: str, line: str) -> None:
        done = self._ssh(f"echo {shlex.quote(line)} >> {shlex.quote(self._path(relative))}")
        if done.returncode != 0:
            raise HarvestError(f"ssh append {relative}: {done.stderr.strip()[:300]}")


@dataclass(frozen=True)
class LocalTransport:
    """A sweep root on this machine (rehearsals)."""

    root: Path

    def read_text(self, relative: str) -> str | None:
        path = self.root / relative
        return path.read_text() if path.is_file() else None

    def fetch(self, relative: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(local.name + ".part")
        shutil.copyfile(self.root / relative, partial)
        partial.rename(local)

    def append_line(self, relative: str, line: str) -> None:
        with (self.root / relative).open("a") as handle:
            handle.write(line + "\n")


# --- configuration and state ---------------------------------------------------------------


@dataclass
class HarvestConfig:
    dest: Path = DEFAULT_DEST
    tracking_uri: str = "http://localhost:5001"
    backup_repo: Path = Path.home() / "movielens-backups"
    push: bool = True
    interval: float = 300.0
    input_dir: Path = REPO_ROOT / "data" / "raw" / "ml-25m"
    repo_dir: Path = REPO_ROOT
    python: str = sys.executable
    pg_container: str = "movielens-coldstart-postgres-1"
    pg_user: str = "recsys"
    import_date: str = field(default_factory=lambda: dt.date.today().isoformat())
    source_label: str = "wo5-sweep pod"
    docker_platform: str = "linux/arm64"
    max_polls: int | None = None
    wait_for_other_training: float = 600.0


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class HarvestState:
    path: Path
    bundles: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: bool = False
    # The last INDEX read from the pod: once it has ended, a stopped pod no longer
    # blocks the bundles that are already home.
    index_text: str = ""

    @classmethod
    def load(cls, path: Path) -> HarvestState:
        if path.is_file():
            data = json.loads(path.read_text())
            return cls(
                path,
                dict(data.get("bundles", {})),
                bool(data.get("finished", False)),
                str(data.get("index_text", "")),
            )
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".tmp")
        document = {
            "bundles": self.bundles,
            "finished": self.finished,
            "index_text": self.index_text,
        }
        partial.write_text(json.dumps(document, indent=2) + "\n")
        partial.replace(self.path)

    def entry(self, seq: str) -> dict[str, Any]:
        return self.bundles.setdefault(seq, {"stages": {}})


# --- the stages -----------------------------------------------------------------------------


def scan_for_credentials(root: Path) -> list[str]:
    """Files under ``root`` (or the file itself) holding credential-shaped text."""
    hits = []
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    for path in paths:
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        for pattern in CREDENTIAL_PATTERNS:
            match = pattern.search(data)
            if match:
                hits.append(f"{path}: {match.group(0)[:12]!r}...")
                break
    return hits


def check_manifest(bundle_dir: Path) -> None:
    manifest = bundle_dir / "MANIFEST.sha256"
    if not manifest.is_file():
        raise HarvestError(f"{bundle_dir} has no MANIFEST.sha256")
    for line in manifest.read_text().splitlines():
        digest, _, rel = line.partition("  ")
        path = bundle_dir / rel.removeprefix("./")
        if not path.is_file() or file_sha256(path) != digest:
            raise HarvestError(f"{rel} does not match the bundle's MANIFEST.sha256")


def unpack(tgz: Path, destination: Path) -> Path:
    """Extract into ``destination``; return the bundle's own top directory."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as archive:
        names = {member.name.split("/", 1)[0] for member in archive.getmembers()}
        if len(names) != 1:
            raise HarvestError(f"{tgz.name} holds {len(names)} top-level entries, not one")
        archive.extractall(destination, filter="data")
    return destination / names.pop()


def other_training_processes() -> list[str]:
    """Training jobs on this Mac; a re-score never runs beside one (one CPU job at a time)."""
    done = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True)
    own = {os.getpid(), os.getppid()}
    found = []
    for line in done.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) not in own and TRAINING_PROCESS.search(command):
            if "python" in command:
                found.append(line.strip()[:160])
    return found


def pg_dump(config: HarvestConfig, path: Path) -> Path:
    """A fresh gzip'd plain dump of the mlflow database, checked with gzip -t."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with partial.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as handle:
        done = subprocess.run(
            [
                "docker",
                "exec",
                config.pg_container,
                "pg_dump",
                "-U",
                config.pg_user,
                "-d",
                "mlflow",
            ],
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            raise HarvestError(f"pg_dump failed: {done.stderr.decode()[:300]}")
        handle.write(done.stdout)
    partial.rename(path)
    with gzip.open(path, "rb") as check:
        while check.read(1 << 20):
            pass
    return path


def import_run(
    config: HarvestConfig, bundle_dir: Path, run_id: str, *, dumps: Path, label: str
) -> list[str]:
    """Import the bundle's run into the shared store with its id and verify it; return lines."""
    from src.training import mlflow_import

    store = json.loads((bundle_dir / "mlflow" / "STORE.json").read_text())
    pod_root = store.get("artifact_root_on_pod")
    if not pod_root:
        raise HarvestError(f"{bundle_dir.name}: STORE.json names no artifact root")
    source = mlflow_import.Source(
        key="wo5-pod",
        root=bundle_dir / "mlflow",
        label=label,
        kind="sqlite",
        db=bundle_dir / "mlflow" / "mlflow.db",
        remap=(str(pod_root), str((bundle_dir / "mlflow" / "files").resolve())),
    )
    dsn = mlflow_import.default_dsn()
    conn = mlflow_import.connect_readonly(dsn)
    try:
        target = mlflow_import.load_target(conn)
    finally:
        conn.close()
    plan = mlflow_import.build_plan(
        target, [source], import_date=config.import_date, only_runs={run_id}
    )
    run = next((r for r in plan.runs if r.run_id == run_id), None)
    if run is None:
        raise HarvestError(f"run {run_id} is not in the bundle's store")
    if run.action == "blocked":
        raise HarvestError(f"import blocked: {'; '.join(run.block_reasons)}")
    if run.action == "import":
        dump = pg_dump(config, dumps / f"mlflow-pre-import-{run_id[:8]}-{_stamp()}.sql.gz")
        code, results = mlflow_import.execute(
            plan,
            dsn=dsn,
            pg_dump=dump,
            docker=mlflow_import.DockerTarget(platform=config.docker_platform),
            log_dir=dumps,
        )
        if code != 0:
            raise HarvestError(f"import failed: {results}")
    failures, lines = mlflow_import.verify(
        plan, dsn=dsn, tracking_uri=config.tracking_uri, run_ids=[run_id], scratch=dumps
    )
    if failures:
        raise HarvestError("import verify failed: " + " | ".join(lines))
    return lines


def _stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def split_for_backup(path: Path, limit: int = BACKUP_PART_BYTES) -> list[Path]:
    """The file itself when under ``limit``, else numbered parts (and the original removed)."""
    if path.stat().st_size <= limit:
        return [path]
    parts = []
    with path.open("rb") as handle:
        index = 0
        while chunk := handle.read(limit):
            part = path.with_name(f"{path.name}.part-{index:02d}")
            part.write_bytes(chunk)
            parts.append(part)
            index += 1
    path.unlink()
    return parts


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise HarvestError(f"git {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def backup(config: HarvestConfig, *, tgz: Path, name: str, message: str, dumps: Path) -> str:
    """Bundle, its .sha256 and a fresh mlflow dump into the backup repo; one commit; push.

    The bundle keeps its name behind a ``wo5-sweep-`` prefix, beside a ``.sha256``
    rewritten to that name so ``shasum -c`` works inside the backup repository.
    """
    repo = config.backup_repo
    if not (repo / ".git").exists():
        raise HarvestError(f"{repo} is not a git repository")
    dump = pg_dump(config, dumps / f"mlflow-post-{name}-{_stamp()}.sql.gz")
    if scan_for_credentials(dump):
        raise HarvestError(f"credential-shaped text in {dump.name}; nothing was backed up")
    files: list[Path] = []
    for source, target_name in ((tgz, f"wo5-sweep-{tgz.name}"), (dump, dump.name)):
        target = repo / target_name
        shutil.copyfile(source, target)
        sidecar = repo / f"{target_name}.sha256"
        sidecar.write_text(f"{file_sha256(target)}  {target_name}\n")
        files.append(sidecar)
        files.extend(split_for_backup(target))
    if any(path.stat().st_size > 100 * 1024 * 1024 for path in files):
        raise HarvestError("a backup file is over 100 MB after splitting")
    _git(repo, "add", *[str(path.relative_to(repo)) for path in files])
    if _git(repo, "status", "--porcelain"):
        _git(repo, "commit", "-q", "-m", message)
    if config.push and _git(repo, "remote"):
        _git(repo, "push", "-q")
    return _git(repo, "rev-parse", "HEAD")


def run_rescore(
    config: HarvestConfig, bundle_dir: Path, *, experiment: str, out: Path, kind: str
) -> dict[str, Any]:
    """Re-score in its own process (the 25M-row load stays out of this one)."""
    command = [
        config.python,
        "-m",
        "src.training.wo5_harvest",
        "rescore",
        str(bundle_dir),
        "--tracking-uri",
        config.tracking_uri,
        "--experiment",
        experiment,
        "--input-dir",
        str(config.input_dir),
        "--run-kind",
        kind,
        "--out",
        str(out),
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(config.repo_dir),
        "OMP_NUM_THREADS": "1",
        "KMP_DUPLICATE_LIB_OK": "TRUE",
    }
    log = out.with_suffix(".log")
    with log.open("ab") as handle:
        done = subprocess.run(
            command, cwd=config.repo_dir, env=env, stdout=handle, stderr=subprocess.STDOUT
        )
    if done.returncode != 0:
        raise HarvestError(f"re-score failed (exit {done.returncode}); see {log}")
    document: dict[str, Any] = json.loads(out.read_text())
    return document


def _experiment_name(bundle_dir: Path) -> str:
    import sqlite3

    con = sqlite3.connect(f"file:{bundle_dir / 'mlflow' / 'mlflow.db'}?mode=ro", uri=True)
    try:
        result = json.loads((bundle_dir / CHILD_RESULT).read_text())
        row = con.execute(
            "SELECT name FROM experiments WHERE experiment_id = ?", (int(result["experiment_id"]),)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise HarvestError("the bundle's store has no experiment for its run")
    return str(row[0])


def process_bundle(
    config: HarvestConfig,
    transport: Transport,
    state: HarvestState,
    entry: Mapping[str, str],
    *,
    summary: Callable[[str], None],
    sweep_ended: bool = False,
) -> None:
    """Take one INDEX entry as far as it will go; every stage is recorded when done.

    The acknowledgement is the one stage that needs the pod after the bundle is home.
    Once the sweep has ended the pod may already have stopped itself (its wait for
    the Mac is bounded), so after ``ACK_ATTEMPTS`` failed tries the ACK is recorded
    as unsent and the harvester can still finish.
    """
    seq, cell, run_id, tgz_name = entry["seq"], entry["cell"], entry["run_id"], entry["tgz"]
    record = state.entry(seq)
    record.update({k: entry[k] for k in ("cell", "run_id", "tgz", "sha256", "status")})
    stages: dict[str, Any] = record["stages"]
    bundles = config.dest / "bundles"
    local = bundles / tgz_name
    sha_local = bundles / f"{tgz_name}.sha256"
    workdir = config.dest / f"{seq}-{cell}"
    dumps = config.dest / "dumps"

    def done(stage: str, value: Any = True) -> None:
        stages[stage] = {"utc": _utc(), "value": value}
        state.save()
        logger.info("%s %s: %s", seq, cell, stage)

    if "fetched" not in stages:
        transport.fetch(f"out/{tgz_name}", local)
        transport.fetch(f"out/{tgz_name}.sha256", sha_local)
        done("fetched")
    if "verified" not in stages:
        actual = file_sha256(local)
        sidecar = sha_local.read_text().split()[0]
        if not actual == sidecar == entry["sha256"]:
            # Fetched again next poll: a partial or corrupted copy is not kept.
            local.unlink(missing_ok=True)
            stages.pop("fetched", None)
            state.save()
            raise HarvestError(
                f"{tgz_name}: sha256 {actual} vs .sha256 {sidecar} vs INDEX {entry['sha256']}"
            )
        if workdir.exists():
            shutil.rmtree(workdir)
        bundle_dir = unpack(local, workdir)
        check_manifest(bundle_dir)
        hits = scan_for_credentials(bundle_dir)
        if hits:
            raise HarvestError("credential-shaped text in the bundle: " + "; ".join(hits))
        record["bundle_dir"] = str(bundle_dir)
        done("verified", actual)
    bundle_dir = Path(record["bundle_dir"])
    has_run = run_id != "-" and (bundle_dir / "mlflow" / "mlflow.db").is_file()
    label = f"{config.source_label} {tgz_name} sha256={entry['sha256']}"
    if "imported" not in stages:
        if has_run:
            lines = import_run(config, bundle_dir, run_id, dumps=dumps, label=label)
            done("imported", lines)
        else:
            done("imported", "no MLflow run in this bundle")
    if "rescored" not in stages:
        has_model = (bundle_dir / "model").is_dir() and (bundle_dir / CHILD_RESULT).is_file()
        if has_run and has_model:
            waited = 0.0
            while (busy := other_training_processes()) and waited < config.wait_for_other_training:
                logger.info("waiting: another training process is running: %s", busy[0])
                time.sleep(30)
                waited += 30
            if busy:
                raise HarvestError(f"another training process is running: {busy[0]}")
            runner = json.loads((bundle_dir / RUNNER_RESULT).read_text())
            document = run_rescore(
                config,
                bundle_dir,
                experiment=_experiment_name(bundle_dir),
                out=workdir / "rescore.json",
                kind=str(json.loads((bundle_dir / CHILD_RESULT).read_text()).get("run_kind")),
            )
            record["rescore"] = {
                key: document.get(key)
                for key in ("rescore_run_id", "cpu", "pod", "difference", "d6")
            }
            record["role"] = runner.get("role")
            done("rescored", document.get("rescore_run_id"))
        else:
            done("rescored", "nothing to re-score (no saved model)")
    if "backed_up" not in stages:
        commit = backup(
            config,
            tgz=local,
            name=f"wo5-sweep-{seq}-{cell}",
            message=(
                f"WO-5 sweep {seq} {cell}: run {run_id} ({entry['status']}), its bundle and the "
                "mlflow dump after its import and CPU re-score"
            ),
            dumps=dumps,
        )
        done("backed_up", commit)
    if "acked" not in stages:
        line = f"{seq} {run_id} {entry['sha256']} OK {_utc()}"
        try:
            transport.append_line(f"out/{ACK_FILE}", line)
            done("acked", line)
        except (HarvestError, OSError, subprocess.SubprocessError) as failure:
            record["ack_failures"] = int(record.get("ack_failures", 0)) + 1
            state.save()
            if not (sweep_ended and record["ack_failures"] >= ACK_ATTEMPTS):
                raise
            done(
                "acked", f"UNSENT after {record['ack_failures']} tries (pod unreachable): {failure}"
            )
        rescore = record.get("rescore") or {}
        cpu = (rescore.get("cpu") or {}).get("warm_recall_at_k_candidates")
        pod = (rescore.get("pod") or {}).get("warm_recall_at_k_candidates")
        relative = (rescore.get("difference") or {}).get("warm_recall_relative")
        summary(
            f"{_utc()} {seq} {cell} run={run_id} status={entry['status']} "
            f"pod_warm={pod} mac_cpu_warm={cpu} rel_diff={relative} "
            f"d6={(rescore.get('d6') or {}).get('verdict', '-')} "
            f"rescore_run={rescore.get('rescore_run_id')} "
            f"backup={stages['backed_up']['value']} ack=ok"
        )


def poll_once(config: HarvestConfig, transport: Transport, state: HarvestState) -> bool:
    """One pass over the INDEX. True once the sweep has ended and everything is home."""
    try:
        index = transport.read_text("out/INDEX") or ""
        state.index_text = index
        state.save()
    except (HarvestError, OSError, subprocess.SubprocessError):
        # A pod that has stopped itself after the sweep ended: carry on from the
        # INDEX already read; anything not yet fetched still needs the pod.
        if not index_terminal(state.index_text):
            raise
        logger.warning("the pod is unreachable; using the INDEX read last, which has ended")
        index = state.index_text
    entries = parse_index(index)
    terminal = index_terminal(index)
    summary_path = config.dest / SUMMARY_LOG

    def summary(line: str) -> None:
        with summary_path.open("a") as handle:
            handle.write(line + "\n")
        logger.info("SUMMARY %s", line)

    error = None
    for entry in entries:
        try:
            process_bundle(
                config, transport, state, entry, summary=summary, sweep_ended=terminal is not None
            )
        except Exception as failure:  # noqa: BLE001 -- recorded, retried next poll
            error = f"{entry['seq']} {entry['cell']}: {type(failure).__name__}: {failure}"
            state.entry(entry["seq"])["last_error"] = {"utc": _utc(), "error": error}
            state.save()
            logger.error("%s", error)
            break
    acked = all("acked" in state.entry(e["seq"])["stages"] for e in entries)
    finished = terminal is not None and acked
    if finished and not state.finished:
        state.finished = True
        state.save()
        summary(f"{_utc()} FINISHED: {terminal}; {len(entries)} bundle(s) home and acknowledged")
    write_status(config, state, entries, terminal, error)
    return finished


def write_status(
    config: HarvestConfig,
    state: HarvestState,
    entries: Sequence[Mapping[str, str]],
    terminal: str | None,
    error: str | None,
) -> None:
    document = {
        "last_poll_utc": _utc(),
        "pid": os.getpid(),
        "terminal": terminal,
        "finished": state.finished,
        "last_error": error,
        "bundles": [
            {
                "seq": e["seq"],
                "cell": e["cell"],
                "run_id": e["run_id"],
                "status": e["status"],
                "stages_done": [s for s in STAGES if s in state.entry(e["seq"])["stages"]],
            }
            for e in entries
        ],
    }
    path = config.dest / STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(path.name + ".tmp").write_text(json.dumps(document, indent=2) + "\n")
    path.with_name(path.name + ".tmp").replace(path)


def run_loop(config: HarvestConfig, transport: Transport) -> int:
    config.dest.mkdir(parents=True, exist_ok=True)
    state = HarvestState.load(config.dest / STATE_FILE)
    polls = 0
    while True:
        polls += 1
        try:
            finished = poll_once(config, transport, state)
        except Exception as failure:  # noqa: BLE001 -- e.g. the pod is unreachable; retry
            logger.error("poll failed: %s: %s", type(failure).__name__, failure)
            write_status(config, state, [], None, f"poll failed: {failure}")
            finished = False
        if finished:
            label = os.environ.get(LAUNCHD_ENV_VAR, "").strip()
            if label:
                logger.info("sweep finished: removing my own LaunchAgent %s", label)
                with (config.dest / SUMMARY_LOG).open("a") as handle:
                    handle.write(f"{_utc()} UNINSTALL: LaunchAgent {label} removed by itself\n")
                uninstall_agent(label)  # does not return when launchd stops this process
            return 0
        if config.max_polls is not None and polls >= config.max_polls:
            return 1
        time.sleep(config.interval)


# --- the CPU re-score (its own process) ------------------------------------------------------


def rescore(
    bundle_dir: Path,
    *,
    tracking_uri: str,
    experiment: str,
    input_dir: Path,
    run_kind: str,
    calibration: CalibrationCheck | None = None,
) -> dict[str, Any]:
    """Score a bundle's saved model on this machine's CPU exactly as run_once scored it."""
    import mlflow
    import pandas as pd

    from src.config import Settings
    from src.evaluation.protocol import (
        COLD_START_THRESHOLD,
        K_CANDIDATES,
        PER_USER_RECALL_ARTIFACT,
        evaluate,
        per_user_recall_document,
    )
    from src.models.candidates.popularity import PopularityModel
    from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, load_sasrec
    from src.models.candidates.sequence_data import build_user_history
    from src.training.candidate_data import load_inputs
    from src.training.sasrec import (
        MODEL_TYPE,
        _configuration_id,
        evaluation_frames,
        retrieval_diagnostics,
    )
    from synthetic.cold_start import harness as synth_cold

    started = time.perf_counter()
    result = json.loads((bundle_dir / CHILD_RESULT).read_text())
    runner = json.loads((bundle_dir / RUNNER_RESULT).read_text())
    gpu_run_id = str(result["run_id"])
    sample_fraction = float(result["sample_fraction"])
    archive = bundle_dir / "model" / "sasrec-model.zip"
    if file_sha256(archive) != result["model"]["archive_sha256"]:
        raise HarvestError("the model archive is not the one the pod recorded")
    ratings, _movies = load_inputs(Settings(), input_dir=input_dir)
    assert isinstance(ratings, pd.DataFrame)
    split, train_frame, cohort = evaluation_frames(ratings, sample_fraction)
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(COLD_START_THRESHOLD),
        stage="retrieval",
        k=K_CANDIDATES,
    )
    if protocol.semantic_hash != result["protocol"]["semantic_hash"]:
        raise HarvestError(
            f"protocol {protocol.semantic_hash} here != {result['protocol']['semantic_hash']} "
            "on the pod: not the same evaluation (is the cohort parquet present?)"
        )
    # load_sasrec checks the archive against its manifest and keeps the encoder on the CPU.
    model = load_sasrec(bundle_dir / "model" / MANIFEST_FILENAME)
    # What fit() leaves behind and an archive does not carry, rebuilt from the same frame.
    model._popularity = PopularityModel().fit(train_frame)
    model._user_history = build_user_history(train_frame, model._item_to_index)
    # run_once scores with the exact torch index (EVALUATION_INDEX), not FAISS.
    model.build_exact_tensor_index()
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = split.train.groupby("userId").size().to_dict()
    user_ids = list(holdout)
    cohort_ids = list(cohort.user_ids) if cohort is not None else []
    recommendations = model.recommend_for_users(user_ids + cohort_ids, K_CANDIDATES)
    evaluation = evaluate(
        recommendations,
        holdout,
        train_counts,
        k=K_CANDIDATES,
        synthetic_cold_users=cohort.targets_by_bucket if cohort is not None else None,
        synthetic_cold_served_by=model.was_served_by_sasrec if cohort is not None else None,
    )
    diagnostics = retrieval_diagnostics(
        recommendations,
        {u: t for u, t in holdout.items() if model.was_served_by_sasrec(u)},
        split.train["movieId"].value_counts().to_dict(),
        catalog_size=len(model._index_to_item),
    )
    cpu = {
        "warm_recall_at_k_candidates": evaluation.warm.recall,
        "warm_ndcg_at_k_candidates": evaluation.warm.ndcg,
        "cold_recall_at_k_candidates": evaluation.cold.recall,
        "cold_ndcg_at_k_candidates": evaluation.cold.ndcg,
        "overall_recall_at_k_candidates": evaluation.overall.recall,
        "overall_ndcg_at_k_candidates": evaluation.overall.ndcg,
        "n_warm_users": float(evaluation.n_warm_users),
        "n_cold_users": float(evaluation.n_cold_users),
        **diagnostics,
    }
    if cohort is not None:
        cpu.update(synth_cold.metrics(evaluation, suffix=synth_cold.SUFFIX_AT_K_CANDIDATES))
    pod = {key: result["metrics"].get(key) for key in cpu}
    pod_warm = pod.get("warm_recall_at_k_candidates")
    difference = {
        "warm_recall_absolute": (
            None if pod_warm is None else cpu["warm_recall_at_k_candidates"] - pod_warm
        ),
        "warm_recall_relative": (
            None if not pod_warm else cpu["warm_recall_at_k_candidates"] / pod_warm - 1.0
        ),
        "cold_recall_absolute": (
            None
            if pod.get("cold_recall_at_k_candidates") is None
            else cpu["cold_recall_at_k_candidates"] - float(pod["cold_recall_at_k_candidates"])
        ),
    }
    d6: dict[str, Any] | None = None
    if runner.get("role") in ("calibration", "calibration-contingent"):
        check = calibration or load_plan().calibration
        value = cpu["warm_recall_at_k_candidates"]
        d6 = {
            "verdict": check.verdict(value),
            "value": value,
            "target": check.target,
            "relative": value / check.target - 1.0,
            "band": list(check.band),
            "pod_verdict": (runner.get("calibration") or {}).get("verdict"),
        }
    config_params = dict(result["config"])
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    gpu_name = str(result.get("run_name") or "sasrec")
    with mlflow.start_run(run_name=f"{gpu_name}-cpu-rescore") as run:
        envelope = protocol_manifest.run_envelope(
            protocol, deterministic=False, seed=int(result["seed"])
        )
        tags = {
            **envelope.tags,
            "model_family": "candidate_generator",
            "model_type": MODEL_TYPE,
            "stage": "candidate",
            "rescored_from": gpu_run_id,
            "rescore_of_record": "true",
            "device": "cpu",
            "scored_on": "mac-cpu",
            "trained_on": str(config_params.get("device")),
            "run_kind": run_kind,
            "wo5_step": str(result.get("label")),
            "wo5_role": str(runner.get("role")),
            "sasrec_artifact_sha256": str(result["model"]["archive_sha256"]),
            "sasrec_weights_sha256": str(result["model"].get("weights_sha256")),
        }
        if d6 is not None:
            tags["d6_calibration"] = str(d6["verdict"])
        mlflow.set_tags(tags)
        mlflow.log_params(
            {
                **envelope.params,
                **{f"config_{k}": v for k, v in config_params.items()},
                "user_sample_fraction": sample_fraction,
                "rescored_from_run": gpu_run_id,
                "evaluation_index": "torch-exact-inner-product-v1",
            }
        )
        metrics: dict[str, float] = {name: float(number) for name, number in cpu.items()}
        for name, delta in difference.items():
            if delta is not None:
                metrics[f"mac_cpu_minus_pod_{name}"] = float(delta)
        mlflow.log_metrics(metrics)
        configuration = _configuration_id(model.config, sample_fraction=sample_fraction)
        mlflow.log_dict(
            per_user_recall_document(
                evaluation,
                run_id=run.info.run_id,
                model_type=MODEL_TYPE,
                seed=int(result["seed"]),
                configuration_id=configuration,
                protocol=protocol.to_dict(),
            ),
            PER_USER_RECALL_ARTIFACT,
        )
        document = {
            "kind": "wo5-cpu-rescore",
            "rescore_run_id": run.info.run_id,
            "rescored_from": gpu_run_id,
            "protocol_hash": protocol.semantic_hash,
            "archive_sha256": result["model"]["archive_sha256"],
            "cpu": cpu,
            "pod": pod,
            "difference": difference,
            "d6": d6,
            "seconds": round(time.perf_counter() - started, 1),
            "utc": _utc(),
        }
        mlflow.log_dict(document, "rescore.json")
    return document


# --- launchd ---------------------------------------------------------------------------------


def plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def agent_plist(label: str, run_args: Sequence[str], dest: Path, repo: Path) -> dict[str, Any]:
    """KeepAlive and RunAtLoad, under caffeinate -i so idle sleep does not stop it."""
    path_dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/Docker.app/Contents/Resources/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return {
        "Label": label,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-i",
            "/bin/bash",
            str(repo / "infra" / "gpu" / "harvest.sh"),
            "run",
            *run_args,
        ],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {
            "PATH": ":".join(path_dirs),
            "HOME": str(Path.home()),
            LAUNCHD_ENV_VAR: label,
            "PYTHON": sys.executable,
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "ProcessType": "Standard",
        "StandardOutPath": str(dest / HARVEST_LOG),
        "StandardErrorPath": str(dest / HARVEST_LOG),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install_agent(label: str, run_args: Sequence[str], dest: Path, repo: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{label}")
    with path.open("wb") as handle:
        plistlib.dump(agent_plist(label, run_args, dest, repo), handle)
    done = _launchctl("bootstrap", domain, str(path))
    if done.returncode != 0:
        raise HarvestError(f"launchctl bootstrap failed: {done.stderr.strip()}")
    return path


def uninstall_agent(label: str) -> None:
    """Remove the plist, then unload the job (which stops it, the caller included)."""
    plist_path(label).unlink(missing_ok=True)
    _launchctl("bootout", f"gui/{os.getuid()}/{label}")


def agent_status(label: str, dest: Path) -> str:
    lines = []
    loaded = _launchctl("print", f"gui/{os.getuid()}/{label}")
    if loaded.returncode == 0:
        wanted = ("state", "pid", "last exit code", "runs")
        lines += [
            f"launchd: {line.strip()}"
            for line in loaded.stdout.splitlines()
            if line.strip().split(" = ")[0] in wanted
        ]
    else:
        lines.append(f"launchd: {label} is not loaded")
    lines.append(
        f"plist: {plist_path(label)} {'exists' if plist_path(label).exists() else 'absent'}"
    )
    status = dest / STATUS_FILE
    if status.is_file():
        document = json.loads(status.read_text())
        lines.append(
            f"last poll: {document.get('last_poll_utc')}  finished: {document.get('finished')}"
        )
        if document.get("last_error"):
            lines.append(f"last error: {document['last_error']}")
        for bundle in document.get("bundles", []):
            lines.append(
                f"  {bundle['seq']} {bundle['cell']:<18} {bundle['status']:<22} "
                f"{','.join(bundle['stages_done'])}"
            )
    summary = dest / SUMMARY_LOG
    if summary.is_file():
        lines += ["summary:", *summary.read_text().splitlines()[-10:]]
    return "\n".join(lines)


# --- command line ---------------------------------------------------------------------------


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--host", help="the pod's public IP (SSH over exposed TCP)")
    where.add_argument("--local-dir", type=Path, help="rehearsal: a sweep root on this machine")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh" / "id_ed25519")
    parser.add_argument("--user", default="root")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--ssh-bin", default="ssh")
    parser.add_argument("--scp-bin", default="scp")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--tracking-uri", default="http://localhost:5001")
    parser.add_argument("--backup-repo", type=Path, default=Path.home() / "movielens-backups")
    parser.add_argument("--no-push", action="store_true", help="commit to the backup repo only")
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between polls")
    parser.add_argument("--input-dir", type=Path, default=REPO_ROOT / "data" / "raw" / "ml-25m")
    parser.add_argument("--repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--pg-container", default="movielens-coldstart-postgres-1")
    parser.add_argument("--source-label", default="wo5-sweep pod")
    parser.add_argument("--max-polls", type=int)


def _config(args: argparse.Namespace) -> tuple[HarvestConfig, Transport]:
    from src.config import Settings

    config = HarvestConfig(
        dest=args.dest.expanduser().resolve(),
        tracking_uri=args.tracking_uri,
        backup_repo=args.backup_repo.expanduser().resolve(),
        push=not args.no_push,
        interval=args.interval,
        input_dir=args.input_dir.expanduser().resolve(),
        repo_dir=args.repo_dir.expanduser().resolve(),
        pg_container=args.pg_container,
        pg_user=Settings().postgres_user,
        source_label=args.source_label,
        max_polls=args.max_polls,
    )
    transport: Transport
    if args.local_dir is not None:
        transport = LocalTransport(args.local_dir.expanduser().resolve())
    else:
        transport = SshTransport(
            host=args.host,
            port=args.port,
            key=args.key.expanduser(),
            user=args.user,
            remote_root=args.remote_root,
            ssh_bin=args.ssh_bin,
            scp_bin=args.scp_bin,
        )
    return config, transport


def _run_args(argv: Sequence[str]) -> list[str]:
    """The arguments after the subcommand, with --label dropped (it names the agent)."""
    out: list[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--label":
            skip = True
            continue
        if item.startswith("--label="):
            continue
        out.append(item)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("run", "poll until the sweep ends (what the LaunchAgent runs)"),
        ("once", "one poll, then exit"),
        ("install", "install and start the LaunchAgent with these arguments"),
    ):
        sub = commands.add_parser(name, help=text)
        _add_run_args(sub)
        sub.add_argument("--label", default=DEFAULT_LABEL)
    remove = commands.add_parser("uninstall", help="stop and remove the LaunchAgent")
    remove.add_argument("--label", default=DEFAULT_LABEL)
    show = commands.add_parser("status", help="the LaunchAgent, the last poll, each bundle")
    show.add_argument("--label", default=DEFAULT_LABEL)
    show.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    score = commands.add_parser("rescore", help="re-score one unpacked bundle on this CPU")
    score.add_argument("bundle_dir", type=Path)
    score.add_argument("--tracking-uri", default="http://localhost:5001")
    score.add_argument("--experiment", required=True)
    score.add_argument("--input-dir", type=Path, required=True)
    score.add_argument("--run-kind", default="rented-gpu")
    score.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(raw)

    if args.command == "uninstall":
        uninstall_agent(args.label)
        print(f"removed {plist_path(args.label)} and unloaded {args.label}")
        return 0
    if args.command == "status":
        print(agent_status(args.label, args.dest.expanduser().resolve()))
        return 0
    if args.command == "rescore":
        document = rescore(
            args.bundle_dir,
            tracking_uri=args.tracking_uri,
            experiment=args.experiment,
            input_dir=args.input_dir,
            run_kind=args.run_kind,
        )
        args.out.write_text(json.dumps(document, indent=2, default=str) + "\n")
        print(json.dumps({k: document[k] for k in ("rescore_run_id", "difference", "d6")}))
        return 0
    config, transport = _config(args)
    if args.command == "install":
        path = install_agent(args.label, _run_args(raw[1:]), config.dest, config.repo_dir)
        print(f"installed {path}; logs in {config.dest / HARVEST_LOG}")
        return 0
    if args.command == "once":
        config.dest.mkdir(parents=True, exist_ok=True)
        finished = poll_once(config, transport, HarvestState.load(config.dest / STATE_FILE))
        return 0 if finished else 1
    return run_loop(config, transport)


if __name__ == "__main__":
    raise SystemExit(main())
