"""Publishing a schema 2 bundle, and proving the published bytes load back.

The manifest tests next door (``test_serving_manifest_v2.py``) hand-build every
``ArtifactRef`` and then assert the loader accepts it. That proves the manifest
vocabulary is coherent; it cannot prove anything about a publisher, because
there was none. These tests are the other half: a bundle is *written* by
``src.models.bundle_publisher`` and then read back through the real loader, so a
publisher whose checksums describe a file it did not write, or whose manifest
the loader would refuse, fails here rather than at a sidecar's boot.

Nothing here loads a real SASRec encoder. The sequence fixtures are blobs with
the right names and the right cross-references, because everything this module
owns is about names, roles, checksums and lineage — realising the encoder is
``test_sidecar_sasrec_load.py``'s job and costs a 9.4 MB artifact to do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from src.feature_contract import FEATURE_COLUMNS
from src.models.artifacts import (
    INDEX_TYPE_FLAT_IP_EXACT,
    RANKER_ROUTE_FALLBACK,
    RANKER_ROUTE_LEARNED,
    RETRIEVER_ARTIFACT_POPULARITY,
    RETRIEVER_FAMILY_ITEM_ITEM,
    RETRIEVER_FAMILY_SASREC,
    CandidateIndex,
    Lineage,
    ServingArtifactBundle,
    ServingManifest,
)
from src.models.bundle_publisher import (
    BUNDLE_KIND_DEMO_FIXTURE,
    BUNDLE_KIND_SERVED,
    KIND_MARKER_FILENAME,
    ArtifactSource,
    BundleKindMarker,
    BundlePublishError,
    BundleSpec,
    RankerSpec,
    RetrieverSpec,
    main,
    parse_utc_timestamp,
    publish_bundle,
    verify_bundle,
)
from src.models.popularity_artifact import (
    POPULARITY_ARTIFACT_FILENAME,
    serialize_popularity_order,
)

TRAINED_AT = "2026-09-01T00:00:00+00:00"
FEATURE_VERSION = "feast-phase3-v1"

# Enough lineage to satisfy a served item-item bundle. A served SASRec bundle
# needs the two native-library fields on top; ``_sasrec_lineage`` adds them.
SERVED_LINEAGE = Lineage(
    protocol_hash="7f3c",
    run_id="a11af5ed0f0745f68572407237cfa4b9",
    raw_data_revision="dvc:9a1",
    code_sha="483d904",
)


# --- source artifacts, as a trainer would leave them behind -----------------


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    """A directory of artifacts to bake, separate from any bundle directory."""
    directory = tmp_path / "runs"
    directory.mkdir()
    return directory


def _candidate_index(directory: Path, name: str = "candidate-index.json") -> Path:
    path = directory / name
    CandidateIndex.build({1: {1, 2}, 2: {1, 3}}).write(path)
    return path


def _booster(directory: Path, name: str, *, n_features: int = len(FEATURE_COLUMNS)) -> Path:
    """Train the way ``LGBMRanker.fit`` does — from a bare matrix, so no names.

    Real LightGBM files rather than blobs because the manifest's per-route
    feature contract opens them: a publisher that wrote a plausible-looking
    ranker reference would pass every check a fake booster could support.
    """
    path = directory / name
    frame = pd.DataFrame(
        np.arange(n_features * 5, dtype=np.float64).reshape(5, n_features),
        columns=[f"f{index}" for index in range(n_features)],
    )
    booster = lgb.train(
        {
            "objective": "lambdarank",
            "num_leaves": 3,
            "min_data_in_leaf": 1,
            "verbose": -1,
            "num_threads": 1,
            "deterministic": True,
            "force_row_wise": True,
        },
        lgb.Dataset(
            frame.to_numpy(dtype=np.float64),
            label=np.array([1, 0, 0, 0, 0], dtype=np.float64),
            group=[5],
            free_raw_data=False,
        ),
        num_boost_round=2,
    )
    booster.save_model(str(path))
    return path


def _sasrec_sources(directory: Path, *, model_filename: str = "sasrec-model.zip") -> dict[str, Any]:
    """An encoder archive and the artifact manifest that describes it.

    Only the fields the publisher cross-checks are real; the archive is a blob.
    ``model_filename`` is a parameter because pointing the two manifests at
    different files is the publishing bug the loader's own comment warns about.
    """
    encoder = directory / "sasrec-model.zip"
    encoder.write_bytes(b"encoder-weights")
    companion = directory / "sasrec-manifest.json"
    companion.write_text(
        json.dumps({"model_filename": model_filename, "n_items": 3}, sort_keys=True),
        encoding="utf-8",
    )
    popularity = directory / POPULARITY_ARTIFACT_FILENAME
    popularity.write_bytes(serialize_popularity_order([3, 1, 2]))
    return {"encoder": encoder, "companion": companion, "popularity": popularity}


# --- specs ------------------------------------------------------------------


def _rankers(directory: Path, *, single: bool = False) -> dict[str, RankerSpec]:
    learned = ArtifactSource(
        path=_booster(directory, "ranker-learned.txt"),
        artifact_type="lightgbm-lambdarank",
        version="learned-v2",
    )
    if single:
        # One booster on both routes: the shape schema 1 always had, and the
        # manifest design explicitly still allows.
        return {
            RANKER_ROUTE_LEARNED: RankerSpec(artifact=learned),
            RANKER_ROUTE_FALLBACK: RankerSpec(artifact=learned),
        }
    fallback = ArtifactSource(
        path=_booster(directory, "ranker-fallback.txt"),
        artifact_type="lightgbm-lambdarank",
        version="fallback-v1",
    )
    return {
        RANKER_ROUTE_LEARNED: RankerSpec(artifact=learned),
        RANKER_ROUTE_FALLBACK: RankerSpec(artifact=fallback),
    }


def _item_item_spec(
    directory: Path,
    *,
    kind: str = BUNDLE_KIND_SERVED,
    lineage: Lineage = SERVED_LINEAGE,
    single_ranker: bool = False,
) -> BundleSpec:
    return BundleSpec(
        kind=kind,
        tenant_id="demo",
        feature_version=FEATURE_VERSION,
        trained_at=parse_utc_timestamp(TRAINED_AT),
        retriever=RetrieverSpec(
            family=RETRIEVER_FAMILY_ITEM_ITEM,
            artifacts={
                "index": ArtifactSource(
                    path=_candidate_index(directory),
                    artifact_type=RETRIEVER_FAMILY_ITEM_ITEM,
                    version="itemitem-v1",
                )
            },
        ),
        rankers=_rankers(directory, single=single_ranker),
        lineage=lineage,
    )


def _sasrec_lineage() -> Lineage:
    return Lineage(
        protocol_hash=SERVED_LINEAGE.protocol_hash,
        run_id=SERVED_LINEAGE.run_id,
        raw_data_revision=SERVED_LINEAGE.raw_data_revision,
        code_sha=SERVED_LINEAGE.code_sha,
        torch_version="2.12.0",
        faiss_version="1.8.0",
    )


def _sasrec_spec(
    directory: Path,
    *,
    roles: tuple[str, ...] = ("encoder", "vocabulary", "config", RETRIEVER_ARTIFACT_POPULARITY),
    lineage: Lineage | None = None,
    model_filename: str = "sasrec-model.zip",
) -> BundleSpec:
    files = _sasrec_sources(directory, model_filename=model_filename)
    # The vocabulary fingerprint and the encoder config both live in
    # sasrec-manifest.json, so both roles point at it; declaring them is what
    # puts that file's checksum into the serving manifest, which nothing else
    # does today.
    available = {
        "encoder": ArtifactSource(
            path=files["encoder"], artifact_type="sasrec-encoder", version="sasrec-v1"
        ),
        "vocabulary": ArtifactSource(
            path=files["companion"], artifact_type="sasrec-vocabulary", version="sasrec-v1"
        ),
        "config": ArtifactSource(
            path=files["companion"], artifact_type="sasrec-config", version="sasrec-v1"
        ),
        RETRIEVER_ARTIFACT_POPULARITY: ArtifactSource(
            path=files["popularity"], artifact_type="popularity-order", version="sasrec-v1"
        ),
    }
    return BundleSpec(
        kind=BUNDLE_KIND_SERVED,
        tenant_id="demo",
        feature_version=FEATURE_VERSION,
        trained_at=parse_utc_timestamp(TRAINED_AT),
        retriever=RetrieverSpec(
            family=RETRIEVER_FAMILY_SASREC,
            artifacts={role: available[role] for role in roles},
            params={
                "max_sequence_length": 50,
                "cold_start_threshold": 5,
                "exclusion_policy": "watched-and-dismissed-excluded-v1",
                "index_type": INDEX_TYPE_FLAT_IP_EXACT,
            },
        ),
        rankers=_rankers(directory),
        lineage=_sasrec_lineage() if lineage is None else lineage,
    )


# --- the round trip ---------------------------------------------------------


class TestItemItemBundle:
    def test_a_published_item_item_bundle_loads_back_through_the_real_loader(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"

        published = publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        manifest = ServingManifest.load(bundle_dir / "manifest.json")
        assert manifest.schema_version == 2
        assert manifest.to_dict() == published.manifest.to_dict()
        assert manifest.retriever.family == RETRIEVER_FAMILY_ITEM_ITEM
        assert manifest.retriever.version == "itemitem-v1"
        assert manifest.route(RANKER_ROUTE_LEARNED).artifact.version == "learned-v2"
        assert manifest.route(RANKER_ROUTE_FALLBACK).artifact.version == "fallback-v1"

    def test_every_recorded_checksum_is_the_hash_of_the_file_that_was_written(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """The publisher's one irreducible promise, checked against the bytes.

        ``ServingManifest.load`` already re-hashes, so this would fail there
        too — asserted directly as well because a manifest agreeing with its own
        directory is the property, and reading it out of a loader that could one
        day skip the check would make the test depend on that decision.
        """
        import hashlib

        bundle_dir = tmp_path / "bundle"
        published = publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        refs = [
            *published.manifest.retriever.artifacts.values(),
            *(published.manifest.rankers[route].artifact for route in published.manifest.rankers),
        ]
        for ref in refs:
            on_disk = (bundle_dir / ref.filename).read_bytes()
            assert ref.sha256 == hashlib.sha256(on_disk).hexdigest()

    def test_a_published_bundle_realises_into_models(self, tmp_path: Path, sources: Path) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir, realise=True)

        bundle = ServingArtifactBundle.load(bundle_dir / "manifest.json")

        assert bundle.candidates is not None
        assert bundle.candidates.retrieve([1], limit=2).movie_ids == [2, 3]
        assert bundle.rankers[RANKER_ROUTE_LEARNED] is not bundle.rankers[RANKER_ROUTE_FALLBACK]

    def test_a_single_ranker_bundle_is_still_valid(self, tmp_path: Path, sources: Path) -> None:
        bundle_dir = tmp_path / "bundle"

        published = publish_bundle(
            _item_item_spec(sources, single_ranker=True), output_dir=bundle_dir, realise=True
        )

        learned = published.manifest.route(RANKER_ROUTE_LEARNED).artifact
        fallback = published.manifest.route(RANKER_ROUTE_FALLBACK).artifact
        assert learned == fallback
        # One file on disk, not two copies of one booster under two names.
        assert published.artifact_filenames == ("candidate-index.json", "ranker-learned.txt")
        bundle = ServingArtifactBundle.load(bundle_dir / "manifest.json")
        assert bundle.rankers[RANKER_ROUTE_LEARNED] is bundle.rankers[RANKER_ROUTE_FALLBACK]


class TestSasrecBundle:
    def test_a_published_sasrec_bundle_loads_back_with_every_required_role(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"

        publish_bundle(_sasrec_spec(sources), output_dir=bundle_dir)

        manifest = ServingManifest.load(bundle_dir / "manifest.json")
        assert manifest.retriever.family == RETRIEVER_FAMILY_SASREC
        assert sorted(manifest.retriever.artifacts) == [
            "config",
            "encoder",
            "popularity",
            "vocabulary",
        ]
        assert manifest.retriever.version == "sasrec-v1"
        assert manifest.retriever.params["max_sequence_length"] == 50
        assert manifest.retriever.params["index_type"] == INDEX_TYPE_FLAT_IP_EXACT

    def test_the_loader_s_companion_file_is_baked_and_checksum_pinned(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """``sasrec-manifest.json`` is read by fixed name and was pinned by nothing.

        The sidecar opens it to find out which encoder to load. Declaring it
        under a role is what puts its checksum in the serving manifest, so a
        swapped companion is caught by the same machinery as a swapped encoder.
        """
        bundle_dir = tmp_path / "bundle"

        published = publish_bundle(_sasrec_spec(sources), output_dir=bundle_dir)

        assert "sasrec-manifest.json" in published.artifact_filenames
        pinned = published.manifest.retriever.artifacts["config"]
        assert pinned.filename == "sasrec-manifest.json"

        (bundle_dir / "sasrec-manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="checksum mismatch"):
            ServingManifest.load(bundle_dir / "manifest.json")

    def test_two_manifests_naming_different_encoders_are_refused_at_publish(
        self, tmp_path: Path, sources: Path
    ) -> None:
        spec = _sasrec_spec(sources, model_filename="some-other-encoder.zip")

        with pytest.raises(BundlePublishError, match="never pinned the checksum of"):
            publish_bundle(spec, output_dir=tmp_path / "bundle")

    def test_a_sequence_bundle_without_the_companion_file_is_refused_at_publish(
        self, tmp_path: Path, sources: Path
    ) -> None:
        files = _sasrec_sources(sources)
        spec = BundleSpec(
            kind=BUNDLE_KIND_SERVED,
            tenant_id="demo",
            feature_version=FEATURE_VERSION,
            trained_at=parse_utc_timestamp(TRAINED_AT),
            retriever=RetrieverSpec(
                family=RETRIEVER_FAMILY_SASREC,
                # Every role present — but the vocabulary and config are
                # standalone files, so nothing lands as sasrec-manifest.json.
                artifacts={
                    "encoder": ArtifactSource(
                        path=files["encoder"], artifact_type="sasrec-encoder", version="sasrec-v1"
                    ),
                    RETRIEVER_ARTIFACT_POPULARITY: ArtifactSource(
                        path=files["popularity"],
                        artifact_type="popularity-order",
                        version="sasrec-v1",
                    ),
                    "vocabulary": ArtifactSource(
                        path=_blob(sources, "sasrec-vocabulary.json"),
                        artifact_type="sasrec-vocabulary",
                        version="sasrec-v1",
                    ),
                    "config": ArtifactSource(
                        path=_blob(sources, "sasrec-config.json"),
                        artifact_type="sasrec-config",
                        version="sasrec-v1",
                    ),
                },
                params={
                    "max_sequence_length": 50,
                    "cold_start_threshold": None,
                    "exclusion_policy": "watched-and-dismissed-excluded-v1",
                    "index_type": INDEX_TYPE_FLAT_IP_EXACT,
                },
            ),
            rankers=_rankers(sources),
            lineage=_sasrec_lineage(),
        )

        with pytest.raises(BundlePublishError, match="must ship sasrec-manifest.json"):
            publish_bundle(spec, output_dir=tmp_path / "bundle")


def _blob(directory: Path, name: str, content: str = "{}") -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


# --- fail-closed publishing -------------------------------------------------


class TestRefusedAtPublishTime:
    def test_a_missing_required_role_is_refused_before_anything_is_written(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """The whole point of publish-time validation.

        Deferred to load time this is a sidecar that will not boot, discovered
        after the bundle has been baked into an image and promoted. The
        assertion on the empty directory is the load-bearing half: a refusal
        that leaves a half-written bundle behind is a refusal somebody will
        later mistake for a bundle.
        """
        bundle_dir = tmp_path / "bundle"

        with pytest.raises(
            BundlePublishError, match=r"needs artifact\(s\) \['vocabulary', 'popularity'\]"
        ):
            publish_bundle(
                _sasrec_spec(sources, roles=("encoder", "config")), output_dir=bundle_dir
            )

        assert not bundle_dir.exists()

    def test_a_missing_route_is_refused(self, tmp_path: Path, sources: Path) -> None:
        spec = _item_item_spec(sources)
        one_route = BundleSpec(
            kind=spec.kind,
            tenant_id=spec.tenant_id,
            feature_version=spec.feature_version,
            trained_at=spec.trained_at,
            retriever=spec.retriever,
            rankers={RANKER_ROUTE_LEARNED: spec.rankers[RANKER_ROUTE_LEARNED]},
            lineage=spec.lineage,
        )

        with pytest.raises(BundlePublishError, match=r"no ranker for route\(s\) \['fallback'\]"):
            publish_bundle(one_route, output_dir=tmp_path / "bundle")

    def test_an_artifact_that_is_not_on_disk_is_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """The open dependency, refused rather than stubbed.

        The full-data fallback booster is another lane's work item. Until it
        exists, a spec naming it must fail loudly at publish; inventing a
        placeholder would put an unmeasured model on the fallback route.
        """
        spec = _item_item_spec(sources)
        missing = BundleSpec(
            kind=spec.kind,
            tenant_id=spec.tenant_id,
            feature_version=spec.feature_version,
            trained_at=spec.trained_at,
            retriever=spec.retriever,
            rankers={
                RANKER_ROUTE_LEARNED: spec.rankers[RANKER_ROUTE_LEARNED],
                RANKER_ROUTE_FALLBACK: RankerSpec(
                    artifact=ArtifactSource(
                        path=sources / "popularity-fallback.txt",
                        artifact_type="lightgbm-lambdarank",
                        version="fallback-v1",
                    )
                ),
            },
            lineage=spec.lineage,
        )

        with pytest.raises(BundlePublishError, match="artifact to bake is missing"):
            publish_bundle(missing, output_dir=tmp_path / "bundle")

    def test_two_different_files_claiming_one_name_are_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        spec = _item_item_spec(sources)
        collided = BundleSpec(
            kind=spec.kind,
            tenant_id=spec.tenant_id,
            feature_version=spec.feature_version,
            trained_at=spec.trained_at,
            retriever=spec.retriever,
            rankers={
                RANKER_ROUTE_LEARNED: spec.rankers[RANKER_ROUTE_LEARNED],
                RANKER_ROUTE_FALLBACK: RankerSpec(
                    artifact=ArtifactSource(
                        path=_booster(sources, "other.txt"),
                        artifact_type="lightgbm-lambdarank",
                        version="fallback-v1",
                        filename="ranker-learned.txt",
                    )
                ),
            },
            lineage=spec.lineage,
        )

        with pytest.raises(BundlePublishError, match="would both be baked as"):
            publish_bundle(collided, output_dir=tmp_path / "bundle")

    def test_an_unknown_retriever_family_is_refused(self, tmp_path: Path, sources: Path) -> None:
        spec = _item_item_spec(sources)
        unknown = BundleSpec(
            kind=spec.kind,
            tenant_id=spec.tenant_id,
            feature_version=spec.feature_version,
            trained_at=spec.trained_at,
            retriever=RetrieverSpec(
                family="two-tower", artifacts=dict(spec.retriever.artifacts), params={}
            ),
            rankers=dict(spec.rankers),
            lineage=spec.lineage,
        )

        with pytest.raises(BundlePublishError, match="unsupported retriever family 'two-tower'"):
            publish_bundle(unknown, output_dir=tmp_path / "bundle")


# --- checksums --------------------------------------------------------------


class TestTamperedArtifacts:
    def test_one_flipped_byte_in_a_published_artifact_is_caught_by_the_real_loader(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        index_path = bundle_dir / "candidate-index.json"
        corrupted = bytearray(index_path.read_bytes())
        corrupted[0] ^= 0x01
        index_path.write_bytes(bytes(corrupted))

        with pytest.raises(ValueError, match="checksum mismatch for candidate-index.json"):
            ServingManifest.load(bundle_dir / "manifest.json")

    def test_a_flipped_byte_also_fails_the_publisher_s_own_check(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        ranker_path = bundle_dir / "ranker-fallback.txt"
        corrupted = bytearray(ranker_path.read_bytes())
        corrupted[-2] ^= 0x01
        ranker_path.write_bytes(bytes(corrupted))

        with pytest.raises(ValueError, match="checksum mismatch"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)

    def test_an_artifact_deleted_after_publish_is_caught(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        (bundle_dir / "candidate-index.json").unlink()

        with pytest.raises(ValueError, match="artifact is missing"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)

    def test_an_unpinned_file_in_the_bundle_directory_is_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        (bundle_dir / "notes.txt").write_text("smuggled", encoding="utf-8")

        with pytest.raises(BundlePublishError, match="does not pin"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)


# --- lineage and kind -------------------------------------------------------


class TestServedBundleLineage:
    def test_lineage_survives_the_round_trip(self, tmp_path: Path, sources: Path) -> None:
        bundle_dir = tmp_path / "bundle"

        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        assert ServingManifest.load(bundle_dir / "manifest.json").lineage == SERVED_LINEAGE

    def test_a_served_bundle_without_a_run_id_is_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        spec = _item_item_spec(sources, lineage=Lineage(protocol_hash="7f3c"))

        with pytest.raises(BundlePublishError, match=r"must record lineage \['run_id'\]"):
            publish_bundle(spec, output_dir=tmp_path / "bundle")

    def test_a_served_sequence_bundle_must_name_its_native_library_versions(
        self, tmp_path: Path, sources: Path
    ) -> None:
        spec = _sasrec_spec(sources, lineage=SERVED_LINEAGE)

        with pytest.raises(
            BundlePublishError, match=r"lineage \['torch_version', 'faiss_version'\]"
        ):
            publish_bundle(spec, output_dir=tmp_path / "bundle")

    def test_a_demo_fixture_is_not_held_to_the_served_lineage_rule(
        self, tmp_path: Path, sources: Path
    ) -> None:
        # A compact fixture is rebuilt from the demo tenant's ratings and has no
        # MLflow run behind it; requiring one would make the kind meaningless.
        spec = _item_item_spec(sources, kind=BUNDLE_KIND_DEMO_FIXTURE, lineage=Lineage())

        published = publish_bundle(spec, output_dir=tmp_path / "bundle")

        assert published.kind == BUNDLE_KIND_DEMO_FIXTURE
        assert published.manifest.lineage == Lineage()


class TestBundleKindMarker:
    def test_a_published_bundle_is_labelled_and_pins_its_own_manifest(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        marker = BundleKindMarker.load(bundle_dir / KIND_MARKER_FILENAME)

        assert marker.kind == BUNDLE_KIND_SERVED
        assert marker.manifest_filename == "manifest.json"
        assert verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED).kind == (
            BUNDLE_KIND_SERVED
        )

    def test_a_demo_fixture_verified_as_a_served_bundle_is_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """The confusion this marker exists to prevent, exercised end to end."""
        bundle_dir = tmp_path / "bundle"
        publish_bundle(
            _item_item_spec(sources, kind=BUNDLE_KIND_DEMO_FIXTURE, lineage=Lineage()),
            output_dir=bundle_dir,
        )

        with pytest.raises(BundlePublishError, match="is labelled 'demo-fixture'"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)

    def test_a_manifest_swapped_under_a_marker_is_refused(
        self, tmp_path: Path, sources: Path
    ) -> None:
        """Nothing hashes the manifest, so the marker does.

        Every artifact in a bundle is checksummed by the manifest, and the
        manifest by nothing — which is precisely the file that would have to
        change for one bundle to impersonate another.
        """
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)

        document = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        document["tenant_id"] = "another-tenant"
        (bundle_dir / "manifest.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        with pytest.raises(BundlePublishError, match="not the manifest this bundle was labelled"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)

    def test_an_unlabelled_directory_is_refused_when_a_kind_is_required(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)
        (bundle_dir / KIND_MARKER_FILENAME).unlink()

        with pytest.raises(BundlePublishError, match="carries no bundle-kind.json"):
            verify_bundle(bundle_dir, expected_kind=BUNDLE_KIND_SERVED)

        # Still a loadable bundle — the marker is a label, not a checksum the
        # sidecar depends on, and the committed demo fixture predates it.
        assert verify_bundle(bundle_dir).manifest.tenant_id == "demo"


# --- the spec document and the CLI ------------------------------------------


class TestSpecDocument:
    def test_a_spec_on_disk_publishes_a_loadable_bundle(
        self, tmp_path: Path, sources: Path
    ) -> None:
        _candidate_index(sources)
        _booster(sources, "ranker-learned.txt")
        _booster(sources, "ranker-fallback.txt")
        spec_path = tmp_path / "served-bundle.spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "kind": BUNDLE_KIND_SERVED,
                    "tenant_id": "demo",
                    "feature_version": FEATURE_VERSION,
                    "trained_at": TRAINED_AT,
                    "retriever": {
                        "family": RETRIEVER_FAMILY_ITEM_ITEM,
                        "artifacts": {
                            "index": {
                                # Relative, and resolved against the spec's own
                                # directory rather than the working directory.
                                "path": "runs/candidate-index.json",
                                "artifact_type": RETRIEVER_FAMILY_ITEM_ITEM,
                                "version": "itemitem-v1",
                            }
                        },
                    },
                    "rankers": {
                        RANKER_ROUTE_LEARNED: {
                            "artifact": {
                                "path": "runs/ranker-learned.txt",
                                "artifact_type": "lightgbm-lambdarank",
                                "version": "learned-v2",
                            }
                        },
                        RANKER_ROUTE_FALLBACK: {
                            "artifact": {
                                "path": "runs/ranker-fallback.txt",
                                "artifact_type": "lightgbm-lambdarank",
                                "version": "fallback-v1",
                            }
                        },
                    },
                    "lineage": {"protocol_hash": "7f3c", "run_id": "a11af5ed"},
                }
            ),
            encoding="utf-8",
        )
        bundle_dir = tmp_path / "bundle"

        main(["--spec", str(spec_path), "--output-dir", str(bundle_dir)])

        manifest = ServingManifest.load(bundle_dir / "manifest.json")
        assert manifest.retriever.version == "itemitem-v1"
        assert manifest.lineage.run_id == "a11af5ed"

    def test_check_verifies_without_writing(self, tmp_path: Path, sources: Path) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(_item_item_spec(sources), output_dir=bundle_dir)
        before = {path.name: path.read_bytes() for path in bundle_dir.iterdir()}

        main(["--check", "--output-dir", str(bundle_dir), "--kind", BUNDLE_KIND_SERVED])

        assert {path.name: path.read_bytes() for path in bundle_dir.iterdir()} == before

    def test_check_exits_nonzero_on_a_mislabelled_bundle(
        self, tmp_path: Path, sources: Path
    ) -> None:
        bundle_dir = tmp_path / "bundle"
        publish_bundle(
            _item_item_spec(sources, kind=BUNDLE_KIND_DEMO_FIXTURE, lineage=Lineage()),
            output_dir=bundle_dir,
        )

        with pytest.raises(SystemExit, match="is labelled 'demo-fixture'"):
            main(["--check", "--output-dir", str(bundle_dir), "--kind", BUNDLE_KIND_SERVED])

    def test_a_naive_trained_at_is_refused(self) -> None:
        # Same rule demo_artifacts applies to --as-of: a naive timestamp resolves
        # against the build host's zone, and two hosts disagreeing about
        # trained_at is what pinning it prevents.
        with pytest.raises(BundlePublishError, match="must carry a UTC offset"):
            parse_utc_timestamp("2026-09-01T00:00:00")

    def test_a_spec_missing_a_field_names_the_field(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps({"tenant_id": "demo"}), encoding="utf-8")

        with pytest.raises(BundlePublishError, match="must declare its ranker routes"):
            BundleSpec.from_json_file(spec_path)
