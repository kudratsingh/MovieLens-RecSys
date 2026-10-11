"""The run-preserving MLflow import: sources, remapping, the plan, the dump check, the DSN."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import mlflow
import pytest
from mlflow.tracking import MlflowClient

from src.training import mlflow_import
from src.training.mlflow_import import Source, Target, build_plan, remap_path


def _target(**changes: object) -> Target:
    target = Target(
        alembic=mlflow_import.EXPECTED_ALEMBIC,
        read_only="on",
        experiments={
            "phase-a-sasrec": {
                "experiment_id": 6,
                "name": "phase-a-sasrec",
                "artifact_location": "mlflow-artifacts:/6",
                "lifecycle_stage": "active",
            }
        },
        run_ids={},
        col_limits={("tags", "value"): 8000, ("params", "value"): 8000},
        not_null={("runs", "run_uuid")},
        checks={"status": {"RUNNING", "FINISHED", "FAILED", "KILLED", "SCHEDULED"}},
        seq_last=6,
        seq_called=True,
    )
    for key, value in changes.items():
        setattr(target, key, value)
    return target


def _sqlite_store(tmp_path: Path, experiment: str = "phase-a-sasrec") -> tuple[Path, list[str]]:
    """A SQLite store with two runs, the way the pod's sweep writes one."""
    db = tmp_path / "pod" / "mlstore" / "mlflow.db"
    db.parent.mkdir(parents=True)
    uri = f"sqlite:///{db}"
    client = MlflowClient(tracking_uri=uri)
    eid = client.create_experiment(
        experiment, artifact_location=(db.parent / "files").resolve().as_uri()
    )
    ids = []
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        for label in ("one", "two"):
            with mlflow.start_run(experiment_id=eid, run_name=f"sasrec-{label}") as run:
                mlflow.log_param("loss", "bce")
                for step in (1, 2):
                    mlflow.log_metric("epoch_warm_recall_at_k_candidates", 0.1 * step, step=step)
                mlflow.log_metric("warm_recall_at_k_candidates", 0.5)
                mlflow.log_dict({"label": label}, "per_user_recall.json")
                ids.append(run.info.run_id)
    finally:
        mlflow.set_tracking_uri(previous)
    return db, ids


def test_a_sqlite_store_unpacked_elsewhere_is_read_through_the_remap(tmp_path: Path) -> None:
    db, ids = _sqlite_store(tmp_path)
    pod_files = str((db.parent / "files").resolve())
    moved = tmp_path / "home" / "mlflow"
    shutil.copytree(db.parent, moved)
    shutil.rmtree(tmp_path / "pod")
    source = Source(
        "pod", moved, "wo5 test", "sqlite", moved / "mlflow.db", (pod_files, str(moved / "files"))
    )
    plan = build_plan(_target(), [source], import_date="2026-10-11", only_runs={ids[0]})
    by_id = {run.run_id: run for run in plan.runs}
    imported = by_id[ids[0]]
    assert imported.action == "import" and imported.target_exp_id == 6
    assert [a.rel for a in imported.artifacts] == ["per_user_recall.json"]
    assert len(imported.metric_points) == 3
    assert imported.latest["epoch_warm_recall_at_k_candidates"].value == pytest.approx(0.2)
    assert imported.import_tags()["imported_from"] == "wo5 test"
    assert imported.import_tags()["import_date"] == "2026-10-11"
    assert by_id[ids[1]].action == "excluded"
    assert plan.new_experiments == {}


def test_without_the_remap_the_missing_artifacts_block_the_run(tmp_path: Path) -> None:
    db, ids = _sqlite_store(tmp_path)
    moved = tmp_path / "home" / "mlflow"
    shutil.copytree(db.parent, moved)
    shutil.rmtree(tmp_path / "pod")
    source = Source("pod", moved, None, "sqlite", moved / "mlflow.db")
    plan = build_plan(_target(), [source], import_date="2026-10-11")
    assert {run.action for run in plan.runs} == {"blocked"}


def test_an_existing_run_is_skipped_and_a_new_experiment_is_predicted(tmp_path: Path) -> None:
    db, ids = _sqlite_store(tmp_path, experiment="wo5-sweep-local-smoke")
    source = Source("pod", db.parent, None, "sqlite", db)
    target = _target(run_ids={ids[0]: (7, "sasrec-one")})
    plan = build_plan(target, [source], import_date="2026-10-11")
    actions = {run.run_id: run.action for run in plan.runs}
    assert actions == {ids[0]: "skip-existing", ids[1]: "import"}
    assert plan.new_experiments == {"wo5-sweep-local-smoke": 7}


def test_a_tag_longer_than_its_column_blocks_the_run(tmp_path: Path) -> None:
    db, ids = _sqlite_store(tmp_path)
    source = Source("pod", db.parent, "x" * 50, "sqlite", db)
    plan = build_plan(
        _target(col_limits={("tags", "value"): 10}),
        [source],
        import_date="2026-10-11",
        only_runs={ids[0]},
    )
    run = next(r for r in plan.runs if r.run_id == ids[0])
    assert run.action == "blocked" and any("tags.value" in r for r in run.block_reasons)


def test_a_file_store_is_read_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    store = tmp_path / "mlruns"
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(store.as_uri())
    try:
        mlflow.set_experiment("phase-a-sasrec")
        with mlflow.start_run(run_name="sasrec-file") as run:
            mlflow.log_param("seed", 42)
            mlflow.log_metric("warm_recall_at_k_candidates", 0.42)
    finally:
        mlflow.set_tracking_uri(previous)
    plan = build_plan(_target(), [Source("file", store)], import_date="2026-10-11")
    (found,) = [r for r in plan.runs if r.run_id == run.info.run_id]
    assert found.action == "import" and found.params == {"seed": "42"}
    assert mlflow_import.warm_recall(found) == ("warm_recall_at_k_candidates", 0.42)


def test_remap_replaces_only_a_whole_leading_path() -> None:
    remap = ("/workspace/wo5-sweep/mlstore/files", "/home/x/files")
    assert remap_path(Path("/workspace/wo5-sweep/mlstore/files/r/artifacts"), remap) == Path(
        "/home/x/files/r/artifacts"
    )
    assert remap_path(Path("/workspace/wo5-sweep/mlstore/files2/r"), remap) == Path(
        "/workspace/wo5-sweep/mlstore/files2/r"
    )
    assert remap_path(Path("/a"), None) == Path("/a")


def test_the_dump_check_wants_a_fresh_complete_plain_dump(tmp_path: Path) -> None:
    good = tmp_path / "good.sql.gz"
    body = (
        "-- PostgreSQL database dump\n"
        + "".join(
            f"COPY public.{t} (x) FROM stdin;\n"
            for t in ("runs", "experiments", "metrics", "alembic_version")
        )
        + "-- PostgreSQL database dump complete\n"
    )
    good.write_bytes(gzip.compress(body.encode()))
    assert mlflow_import.check_pg_dump(good, 2.0) == []
    truncated = tmp_path / "bad.sql.gz"
    truncated.write_bytes(gzip.compress(body.replace("complete", "").encode()))
    assert any("completion" in p for p in mlflow_import.check_pg_dump(truncated, 2.0))
    assert mlflow_import.check_pg_dump(tmp_path / "missing.sql.gz", 2.0)


def test_the_dsn_comes_from_the_environment_or_settings_never_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(mlflow_import.DSN_ENV_VAR, "postgresql://u:p@db:5432/mlflow")
    assert mlflow_import.default_dsn() == "postgresql://u:p@db:5432/mlflow"
    monkeypatch.delenv(mlflow_import.DSN_ENV_VAR)
    monkeypatch.setenv("POSTGRES_USER", "someone")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret-from-env")
    assert mlflow_import.default_dsn().startswith("postgresql://someone:secret-from-env@")
    assert mlflow_import.mask("postgresql://u:p@db/mlflow") == "postgresql://u:***@db/mlflow"
    source = Path(mlflow_import.__file__).read_text()
    assert "recsys:recsys" not in source


def test_sources_parse_labels_and_remaps() -> None:
    sources = mlflow_import.parse_sources(
        ["pod=/tmp/x/mlflow.db", "old=/tmp/mlruns"], ["pod=label text"], ["pod=/a=/b"]
    )
    assert sources[0].kind == "sqlite" and sources[0].label == "label text"
    assert sources[0].remap == ("/a", "/b")
    assert sources[1].kind == "file" and sources[1].remap is None
    with pytest.raises(ValueError, match="unknown"):
        mlflow_import.parse_sources(["pod=/tmp/x.db"], ["other=x"], [])
