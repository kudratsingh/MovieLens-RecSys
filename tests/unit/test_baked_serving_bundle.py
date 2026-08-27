"""The committed serving bundle and the registry the features image bakes.

Production serves artifacts baked into the image rather than written to a
volume, so the two things that can rot silently are the bundle in
``infra/model-bundle/`` and the Feast registry ``feast apply`` writes at build
time. These tests pin what a rebuild cannot check on its own: that the
committed files still match their manifest, that the manifest still matches the
``as-of`` the Makefile builds with, and that the image declares the placeholders
``feast apply`` needs as build arguments rather than as environment defaults a
production container would inherit.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from feast import Entity, FeatureView, Field
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)
from feast.types import Float64, Int64, PrimitiveFeastType
from feast.value_type import ValueType

from src.feature_contract import FEATURE_COLUMNS
from src.features.registry_check import (
    RegistryCheckError,
    describe,
    describe_declared_definitions,
    describe_registry,
    registry_differences,
)
from src.models.artifacts import ServingArtifactBundle, ServingManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_DIR = REPO_ROOT / "infra" / "model-bundle"
DOCKERFILE = REPO_ROOT / "infra" / "features" / "Dockerfile"
FEATURE_STORE_YAML = REPO_ROOT / "src" / "features" / "feast_repo" / "feature_store.yaml"
MAKEFILE = REPO_ROOT / "Makefile"


def makefile_artifact_as_of() -> str:
    match = re.search(r"^ARTIFACT_AS_OF\s*:?=\s*(\S+)\s*$", MAKEFILE.read_text(), re.MULTILINE)
    assert match is not None, "the Makefile no longer pins ARTIFACT_AS_OF"
    return match.group(1)


class TestCommittedBundle:
    def test_the_committed_bundle_self_verifies(self) -> None:
        # The same call the sidecar makes at boot: it re-hashes both artifact
        # files against the manifest, so a hand-edited ranker.txt fails here
        # rather than in production.
        bundle = ServingArtifactBundle.load(BUNDLE_DIR / "manifest.json")
        assert bundle.manifest.tenant_id == "demo"
        # A bundle whose index has no neighbours would serve the popularity
        # fill under a learned label — PR #64's `unseeded-retrieval` shape.
        assert bundle.candidates.neighbors
        assert bundle.candidates.popularity

    def test_the_manifest_pins_the_serving_feature_contract(self) -> None:
        manifest = ServingManifest.load(BUNDLE_DIR / "manifest.json")
        assert manifest.feature_columns == tuple(FEATURE_COLUMNS)

    def test_the_manifest_was_trained_at_the_as_of_the_makefile_builds_with(self) -> None:
        # trained_at is derived from --as-of, so this is what catches a bundle
        # that was not rebuilt after ARTIFACT_AS_OF moved. Compared as instants
        # because either side may spell UTC differently.
        manifest = ServingManifest.load(BUNDLE_DIR / "manifest.json")
        trained_at = datetime.fromisoformat(manifest.trained_at).astimezone(UTC)
        pinned = datetime.fromisoformat(makefile_artifact_as_of()).astimezone(UTC)
        assert trained_at == pinned


class TestFeaturesImageBakesItsInputs:
    def test_the_image_copies_the_committed_bundle_to_the_serving_path(self) -> None:
        dockerfile = DOCKERFILE.read_text()
        assert "COPY infra/model-bundle/ ./models/serving/" in dockerfile

    def test_the_image_applies_and_then_checks_the_feast_registry(self) -> None:
        dockerfile = DOCKERFILE.read_text()
        assert "feast -c src/features/feast_repo apply" in dockerfile
        assert "python -m src.features.registry_check" in dockerfile

    def test_every_feature_store_placeholder_is_a_build_arg_and_not_an_env_default(self) -> None:
        # An ENV default would survive into the running container, so a
        # deployment that forgot REDIS_CONNECTION_STRING would resolve to the
        # placeholder host instead of refusing to start. Deriving the names
        # from feature_store.yaml keeps this from falling behind a new one.
        placeholders = set(re.findall(r"\$\{([A-Z0-9_]+)\}", FEATURE_STORE_YAML.read_text()))
        assert placeholders, "feature_store.yaml no longer interpolates anything"
        dockerfile = DOCKERFILE.read_text()
        env_names = set(re.findall(r"^\s*(?:ENV\s+)?([A-Z0-9_]+)=", dockerfile, re.MULTILINE))
        arg_names = set(re.findall(r"^ARG\s+([A-Z0-9_]+)=", dockerfile, re.MULTILINE))
        for name in sorted(placeholders):
            assert name in arg_names, f"{name} is not declared as a build ARG"
            assert name not in env_names - arg_names, f"{name} must not be baked as ENV"


def _described(*, ttl_days: int = 3650, dtype: PrimitiveFeastType = Int64) -> dict[str, Any]:
    """One entity and one view, so a probe changes exactly one described value."""
    entity = Entity(name="user", join_keys=["user_id"], value_type=ValueType.INT64)
    view = FeatureView(
        name="probe_features",
        entities=[entity],
        ttl=timedelta(days=ttl_days),
        # The join key is declared here the way an applied registry carries it.
        schema=[Field(name="probe_count", dtype=dtype), Field(name="user_id", dtype=Int64)],
        source=PostgreSQLSource(
            name="probe_source",
            table="feature_store.probe",
            timestamp_field="event_timestamp",
        ),
    )
    return describe(project="movielens_recsys", entities=[entity], feature_views=[view])


class TestRegistryDescription:
    def test_a_join_key_is_not_reported_as_a_feature(self) -> None:
        # `apply` adds one schema column per join key, so a description that
        # kept them could never compare a declaration with an applied registry.
        described = _described()
        fields = described["feature_views"]["probe_features"]["fields"]  # type: ignore[index]
        assert fields == {"probe_count": "Int64"}

    def test_identical_definitions_have_no_differences(self) -> None:
        assert registry_differences(_described(), _described()) == []

    def test_a_changed_dtype_is_reported_by_path(self) -> None:
        differences = registry_differences(_described(), _described(dtype=Float64))
        assert differences == ["feature_views.probe_features.fields.probe_count"]

    def test_a_changed_ttl_is_reported_by_path(self) -> None:
        differences = registry_differences(_described(), _described(ttl_days=1))
        assert differences == ["feature_views.probe_features.ttl_seconds"]

    def test_a_missing_feature_view_is_reported_rather_than_skipped(self) -> None:
        empty = describe(project="movielens_recsys", entities=[], feature_views=[])
        differences = registry_differences(empty, _described())
        assert "feature_views.probe_features.fields.probe_count" in differences
        assert "entities.user.join_key" in differences


class TestDeclaredDefinitions:
    def test_the_declared_views_cover_the_whole_ranker_feature_contract(self) -> None:
        described = describe_declared_definitions(project="movielens_recsys")
        views = described["feature_views"]
        assert set(views) == {"user_features", "item_features", "user_item_features"}
        served = {name for view in views.values() for name in view["fields"]}  # type: ignore[index,union-attr]
        assert served == set(FEATURE_COLUMNS)

    def test_every_declared_view_is_scoped_by_the_tenant_entity(self) -> None:
        described = describe_declared_definitions(project="movielens_recsys")
        for view in described["feature_views"].values():  # type: ignore[union-attr]
            assert "tenant" in view["entities"]

    def test_describing_a_repo_with_no_registry_is_a_named_failure(self, tmp_path: Path) -> None:
        # Distinguishable from drift: the release path has to tell "nobody
        # applied this" apart from "this was applied from other definitions".
        (tmp_path / "feature_store.yaml").write_text(
            "project: movielens_recsys\nregistry: data/registry.db\nprovider: local\n"
        )
        with pytest.raises(RegistryCheckError, match="no Feast registry"):
            describe_registry(tmp_path)
