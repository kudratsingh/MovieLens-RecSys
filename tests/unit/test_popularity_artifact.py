"""W21: the popularity fill order a SASRec bundle publishes.

Three things have to hold or the artifact is not worth shipping: the bytes are
the same on two builds from the same inputs, what comes back is what went in,
and a bundle whose file no longer matches its recorded checksum is refused
rather than served.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.models.artifacts import (
    _REQUIRED_RETRIEVER_ARTIFACTS,
    RETRIEVER_ARTIFACT_POPULARITY,
    RETRIEVER_FAMILY_SASREC,
    ArtifactRef,
    RetrieverRef,
)
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec import SASRecConfig, SASRecModel
from src.models.candidates.sasrec_artifact import (
    MANIFEST_FILENAME,
    SASRecArtifactManifest,
    export_sasrec,
    load_sasrec,
)
from src.models.popularity_artifact import (
    POPULARITY_ARTIFACT_FILENAME,
    popularity_order_from_counts,
    popularity_order_sha256,
    read_popularity_order,
    serialize_popularity_order,
    write_popularity_order,
)
from src.training.export_popularity_order import build_order

# One movie per popularity tier plus a deliberate three-way tie, so the tiebreak
# is exercised rather than assumed. 300/301/302 all appear twice.
_COUNTS = {700: 5, 300: 2, 302: 2, 301: 2, 900: 1}
_EXPECTED_ORDER = (700, 300, 301, 302, 900)


def _train() -> pd.DataFrame:
    return pd.DataFrame(
        [(user, 100 + user * 10 + item, item) for user in range(1, 5) for item in range(6)],
        columns=["userId", "movieId", "timestamp"],
    )


def _model() -> SASRecModel:
    return SASRecModel(
        config=SASRecConfig(
            max_sequence_length=5,
            hidden_dim=8,
            num_blocks=1,
            num_heads=2,
            feedforward_dim=16,
            dropout=0.2,
            negative_count=2,
            batch_size=8,
            epochs=1,
            faiss_exact=True,
            seed=42,
        ),
        cold_start_threshold=None,
    ).fit(_train())


class TestOrdering:
    def test_ties_break_on_ascending_movie_id(self) -> None:
        assert popularity_order_from_counts(_COUNTS) == _EXPECTED_ORDER

    def test_order_is_independent_of_mapping_insertion_order(self) -> None:
        reversed_counts = dict(reversed(list(_COUNTS.items())))
        assert popularity_order_from_counts(reversed_counts) == _EXPECTED_ORDER

    def test_agrees_with_the_popularity_model_on_everything_but_ties(self) -> None:
        """Same multiset, same non-increasing counts — only the tiebreak is new.

        `PopularityModel.fit` sorts with pandas' default (non-stable) quicksort,
        so its order among equal-count movies is an implementation detail. This
        is what pins that the artifact refines that ordering rather than
        replacing it: serving and evaluation still agree on what "popular" meant.
        """
        model = PopularityModel().fit(_train())
        order = popularity_order_from_counts(model.counts)

        assert set(order) == set(model.ranking)
        counts_in_order = [model.counts[movie_id] for movie_id in order]
        assert counts_in_order == sorted(counts_in_order, reverse=True)
        assert counts_in_order == [model.counts[movie_id] for movie_id in model.ranking]


class TestBytes:
    def test_two_writes_of_the_same_inputs_are_byte_identical(self, tmp_path: Path) -> None:
        first = tmp_path / "first" / POPULARITY_ARTIFACT_FILENAME
        second = tmp_path / "second" / POPULARITY_ARTIFACT_FILENAME
        order = popularity_order_from_counts(_COUNTS)

        first_sha = write_popularity_order(first, order)
        second_sha = write_popularity_order(second, order)

        assert first.read_bytes() == second.read_bytes()
        assert first_sha == second_sha == popularity_order_sha256(order)

    def test_payload_is_canonical_and_carries_no_provenance(self) -> None:
        data = serialize_popularity_order(_EXPECTED_ORDER)

        assert data == b'{"movie_ids":[700,300,301,302,900],"schema_version":1}'
        assert set(json.loads(data)) == {"schema_version", "movie_ids"}

    def test_write_refuses_to_overwrite(self, tmp_path: Path) -> None:
        path = tmp_path / POPULARITY_ARTIFACT_FILENAME
        write_popularity_order(path, _EXPECTED_ORDER)

        with pytest.raises(FileExistsError):
            write_popularity_order(path, _EXPECTED_ORDER)

    def test_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / POPULARITY_ARTIFACT_FILENAME
        digest = write_popularity_order(path, _EXPECTED_ORDER)

        assert read_popularity_order(path, expected_sha256=digest) == _EXPECTED_ORDER

    def test_read_refuses_a_checksum_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / POPULARITY_ARTIFACT_FILENAME
        digest = write_popularity_order(path, _EXPECTED_ORDER)
        path.write_bytes(serialize_popularity_order(reversed(_EXPECTED_ORDER)))

        with pytest.raises(ValueError, match="checksum mismatch"):
            read_popularity_order(path, expected_sha256=digest)

    @pytest.mark.parametrize(
        "payload",
        [
            b'{"movie_ids":[1,2],"schema_version":2}',
            b'{"schema_version":1}',
            b'{"movie_ids":[1,1],"schema_version":1}',
            b'{"movie_ids":["1"],"schema_version":1}',
            b"[1,2,3]",
            b"not json",
        ],
        ids=["schema", "missing", "duplicate", "not-ints", "not-object", "not-json"],
    )
    def test_read_refuses_a_malformed_payload(self, tmp_path: Path, payload: bytes) -> None:
        path = tmp_path / POPULARITY_ARTIFACT_FILENAME
        path.write_bytes(payload)

        with pytest.raises(ValueError):
            read_popularity_order(path)

    def test_serialize_refuses_duplicates(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            serialize_popularity_order([1, 2, 1])


class TestInsideTheSasrecBundle:
    def test_export_writes_the_order_and_pins_it(self, tmp_path: Path) -> None:
        model = _model()
        manifest = export_sasrec(model, tmp_path / "run")
        path = tmp_path / "run" / POPULARITY_ARTIFACT_FILENAME

        assert manifest.popularity_filename == POPULARITY_ARTIFACT_FILENAME
        assert manifest.popularity_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert read_popularity_order(path) == popularity_order_from_counts(model._popularity.counts)

    def test_export_is_byte_deterministic_across_two_builds(self, tmp_path: Path) -> None:
        first = export_sasrec(_model(), tmp_path / "first")
        second = export_sasrec(_model(), tmp_path / "second")

        assert first.popularity_sha256 == second.popularity_sha256
        assert (tmp_path / "first" / POPULARITY_ARTIFACT_FILENAME).read_bytes() == (
            tmp_path / "second" / POPULARITY_ARTIFACT_FILENAME
        ).read_bytes()

    def test_load_restores_the_fallback(self, tmp_path: Path) -> None:
        model = _model()
        export_sasrec(model, tmp_path / "run")

        loaded = load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

        assert loaded._popularity.ranking == list(
            popularity_order_from_counts(model._popularity.counts)
        )
        # The point of restoring it: a loaded bundle can answer a cold-routed
        # user, which before this it could not.
        assert loaded._popularity.recommend(user_id=999_999, k=3) == loaded._popularity.ranking[:3]

    def test_load_refuses_a_tampered_order(self, tmp_path: Path) -> None:
        export_sasrec(_model(), tmp_path / "run")
        path = tmp_path / "run" / POPULARITY_ARTIFACT_FILENAME
        order = read_popularity_order(path)
        path.write_bytes(serialize_popularity_order(reversed(order)))

        with pytest.raises(ValueError, match="checksum mismatch"):
            load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

    def test_load_refuses_a_missing_order(self, tmp_path: Path) -> None:
        export_sasrec(_model(), tmp_path / "run")
        (tmp_path / "run" / POPULARITY_ARTIFACT_FILENAME).unlink()

        with pytest.raises((ValueError, FileNotFoundError)):
            load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

    def test_a_manifest_declaring_half_the_pair_is_refused(self, tmp_path: Path) -> None:
        export_sasrec(_model(), tmp_path / "run")
        manifest_path = tmp_path / "run" / MANIFEST_FILENAME
        raw = json.loads(manifest_path.read_text())
        raw["popularity_sha256"] = None
        manifest_path.write_text(json.dumps(raw, sort_keys=True, indent=2) + "\n")

        with pytest.raises(ValueError, match="together"):
            SASRecArtifactManifest.load(manifest_path)

    def test_a_legacy_artifact_still_loads_and_picks_the_order_up_by_name(
        self, tmp_path: Path
    ) -> None:
        """Artifacts exported before the manifest fields existed keep working.

        The pinned full-data encoder is one of them, and its manifest cannot be
        rewritten — a published bundle pins those bytes under the `vocabulary`
        and `config` roles. So the fill order is attached by filename there, and
        the serving manifest is what pins it.
        """
        export_sasrec(_model(), tmp_path / "run")
        manifest_path = tmp_path / "run" / MANIFEST_FILENAME
        raw = json.loads(manifest_path.read_text())
        expected = raw.pop("popularity_sha256")
        raw.pop("popularity_filename")
        manifest_path.write_text(json.dumps(raw, sort_keys=True, indent=2) + "\n")

        manifest = SASRecArtifactManifest.load(manifest_path)
        loaded = load_sasrec(manifest_path)

        assert manifest.popularity_filename is None
        assert manifest.popularity_sha256 is None
        assert (
            popularity_order_sha256(loaded._popularity.ranking) == expected
        ), "the order is still read, just pinned one layer up"

    def test_export_refuses_a_model_with_no_fitted_fallback(self, tmp_path: Path) -> None:
        model = _model()
        model._popularity = PopularityModel()

        with pytest.raises(ValueError, match="popularity fallback is unfitted"):
            export_sasrec(model, tmp_path / "run")


class TestServingManifestRole:
    def test_the_role_is_required_for_sasrec(self) -> None:
        assert (
            RETRIEVER_ARTIFACT_POPULARITY in _REQUIRED_RETRIEVER_ARTIFACTS[RETRIEVER_FAMILY_SASREC]
        )

    def _retriever(self, artifacts: dict[str, ArtifactRef]) -> RetrieverRef:
        return RetrieverRef(
            family=RETRIEVER_FAMILY_SASREC,
            artifacts=artifacts,
            params={
                "max_sequence_length": 5,
                "cold_start_threshold": 10,
                "exclusion_policy": "watched-and-dismissed-excluded-v1",
                "index_type": "flat-ip-exact",
            },
        )

    def _bundle(self, tmp_path: Path) -> tuple[Path, dict[str, ArtifactRef]]:
        directory = tmp_path / "run"
        manifest = export_sasrec(_model(), directory)
        from src.models.artifacts import file_sha256

        encoder = ArtifactRef(
            artifact_type="sasrec-encoder",
            version="sasrec-test",
            filename=manifest.model_filename,
            sha256=manifest.model_sha256,
        )
        sasrec_manifest_sha = file_sha256(directory / MANIFEST_FILENAME)
        artifacts = {
            "encoder": encoder,
            "vocabulary": ArtifactRef(
                artifact_type="sasrec-vocabulary",
                version="sasrec-test",
                filename=MANIFEST_FILENAME,
                sha256=sasrec_manifest_sha,
            ),
            "config": ArtifactRef(
                artifact_type="sasrec-config",
                version="sasrec-test",
                filename=MANIFEST_FILENAME,
                sha256=sasrec_manifest_sha,
            ),
            RETRIEVER_ARTIFACT_POPULARITY: ArtifactRef(
                artifact_type="popularity-order",
                version="sasrec-test",
                filename=POPULARITY_ARTIFACT_FILENAME,
                sha256=manifest.popularity_sha256 or "",
            ),
        }
        return directory, artifacts

    def test_a_complete_bundle_validates(self, tmp_path: Path) -> None:
        directory, artifacts = self._bundle(tmp_path)

        self._retriever(artifacts).validate(directory)

    def test_a_bundle_without_the_order_is_refused(self, tmp_path: Path) -> None:
        directory, artifacts = self._bundle(tmp_path)
        del artifacts[RETRIEVER_ARTIFACT_POPULARITY]

        with pytest.raises(ValueError, match="missing artifact"):
            self._retriever(artifacts).validate(directory)

    def test_a_bundle_whose_order_checksum_mismatches_is_refused(self, tmp_path: Path) -> None:
        directory, artifacts = self._bundle(tmp_path)
        path = directory / POPULARITY_ARTIFACT_FILENAME
        path.write_bytes(serialize_popularity_order(reversed(read_popularity_order(path))))

        with pytest.raises(ValueError, match="checksum mismatch"):
            self._retriever(artifacts).validate(directory)


class TestExportEntrypoint:
    def test_build_order_matches_the_evaluation_fallback(self) -> None:
        """The CLI's ordering is the one `PopularityModel` would have produced.

        Built from the split's own train slice rather than the whole frame — the
        thing the evaluation fits on — so a leak into the ordering shows up here
        as a mismatch.
        """
        from src.data.split import temporal_split

        ratings = pd.DataFrame(
            [
                (user, 100 + (user * item) % 7, 1_000 + user * 100 + item)
                for user in range(1, 30)
                for item in range(8)
            ],
            columns=["userId", "movieId", "timestamp"],
        )
        split = temporal_split(ratings)

        order = build_order(ratings, expect_rows=len(ratings), expect_cutoff=split.cutoff)

        assert order == popularity_order_from_counts(PopularityModel().fit(split.train).counts)

    def test_build_order_refuses_a_frame_of_the_wrong_size(self) -> None:
        ratings = pd.DataFrame(
            [(1, 10, 1), (1, 11, 2), (2, 10, 3)], columns=["userId", "movieId", "timestamp"]
        )

        with pytest.raises(ValueError, match="expected 25,000,095 ratings rows"):
            build_order(ratings, expect_rows=25_000_095)

    def test_build_order_refuses_a_moved_cutoff(self) -> None:
        ratings = pd.DataFrame(
            [(user, 10 + user, user * 10) for user in range(1, 20)],
            columns=["userId", "movieId", "timestamp"],
        )

        with pytest.raises(ValueError, match="expected split cutoff"):
            build_order(ratings, expect_cutoff=1)
