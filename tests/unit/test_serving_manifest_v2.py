"""Schema 2 serving manifests, and the schema 1 documents that still have to load.

The two shapes are exercised side by side on purpose. A rollback is an image
rollback (ADR 0013), so at any moment a running sidecar may be reading a bundle
published before the retriever/route split — "both versions load" is a property
of the deployment, not a courtesy to old tests.
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
    RETRIEVER_FAMILY_ITEM_ITEM,
    RETRIEVER_FAMILY_SASREC,
    ArtifactRef,
    CandidateIndex,
    Lineage,
    RankerRef,
    RetrieverRef,
    ServingArtifactBundle,
    ServingManifest,
    file_sha256,
)
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig

TRAINED_AT = "2026-09-01T00:00:00+00:00"
FEATURE_VERSION = "feast-phase3-v1"


# --- fixtures on disk -------------------------------------------------------
#
# Every artifact a manifest points at is checksummed, so the tests that are not
# about a model's contents write the cheapest file that can carry a hash.


def _blob(path: Path, *, artifact_type: str, version: str, content: str = "x") -> ArtifactRef:
    path.write_text(content, encoding="utf-8")
    return ArtifactRef(
        artifact_type=artifact_type,
        version=version,
        filename=path.name,
        sha256=file_sha256(path),
    )


def _candidate_index(path: Path, *, version: str = "demo-itemitem-v1") -> ArtifactRef:
    CandidateIndex.build({1: {1, 2}, 2: {1, 3}}).write(path)
    return ArtifactRef(
        artifact_type=RETRIEVER_FAMILY_ITEM_ITEM,
        version=version,
        filename=path.name,
        sha256=file_sha256(path),
    )


def _unnamed_booster(path: Path, *, n_features: int = len(FEATURE_COLUMNS), seed: int = 0) -> None:
    """Train the way ``LGBMRanker.fit`` does: from a bare matrix, so no names."""
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
            "seed": seed,
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


def _named_booster(path: Path, columns: list[str]) -> None:
    """Train from a named frame, so LightGBM records a real feature order."""
    frame = pd.DataFrame(
        np.arange(len(columns) * 5, dtype=np.float64).reshape(5, len(columns)),
        columns=columns,
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
            frame,
            label=np.array([1, 0, 0, 0, 0], dtype=np.float64),
            group=[5],
            free_raw_data=False,
        ),
        num_boost_round=2,
    )
    booster.save_model(str(path))


def _ranker(path: Path, *, version: str = "demo-lgbm-v1", named: bool = False) -> ArtifactRef:
    if named:
        _named_booster(path, list(FEATURE_COLUMNS))
    else:
        _unnamed_booster(path)
    return ArtifactRef(
        artifact_type="lightgbm-lambdarank",
        version=version,
        filename=path.name,
        sha256=file_sha256(path),
    )


def _sasrec_retriever(directory: Path, **params: Any) -> RetrieverRef:
    settings: dict[str, Any] = {
        "max_sequence_length": 200,
        "cold_start_threshold": 5,
        "exclusion_policy": "watched-and-dismissed-excluded-v1",
        "index_type": INDEX_TYPE_FLAT_IP_EXACT,
    }
    settings.update(params)
    return RetrieverRef(
        family=RETRIEVER_FAMILY_SASREC,
        artifacts={
            "encoder": _blob(
                directory / "sasrec-model.zip",
                artifact_type="sasrec-encoder",
                version="sasrec-v1",
                content="weights",
            ),
            "vocabulary": _blob(
                directory / "sasrec-vocabulary.json",
                artifact_type="sasrec-vocabulary",
                version="sasrec-v1",
                content="[1,2,3]",
            ),
            "config": _blob(
                directory / "sasrec-config.json",
                artifact_type="sasrec-config",
                version="sasrec-v1",
                content="{}",
            ),
        },
        params=settings,
    )


def _v1_manifest(tmp_path: Path) -> ServingManifest:
    return ServingManifest(
        tenant_id="demo",
        candidate=_candidate_index(tmp_path / "candidate-index.json"),
        ranker=_ranker(tmp_path / "ranker.txt"),
        feature_version=FEATURE_VERSION,
        trained_at=TRAINED_AT,
    )


def _v2_manifest(
    tmp_path: Path,
    *,
    retriever: RetrieverRef | None = None,
    rankers: dict[str, RankerRef] | None = None,
    lineage: Lineage = Lineage(),
) -> ServingManifest:
    if retriever is None:
        retriever = RetrieverRef(
            family=RETRIEVER_FAMILY_ITEM_ITEM,
            artifacts={"index": _candidate_index(tmp_path / "candidate-index.json")},
        )
    if rankers is None:
        learned = RankerRef(artifact=_ranker(tmp_path / "ranker.txt"))
        rankers = {RANKER_ROUTE_LEARNED: learned, RANKER_ROUTE_FALLBACK: learned}
    return ServingManifest(
        tenant_id="demo",
        retriever=retriever,
        rankers=rankers,
        feature_version=FEATURE_VERSION,
        trained_at=TRAINED_AT,
        lineage=lineage,
    )


def _published(manifest: ServingManifest, tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    manifest.write(path)
    return path


def _edit(path: Path, mutate: Any) -> None:
    """Rewrite a published manifest document, bypassing the writer's validation."""
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- schema 1 still loads ---------------------------------------------------


class TestSchemaOneNormalisation:
    def test_a_v1_manifest_loads_and_normalises_both_routes_onto_one_ranker(
        self, tmp_path: Path
    ) -> None:
        path = _published(_v1_manifest(tmp_path), tmp_path)

        manifest = ServingManifest.load(path)

        assert manifest.schema_version == 1
        assert manifest.retriever.family == RETRIEVER_FAMILY_ITEM_ITEM
        assert manifest.retriever.version == "demo-itemitem-v1"
        assert sorted(manifest.rankers) == [RANKER_ROUTE_FALLBACK, RANKER_ROUTE_LEARNED]
        # The same ref on both routes, not two copies that could drift apart.
        assert (
            manifest.route(RANKER_ROUTE_LEARNED).artifact
            == manifest.route(RANKER_ROUTE_FALLBACK).artifact
        )
        assert manifest.ranker_version == "demo-lgbm-v1"
        assert manifest.route(RANKER_ROUTE_FALLBACK).feature_columns == tuple(FEATURE_COLUMNS)

    def test_a_v1_manifest_keeps_the_v1_spelling_for_callers_that_predate_the_split(
        self, tmp_path: Path
    ) -> None:
        manifest = ServingManifest.load(_published(_v1_manifest(tmp_path), tmp_path))

        assert manifest.candidate.filename == "candidate-index.json"
        assert manifest.ranker.filename == "ranker.txt"

    def test_a_v1_bundle_loads_one_booster_and_serves_it_on_both_routes(
        self, tmp_path: Path
    ) -> None:
        bundle = ServingArtifactBundle.load(_published(_v1_manifest(tmp_path), tmp_path))

        assert bundle.candidates.retrieve([1], limit=2).movie_ids == [2, 3]
        assert bundle.rankers[RANKER_ROUTE_LEARNED] is bundle.ranker
        # One file, one booster in memory.
        assert bundle.rankers[RANKER_ROUTE_FALLBACK] is bundle.ranker

    def test_lineage_is_refused_on_the_shape_that_cannot_write_it_back(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="nowhere to write it back"):
            ServingManifest(
                tenant_id="demo",
                candidate=_candidate_index(tmp_path / "candidate-index.json"),
                ranker=_ranker(tmp_path / "ranker.txt"),
                feature_version=FEATURE_VERSION,
                trained_at=TRAINED_AT,
                lineage=Lineage(run_id="abc"),
            )

    def test_declaring_both_shapes_at_once_is_refused(self, tmp_path: Path) -> None:
        candidate = _candidate_index(tmp_path / "candidate-index.json")
        with pytest.raises(ValueError, match="never both"):
            ServingManifest(
                tenant_id="demo",
                candidate=candidate,
                retriever=RetrieverRef(
                    family=RETRIEVER_FAMILY_ITEM_ITEM, artifacts={"index": candidate}
                ),
                feature_version=FEATURE_VERSION,
                trained_at=TRAINED_AT,
            )


# --- schema 2 ---------------------------------------------------------------


class TestSchemaTwo:
    def test_per_route_rankers_load_and_are_kept_apart(self, tmp_path: Path) -> None:
        learned = RankerRef(artifact=_ranker(tmp_path / "ranker.txt", version="learned-v2"))
        fallback = RankerRef(
            artifact=_ranker(tmp_path / "fallback.txt", version="fallback-v1"),
        )
        path = _published(
            _v2_manifest(
                tmp_path,
                rankers={RANKER_ROUTE_LEARNED: learned, RANKER_ROUTE_FALLBACK: fallback},
            ),
            tmp_path,
        )

        bundle = ServingArtifactBundle.load(path)

        assert bundle.manifest.schema_version == 2
        assert bundle.manifest.ranker_version == "learned-v2"
        assert bundle.manifest.route(RANKER_ROUTE_FALLBACK).artifact.version == "fallback-v1"
        # Two files, two boosters — the fallback must not silently be the
        # learned model under another name.
        assert bundle.rankers[RANKER_ROUTE_LEARNED] is not bundle.rankers[RANKER_ROUTE_FALLBACK]

    def test_the_two_routes_may_declare_different_feature_contracts(self, tmp_path: Path) -> None:
        narrow = ["item_popularity_all_time", "item_age_days", "user_interaction_count"]
        fallback_path = tmp_path / "fallback.txt"
        _unnamed_booster(fallback_path, n_features=len(narrow))
        rankers = {
            RANKER_ROUTE_LEARNED: RankerRef(artifact=_ranker(tmp_path / "ranker.txt")),
            RANKER_ROUTE_FALLBACK: RankerRef(
                artifact=ArtifactRef(
                    artifact_type="lightgbm-lambdarank",
                    version="fallback-v0",
                    filename=fallback_path.name,
                    sha256=file_sha256(fallback_path),
                ),
                feature_columns=tuple(narrow),
            ),
        }
        path = _published(_v2_manifest(tmp_path, rankers=rankers), tmp_path)

        manifest = ServingManifest.load(path)

        assert manifest.route(RANKER_ROUTE_LEARNED).feature_columns == tuple(FEATURE_COLUMNS)
        assert manifest.route(RANKER_ROUTE_FALLBACK).feature_columns == tuple(narrow)

    def test_a_sasrec_retriever_declares_its_artifacts_and_retrieval_params(
        self, tmp_path: Path
    ) -> None:
        path = _published(_v2_manifest(tmp_path, retriever=_sasrec_retriever(tmp_path)), tmp_path)

        manifest = ServingManifest.load(path)

        assert manifest.retriever.family == RETRIEVER_FAMILY_SASREC
        assert sorted(manifest.retriever.artifacts) == ["config", "encoder", "vocabulary"]
        assert manifest.retriever.version == "sasrec-v1"
        assert manifest.retriever.params["max_sequence_length"] == 200
        assert manifest.retriever.params["cold_start_threshold"] == 5

    def test_lineage_survives_a_publish(self, tmp_path: Path) -> None:
        lineage = Lineage(
            protocol_hash="7f3c",
            raw_data_revision="dvc:9a1",
            run_id="a11af5ed0f0745f68572407237cfa4b9",
            code_sha="8574488",
            faiss_version="1.8.0",
            torch_version="2.4.1",
        )
        path = _published(_v2_manifest(tmp_path, lineage=lineage), tmp_path)

        assert ServingManifest.load(path).lineage == lineage

    def test_an_unknown_lineage_field_is_refused_rather_than_dropped(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)
        _edit(path, lambda document: document["lineage"].update({"sklearn_version": "1.5"}))

        with pytest.raises(ValueError, match="unknown field"):
            ServingManifest.load(path)


# --- fail-closed validation -------------------------------------------------


class TestRankerRoutes:
    def test_a_missing_route_is_refused_at_validate_time(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)
        _edit(path, lambda document: document["rankers"].pop(RANKER_ROUTE_FALLBACK))

        with pytest.raises(ValueError, match=r"no ranker for route\(s\) \['fallback'\]"):
            ServingManifest.load(path)

    def test_an_unknown_route_is_refused(self, tmp_path: Path) -> None:
        manifest = _v2_manifest(tmp_path)
        path = _published(manifest, tmp_path)
        _edit(
            path,
            lambda document: document["rankers"].update(
                {"shadow": document["rankers"][RANKER_ROUTE_LEARNED]}
            ),
        )

        with pytest.raises(ValueError, match="unknown ranker route"):
            ServingManifest.load(path)


class TestRankerFeatureContract:
    def test_a_declared_order_that_disagrees_with_the_booster_file_is_refused(
        self, tmp_path: Path
    ) -> None:
        # The booster carries real names, so the file can be held to the order
        # and not only to the width.
        ranker = _ranker(tmp_path / "ranker.txt", named=True)
        permuted = [FEATURE_COLUMNS[1], FEATURE_COLUMNS[0], *FEATURE_COLUMNS[2:]]
        path = _published(
            _v2_manifest(
                tmp_path,
                rankers={
                    RANKER_ROUTE_LEARNED: RankerRef(
                        artifact=ranker, feature_columns=tuple(permuted)
                    ),
                    RANKER_ROUTE_FALLBACK: RankerRef(artifact=ranker),
                },
            ),
            tmp_path,
        )

        with pytest.raises(ValueError, match="declares feature order"):
            ServingManifest.load(path)

    def test_a_declared_order_that_matches_the_booster_file_is_accepted(
        self, tmp_path: Path
    ) -> None:
        ranker = _ranker(tmp_path / "ranker.txt", named=True)
        path = _published(
            _v2_manifest(
                tmp_path,
                rankers={
                    RANKER_ROUTE_LEARNED: RankerRef(artifact=ranker),
                    RANKER_ROUTE_FALLBACK: RankerRef(artifact=ranker),
                },
            ),
            tmp_path,
        )

        assert ServingManifest.load(path).ranker_version == "demo-lgbm-v1"

    def test_a_declared_width_that_disagrees_with_the_booster_file_is_refused(
        self, tmp_path: Path
    ) -> None:
        # The width is the half of the contract that holds even for a booster
        # trained from a bare matrix, which is every booster this repo trains.
        ranker = _ranker(tmp_path / "ranker.txt")
        path = _published(
            _v2_manifest(
                tmp_path,
                rankers={
                    RANKER_ROUTE_LEARNED: RankerRef(
                        artifact=ranker, feature_columns=tuple(FEATURE_COLUMNS[:3])
                    ),
                    RANKER_ROUTE_FALLBACK: RankerRef(artifact=ranker),
                },
            ),
            tmp_path,
        )

        with pytest.raises(ValueError, match="declares 3 features but"):
            ServingManifest.load(path)

    def test_an_unnamed_booster_can_only_be_held_to_its_width(self, tmp_path: Path) -> None:
        # Pinned rather than asserted as desirable: LightGBM stores
        # `Column_0 … Column_n` for a booster trained from a bare matrix, which
        # is a placeholder and not a feature order, so a same-width reordering
        # is undetectable. Training from a named frame is what closes this.
        ranker = _ranker(tmp_path / "ranker.txt")
        permuted = [FEATURE_COLUMNS[1], FEATURE_COLUMNS[0], *FEATURE_COLUMNS[2:]]
        path = _published(
            _v2_manifest(
                tmp_path,
                rankers={
                    RANKER_ROUTE_LEARNED: RankerRef(
                        artifact=ranker, feature_columns=tuple(permuted)
                    ),
                    RANKER_ROUTE_FALLBACK: RankerRef(artifact=ranker),
                },
            ),
            tmp_path,
        )

        assert ServingManifest.load(path).route(RANKER_ROUTE_LEARNED).feature_columns == tuple(
            permuted
        )


class TestRetrieverValidation:
    def test_an_index_type_other_than_exact_search_is_refused(self, tmp_path: Path) -> None:
        retriever = _sasrec_retriever(tmp_path, index_type="ivf-flat-ip")
        path = _published(_v2_manifest(tmp_path, retriever=retriever), tmp_path)

        with pytest.raises(ValueError, match="only 'flat-ip-exact' is accepted"):
            ServingManifest.load(path)

    def test_a_sasrec_retriever_that_declares_no_index_type_is_refused(
        self, tmp_path: Path
    ) -> None:
        retriever = RetrieverRef(
            family=RETRIEVER_FAMILY_SASREC,
            artifacts=dict(_sasrec_retriever(tmp_path).artifacts),
            params={
                "max_sequence_length": 200,
                "cold_start_threshold": 5,
                "exclusion_policy": "watched-and-dismissed-excluded-v1",
            },
        )
        path = _published(_v2_manifest(tmp_path, retriever=retriever), tmp_path)

        with pytest.raises(ValueError, match=r"does not declare \['index_type'\]"):
            ServingManifest.load(path)

    def test_a_sasrec_retriever_missing_the_vocabulary_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path, retriever=_sasrec_retriever(tmp_path)), tmp_path)
        _edit(path, lambda document: document["retriever"]["artifacts"].pop("vocabulary"))

        with pytest.raises(ValueError, match=r"missing artifact\(s\) \['vocabulary'\]"):
            ServingManifest.load(path)

    def test_a_cold_start_threshold_of_null_is_a_setting_not_an_omission(
        self, tmp_path: Path
    ) -> None:
        retriever = _sasrec_retriever(tmp_path, cold_start_threshold=None)
        path = _published(_v2_manifest(tmp_path, retriever=retriever), tmp_path)

        assert ServingManifest.load(path).retriever.params["cold_start_threshold"] is None

    def test_an_unknown_retriever_family_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)
        _edit(path, lambda document: document["retriever"].update({"family": "two-tower"}))

        with pytest.raises(ValueError, match="unsupported retriever family"):
            ServingManifest.load(path)

    def test_a_bundle_refuses_to_load_a_family_this_build_cannot_serve(
        self, tmp_path: Path
    ) -> None:
        path = _published(_v2_manifest(tmp_path, retriever=_sasrec_retriever(tmp_path)), tmp_path)

        with pytest.raises(ValueError, match="loads only the 'item-item-cosine'"):
            ServingArtifactBundle.load(path)


class TestChecksums:
    def test_a_tampered_v1_artifact_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v1_manifest(tmp_path), tmp_path)

        (tmp_path / "candidate-index.json").write_text("tampered", encoding="utf-8")

        with pytest.raises(ValueError, match="checksum mismatch"):
            ServingManifest.load(path)

    def test_a_tampered_v2_ranker_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)

        (tmp_path / "ranker.txt").write_text("tampered", encoding="utf-8")

        with pytest.raises(ValueError, match="checksum mismatch"):
            ServingManifest.load(path)

    def test_a_tampered_sasrec_encoder_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path, retriever=_sasrec_retriever(tmp_path)), tmp_path)

        (tmp_path / "sasrec-model.zip").write_text("tampered", encoding="utf-8")

        with pytest.raises(ValueError, match="checksum mismatch"):
            ServingManifest.load(path)

    def test_a_missing_artifact_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)

        (tmp_path / "candidate-index.json").unlink()

        with pytest.raises(ValueError, match="artifact is missing"):
            ServingManifest.load(path)


class TestRoundTrip:
    def test_a_v1_document_round_trips_byte_for_byte(self, tmp_path: Path) -> None:
        path = _published(_v1_manifest(tmp_path), tmp_path)
        before = path.read_bytes()

        ServingManifest.load(path).write(path)

        assert path.read_bytes() == before

    def test_a_v2_document_round_trips_byte_for_byte(self, tmp_path: Path) -> None:
        manifest = _v2_manifest(
            tmp_path,
            retriever=_sasrec_retriever(tmp_path),
            lineage=Lineage(run_id="a11af5ed", torch_version="2.4.1"),
        )
        path = _published(manifest, tmp_path)
        before = path.read_bytes()

        assert ServingManifest.load(path).to_dict() == manifest.to_dict()
        ServingManifest.load(path).write(path)
        assert path.read_bytes() == before

    def test_a_v1_document_does_not_grow_schema_two_keys(self, tmp_path: Path) -> None:
        path = _published(_v1_manifest(tmp_path), tmp_path)

        document = json.loads(path.read_text(encoding="utf-8"))

        assert sorted(document) == [
            "candidate",
            "feature_columns",
            "feature_version",
            "ranker",
            "schema_version",
            "tenant_id",
            "trained_at",
        ]


class TestSchemaVersion:
    def test_an_unsupported_schema_version_is_refused(self, tmp_path: Path) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)
        _edit(path, lambda document: document.update({"schema_version": 3}))

        with pytest.raises(ValueError, match="unsupported serving manifest schema 3"):
            ServingManifest.load(path)

    def test_a_manifest_that_declares_neither_shape_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must declare a retriever"):
            ServingManifest(
                tenant_id="demo",
                feature_version=FEATURE_VERSION,
                trained_at=TRAINED_AT,
            )

    def test_the_serving_feature_contract_is_still_pinned_at_the_manifest_level(
        self, tmp_path: Path
    ) -> None:
        path = _published(_v2_manifest(tmp_path), tmp_path)
        _edit(path, lambda document: document["feature_columns"].reverse())

        with pytest.raises(ValueError, match="does not match the serving feature contract"):
            ServingManifest.load(path)


def test_the_ranker_loader_still_refuses_a_booster_of_the_wrong_width(tmp_path: Path) -> None:
    """Where a per-route contract of a different width currently stops.

    The manifest is happy to *declare* a narrower fallback contract, and
    validates it against that booster's own file. Scoring is a separate
    question: ``LGBMRanker`` slices with the global ``FEATURE_COLUMNS``, so a
    bundle whose fallback booster is not that width fails at load rather than
    quietly scoring against the wrong columns.
    """
    path = tmp_path / "narrow.txt"
    _unnamed_booster(path, n_features=3)

    with pytest.raises(ValueError, match="serving contract requires"):
        LGBMRanker.load_model(path, config=LGBMRankerConfig())
