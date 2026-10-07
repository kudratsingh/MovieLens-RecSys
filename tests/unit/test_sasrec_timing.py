"""WO-5 step one: the time-bounded step timing, its output, and the session record.

The timing replaces the WO-4 throwaway script, so the tests pin both of its
bounds (a step count, and seconds of steady stepping that exclude setup and
warm-up), the document a rented GPU will send back, the three cells files the
pod runs, the dataset checks the pod makes before any of it, and the MLflow
record the Mac writes afterwards.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import pytest
from mlflow.tracking import MlflowClient

from src.models.candidates.sasrec import LEGACY_TRAINING_OBJECTIVE, SASRecConfig
from src.training import sasrec_timing as timing
from src.training.sasrec_sweep import parse_grid
from src.training.sasrec_timing import (
    StepClock,
    TimingBudget,
    TimingCheckError,
    prepare_inputs,
    published_md5,
    record_session,
    run_cells,
    step_statistics,
    steps_per_pass,
    summarize_session,
    time_cell,
    verify_inputs,
)

SASREC_DIR = Path(__file__).resolve().parents[2] / "docs" / "experiments" / "sasrec"
CELLS_FILES = {
    "A": "wo5-gpu-timing-cellA-600s.json",
    "0b": "wo5-gpu-timing-cell0b-180s.json",
    "B": "wo5-gpu-timing-cellB-180s.json",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# --- the budget -----------------------------------------------------------------


def test_a_budget_is_steps_or_seconds_never_both_and_never_without_warmup() -> None:
    TimingBudget(timing_seconds=600).validate()
    TimingBudget(steps=2000).validate()
    for bad in (
        TimingBudget(),
        TimingBudget(timing_seconds=600, steps=2000),
        TimingBudget(timing_seconds=600, warmup_steps=0),
        TimingBudget(timing_seconds=0),
        TimingBudget(steps=20, warmup_steps=20),
    ):
        with pytest.raises(ValueError):
            bad.validate()


def test_the_wo4_step_count_form_is_still_honoured() -> None:
    spec = json.loads((SASREC_DIR / "wo5-prep-cellA-step-timing-full.json").read_text())

    budget = TimingBudget.from_spec(spec)

    assert (budget.mode, budget.steps, budget.warmup_steps) == ("steps", 2000, 20)
    assert budget.timing_seconds is None


def test_a_command_line_bound_replaces_the_files_bound_of_either_kind() -> None:
    from_file = TimingBudget(timing_seconds=600)

    assert from_file.overridden(steps=50).as_dict() == {
        "mode": "steps",
        "timing_seconds": None,
        "steps": 50,
        "warmup_steps_excluded": 20,
    }
    assert TimingBudget(steps=2000).overridden(timing_seconds=20, warmup_steps=5) == TimingBudget(
        timing_seconds=20, warmup_steps=5
    )
    with pytest.raises(ValueError):
        from_file.overridden(timing_seconds=1, steps=50)


# --- the clock ------------------------------------------------------------------


def test_seconds_count_steady_stepping_only_not_setup_or_warmup() -> None:
    clock = FakeClock()
    stepper = StepClock(TimingBudget(timing_seconds=2.0, warmup_steps=3), clock=clock)
    stepper.start()
    clock.now = 100.5  # the first step ends after 100 s of setup
    spent = [stepper.tick()]
    while not spent[-1]:
        clock.now += 0.5
        spent.append(stepper.tick())

    # Warm-up ends at the third stamp (101.5); 2 s of steady steps is four more.
    assert len(spent) == 7
    assert spent.count(True) == 1
    assert stepper.step_seconds[0] == pytest.approx(100.5)
    assert stepper.steady_seconds == pytest.approx([0.5] * 4)
    assert stepper.steady_started_at == pytest.approx(101.5)


def test_step_mode_stops_at_the_count() -> None:
    clock = FakeClock()
    stepper = StepClock(TimingBudget(steps=5, warmup_steps=2), clock=clock)
    stepper.start()
    results = []
    for _ in range(5):
        clock.now += 1.0
        results.append(stepper.tick())

    assert results == [False, False, False, False, True]
    assert len(stepper.steady_seconds) == 3


def test_a_clock_that_was_not_started_refuses() -> None:
    with pytest.raises(RuntimeError):
        StepClock(TimingBudget(steps=5)).tick()


def test_step_statistics_report_what_the_wo4_timing_reported() -> None:
    steady = [1.0, 2.0, 3.0, 4.0]

    stats = step_statistics(steady)

    assert stats["mean"] == 2.5 and stats["median"] == 2.5
    assert stats["first_half_mean"] == 1.5 and stats["second_half_mean"] == 3.5
    assert stats["p10"] is not None and stats["p90"] is not None
    assert stats["p10"] <= stats["median"] <= stats["p90"]
    assert step_statistics([])["mean"] is None
    assert step_statistics([0.25])["p90"] == 0.25


def test_a_full_pass_of_the_cells_is_38554_steps() -> None:
    # 19,739,546 strict-prefix examples at 512 a step (wo5-cell-repricing.md).
    assert steps_per_pass(19_739_546, 512) == 38_554
    assert steps_per_pass(1024, 512) == 2
    assert steps_per_pass(1025, 512) == 3


# --- one timed cell -------------------------------------------------------------


def _ratings(n_users: int = 120, n_items: int = 300, per_user: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for user in range(1, n_users + 1):
        movies = rng.choice(np.arange(1, n_items + 1), size=per_user, replace=False)
        times = np.sort(rng.integers(1_400_000_000, 1_400_000_000 + 400 * 86_400, per_user))
        rows.extend((user, int(m), 4.0, int(t)) for m, t in zip(movies, times))
    return pd.DataFrame(rows, columns=["userId", "movieId", "rating", "timestamp"])


def _tiny_config(**changes: Any) -> SASRecConfig:
    base = SASRecConfig(
        max_sequence_length=10,
        hidden_dim=16,
        num_blocks=1,
        num_heads=2,
        feedforward_dim=32,
        loss="sampled-softmax",
        negative_count=16,
        batch_size=16,
        epochs=50,
        seed=42,
        training_objective=LEGACY_TRAINING_OBJECTIVE,
    )
    return dataclasses.replace(base, **changes)


# What the WO-5 brief asks every timing JSON to carry, by path into the document.
REQUIRED_PATHS = (
    ("device", "name"),
    ("device", "type"),
    ("software", "torch"),
    ("software", "torch_cuda"),
    ("software", "cudnn"),
    ("steps", "completed"),
    ("steps", "warmup_excluded"),
    ("step_seconds_steady", "mean"),
    ("step_seconds_steady", "median"),
    ("step_seconds_steady", "p10"),
    ("step_seconds_steady", "p90"),
    ("seconds", "split"),
    ("seconds", "build_user_history_seconds"),
    ("seconds", "build_strict_prefix_example_store_seconds"),
    ("seconds", "fit_setup_before_first_step"),
    ("memory", "peak_rss_bytes"),
    ("memory", "device"),
    ("git", "commit"),
    ("protocol", "semantic_hash"),
    ("data", "raw_data_revision"),
    ("window_utc", "start"),
    ("window_utc", "end"),
    ("extrapolation", "steps_per_pass"),
    ("extrapolation", "seconds_per_pass"),
    ("result_status",),
)


def _get(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for key in path:
        assert key in value, f"missing {'.'.join(path)}"
        value = value[key]
    return value


def test_a_timed_cell_stops_on_seconds_and_reports_the_schema() -> None:
    result = time_cell(
        _ratings(),
        _tiny_config(),
        TimingBudget(timing_seconds=0.3, warmup_steps=2),
    )
    document = result.document

    assert result.failed_checks == []
    assert document["stopped_by"] == "seconds"
    assert document["result_status"] == timing.RESULT_STATUS
    for path in REQUIRED_PATHS:
        _get(document, path)
    assert document["steps"]["steady"] == document["steps"]["completed"] - 2 > 0
    assert sum(document["step_seconds_all"][2:]) >= 0.3
    assert len(document["step_seconds_all"]) == document["steps"]["completed"]
    n_targets = document["training_examples"]["n_targets"]
    assert document["extrapolation"]["steps_per_pass"] == -(-n_targets // 16)
    assert document["extrapolation"]["seconds_per_pass"] == pytest.approx(
        document["step_seconds_steady"]["mean"] * document["extrapolation"]["steps_per_pass"]
    )
    assert document["device"]["type"] == "cpu"
    assert (
        document["partition"]["latest_fit_timestamp"]
        < document["partition"]["sealed_boundary_timestamp"]
    )
    json.dumps(document)  # every value serializes


def test_a_timed_cell_stops_on_steps_too() -> None:
    result = time_cell(_ratings(), _tiny_config(), TimingBudget(steps=6, warmup_steps=2))

    assert result.document["stopped_by"] == "steps"
    assert result.document["steps"]["completed"] == 6


def test_a_pinned_protocol_hash_or_step_count_that_does_not_match_fails_the_cell() -> None:
    result = time_cell(
        _ratings(),
        _tiny_config(),
        TimingBudget(steps=4, warmup_steps=1),
        expected_protocol_hash="sha256:" + "0" * 64,
        expected_steps_per_pass=38_554,
    )

    assert set(result.failed_checks) == {
        "protocol_hash_matches_expected",
        "steps_per_pass_matches_expected",
    }


def test_the_builders_are_put_back_after_a_timing() -> None:
    import src.models.candidates.sasrec as sasrec

    before = sasrec.build_strict_prefix_example_store
    time_cell(_ratings(), _tiny_config(), TimingBudget(steps=3, warmup_steps=1))

    assert sasrec.build_strict_prefix_example_store is before


# --- the cells files a pod runs -------------------------------------------------


def _cells(name: str) -> tuple[dict[str, Any], float, SASRecConfig]:
    spec = json.loads((SASREC_DIR / name).read_text())
    fraction, cells = parse_grid(spec)
    assert len(cells) == 1
    return spec, fraction, cells[0][1]


@pytest.mark.parametrize("cell", sorted(CELLS_FILES))
def test_each_gpu_timing_file_is_its_adr_0020_cell_on_cuda(cell: str) -> None:
    spec, fraction, config = _cells(CELLS_FILES[cell])
    budget = TimingBudget.from_spec(spec)

    assert fraction == 1.0
    assert spec["result_status"] == timing.RESULT_STATUS
    assert spec["timing"]["cell"] == cell
    assert spec["timing"]["expected_steps_per_pass"] == 38_554
    assert budget.mode == "seconds" and budget.warmup_steps == 20
    assert budget.timing_seconds == (600 if cell == "A" else 180)
    assert config.device == "cuda"
    assert config.loss == "sampled-softmax" and config.negative_count == 1024
    assert config.training_objective == LEGACY_TRAINING_OBJECTIVE
    assert config.seed == 42 and not config.early_stopping
    assert (config.batch_size, config.dropout, config.learning_rate) == (512, 0.2, 0.001)
    assert config.feedforward_dim == 4 * config.hidden_dim
    for reference in spec["timing"]["references"].values():
        assert reference["step_seconds"] > 0 and reference["basis"] and reference["source"]


def test_the_three_files_differ_from_cell_a_only_where_adr_0020_says() -> None:
    _, _, a = _cells(CELLS_FILES["A"])
    _, _, b = _cells(CELLS_FILES["B"])
    _, _, zero_b = _cells(CELLS_FILES["0b"])
    hashes = {_cells(name)[0]["timing"]["expected_protocol_hash"] for name in CELLS_FILES.values()}

    assert (a.hidden_dim, a.num_blocks, a.max_sequence_length, a.num_heads) == (128, 2, 100, 4)
    assert dataclasses.replace(b, hidden_dim=128, num_heads=4, feedforward_dim=512) == a
    assert (zero_b.hidden_dim, zero_b.num_blocks, zero_b.max_sequence_length) == (64, 2, 50)
    assert zero_b.num_heads == 2
    assert len(hashes) == 1


# --- a session ------------------------------------------------------------------


def test_run_cells_writes_one_document_per_cell_and_names_an_override(tmp_path: Path) -> None:
    spec = {
        "sample_fraction": 1.0,
        "timing": {"cell": "T", "steps": 4, "warmup_steps_excluded": 1},
        "cells": [{"label": "tiny", **dataclasses.asdict(_tiny_config())}],
    }
    cells_path = tmp_path / "cells.json"
    cells_path.write_text(json.dumps(spec))

    as_written = run_cells(cells_path, tmp_path / "out", ratings=_ratings())
    overridden = run_cells(
        cells_path,
        tmp_path / "out",
        ratings=_ratings(),
        overrides=timing.Overrides(steps=3, warmup_steps=1),
        run_kind="local-smoke",
    )

    assert [path.name for path in as_written] == ["tiny.json"]
    assert [path.name for path in overridden] == ["tiny__local-smoke-cpu.json"]
    document = json.loads(overridden[0].read_text())
    assert document["overrides"] == {"steps": 3, "warmup_steps": 1}
    assert document["run_kind"] == "local-smoke"
    assert document["cells_file"]["sha256"] == hashlib.sha256(cells_path.read_bytes()).hexdigest()


def test_run_cells_writes_the_document_before_it_reports_a_failed_check(tmp_path: Path) -> None:
    spec = {
        "timing": {"steps": 3, "warmup_steps_excluded": 1, "expected_steps_per_pass": 1},
        "cells": [{"label": "tiny", **dataclasses.asdict(_tiny_config())}],
    }
    cells_path = tmp_path / "cells.json"
    cells_path.write_text(json.dumps(spec))

    with pytest.raises(TimingCheckError, match="steps_per_pass_matches_expected"):
        run_cells(cells_path, tmp_path / "out", ratings=_ratings())
    assert (tmp_path / "out" / "tiny.json").is_file()


def _session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    spec = {
        "timing": {
            "cell": "A",
            "steps": 4,
            "warmup_steps_excluded": 1,
            "references": {"cpu": {"step_seconds": 10.0, "basis": "test", "source": "test"}},
        },
        "cells": [{"label": "tiny", **dataclasses.asdict(_tiny_config())}],
    }
    cells_path = tmp_path / "cells.json"
    cells_path.write_text(json.dumps(spec))
    run_cells(cells_path, session / "results", ratings=_ratings())
    (session / "results" / "gpu-smoke.json").write_text(
        json.dumps({"kind": "gpu-smoke", "passed": True, "device": "cpu", "checks": []})
    )
    (session / "env").mkdir()
    (session / "env" / "bootstrap-facts.env").write_text(
        "# facts\nCOMMIT=abc\nRUNPOD_POD_ID=pod123\nSETUP_SECONDS=12.5\n"
    )
    return session


def test_a_session_summary_carries_each_cell_the_smoke_and_the_facts(tmp_path: Path) -> None:
    summary = summarize_session(_session(tmp_path))

    assert summary["passed"] is True
    assert summary["facts"]["RUNPOD_POD_ID"] == "pod123"
    assert summary["gpu_smoke"]["passed"] is True
    (cell,) = summary["cells"]
    assert cell["cell"] == "A" and cell["steps_completed"] == 4
    assert cell["speedups"]["cpu"] == pytest.approx(10.0 / cell["mean_step_seconds"])
    assert "gpu smoke: PASS" in timing.summary_table(summary)


def test_record_logs_one_tagged_timing_run_with_the_session_attached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    session = _session(tmp_path)
    tarball = tmp_path / "session.tgz"
    tarball.write_bytes(b"not really a tarball")
    tarball.with_name("session.tgz.sha256").write_text(
        hashlib.sha256(b"not really a tarball").hexdigest() + "  session.tgz\n"
    )
    uri = (tmp_path / "mlruns").as_uri()
    previous = mlflow.get_tracking_uri()
    try:
        outcome = record_session(
            session,
            tracking_uri=uri,
            experiment="phase-a-sasrec",
            provider="local",
            instance="test",
            rate_usd_per_hour="0",
            label="local-smoke",
            tarball=tarball,
        )
    finally:
        mlflow.set_tracking_uri(previous)

    run = MlflowClient(tracking_uri=uri).get_run(outcome["run_id"])
    assert run.data.tags["timing_only"] == "true"
    assert run.data.tags["not_a_result_of_record"] == "true"
    assert run.data.tags["run_kind"] == "local-smoke"
    assert run.data.tags["pod_id"] == "pod123"
    assert run.data.tags["model_type"] != "sasrec"
    assert "cellA_mean_step_seconds" in run.data.metrics
    assert "warm_recall_at_k_candidates" not in run.data.metrics
    assert "timing-session/results" in outcome["artifacts"]
    assert (session / "summary.json").is_file()


# --- the dataset checks the pod makes -------------------------------------------


def test_the_committed_input_manifest_pins_both_files_the_trainer_reads() -> None:
    manifest = timing.load_input_manifest()

    assert set(manifest["files"]) == {"ratings.csv", "movies.csv"}
    for entry in manifest["files"].values():
        assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) >= 0
    assert manifest["raw_data_revision"] == "md5:c3ce6309f6f0ec347a9e0a662c640021.dir"
    # GroupLens's published MD5 for ml-25m.zip, matched on 2026-10-07.
    assert manifest["zip"]["md5"] == "6b51fb2759a8657d3bfcbfc42b592ada"


def test_published_md5_reads_either_layout() -> None:
    digest = "0123456789abcdef0123456789abcdef"

    assert published_md5(f"MD5 (ml-25m.zip) = {digest}\n") == digest
    assert published_md5(f"{digest.upper()}  ml-25m.zip\n") == digest
    with pytest.raises(ValueError):
        published_md5("no digest here")


def _zip_and_manifest(tmp_path: Path, ratings: bytes, movies: bytes) -> tuple[Path, Path, str]:
    archive = tmp_path / "ml-25m.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ml-25m/ratings.csv", ratings)
        bundle.writestr("ml-25m/movies.csv", movies)
        bundle.writestr("ml-25m/tags.csv", b"not extracted")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "files": {
                    "ratings.csv": {"sha256": hashlib.sha256(b"userId\n1\n").hexdigest()},
                    "movies.csv": {"sha256": hashlib.sha256(b"movieId\n1\n").hexdigest()},
                }
            }
        )
    )
    return archive, manifest, hashlib.md5(archive.read_bytes()).hexdigest()


def test_prepare_inputs_checks_the_md5_then_extracts_and_verifies_the_two_files(
    tmp_path: Path,
) -> None:
    archive, manifest, md5 = _zip_and_manifest(tmp_path, b"userId\n1\n", b"movieId\n1\n")

    outcome = prepare_inputs(archive, f"{md5}  ml-25m.zip", tmp_path / "data", manifest)

    assert outcome["zip_md5"] == md5
    assert sorted(path.name for path in (tmp_path / "data").iterdir()) == [
        "movies.csv",
        "ratings.csv",
    ]
    with pytest.raises(TimingCheckError, match="MD5"):
        prepare_inputs(archive, "0" * 32, tmp_path / "other", manifest)


def test_prepare_inputs_refuses_a_zip_the_manifest_does_not_pin(tmp_path: Path) -> None:
    archive, manifest, md5 = _zip_and_manifest(tmp_path, b"userId\n1\n", b"movieId\n1\n")
    pinned = json.loads(manifest.read_text())
    pinned["zip"] = {"md5": "f" * 32}
    manifest.write_text(json.dumps(pinned))

    with pytest.raises(TimingCheckError, match="manifest pins"):
        prepare_inputs(archive, md5, tmp_path / "data", manifest)


def test_prepare_inputs_refuses_a_zip_whose_contents_moved(tmp_path: Path) -> None:
    archive, manifest, md5 = _zip_and_manifest(tmp_path, b"userId\n2\n", b"movieId\n1\n")

    with pytest.raises(TimingCheckError, match="ratings.csv"):
        prepare_inputs(archive, md5, tmp_path / "data", manifest)
    checks = verify_inputs(tmp_path / "data", manifest)
    assert checks["ratings.csv"]["matches_manifest"] is False
    assert checks["movies.csv"]["matches_manifest"] is True
