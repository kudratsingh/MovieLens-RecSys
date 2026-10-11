"""WO-5 step two's sweep runner: the plan, the order, the limits, HALT, the bundles."""

from __future__ import annotations

import json
import sqlite3
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.training import wo5_sweep
from src.training.sasrec_sweep import parse_grid
from src.training.wo5_sweep import (
    FINISHED,
    MISS,
    PASS,
    CalibrationCheck,
    Outcome,
    Overrides,
    Step,
    Terminal,
    load_plan,
    next_action,
    outcomes_from_index,
    parse_index,
)

V1_PROTOCOL = "sha256:b4ed5afa0a6a798a17bcb5dc9a2b8fe4aa8f66b2bc316d3609c8d15244b0fb28"
CELLS_DIR = Path("docs/experiments/sasrec")


# --- the committed plan and cells ----------------------------------------------------------


def test_the_committed_plan_runs_calibration_then_the_cells_cheapest_first() -> None:
    plan = load_plan()
    assert [step.name for step in plan.steps] == [
        "calibration-v1-s42",
        "calibration-v1-s7",
        "cell-0b",
        "cell-A",
        "cell-E",
        "cell-B",
        "cell-C",
        "cell-D",
    ]
    hours = {step.name: round(step.projection.projected_hours, 3) for step in plan.steps}
    assert hours == {
        "calibration-v1-s42": 1.208,
        "calibration-v1-s7": 1.208,
        "cell-0b": 2.645,
        "cell-A": 3.375,
        "cell-E": 3.7,
        "cell-B": 4.98,
        "cell-C": 6.55,
        "cell-D": 7.25,
    }
    for step in plan.steps:
        assert step.projection.kill_hours == pytest.approx(2 * step.projection.projected_hours)
        assert step.expected_protocol_hash == V1_PROTOCOL
    assert plan.calibration.target == 0.5091713455
    assert plan.calibration.relative_tolerance == 0.01
    assert plan.hard_limit_hours == 42
    assert plan.rate_usd_per_hour_max == 0.89
    total = sum(s.projection.projected_hours for s in plan.steps if s.name != "calibration-v1-s7")
    assert total < plan.hard_limit_hours


def _cell(name: str) -> dict[str, Any]:
    spec = json.loads((CELLS_DIR / name).read_text())
    cells = spec["cells"]
    assert len(cells) == 1
    return dict(cells[0])


def test_the_calibration_is_v1s_exact_cell_on_cuda() -> None:
    v1 = _cell("full.json")
    for name, seed in (
        ("wo5-calibration-v1-cuda.json", 42),
        ("wo5-calibration-v1-cuda-seed2.json", 7),
    ):
        cell = _cell(name)
        _, configs = parse_grid(json.loads((CELLS_DIR / name).read_text()))
        config = configs[0][1]
        for key in ("loss", "negative_count", "epochs", "faiss_exact"):
            assert cell[key] == v1[key]
        assert config.seed == seed
        assert config.device == "cuda"
        assert (config.hidden_dim, config.num_blocks, config.num_heads) == (64, 2, 2)
        assert (config.max_sequence_length, config.feedforward_dim) == (50, 256)
        assert config.training_objective == "strict-prefix-final-position-v1"
        assert config.early_stopping is False
        assert (config.batch_size, config.learning_rate, config.dropout) == (512, 1e-3, 0.2)


@pytest.mark.parametrize(
    ("cell", "shape", "loss"),
    [
        ("0b", (64, 2, 2, 50), "sampled-softmax"),
        ("A", (128, 2, 4, 100), "sampled-softmax"),
        ("E", (128, 2, 4, 100), "full-softmax"),
        ("B", (256, 2, 8, 100), "sampled-softmax"),
        ("C", (128, 4, 4, 100), "sampled-softmax"),
        ("D", (128, 2, 4, 200), "sampled-softmax"),
    ],
)
def test_each_cell_is_its_adr_0020_cell(cell: str, shape: tuple[int, ...], loss: str) -> None:
    spec = json.loads((CELLS_DIR / f"wo5-cell-{cell}-cuda.json").read_text())
    fraction, configs = parse_grid(spec)
    config = configs[0][1]
    assert fraction == 1.0
    assert (
        config.hidden_dim,
        config.num_blocks,
        config.num_heads,
        config.max_sequence_length,
    ) == shape
    assert config.feedforward_dim == 4 * config.hidden_dim
    assert config.loss == loss
    assert config.negative_count == 1024
    assert (config.dropout, config.learning_rate, config.seed) == (0.2, 1e-3, 42)
    assert config.early_stopping is True and config.epochs == 5
    assert config.early_stopping_min_epochs == 3
    assert config.device == "cuda"
    assert config.training_objective == "strict-prefix-final-position-v1"
    assert spec["expected"]["cold_recall_at_k_candidates"] == 0.526272952


# --- what runs next ------------------------------------------------------------------------


def _plan(tmp_path: Path, cells: int = 2, hours: tuple[float, ...] = ()) -> wo5_sweep.SweepPlan:
    """A small plan whose cells files are tiny copies of the committed shapes."""
    steps = [
        {"name": "cal", "role": "calibration", "cells_file": "cal.json"},
        {"name": "cal2", "role": "calibration-contingent", "cells_file": "cal2.json"},
    ]
    files = {"cal.json": 0.01, "cal2.json": 0.01}
    for index in range(cells):
        steps.append({"name": f"cell-{index}", "role": "cell", "cells_file": f"c{index}.json"})
        files[f"c{index}.json"] = hours[index] if hours else 0.01 * (index + 1)
    for name, per_pass in files.items():
        projected = per_pass * 1 + 0.0
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "expected_protocol_hash": "sha256:x",
                    "projection": {
                        "hours_per_pass": per_pass,
                        "passes_ceiling": 1,
                        "overhead_hours": 0.0,
                        "projected_hours": projected,
                        "kill_hours": 2 * projected,
                    },
                    "sample_fraction": 1.0,
                    "cells": [{"label": name, "epochs": 1, "loss": "bce", "negative_count": 4}],
                }
            )
        )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "hard_limit_hours": 42,
                "rate_usd_per_hour_max": 0.89,
                "experiment": "test",
                "calibration": {
                    "metric": "warm_recall_at_k_candidates",
                    "target": 0.5,
                    "relative_tolerance": 0.01,
                },
                "steps": steps,
            }
        )
    )
    return load_plan(plan_path)


def _done(name: str, verdict: str | None = None, status: str = FINISHED) -> Outcome:
    return Outcome(seq=0, step_name=name, status=status, verdict=verdict)


def test_a_passing_calibration_goes_straight_to_the_cells_in_order(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    ran: list[Outcome] = []
    names = []
    while not isinstance(action := next_action(plan, ran), Terminal):
        names.append(action.name)
        ran.append(_done(action.name, PASS if action.role.startswith("calibration") else None))
    assert names == ["cal", "cell-0", "cell-1"]
    assert action.kind == "DONE"


def test_one_miss_runs_the_contingent_seed_and_a_pass_continues(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = next_action(plan, [_done("cal", MISS)])
    assert isinstance(first, Step) and first.name == "cal2"
    after = next_action(plan, [_done("cal", MISS), _done("cal2", PASS)])
    assert isinstance(after, Step) and after.name == "cell-0"


def test_a_second_miss_stops_the_sweep(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    action = next_action(plan, [_done("cal", MISS), _done("cal2", MISS)])
    assert isinstance(action, Terminal) and action.kind == "HALT"
    assert "second calibration miss" in action.reason


@pytest.mark.parametrize("status", ["FAILED", "KILLED", "HARD-LIMIT", "PROTOCOL-MISMATCH"])
def test_any_run_that_did_not_finish_clean_halts(tmp_path: Path, status: str) -> None:
    plan = _plan(tmp_path)
    action = next_action(plan, [_done("cal", PASS), _done("cell-0", status=status)])
    assert isinstance(action, Terminal) and action.kind == "HALT"
    assert status in action.reason


def test_a_failed_check_halts_even_when_the_run_finished(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    outcome = _done("cell-0")
    outcome.problems = ["cold recall 0.5 != 0.526 (a defect)"]
    action = next_action(plan, [_done("cal", PASS), outcome])
    assert isinstance(action, Terminal) and "cold recall" in action.reason


def test_a_plan_whose_cells_are_not_cheapest_first_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cheapest"):
        _plan(tmp_path, hours=(0.5, 0.2))


def test_the_calibration_band_is_one_percent_relative() -> None:
    check = CalibrationCheck("warm", 0.5091713455, 0.01)
    low, high = check.band
    assert low == pytest.approx(0.504079632045) and high == pytest.approx(0.514263058955)
    assert check.verdict(0.5091713455) == PASS
    assert check.verdict(low + 1e-9) == PASS and check.verdict(high - 1e-9) == PASS
    assert check.verdict(low - 1e-6) == MISS and check.verdict(high + 1e-6) == MISS
    assert check.verdict(None) == MISS


def test_index_lines_round_trip_and_the_terminal_line_is_not_a_bundle(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    digest = "a" * 64
    lines = [
        Outcome(1, "cal", FINISHED, "r1", 0.4, MISS, bundle="01-cal-r1.tgz", sha256=digest),
        Outcome(2, "cal2", FINISHED, "r2", 0.5, PASS, bundle="02-cal2-r2.tgz", sha256=digest),
        Outcome(3, "cell-0", "KILLED", None, bundle="03-0-norun.tgz", sha256=digest),
    ]
    text = "\n".join(o.index_line(wo5_sweep.step_label(o.step_name)) for o in lines)
    text += "\nHALT cell-0 ended KILLED in six words\n"
    assert [e["status"] for e in parse_index(text)] == [
        "FINISHED-CAL-MISS",
        "FINISHED-CAL-PASS",
        "KILLED",
    ]
    assert wo5_sweep.index_terminal(text) == "HALT cell-0 ended KILLED in six words"
    resumed = outcomes_from_index(plan, text)
    assert [(o.step_name, o.status, o.verdict, o.run_id) for o in resumed] == [
        ("cal", FINISHED, MISS, "r1"),
        ("cal2", FINISHED, PASS, "r2"),
        ("cell-0", "KILLED", None, None),
    ]


def test_rehearsal_overrides_shrink_the_cell_and_say_so() -> None:
    _, configs = parse_grid(json.loads((CELLS_DIR / "wo5-cell-D-cuda.json").read_text()))
    overrides = Overrides(sample_fraction=0.01, device="cpu", epochs=1, tiny=True)
    config = overrides.apply(configs[0][1])
    assert overrides.active
    assert (config.device, config.epochs, config.early_stopping) == ("cpu", 1, False)
    assert (config.hidden_dim, config.max_sequence_length, config.loss) == (
        16,
        20,
        "sampled-softmax",
    )
    assert not Overrides().active


# --- the runner, with a fake training process -------------------------------------------------

FAKE_CHILD = """
import json, os, sys, time
cells, run_dir = sys.argv[1], sys.argv[2]
name = os.path.basename(cells).split(".")[0]
behaviour = json.loads(os.environ["FAKE_BEHAVIOUR"]).get(name, {})
time.sleep(behaviour.get("sleep", 0))
if behaviour.get("crash"):
    sys.exit(1)
os.makedirs(run_dir, exist_ok=True)
with open(os.path.join(run_dir, "result.json"), "w") as handle:
    json.dump({"status": "FINISHED", "run_id": None,
               "metrics": {"warm_recall_at_k_candidates": behaviour.get("warm", 0.5)}}, handle)
print("trained", name)
"""


def _run(
    tmp_path: Path,
    behaviour: dict[str, Any],
    *,
    deadline_hours: float = 1.0,
    scale: float = 1.0,
    stop_file: bool = False,
) -> tuple[Terminal, str]:
    plan = _plan(tmp_path)
    child = tmp_path / "fake_child.py"
    child.write_text(FAKE_CHILD)
    root = tmp_path / "root"
    if stop_file:
        (root / "out").mkdir(parents=True)
        (root / "out" / "STOP").write_text("")

    def command(step: Step, run_dir: Path) -> list[str]:
        return [sys.executable, str(child), str(step.cells_file), str(run_dir)]

    import os

    os.environ["FAKE_BEHAVIOUR"] = json.dumps(behaviour)
    terminal = wo5_sweep.run_sweep(
        plan,
        root=root,
        deadline_epoch=time.time() + deadline_hours * 3600,
        child_command=command,
        overrides=Overrides(hours_scale=scale),
        experiment="test",
    )
    index = (root / "out" / "INDEX").read_text() if (root / "out" / "INDEX").exists() else ""
    return terminal, index


def test_the_runner_runs_the_plan_bundles_every_run_and_ends_done(tmp_path: Path) -> None:
    terminal, index = _run(tmp_path, {"cal": {"warm": 0.5}})
    assert terminal.kind == "DONE"
    entries = parse_index(index)
    assert [(e["seq"], e["cell"], e["status"]) for e in entries] == [
        ("01", "cal", "FINISHED-CAL-PASS"),
        ("02", "0", "FINISHED"),
        ("03", "1", "FINISHED"),
    ]
    out = tmp_path / "root" / "out"
    assert (out / "RESULT").read_text().startswith("DONE")
    for entry in entries:
        tgz = out / entry["tgz"]
        assert wo5_sweep.file_sha256(tgz) == entry["sha256"]
        assert (out / f"{entry['tgz']}.sha256").read_text().split()[0] == entry["sha256"]
        with tarfile.open(tgz) as archive:
            names = archive.getnames()
        top = entry["tgz"].removesuffix(".tgz")
        for member in ("MANIFEST.sha256", "runner.json", "result.json", "logs/console.log"):
            assert f"{top}/{member}" in names


def test_a_calibration_miss_then_a_second_miss_halts_with_a_marker(tmp_path: Path) -> None:
    terminal, index = _run(tmp_path, {"cal": {"warm": 0.3}, "cal2": {"warm": 0.3}})
    assert terminal.kind == "HALT" and "second calibration miss" in terminal.reason
    assert [e["status"] for e in parse_index(index)] == ["FINISHED-CAL-MISS", "FINISHED-CAL-MISS"]
    assert "second calibration miss" in (tmp_path / "root" / "out" / "HALT").read_text()


def test_a_run_past_twice_its_projection_is_killed_and_the_sweep_halts(tmp_path: Path) -> None:
    # cell-0's projection is 0.02 h; at scale 0.01 that is 0.72 s, killed past 1.44 s.
    terminal, index = _run(tmp_path, {"c0": {"sleep": 30}}, scale=0.01)
    statuses = [(e["cell"], e["status"]) for e in parse_index(index)]
    assert statuses[-1] == ("0", "KILLED")
    assert terminal.kind == "HALT" and "KILLED" in terminal.reason
    assert len(statuses) == 2


def test_a_crashed_run_is_bundled_failed_and_halts(tmp_path: Path) -> None:
    terminal, index = _run(tmp_path, {"c0": {"crash": True}})
    assert parse_index(index)[-1]["status"] == "FAILED"
    assert terminal.kind == "HALT"


def test_the_hard_limit_stops_before_a_step_that_cannot_fit(tmp_path: Path) -> None:
    # 0.005 h left; the calibration alone is projected at 0.01 h.
    terminal, index = _run(tmp_path, {}, deadline_hours=0.005)
    assert terminal.kind == "HALT" and "hard limit" in terminal.reason
    assert parse_index(index) == []


def test_a_stop_file_halts_before_the_next_step(tmp_path: Path) -> None:
    terminal, _index = _run(tmp_path, {}, stop_file=True)
    assert terminal.kind == "HALT" and "STOP" in terminal.reason


# --- the store snapshot and a real run ----------------------------------------------------


def _ratings(n_users: int = 80, n_items: int = 120, per_user: int = 25) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for user in range(1, n_users + 1):
        movies = rng.choice(np.arange(1, n_items + 1), size=per_user, replace=False)
        times = np.sort(rng.integers(1_400_000_000, 1_400_000_000 + 400 * 86_400, per_user))
        rows.extend((user, int(m), 4.0, int(t)) for m, t in zip(movies, times, strict=True))
    return pd.DataFrame(rows, columns=["userId", "movieId", "rating", "timestamp"])


TINY_CELL = {
    "label": "tiny",
    "max_sequence_length": 10,
    "hidden_dim": 16,
    "num_blocks": 1,
    "num_heads": 2,
    "feedforward_dim": 32,
    "loss": "sampled-softmax",
    "negative_count": 16,
    "batch_size": 64,
    "epochs": 2,
    "faiss_exact": True,
    "seed": 42,
}


def train_tiny(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One real run_once through train_cell into a SQLite store, in an empty cwd."""
    monkeypatch.chdir(tmp_path)
    cells = tmp_path / "tiny.json"
    cells.write_text(json.dumps({"expected_protocol_hash": "sha256:x", "cells": [TINY_CELL]}))
    return wo5_sweep.train_cell(
        cells,
        run_dir=tmp_path / "work" / "01-tiny",
        store=tmp_path / "mlstore",
        experiment="wo5-test",
        overrides=Overrides(device="cpu"),
        input_dir=None,
        run_kind="local-smoke",
        ratings=_ratings(),
    )


def test_train_cell_runs_run_once_and_records_everything_a_run_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = train_tiny(tmp_path, monkeypatch)
    saved = json.loads((tmp_path / "work" / "01-tiny" / "result.json").read_text())
    assert saved["status"] == FINISHED and saved["run_id"] == result["run_id"]
    assert saved["protocol"]["checked"] is False  # a rehearsal override
    for key in ("git", "device", "software", "window_utc", "config", "model", "params", "tags"):
        assert key in saved
    assert len(saved["per_pass"]["train_loss"]) == 2
    assert len(saved["per_pass"]["epoch_warm_recall_at_k_candidates"]) == 2
    assert saved["model"]["archive_sha256"] == saved["model"]["manifest_model_sha256"]
    assert saved["tags"]["wo5_step"] == "tiny" and saved["tags"]["rescore_of_record"] == "false"
    assert "warm_recall_at_k_candidates" in saved["metrics"]
    assert (tmp_path / "work" / "01-tiny" / "model-export" / result["run_id"]).is_dir()


def test_train_cell_refuses_data_that_gives_another_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cells = tmp_path / "tiny.json"
    cells.write_text(json.dumps({"expected_protocol_hash": "sha256:not-it", "cells": [TINY_CELL]}))
    with pytest.raises(wo5_sweep.ProtocolMismatchError):
        wo5_sweep.train_cell(
            cells,
            run_dir=tmp_path / "run",
            store=tmp_path / "mlstore",
            experiment="wo5-test",
            overrides=Overrides(),
            input_dir=None,
            run_kind="rented-gpu",
            ratings=_ratings(),
        )
    saved = json.loads((tmp_path / "run" / "result.json").read_text())
    assert saved["status"] == "PROTOCOL-MISMATCH"
    assert saved["protocol"]["matches_expected"] is False


def test_the_bundle_holds_one_run_of_the_store_and_its_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = train_tiny(tmp_path, monkeypatch)
    # A second run in the same store, which the first run's bundle must not carry.
    second = wo5_sweep.train_cell(
        tmp_path / "tiny.json",
        run_dir=tmp_path / "work" / "02-tiny",
        store=tmp_path / "mlstore",
        experiment="wo5-test",
        overrides=Overrides(device="cpu"),
        input_dir=None,
        run_kind="local-smoke",
        ratings=_ratings(),
    )
    (tmp_path / "plan").mkdir()
    plan = _plan(tmp_path / "plan")
    step = plan.steps[2]
    outcome = Outcome(1, step.name, FINISHED, first["run_id"])
    name, digest = wo5_sweep.package(
        root=tmp_path,
        seq=1,
        step=step,
        outcome=outcome,
        run_dir=tmp_path / "work" / "01-tiny",
        runner={"status": FINISHED},
        tracking_uri=wo5_sweep.store_uri(tmp_path / "mlstore"),
    )
    tgz = tmp_path / "out" / name
    assert wo5_sweep.file_sha256(tgz) == digest
    extract = tmp_path / "extract"
    with tarfile.open(tgz) as archive:
        archive.extractall(extract, filter="data")
    bundle = extract / name.removesuffix(".tgz")
    con = sqlite3.connect(bundle / "mlflow" / "mlflow.db")
    runs = {row[0] for row in con.execute("SELECT run_uuid FROM runs")}
    metric_runs = {row[0] for row in con.execute("SELECT DISTINCT run_uuid FROM metrics")}
    con.close()
    assert runs == {first["run_id"]} == metric_runs
    assert second["run_id"] not in runs
    store = json.loads((bundle / "mlflow" / "STORE.json").read_text())
    artifacts = bundle / store["artifact_root_in_bundle"] / first["run_id"] / "artifacts"
    assert (artifacts / "per_user_recall.json").is_file()
    assert (bundle / "model" / "sasrec-model.zip").is_file()
    manifest = (bundle / "MANIFEST.sha256").read_text().splitlines()
    for line in manifest:
        sha, _, rel = line.partition("  ")
        assert wo5_sweep.file_sha256(bundle / rel.removeprefix("./")) == sha
