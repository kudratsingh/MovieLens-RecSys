"""What the champion promotion refuses to do, and what it leaves behind.

Every test here is about one of two properties. The first is that a refusal
changes *nothing*: a bundle whose bytes do not match its manifest, a bundle
published for another tenant, a production run that was not confirmed, and a
database missing migration 0016's ``updated_at`` trigger all have to leave the
registry row exactly as they found it, because a tenant pinned to coordinates
nobody checked is the failure the checksum pinning exists to prevent. The second
is that the undo works: a promotion writes the coordinates it replaced to a
snapshot before it writes the row, and ``--revert`` puts them back — including
the all-null "this tenant has no learned serving" state, which is a real
champion value and not a missing one.

The schema is in-memory SQLite with an ``ATTACH``ed ``public`` database, the same
shape ``tests/unit/test_release_bootstrap.py`` uses. It carries a check
constraint equivalent to ``ck_tenants_champion_complete`` and an
``updated_at``-maintaining trigger equivalent to migration 0016's, because both
are things this tool is asserted *against* rather than things it implements. The
Postgres-specific halves — ``num_nonnulls``, ``now()``, the migrator role's
grants — are proven by the migration and the rehearsal, not here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from src.config import Settings
from src.feature_contract import FEATURE_COLUMNS
from src.models.artifacts import ArtifactRef, CandidateIndex, ServingManifest, file_sha256
from src.release import promote
from src.release.promote import (
    PromotionError,
    PromotionRefusedError,
    build_promotion,
    build_revert,
    check_run_is_authorized,
    run_promotion,
    snapshot_path_for,
)
from src.serving.tenancy import TenantChampion

TENANT = "demo"
SEEDED = TenantChampion(
    candidate_version="demo-itemitem-v1",
    ranker_version="demo-lgbm-v1",
    feature_version="feast-phase3-v1",
)


# --- the database ----------------------------------------------------------


def _tenants_engine(
    *, with_trigger: bool = True, champion: TenantChampion | None = SEEDED
) -> Engine:
    """A ``public.tenants`` shaped like migration 0016 left it."""
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        future=True,
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
        # `num_nonnulls(...) IN (0, 3)` in SQLite terms. The constraint is the
        # database's own enforcement of "a champion is all three columns or
        # none", and it stays the enforcer of last resort (ADR 0008).
        connection.execute(text("""
            CREATE TABLE public.tenants (
                id TEXT PRIMARY KEY,
                champion_candidate_version TEXT,
                champion_ranker_version TEXT,
                champion_feature_version TEXT,
                updated_at TEXT NOT NULL DEFAULT 'created',
                CONSTRAINT ck_tenants_champion_complete CHECK (
                    ((champion_candidate_version IS NOT NULL)
                     + (champion_ranker_version IS NOT NULL)
                     + (champion_feature_version IS NOT NULL)) IN (0, 3)
                )
            )
            """))
        if with_trigger:
            # Migration 0016 sets `NEW.updated_at := now()` from a BEFORE UPDATE
            # trigger. SQLite cannot assign to NEW, and its clocks are too coarse
            # to guarantee two updates differ, so this stands in with a value
            # that always moves — which is the only property the tool reads.
            connection.execute(text("""
                CREATE TRIGGER public.tenants_after_write AFTER UPDATE ON tenants
                FOR EACH ROW BEGIN
                    UPDATE tenants SET updated_at = OLD.updated_at || '!' WHERE id = NEW.id;
                END
                """))
        connection.execute(
            text("""
            INSERT INTO public.tenants (
                id,
                champion_candidate_version,
                champion_ranker_version,
                champion_feature_version
            ) VALUES (:id, :candidate, :ranker, :feature)
            """),
            {
                "id": TENANT,
                "candidate": champion.candidate_version if champion else None,
                "ranker": champion.ranker_version if champion else None,
                "feature": champion.feature_version if champion else None,
            },
        )
    return engine


def _row(engine: Engine, tenant_id: str = TENANT) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM public.tenants WHERE id = :id"), {"id": tenant_id}
            )
            .mappings()
            .one()
        )
    return dict(row)


# --- the bundle ------------------------------------------------------------


def _booster(path: Path) -> None:
    """Train the way ``LGBMRanker.fit`` does — from a bare matrix, so no names."""
    width = len(FEATURE_COLUMNS)
    booster = lgb.train(
        {
            "objective": "lambdarank",
            "num_leaves": 3,
            "min_data_in_leaf": 1,
            "verbose": -1,
            "num_threads": 1,
            "deterministic": True,
            "force_row_wise": True,
            "seed": 0,
        },
        lgb.Dataset(
            pd.DataFrame(
                np.arange(width * 5, dtype=np.float64).reshape(5, width),
                columns=[f"f{index}" for index in range(width)],
            ).to_numpy(dtype=np.float64),
            label=np.array([1, 0, 0, 0, 0], dtype=np.float64),
            group=[5],
            free_raw_data=False,
        ),
        num_boost_round=2,
    )
    booster.save_model(str(path))


def _bundle(
    directory: Path,
    *,
    tenant_id: str = TENANT,
    candidate_version: str = "sasrec-served-v1",
    ranker_version: str = "served-lgbm-v1",
    feature_version: str = "feast-phase3-v1",
) -> Path:
    """A schema 1 bundle that verifies: manifest, index and booster on disk."""
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "candidate-index.json"
    ranker_path = directory / "ranker.txt"
    CandidateIndex.build({1: {1, 2}, 2: {1, 3}}).write(index_path)
    _booster(ranker_path)
    ServingManifest(
        tenant_id=tenant_id,
        candidate=ArtifactRef(
            artifact_type="item-item-cosine",
            version=candidate_version,
            filename=index_path.name,
            sha256=file_sha256(index_path),
        ),
        ranker=ArtifactRef(
            artifact_type="lightgbm-lambdarank",
            version=ranker_version,
            filename=ranker_path.name,
            sha256=file_sha256(ranker_path),
        ),
        feature_version=feature_version,
        trained_at="2026-09-05T00:00:00+00:00",
        feature_columns=tuple(FEATURE_COLUMNS),
    ).write(directory / "manifest.json")
    return directory


def _settings(environment: str = "dev") -> Settings:
    if environment == "production":
        return Settings(
            _env_file=None,
            environment="production",
            model_server_auth_token="a-generated-model-server-token",
            pgbouncer_admin_password="a-generated-pgbouncer-password",
        )
    return Settings(_env_file=None, environment=environment)


def _promotion(tmp_path: Path, **overrides: Any) -> Any:
    bundle = _bundle(tmp_path / "bundle", **overrides)
    return build_promotion(
        _settings(),
        tenant_id=TENANT,
        bundle_dir=bundle,
        snapshot_dir=tmp_path / "snapshots",
        actor="tester@fixture",
    )


# --- verification refuses before it writes ---------------------------------


def test_a_bundle_whose_bytes_moved_refuses_and_leaves_the_row_alone(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    (bundle / "ranker.txt").write_text("not the booster the manifest pinned", encoding="utf-8")

    with pytest.raises(PromotionError, match="did not verify"):
        build_promotion(
            _settings(),
            tenant_id=TENANT,
            bundle_dir=bundle,
            snapshot_dir=tmp_path / "snapshots",
            actor="tester@fixture",
        )
    assert not (tmp_path / "snapshots").exists()


def test_a_bundle_published_for_another_tenant_is_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle", tenant_id="someone-else")

    with pytest.raises(PromotionError, match="published for tenant 'someone-else'"):
        build_promotion(
            _settings(),
            tenant_id=TENANT,
            bundle_dir=bundle,
            snapshot_dir=tmp_path / "snapshots",
            actor="tester@fixture",
        )


def test_a_directory_with_no_manifest_says_so(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PromotionError, match="no serving manifest at"):
        build_promotion(
            _settings(),
            tenant_id=TENANT,
            bundle_dir=empty,
            snapshot_dir=tmp_path / "snapshots",
            actor="tester@fixture",
        )


def test_an_unknown_tenant_is_never_invented(tmp_path: Path) -> None:
    engine = _tenants_engine()
    request = _promotion(tmp_path)
    unknown = type(request)(**{**request.__dict__, "tenant_id": "no-such-tenant"})

    with pytest.raises(PromotionError, match="no tenant 'no-such-tenant'"):
        run_promotion(engine, unknown)


# --- the write -------------------------------------------------------------


def test_a_promotion_writes_all_three_columns_and_snapshots_the_previous_ones(
    tmp_path: Path,
) -> None:
    engine = _tenants_engine()
    before = _row(engine)
    request = _promotion(tmp_path)

    summary = run_promotion(engine, request)

    after = _row(engine)
    assert summary["changed"] is True
    assert after["champion_candidate_version"] == "sasrec-served-v1"
    assert after["champion_ranker_version"] == "served-lgbm-v1"
    assert after["champion_feature_version"] == "feast-phase3-v1"
    # The trigger's job, not the tool's: the tool only asserts it happened.
    assert after["updated_at"] != before["updated_at"]

    snapshot = json.loads(snapshot_path_for(TENANT, tmp_path / "snapshots").read_text())
    assert snapshot["tenant_id"] == TENANT
    assert snapshot["previous"] == {
        "candidate_version": "demo-itemitem-v1",
        "ranker_version": "demo-lgbm-v1",
        "feature_version": "feast-phase3-v1",
    }
    assert snapshot["promoted_to"]["candidate_version"] == "sasrec-served-v1"
    assert snapshot["actor"] == "tester@fixture"


def test_the_summary_reports_both_sides_of_the_move(tmp_path: Path) -> None:
    engine = _tenants_engine()

    summary = run_promotion(engine, _promotion(tmp_path))

    assert summary["before"]["candidate_version"] == "demo-itemitem-v1"
    assert summary["after"]["candidate_version"] == "sasrec-served-v1"
    assert summary["details"]["retriever_family"] == "item-item-cosine"
    assert "30s" in summary["propagation"]


def test_rerunning_the_same_promotion_writes_nothing(tmp_path: Path) -> None:
    engine = _tenants_engine()
    request = _promotion(tmp_path)
    run_promotion(engine, request)
    settled = _row(engine)
    snapshot_path = snapshot_path_for(TENANT, tmp_path / "snapshots")
    snapshot = snapshot_path.read_text()

    summary = run_promotion(engine, request)

    assert summary["changed"] is False
    assert "already stands" in summary["reason"]
    # No burnt `updated_at`, and — the one that matters — the snapshot still
    # names the champion the *first* run replaced, so the revert target survives
    # a re-run.
    assert _row(engine) == settled
    assert snapshot_path.read_text() == snapshot


def test_a_dry_run_verifies_and_changes_nothing(tmp_path: Path) -> None:
    engine = _tenants_engine()
    before = _row(engine)

    summary = run_promotion(engine, _promotion(tmp_path), dry_run=True)

    assert summary["changed"] is False
    assert summary["after"]["candidate_version"] == "sasrec-served-v1"
    assert _row(engine) == before
    assert not snapshot_path_for(TENANT, tmp_path / "snapshots").exists()


# --- the constraint and the trigger ----------------------------------------


def test_a_half_written_champion_is_refused_by_the_database(tmp_path: Path) -> None:
    """The tool cannot express one, and the database would refuse it anyway."""
    engine = _tenants_engine()
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE public.tenants SET champion_ranker_version = NULL WHERE id = :id"),
                {"id": TENANT},
            )

    # And the tool's own write moves all three in one statement, so the state
    # the constraint forbids is never even momentarily on the table.
    run_promotion(engine, _promotion(tmp_path))
    row = _row(engine)
    assert all(
        row[f"champion_{field}"]
        for field in ("candidate_version", "ranker_version", "feature_version")
    )


def test_a_database_without_the_updated_at_trigger_rolls_the_promotion_back(
    tmp_path: Path,
) -> None:
    engine = _tenants_engine(with_trigger=False)
    before = _row(engine)

    with pytest.raises(PromotionError, match="tenants_before_write trigger"):
        run_promotion(engine, _promotion(tmp_path))

    assert _row(engine) == before


# --- revert ----------------------------------------------------------------


def test_revert_restores_the_coordinates_the_promotion_replaced(tmp_path: Path) -> None:
    engine = _tenants_engine()
    run_promotion(engine, _promotion(tmp_path))

    summary = run_promotion(
        engine,
        build_revert(tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture"),
    )

    assert summary["changed"] is True
    row = _row(engine)
    assert row["champion_candidate_version"] == "demo-itemitem-v1"
    assert row["champion_ranker_version"] == "demo-lgbm-v1"
    assert row["champion_feature_version"] == "feast-phase3-v1"


def test_reverting_twice_is_not_destructive(tmp_path: Path) -> None:
    engine = _tenants_engine()
    run_promotion(engine, _promotion(tmp_path))
    revert = build_revert(
        tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture"
    )
    run_promotion(engine, revert)
    reverted = _row(engine)

    summary = run_promotion(engine, revert)

    assert summary["changed"] is False
    assert _row(engine) == reverted


def test_revert_restores_a_tenant_that_had_no_champion_at_all(tmp_path: Path) -> None:
    """All-null is a champion value, not a missing one, and must come back."""
    engine = _tenants_engine(champion=None)
    run_promotion(engine, _promotion(tmp_path))

    run_promotion(
        engine,
        build_revert(tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture"),
    )

    row = _row(engine)
    assert row["champion_candidate_version"] is None
    assert row["champion_ranker_version"] is None
    assert row["champion_feature_version"] is None


def test_revert_with_no_snapshot_refuses_rather_than_guessing(tmp_path: Path) -> None:
    with pytest.raises(PromotionError, match="nothing to revert to"):
        build_revert(tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture")


def test_a_snapshot_from_another_tenant_is_refused(tmp_path: Path) -> None:
    engine = _tenants_engine()
    run_promotion(engine, _promotion(tmp_path))
    path = snapshot_path_for(TENANT, tmp_path / "snapshots")
    document = json.loads(path.read_text())
    document["tenant_id"] = "another-tenant"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PromotionError, match="belongs to tenant 'another-tenant'"):
        build_revert(tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture")


def test_a_snapshot_naming_half_a_champion_is_refused(tmp_path: Path) -> None:
    engine = _tenants_engine()
    run_promotion(engine, _promotion(tmp_path))
    path = snapshot_path_for(TENANT, tmp_path / "snapshots")
    document = json.loads(path.read_text())
    document["previous"]["ranker_version"] = None
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(PromotionError, match="is malformed"):
        build_revert(tenant_id=TENANT, snapshot_dir=tmp_path / "snapshots", actor="tester@fixture")


# --- the production guard --------------------------------------------------


def test_production_refuses_an_unconfirmed_run() -> None:
    with pytest.raises(PromotionRefusedError, match="--yes"):
        check_run_is_authorized(_settings("production"), confirmed=False)


def test_production_accepts_a_confirmed_run() -> None:
    assert "confirmed" in check_run_is_authorized(_settings("production"), confirmed=True)


def test_dev_needs_no_confirmation() -> None:
    assert "no confirmation" in check_run_is_authorized(_settings("dev"), confirmed=False)


# --- the CLI ---------------------------------------------------------------


def test_the_cli_promotes_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = _tenants_engine()
    monkeypatch.setenv("ENVIRONMENT", "dev")
    # The DSN is never opened: the one engine this run gets is the SQLite fixture
    # above. Promoting against a real database is not a unit test's business.
    monkeypatch.setattr(promote, "create_engine", lambda *args, **kwargs: engine)
    # `main` disposes the engine it built, which for a StaticPool closes the one
    # connection the ATTACHed in-memory database lives in — so the assertions
    # below would query a database that no longer exists.
    monkeypatch.setattr(engine, "dispose", lambda *args, **kwargs: None)

    exit_code = promote.main(
        [
            "--tenant",
            TENANT,
            "--bundle",
            str(_bundle(tmp_path / "bundle")),
            "--snapshot-dir",
            str(tmp_path / "snapshots"),
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["changed"] is True
    assert summary["after"]["ranker_version"] == "served-lgbm-v1"
    assert _row(engine)["champion_ranker_version"] == "served-lgbm-v1"


def test_the_cli_refuses_a_bundle_with_revert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    with pytest.raises(SystemExit):
        promote.main(["--tenant", TENANT, "--revert", "--bundle", str(tmp_path)])


def test_the_cli_reports_a_refusal_on_stderr_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    bundle = _bundle(tmp_path / "bundle")
    (bundle / "candidate-index.json").write_text("{}", encoding="utf-8")

    exit_code = promote.main(
        ["--tenant", TENANT, "--bundle", str(bundle), "--snapshot-dir", str(tmp_path / "s")]
    )

    assert exit_code == 1
    assert "did not verify" in capsys.readouterr().err
