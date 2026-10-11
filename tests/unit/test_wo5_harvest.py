"""WO-5 step two's Mac-side harvester: transport, verification, stages, ACK, re-score, launchd."""

from __future__ import annotations

import gzip
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.training import wo5_harvest, wo5_sweep
from src.training.wo5_harvest import (
    HarvestConfig,
    HarvestError,
    HarvestState,
    LocalTransport,
    SshTransport,
)
from src.training.wo5_sweep import FINISHED, Outcome, Overrides
from tests.unit.test_wo5_sweep import TINY_CELL, _plan, _ratings, train_tiny

# --- checks on what comes home ---------------------------------------------------------------


def test_the_credential_scan_finds_a_runpod_key_in_text_and_in_a_gzip(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "log.txt").write_text("Epoch 1 loss=4.2 the unknown token is index 0\n")
    assert wo5_harvest.scan_for_credentials(clean) == []
    leaky = tmp_path / "leaky"
    leaky.mkdir()
    (leaky / "env.txt").write_text("RUNPOD_API_KEY=rpa_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n")
    assert wo5_harvest.scan_for_credentials(leaky)
    dump = tmp_path / "dump.sql.gz"
    dump.write_bytes(gzip.compress(b"COPY tags ... password=hunter2hunter2\n"))
    assert wo5_harvest.scan_for_credentials(dump)


def _bundle(tmp_path: Path, name: str = "01-cal-norun") -> tuple[Path, str]:
    staging = tmp_path / "staging" / name
    (staging / "logs").mkdir(parents=True)
    (staging / "logs" / "console.log").write_text("trained\n")
    (staging / "result.json").write_text("{}\n")
    wo5_sweep.write_manifest(staging)
    tgz = tmp_path / f"{name}.tgz"
    with tarfile.open(tgz, "w:gz") as archive:
        archive.add(staging, arcname=name)
    return tgz, wo5_sweep.file_sha256(tgz)


def test_unpack_and_the_manifest_catch_a_changed_file(tmp_path: Path) -> None:
    tgz, _ = _bundle(tmp_path)
    bundle = wo5_harvest.unpack(tgz, tmp_path / "home")
    wo5_harvest.check_manifest(bundle)
    (bundle / "logs" / "console.log").write_text("edited\n")
    with pytest.raises(HarvestError, match="MANIFEST"):
        wo5_harvest.check_manifest(bundle)


def test_a_large_backup_file_is_split_under_the_limit(tmp_path: Path) -> None:
    big = tmp_path / "big.tgz"
    big.write_bytes(os.urandom(2500))
    parts = wo5_harvest.split_for_backup(big, limit=1000)
    assert [p.name for p in parts] == ["big.tgz.part-00", "big.tgz.part-01", "big.tgz.part-02"]
    assert not big.exists() and all(p.stat().st_size <= 1000 for p in parts)
    small = tmp_path / "small"
    small.write_bytes(b"x")
    assert wo5_harvest.split_for_backup(small, limit=1000) == [small]


# --- the transport ---------------------------------------------------------------------------


def _fake_ssh_tools(tmp_path: Path) -> tuple[str, str]:
    """ssh and scp stand-ins that run the 'remote' side on this machine, as the rehearsal does."""
    ssh = tmp_path / "fake-ssh"
    ssh.write_text('#!/usr/bin/env bash\nfor last; do :; done\nexec bash -c "$last"\n')
    scp = tmp_path / "fake-scp"
    scp.write_text(
        "#!/usr/bin/env bash\n"
        'args=("$@"); n=${#args[@]}; src="${args[$((n-2))]}"; dst="${args[$((n-1))]}"\n'
        'exec cp "${src#*:}" "$dst"\n'
    )
    for tool in (ssh, scp):
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return str(ssh), str(scp)


@pytest.mark.parametrize("kind", ["ssh", "local"])
def test_both_transports_read_fetch_and_append(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "pod"
    (root / "out").mkdir(parents=True)
    (root / "out" / "INDEX").write_text("01 cal r a b c\n")
    (root / "out" / "x.tgz").write_bytes(b"bytes")
    transport: Any
    if kind == "ssh":
        ssh, scp = _fake_ssh_tools(tmp_path)
        transport = SshTransport(
            "pod", 22, tmp_path / "key", remote_root=str(root), ssh_bin=ssh, scp_bin=scp
        )
    else:
        transport = LocalTransport(root)
    assert transport.read_text("out/INDEX") == "01 cal r a b c\n"
    assert transport.read_text("out/MISSING") is None
    transport.fetch("out/x.tgz", tmp_path / "home" / "x.tgz")
    assert (tmp_path / "home" / "x.tgz").read_bytes() == b"bytes"
    transport.append_line("out/HARVESTED", "01 r sha OK 2026-10-11T00:00:00Z")
    transport.append_line("out/HARVESTED", "02 r sha OK 2026-10-11T00:00:01Z")
    assert (root / "out" / "HARVESTED").read_text().splitlines()[1].startswith("02 r sha OK")


# --- the stages, with the shared store and the backup stubbed --------------------------


def _pod_with_bundles(tmp_path: Path, n: int, terminal: str | None) -> Path:
    root = tmp_path / "pod"
    out = root / "out"
    out.mkdir(parents=True)
    lines = []
    for seq in range(1, n + 1):
        name = f"{seq:02d}-step{seq}-norun"
        tgz, digest = _bundle(tmp_path / f"b{seq}", name)
        (out / tgz.name).write_bytes(tgz.read_bytes())
        (out / f"{tgz.name}.sha256").write_text(f"{digest}  {tgz.name}\n")
        lines.append(f"{seq:02d} step{seq} - {tgz.name} {digest} FINISHED")
    if terminal:
        lines.append(terminal)
    (out / "INDEX").write_text("\n".join(lines) + "\n")
    return root


def _stub_backup(calls: list[str]) -> Any:
    def backup(config: HarvestConfig, *, tgz: Path, name: str, message: str, dumps: Path) -> str:
        calls.append(name)
        return f"commit-{len(calls)}"

    return backup


def test_every_bundle_goes_through_each_stage_once_and_is_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pod_with_bundles(tmp_path, 2, "DONE every planned step finished clean")
    calls: list[str] = []
    monkeypatch.setattr(wo5_harvest, "backup", _stub_backup(calls))
    config = HarvestConfig(dest=tmp_path / "home", interval=0)
    state = HarvestState.load(config.dest / wo5_harvest.STATE_FILE)
    assert wo5_harvest.poll_once(config, LocalTransport(root), state) is True
    assert calls == ["wo5-sweep-01-step1", "wo5-sweep-02-step2"]
    acks = (root / "out" / "HARVESTED").read_text().splitlines()
    index = wo5_sweep.parse_index((root / "out" / "INDEX").read_text())
    for ack, entry in zip(acks, index, strict=True):
        assert ack.startswith(f"{entry['seq']} - {entry['sha256']} OK ")
    status = json.loads((config.dest / wo5_harvest.STATUS_FILE).read_text())
    assert status["finished"] and status["bundles"][0]["stages_done"] == list(wo5_harvest.STAGES)
    summary = (config.dest / wo5_harvest.SUMMARY_LOG).read_text()
    assert "01 step1 run=- status=FINISHED" in summary and "FINISHED: DONE" in summary
    # A second pass does nothing again: every stage is recorded.
    assert wo5_harvest.poll_once(config, LocalTransport(root), HarvestState.load(state.path))
    assert calls == ["wo5-sweep-01-step1", "wo5-sweep-02-step2"]
    assert len((root / "out" / "HARVESTED").read_text().splitlines()) == 2


def test_the_harvester_is_not_finished_before_the_index_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pod_with_bundles(tmp_path, 1, None)
    monkeypatch.setattr(wo5_harvest, "backup", _stub_backup([]))
    config = HarvestConfig(dest=tmp_path / "home")
    state = HarvestState.load(config.dest / wo5_harvest.STATE_FILE)
    assert wo5_harvest.poll_once(config, LocalTransport(root), state) is False
    assert (root / "out" / "HARVESTED").exists()


def test_a_bundle_whose_digest_does_not_match_is_refetched_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pod_with_bundles(tmp_path, 1, "HALT x")
    index = (root / "out" / "INDEX").read_text().split()
    (root / "out" / index[3]).write_bytes(b"corrupted in transit")
    monkeypatch.setattr(wo5_harvest, "backup", _stub_backup([]))
    config = HarvestConfig(dest=tmp_path / "home")
    state = HarvestState.load(config.dest / wo5_harvest.STATE_FILE)
    assert wo5_harvest.poll_once(config, LocalTransport(root), state) is False
    assert "sha256" in state.entry("01")["last_error"]["error"]
    assert "fetched" not in state.entry("01")["stages"]
    assert not (root / "out" / "HARVESTED").exists()


def test_a_finished_launchd_harvester_removes_its_own_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pod_with_bundles(tmp_path, 1, "DONE ok")
    monkeypatch.setattr(wo5_harvest, "backup", _stub_backup([]))
    removed: list[str] = []
    monkeypatch.setattr(wo5_harvest, "uninstall_agent", removed.append)
    monkeypatch.setenv(wo5_harvest.LAUNCHD_ENV_VAR, "io.movielens.wo5-harvest-test")
    config = HarvestConfig(dest=tmp_path / "home", interval=0)
    assert wo5_harvest.run_loop(config, LocalTransport(root)) == 0
    assert removed == ["io.movielens.wo5-harvest-test"]
    assert "UNINSTALL" in (config.dest / wo5_harvest.SUMMARY_LOG).read_text()


def test_the_launch_agent_keeps_alive_under_caffeinate_and_names_itself(tmp_path: Path) -> None:
    plist = wo5_harvest.agent_plist(
        "io.movielens.wo5-harvest", ["--host", "1.2.3.4", "--port", "40022"], tmp_path, tmp_path
    )
    assert plist["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-i"]
    assert plist["ProgramArguments"][-5:] == ["run", "--host", "1.2.3.4", "--port", "40022"]
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["EnvironmentVariables"][wo5_harvest.LAUNCHD_ENV_VAR] == "io.movielens.wo5-harvest"
    assert plist["StandardOutPath"].endswith("harvest.log")
    assert wo5_harvest._run_args(["--host", "h", "--label", "x", "--port", "1"]) == [
        "--host",
        "h",
        "--port",
        "1",
    ]


# --- the CPU re-score reproduces run_once's own scoring ---------------------------------


def test_the_cpu_rescore_reproduces_the_runs_numbers_from_its_saved_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = train_tiny(tmp_path, monkeypatch)
    (tmp_path / "plan").mkdir()
    plan = _plan(tmp_path / "plan")
    step = plan.steps[0]  # the calibration, so the D6 check runs too
    name, _digest = wo5_sweep.package(
        root=tmp_path,
        seq=1,
        step=step,
        outcome=Outcome(1, step.name, FINISHED, result["run_id"]),
        run_dir=tmp_path / "work" / "01-tiny",
        runner={"role": "calibration", "calibration": {"verdict": "MISS"}},
        tracking_uri=wo5_sweep.store_uri(tmp_path / "mlstore"),
    )
    bundle = wo5_harvest.unpack(tmp_path / "out" / name, tmp_path / "home")
    data = tmp_path / "csv"
    data.mkdir()
    _ratings().to_csv(data / "ratings.csv", index=False)
    pd.DataFrame({"movieId": [1], "title": ["x"], "genres": ["Drama"]}).to_csv(
        data / "movies.csv", index=False
    )
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    document = wo5_harvest.rescore(
        bundle,
        tracking_uri=(tmp_path / "shared").as_uri(),
        experiment="wo5-test",
        input_dir=data,
        run_kind="local-smoke",
        calibration=plan.calibration,
    )
    for key in ("warm_recall_at_k_candidates", "cold_recall_at_k_candidates", "n_warm_users"):
        assert document["cpu"][key] == pytest.approx(document["pod"][key], abs=0, rel=0)
    assert document["difference"]["warm_recall_absolute"] == 0.0
    assert document["d6"]["verdict"] in ("PASS", "MISS")
    assert document["d6"]["pod_verdict"] == "MISS"
    import mlflow

    mlflow.set_tracking_uri((tmp_path / "shared").as_uri())
    run = mlflow.get_run(document["rescore_run_id"])
    assert run.data.tags["rescored_from"] == result["run_id"]
    assert run.data.tags["rescore_of_record"] == "true" and run.data.tags["device"] == "cpu"
    assert TINY_CELL["label"] == run.data.tags["wo5_step"]


def test_the_rescore_refuses_a_model_that_is_not_the_one_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = train_tiny(tmp_path, monkeypatch)
    (tmp_path / "plan").mkdir()
    plan = _plan(tmp_path / "plan")
    name, _ = wo5_sweep.package(
        root=tmp_path,
        seq=1,
        step=plan.steps[2],
        outcome=Outcome(1, plan.steps[2].name, FINISHED, result["run_id"]),
        run_dir=tmp_path / "work" / "01-tiny",
        runner={"role": "cell"},
        tracking_uri=wo5_sweep.store_uri(tmp_path / "mlstore"),
    )
    bundle = wo5_harvest.unpack(tmp_path / "out" / name, tmp_path / "home")
    with (bundle / "model" / "sasrec-model.zip").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(HarvestError, match="not the one the pod recorded"):
        wo5_harvest.rescore(
            bundle,
            tracking_uri=(tmp_path / "shared").as_uri(),
            experiment="wo5-test",
            input_dir=tmp_path,
            run_kind="local-smoke",
        )


def test_overrides_are_never_active_on_the_committed_cells() -> None:
    # The pod runs the committed cells with no overrides, so every check applies.
    assert not Overrides().active


class _StoppedPod(LocalTransport):
    """The pod after it stopped itself: the INDEX was read once, then nothing answers."""

    reads = 0

    def read_text(self, relative: str) -> str | None:
        type(self).reads += 1
        if type(self).reads > 1:
            raise HarvestError("ssh: connect to host: Connection refused")
        return super().read_text(relative)

    def append_line(self, relative: str, line: str) -> None:
        raise HarvestError("ssh: connect to host: Connection refused")


def test_a_pod_that_stopped_after_the_sweep_ended_does_not_hold_the_harvester(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pod_with_bundles(tmp_path, 1, "DONE every planned step finished clean")
    monkeypatch.setattr(wo5_harvest, "backup", _stub_backup([]))
    config = HarvestConfig(dest=tmp_path / "home")
    state = HarvestState.load(config.dest / wo5_harvest.STATE_FILE)
    _StoppedPod.reads = 0
    transport = _StoppedPod(root)
    results = [wo5_harvest.poll_once(config, transport, state) for _ in range(3)]
    assert results == [False, False, True]
    acked = state.entry("01")["stages"]["acked"]["value"]
    assert acked.startswith("UNSENT after 3 tries")


def test_before_the_sweep_ends_an_unreachable_pod_is_an_error_to_retry(tmp_path: Path) -> None:
    root = _pod_with_bundles(tmp_path, 1, None)
    config = HarvestConfig(dest=tmp_path / "home")
    state = HarvestState.load(config.dest / wo5_harvest.STATE_FILE)
    _StoppedPod.reads = 1  # every read fails
    with pytest.raises(HarvestError):
        wo5_harvest.poll_once(config, _StoppedPod(root), state)
