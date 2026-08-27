"""
Reproducibility contract for the serving-artifact build (non-negotiable #5).

These cover what a unit test can settle in one process: that the LightGBM
determinism pins are set and actually reach the trainer, that the manifest
derives its clock from ``--as-of`` and from nothing else, and that the staleness
comparison reports the fields it should. The cross-machine half of the promise —
the same bundle from a different host — is what ``make serving-artifacts-check``
exists for, because only a rebuild on the checking architecture can prove it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.features import FEATURE_COLUMNS
from src.models.artifacts import ServingManifest, file_sha256
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training.demo_artifacts import (
    build_parser,
    main,
    manifest_differences,
    parse_as_of,
    publish_serving_artifacts,
)

_AS_OF = datetime(2026, 9, 1, tzinfo=UTC)


def _ratings() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"user_id": 30, "item_id": 1, "rating": 4.0, "timestamp": 80},
            {"user_id": 20, "item_id": 1, "rating": 4.0, "timestamp": 90},
            {"user_id": 10, "item_id": 1, "rating": 5.0, "timestamp": 100},
            {"user_id": 20, "item_id": 2, "rating": 5.0, "timestamp": 150},
            {"user_id": 30, "item_id": 3, "rating": 5.0, "timestamp": 180},
            {"user_id": 10, "item_id": 2, "rating": 4.5, "timestamp": 200},
        ]
    )


def _movies() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"movieId": 1, "genres": "Action"},
            {"movieId": 2, "genres": "Action|Drama"},
            {"movieId": 3, "genres": "Comedy"},
        ]
    )


def _publish(output_dir: Path, *, as_of: datetime = _AS_OF) -> ServingManifest:
    return publish_serving_artifacts(
        _ratings(),
        movies=_movies(),
        tenant_id="demo",
        output_dir=output_dir,
        as_of=as_of,
    )


def _synthetic_training_set() -> tuple[pd.DataFrame, list[int], np.ndarray]:
    rng = np.random.default_rng(7)
    features = pd.DataFrame(rng.random((40, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    labels = np.zeros(40, dtype=np.float64)
    labels[::4] = 1.0
    return features, [4] * 10, labels


def test_ranker_config_pins_the_three_sources_of_thread_dependent_training() -> None:
    config = LGBMRankerConfig()
    assert config.num_threads == 1
    assert config.deterministic is True
    assert config.force_row_wise is True


def test_determinism_pins_reach_lightgbm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_train(params: dict[str, Any], *args: Any, **kwargs: Any) -> object:
        captured.update(params)
        return object()

    monkeypatch.setattr("src.models.ranker.lgbm.lgb.train", fake_train)
    features, groups, labels = _synthetic_training_set()

    LGBMRanker().fit(features, groups, labels)

    assert captured["num_threads"] == 1
    assert captured["deterministic"] is True
    assert captured["force_row_wise"] is True
    assert captured["seed"] == 42


def test_repeated_fit_saves_an_identical_booster(tmp_path: Path) -> None:
    features, groups, labels = _synthetic_training_set()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    LGBMRanker(config=LGBMRankerConfig(num_boost_round=20)).fit(
        features, groups, labels
    ).save_model(first)
    LGBMRanker(config=LGBMRankerConfig(num_boost_round=20)).fit(
        features, groups, labels
    ).save_model(second)

    assert file_sha256(first) == file_sha256(second)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-01T00:00:00+00:00", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-09-01T00:00:00Z", datetime(2026, 9, 1, tzinfo=UTC)),
        # Normalized to UTC, so the same instant written two ways pins the
        # same trained_at.
        ("2026-08-31T21:00:00-03:00", datetime(2026, 9, 1, tzinfo=UTC)),
    ],
)
def test_parse_as_of_normalizes_to_utc(value: str, expected: datetime) -> None:
    assert parse_as_of(value) == expected
    assert parse_as_of(value).tzinfo is UTC


def test_parse_as_of_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        parse_as_of("2026-09-01T00:00:00")


def test_parse_as_of_refuses_a_non_timestamp() -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_as_of("last tuesday")


def test_manifest_trained_at_comes_from_as_of_not_the_wall_clock(tmp_path: Path) -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    manifest = _publish(tmp_path, as_of=datetime(2026, 9, 1, 5, 30, tzinfo=offset))

    assert manifest.trained_at == "2026-09-01T00:00:00+00:00"
    # The manifest on disk is the one the sidecar boots against, so re-load it
    # rather than trusting the returned object.
    assert ServingManifest.load(tmp_path / "manifest.json").trained_at == manifest.trained_at


def test_same_as_of_publishes_a_byte_identical_bundle(tmp_path: Path) -> None:
    first = _publish(tmp_path / "first")
    second = _publish(tmp_path / "second")

    assert manifest_differences(first, second) == []
    for name in ("candidate-index.json", "ranker.txt", "manifest.json"):
        assert file_sha256(tmp_path / "first" / name) == file_sha256(tmp_path / "second" / name)


def test_manifest_differences_names_the_stale_fields(tmp_path: Path) -> None:
    manifest = _publish(tmp_path)
    drifted = ServingManifest(
        tenant_id="other",
        candidate=manifest.candidate,
        ranker=ServingManifest.load(tmp_path / "manifest.json").ranker,
        feature_version=manifest.feature_version,
        trained_at="2020-01-01T00:00:00+00:00",
    )

    assert manifest_differences(manifest, drifted) == ["tenant_id", "trained_at"]


def test_release_flags_are_accepted() -> None:
    args = build_parser().parse_args(
        [
            "--train-only",
            "--as-of",
            "2026-09-01T00:00:00+00:00",
            "--output-dir",
            "infra/model-bundle",
        ]
    )

    assert args.train_only is True
    assert args.as_of == "2026-09-01T00:00:00+00:00"
    assert args.output_dir == Path("infra/model-bundle")
    assert args.check is False


def _stub_rebuild(monkeypatch: pytest.MonkeyPatch, *, tenant_id: str) -> None:
    """Stand in for the database-backed rebuild `--check` performs."""

    def rebuild(settings: Any, *, output_dir: Path, as_of: datetime) -> ServingManifest:
        return publish_serving_artifacts(
            _ratings(),
            movies=_movies(),
            tenant_id=tenant_id,
            output_dir=output_dir,
            as_of=as_of,
        )

    monkeypatch.setattr("src.training.demo_artifacts.train_serving_artifacts", rebuild)


def test_check_names_the_drift_when_the_committed_bundle_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(tmp_path)
    _stub_rebuild(monkeypatch, tenant_id="somebody-else")

    with pytest.raises(SystemExit) as exit_info:
        main(["--check", "--as-of", "2026-09-01T00:00:00+00:00", "--output-dir", str(tmp_path)])

    assert "tenant_id" in str(exit_info.value)


def test_check_leaves_a_reproducing_bundle_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = _publish(tmp_path)
    before = {path.name: file_sha256(path) for path in sorted(tmp_path.iterdir())}
    _stub_rebuild(monkeypatch, tenant_id=committed.tenant_id)

    main(["--check", "--as-of", "2026-09-01T00:00:00+00:00", "--output-dir", str(tmp_path)])

    assert {path.name: file_sha256(path) for path in sorted(tmp_path.iterdir())} == before


def test_check_refuses_to_run_without_a_pinned_as_of() -> None:
    # An unpinned --check would compare a rebuild stamped "now" against a
    # committed manifest and disagree every single time. Refuse before the
    # command reaches settings or the database.
    with pytest.raises(SystemExit) as exit_info:
        main(["--check"])

    assert exit_info.value.code == 2
