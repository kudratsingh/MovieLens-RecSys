"""The pod-side shell pieces of WO-5 step two: self-stop, the Mac's ACK wait, the watchdog.

RunPod is never called: runpodctl and curl are stand-ins that record what they were
asked and answer as the real ones would.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

GPU = Path("infra/gpu")
KEY = "rpa_TESTKEYTESTKEYTESTKEYTESTKEY0123456789"
POD = "nqx5q11n02d9hz"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _stop_env(tmp_path: Path, *, ctl: str, curl: str, key: str = KEY) -> dict[str, str]:
    environ = tmp_path / "environ"
    environ.write_bytes(f"PATH=/usr/bin\0RUNPOD_API_KEY={key}\0RUNPOD_POD_ID={POD}\0".encode())
    calls = tmp_path / "calls"
    runpodctl = _executable(tmp_path / "runpodctl", f'echo "runpodctl $*" >>{calls}\n{ctl}')
    fake_curl = _executable(tmp_path / "curl", f'echo "curl $*" >>{calls}\n{curl}')
    return {
        **os.environ,
        "WO5_SELF_STOP_ENVIRON": str(environ),
        "WO5_RUNPODCTL": str(runpodctl),
        "WO5_CURL": str(fake_curl),
        "WO5_SELF_STOP_LOG": str(tmp_path / "self-stop.log"),
        "WO5_SELF_STOP_MARKER": str(tmp_path / "SELF_STOPPED"),
    }


def _self_stop(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GPU / "self_stop.sh"), *args], env=env, capture_output=True, text=True
    )


def test_simulate_calls_nothing_and_leaves_a_marker(tmp_path: Path) -> None:
    env = _stop_env(tmp_path, ctl="exit 0", curl="exit 0")
    done = _self_stop(env, "--simulate")
    assert done.returncode == 0 and "SIMULATED" in done.stdout
    assert not (tmp_path / "calls").exists()
    assert "SIMULATED STOP of pod" in (tmp_path / "SELF_STOPPED").read_text()


def test_probe_reports_which_ways_authenticate_and_stops_nothing(tmp_path: Path) -> None:
    ctl = 'case "$1 $2" in "pod get") echo \'{"id":"x","desiredStatus":"RUNNING"}\';; ' "esac"
    curl = 'case "$*" in *query*) echo \'{"data":{"pod":{"desiredStatus":"RUNNING"}}}\';; ' "esac"
    done = _self_stop(_stop_env(tmp_path, ctl=ctl, curl=curl), "--probe")
    assert done.returncode == 0
    assert "PROBE PASS" in done.stdout and " A" in done.stdout and " C" in done.stdout
    calls = (tmp_path / "calls").read_text()
    assert "pod stop" not in calls and "podStop" not in calls
    assert KEY not in done.stdout + done.stderr + (tmp_path / "self-stop.log").read_text()
    assert KEY not in calls  # the key travels in the environment and a header file


def test_stop_falls_through_to_graphql_when_runpodctl_cannot(tmp_path: Path) -> None:
    ctl = 'echo \'{"error":"forbidden","code":"forbidden"}\' >&2; exit 1'
    curl = (
        'case "$*" in *podStop*) echo \'{"data":{"podStop":{"id":"x",'
        '"desiredStatus":"EXITED"}}}\';; *) echo 403;; esac'
    )
    done = _self_stop(_stop_env(tmp_path, ctl=ctl, curl=curl))
    assert done.returncode == 0 and "STOPPED via C" in done.stdout
    calls = (tmp_path / "calls").read_text()
    assert "pod stop" in calls and "stop pod" in calls and "podStop" in calls
    assert "terminate" not in calls.lower() and "remove" not in calls


def test_every_method_failing_exits_non_zero(tmp_path: Path) -> None:
    done = _self_stop(_stop_env(tmp_path, ctl="exit 1", curl='echo "403"'))
    assert done.returncode == 1 and "FAILED" in done.stdout


def test_a_missing_key_is_reported_not_guessed(tmp_path: Path) -> None:
    env = _stop_env(tmp_path, ctl="exit 0", curl="exit 0", key="")
    env.pop("RUNPOD_API_KEY", None)
    done = _self_stop(env)
    assert done.returncode == 2 and "key=MISSING" in done.stdout


# --- the wait for the Mac ---------------------------------------------------------------------


def _common(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    prelude = f"STARTED_EPOCH=$(date +%s); . {GPU / 'gpu_common.sh'}\n"
    return subprocess.run(
        ["bash", "-c", prelude + script],
        capture_output=True,
        text=True,
        env={**os.environ, "WO5_ACK_POLL_SECONDS": "1", **(env or {})},
    )


def _sweep_root(tmp_path: Path, acked: int) -> Path:
    out = tmp_path / "root" / "out"
    out.mkdir(parents=True)
    digests = ["a" * 64, "b" * 64]
    (out / "INDEX").write_text(
        f"01 cal r1 01-cal-r1.tgz {digests[0]} FINISHED-CAL-PASS\n"
        f"02 sweeplog - 02-sweeplog-norun.tgz {digests[1]} LOGS\n"
        "DONE every planned step finished clean\n"
    )
    acks = [f"01 r1 {digests[0]} OK 2026-10-11T00:00:00Z", f"02 - {digests[1]} OK x"][:acked]
    if acks:
        (out / "HARVESTED").write_text("\n".join(acks) + "\n")
    return tmp_path / "root"


def test_the_wait_ends_as_soon_as_every_bundle_is_acknowledged(tmp_path: Path) -> None:
    root = _sweep_root(tmp_path, acked=2)
    done = _common(f"wo5_wait_for_acks {root} 600 $(( $(date +%s) + 3600 ))")
    assert done.returncode == 0 and "acknowledged" in done.stdout


def test_without_an_ack_the_wait_gives_up_after_its_limit(tmp_path: Path) -> None:
    root = _sweep_root(tmp_path, acked=1)
    started = time.monotonic()
    done = _common(f"wo5_wait_for_acks {root} 2 $(( $(date +%s) + 3600 ))")
    assert done.returncode == 1 and "no acknowledgement after 2 s for seq 02" in done.stdout
    assert time.monotonic() - started < 15


def test_the_hard_limit_overrides_the_wait(tmp_path: Path) -> None:
    root = _sweep_root(tmp_path, acked=0)
    done = _common(f"wo5_wait_for_acks {root} 1800 $(date +%s)")
    assert done.returncode == 1 and "hard limit reached" in done.stdout


# --- the watchdog -------------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["deadline", "sweep-died"])
def test_the_watchdog_stops_the_pod_on_its_own(tmp_path: Path, case: str) -> None:
    root = _sweep_root(tmp_path, acked=2)
    (root / "out" / "INDEX").write_text(
        (root / "out" / "INDEX").read_text().replace("DONE every planned step finished clean\n", "")
    )
    dead = subprocess.run(["bash", "-c", "echo $$"], capture_output=True, text=True).stdout
    (root / "sweep.pid").write_text(dead)
    deadline = int(time.time()) - 1 if case == "deadline" else int(time.time()) + 3600
    (root / "DEADLINE").write_text(f"{deadline}\n")
    env = {
        **os.environ,
        "WO5_WATCHDOG_INTERVAL": "1",
        "WO5_ACK_POLL_SECONDS": "1",
        "WO5_ACK_WAIT_SECONDS": "5",
        "WO5_SELF_STOP_ARGS": "--simulate",
        "WO5_SELF_STOP_LOG": str(root / "self-stop.log"),
        "WO5_SELF_STOP_MARKER": str(root / "SELF_STOPPED"),
    }
    done = subprocess.run(
        ["bash", str(GPU / "watchdog.sh"), str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (root / "SELF_STOPPED").is_file()
    index = (root / "out" / "INDEX").read_text()
    reason = "hard limit reached" if case == "deadline" else "sweep.sh died"
    assert f"HALT {reason}" in index
    assert (root / "WATCHDOG_HEARTBEAT").is_file() and (root / "watchdog.pid").is_file()
