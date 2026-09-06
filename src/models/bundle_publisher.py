"""Publishing a schema 2 serving bundle from artifacts someone else trained.

The schema 2 manifest and the sidecar loader that realises one have both been on
``main`` for a while, and between them there was nothing that could *write* one.
``src/training/demo_artifacts.py`` publishes the compact demo fixture and can
only ever publish schema 1 — it fits both stages from the demo tenant's ratings,
so it is a trainer that happens to publish. A model trained on the full 25M
cannot be produced that way, which meant a sequence bundle could be loaded, but
never assembled; and a bundle that cannot be assembled cannot be put under the
k6 latency gate.

This module is the missing half, and it is deliberately *only* a publisher. It
trains nothing and imports no trainer. It takes files somebody else produced,
copies them into a bundle directory, hashes what it wrote, and writes a manifest
that the real loader accepts — then proves that by loading it back.

**Where the payload comes from.** Nowhere in here. Which encoder and which
boosters get baked is an owner decision recorded in a spec document
(``BundleSpec.from_json_file``), not a constant in this file. The one thing this
module hardcodes is the *shape* of a valid bundle, which is a property of the
loader rather than of any model.

**Two bundles, and no way to confuse them.** The repo carries the compact demo
fixture in ``infra/model-bundle/`` and, separately, a served bundle built from
full-data artifacts. They are told apart three ways, in increasing order of how
much good they do:

* by directory, so a reader can see it at a glance;
* by schema, because a served bundle is schema 2 with lineage and the demo
  fixture is schema 1, which *cannot* carry lineage (``ServingManifest`` refuses
  it);
* by a ``bundle-kind.json`` marker that names the kind and pins the manifest's
  own checksum — the one thing the manifest cannot pin about itself. Copy a
  bundle into the other kind's directory and the marker either disagrees about
  the kind or about the manifest hash, and ``verify_bundle`` refuses.

**Every byte in a bundle is pinned.** There is no "extra files" escape hatch:
anything that has to ship alongside the artifacts is declared as an artifact
with a role, so its checksum is in the manifest. That is not tidiness. A SASRec
bundle needs ``sasrec-manifest.json`` beside the encoder archive, the sidecar
reads it by fixed name, and until now nothing pinned it — so the file that says
which encoder to load could be swapped without any checksum noticing.

The spec document this reads looks like::

    {
      "kind": "served",
      "tenant_id": "demo",
      "feature_version": "feast-phase3-v1",
      "trained_at": "2026-09-01T00:00:00+00:00",
      "retriever": {
        "family": "sasrec",
        "params": {"max_sequence_length": 50, "cold_start_threshold": 5,
                   "exclusion_policy": "watched-and-dismissed-excluded-v1",
                   "index_type": "flat-ip-exact"},
        "artifacts": {
          "encoder":    {"path": "…/sasrec-model.zip",     "artifact_type": "sasrec-encoder",
                         "version": "sasrec-…"},
          "vocabulary": {"path": "…/sasrec-manifest.json", "artifact_type": "sasrec-vocabulary",
                         "version": "sasrec-…"},
          "config":     {"path": "…/sasrec-manifest.json", "artifact_type": "sasrec-config",
                         "version": "sasrec-…"}
        }
      },
      "rankers": {
        "learned":  {"artifact": {"path": "…/ranker-learned.txt",
                                  "artifact_type": "lightgbm-lambdarank", "version": "…"}},
        "fallback": {"artifact": {"path": "…/ranker-fallback.txt",
                                  "artifact_type": "lightgbm-lambdarank", "version": "…"}}
      },
      "lineage": {"protocol_hash": "…", "run_id": "…", "torch_version": "…",
                  "faiss_version": "…"}
    }

Relative ``path`` values resolve against the spec document's own directory, so a
spec committed next to the artifacts it names stays valid wherever the checkout
lives.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.feature_contract import FEATURE_COLUMNS
from src.models.artifacts import (
    # Read out of the manifest module rather than restated: a family that grows
    # a required role there must not be publishable without one here, and the
    # only way to guarantee that is to ask the same table the validator asks.
    # Reaching for the private name is the price of not keeping a second copy
    # that would drift from it.
    _REQUIRED_RETRIEVER_ARTIFACTS,
    RANKER_ROUTE_FALLBACK,
    RANKER_ROUTE_LEARNED,
    RANKER_ROUTES,
    RETRIEVER_FAMILY_SASREC,
    ArtifactRef,
    Lineage,
    RankerRef,
    RetrieverRef,
    ServingArtifactBundle,
    ServingManifest,
    file_sha256,
)

logger = logging.getLogger(__name__)

# What role a bundle directory plays. A served bundle answers production traffic
# and is built from full-data artifacts; a demo fixture is the compact,
# retrain-reproducible bundle in ``infra/model-bundle/``. The distinction has to
# be recorded rather than inferred, because the two are the same file layout and
# the checks that apply to them are not the same ones.
BUNDLE_KIND_SERVED = "served"
BUNDLE_KIND_DEMO_FIXTURE = "demo-fixture"
BUNDLE_KINDS: tuple[str, ...] = (BUNDLE_KIND_SERVED, BUNDLE_KIND_DEMO_FIXTURE)

KIND_MARKER_FILENAME = "bundle-kind.json"
KIND_MARKER_SCHEMA_VERSION = 1

DEFAULT_MANIFEST_NAME = "manifest.json"

# Lineage a served bundle must carry. ``Lineage`` itself keeps every field
# optional, and rightly so — a local rebuild has no MLflow run. A bundle that
# serves production traffic is the opposite case: it has to be traceable to the
# experiment that earned it, or the promotion decision behind it cannot be
# reconstructed later.
_REQUIRED_LINEAGE_FIELDS: tuple[str, ...] = ("protocol_hash", "run_id")

# Extra lineage a family needs because its numbers are native-library-dependent.
# A SASRec retriever's results come out of a torch forward pass and a FAISS
# search, and "which versions was this measured under" is not answerable from
# the encoder weights.
_REQUIRED_LINEAGE_FIELDS_BY_FAMILY: Mapping[str, tuple[str, ...]] = {
    RETRIEVER_FAMILY_SASREC: ("torch_version", "faiss_version"),
}


class BundlePublishError(ValueError):
    """A bundle could not be published, or a published bundle did not verify.

    A ``ValueError`` so it reads the same way as every other refusal in the
    manifest vocabulary, and a distinct type so a caller assembling a bundle can
    tell "your spec is wrong" from a load-time failure deeper down.
    """


@dataclass(frozen=True)
class ArtifactSource:
    """One file to bake, and what the manifest should say about it.

    ``filename`` overrides the name the file takes inside the bundle. It exists
    because two of the loaders care about the name and not the path: a SASRec
    bundle's ``sasrec-manifest.json`` is read by fixed name, and the encoder
    archive it points at must match the name recorded inside it.
    """

    path: Path
    artifact_type: str
    version: str
    filename: str | None = None

    @property
    def target_name(self) -> str:
        """The basename this artifact takes inside the bundle."""
        name = self.filename or self.path.name
        # The same rule ``ArtifactRef.from_dict`` applies when the manifest is
        # read back, checked here because this is the side that turns the name
        # into a path to write to.
        if not name or name in {".", ".."} or Path(name).name != name:
            raise BundlePublishError(f"artifact filename must be a safe basename: {name!r}")
        return name

    @classmethod
    def from_dict(cls, value: Any, *, root: Path, where: str) -> ArtifactSource:
        if not isinstance(value, dict):
            raise BundlePublishError(f"{where} must be an object")
        try:
            path = Path(str(value["path"]))
            return cls(
                path=path if path.is_absolute() else root / path,
                artifact_type=str(value["artifact_type"]),
                version=str(value["version"]),
                filename=None if value.get("filename") is None else str(value["filename"]),
            )
        except KeyError as error:
            raise BundlePublishError(f"{where} is missing {error.args[0]!r}") from error


@dataclass(frozen=True)
class RetrieverSpec:
    """The retrieval stage to bake, keyed by the roles its family declares."""

    family: str
    artifacts: Mapping[str, ArtifactSource]
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @classmethod
    def from_dict(cls, value: Any, *, root: Path) -> RetrieverSpec:
        if not isinstance(value, dict):
            raise BundlePublishError("spec retriever must be an object")
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise BundlePublishError("spec retriever must declare at least one artifact")
        params = value.get("params", {})
        if not isinstance(params, dict):
            raise BundlePublishError("spec retriever params must be an object")
        try:
            family = str(value["family"])
        except KeyError as error:
            raise BundlePublishError(f"spec retriever is missing {error.args[0]!r}") from error
        return cls(
            family=family,
            artifacts={
                str(role): ArtifactSource.from_dict(
                    source, root=root, where=f"spec retriever artifact {role!r}"
                )
                for role, source in artifacts.items()
            },
            params={str(name): setting for name, setting in params.items()},
        )


@dataclass(frozen=True)
class RankerSpec:
    """One route's booster, and the feature order that booster was fit on."""

    artifact: ArtifactSource
    feature_columns: tuple[str, ...] = tuple(FEATURE_COLUMNS)

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_columns", tuple(self.feature_columns))

    @classmethod
    def from_dict(cls, value: Any, *, root: Path, route: str) -> RankerSpec:
        if not isinstance(value, dict):
            raise BundlePublishError(f"spec ranker route {route!r} must be an object")
        try:
            artifact = value["artifact"]
        except KeyError as error:
            raise BundlePublishError(
                f"spec ranker route {route!r} is missing {error.args[0]!r}"
            ) from error
        columns = value.get("feature_columns")
        return cls(
            artifact=ArtifactSource.from_dict(
                artifact, root=root, where=f"spec ranker route {route!r} artifact"
            ),
            feature_columns=(
                tuple(FEATURE_COLUMNS)
                if columns is None
                else tuple(str(column) for column in columns)
            ),
        )


@dataclass(frozen=True)
class BundleSpec:
    """Everything needed to publish one bundle, and nothing about how to train one."""

    kind: str
    tenant_id: str
    feature_version: str
    trained_at: datetime
    retriever: RetrieverSpec
    rankers: Mapping[str, RankerSpec]
    lineage: Lineage = Lineage()
    manifest_name: str = DEFAULT_MANIFEST_NAME

    def __post_init__(self) -> None:
        object.__setattr__(self, "rankers", MappingProxyType(dict(self.rankers)))

    @classmethod
    def from_dict(cls, value: Any, *, root: Path) -> BundleSpec:
        if not isinstance(value, dict):
            raise BundlePublishError("bundle spec must contain an object")
        rankers = value.get("rankers")
        if not isinstance(rankers, dict) or not rankers:
            raise BundlePublishError("bundle spec must declare its ranker routes")
        try:
            return cls(
                kind=str(value.get("kind", BUNDLE_KIND_SERVED)),
                tenant_id=str(value["tenant_id"]),
                feature_version=str(value["feature_version"]),
                trained_at=parse_utc_timestamp(str(value["trained_at"])),
                retriever=RetrieverSpec.from_dict(value["retriever"], root=root),
                rankers={
                    str(route): RankerSpec.from_dict(ref, root=root, route=str(route))
                    for route, ref in rankers.items()
                },
                lineage=Lineage.from_dict(value.get("lineage")),
                manifest_name=str(value.get("manifest_name", DEFAULT_MANIFEST_NAME)),
            )
        except KeyError as error:
            raise BundlePublishError(f"bundle spec is missing {error.args[0]!r}") from error

    @classmethod
    def from_json_file(cls, path: Path) -> BundleSpec:
        """Read a spec, resolving its relative artifact paths against its own directory."""
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BundlePublishError(f"bundle spec {path} is not readable JSON: {error}") from error
        return cls.from_dict(document, root=path.parent)


@dataclass(frozen=True)
class BundleKindMarker:
    """The label that says which role a bundle directory plays.

    It pins the manifest's checksum for one reason: the manifest hashes every
    artifact beside it but nothing hashes the manifest, so a served manifest
    dropped into a demo directory would otherwise verify perfectly and answer
    with the wrong model. That is the exact confusion this file exists to make
    impossible.
    """

    kind: str
    manifest_filename: str
    manifest_sha256: str
    schema_version: int = KIND_MARKER_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> BundleKindMarker:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BundlePublishError(f"{path.name} is not readable JSON: {error}") from error
        if not isinstance(value, dict):
            raise BundlePublishError(f"{path.name} must contain an object")
        try:
            marker = cls(
                kind=str(value["kind"]),
                manifest_filename=str(value["manifest_filename"]),
                manifest_sha256=str(value["manifest_sha256"]),
                schema_version=int(value["schema_version"]),
            )
        except KeyError as error:
            raise BundlePublishError(f"{path.name} is missing {error.args[0]!r}") from error
        if marker.schema_version != KIND_MARKER_SCHEMA_VERSION:
            raise BundlePublishError(
                f"unsupported {path.name} schema {marker.schema_version}; "
                f"expected {KIND_MARKER_SCHEMA_VERSION}"
            )
        if marker.kind not in BUNDLE_KINDS:
            raise BundlePublishError(
                f"{path.name} names unknown bundle kind {marker.kind!r}; "
                f"expected one of {list(BUNDLE_KINDS)}"
            )
        return marker

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "manifest_filename": self.manifest_filename,
            "manifest_sha256": self.manifest_sha256,
        }

    def write(self, path: Path) -> None:
        temporary = path.parent / f".{path.name}.tmp"
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)


@dataclass(frozen=True)
class PublishedBundle:
    """What a publish produced, after it was read back off disk."""

    kind: str
    manifest: ServingManifest
    manifest_path: Path
    artifact_filenames: tuple[str, ...]

    @property
    def bundle_dir(self) -> Path:
        return self.manifest_path.parent


def publish_bundle(
    spec: BundleSpec,
    *,
    output_dir: Path,
    realise: bool = False,
) -> PublishedBundle:
    """Bake the spec's artifacts into ``output_dir`` and verify what was written.

    The order is the order it is for a reason. Everything the spec claims is
    checked before a single byte is copied, so a spec missing an artifact leaves
    no half-written directory behind; then the files are copied; then each
    checksum is taken **from the copy, not the source**, because the copy is what
    a sidecar will hash; then the manifest, then the kind marker; and only then
    is the whole thing loaded back through the real loader.
    """
    _validate_spec(spec)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = _bake_artifacts(spec, output_dir)
    retriever = RetrieverRef(
        family=spec.retriever.family,
        artifacts={
            role: _artifact_ref(source, output_dir / source.target_name)
            for role, source in spec.retriever.artifacts.items()
        },
        params=dict(spec.retriever.params),
    )
    rankers = {
        route: RankerRef(
            artifact=_artifact_ref(ref.artifact, output_dir / ref.artifact.target_name),
            feature_columns=ref.feature_columns,
        )
        for route, ref in spec.rankers.items()
    }
    manifest = ServingManifest(
        tenant_id=spec.tenant_id,
        retriever=retriever,
        rankers=rankers,
        feature_version=spec.feature_version,
        trained_at=spec.trained_at.astimezone(UTC).isoformat(),
        lineage=spec.lineage,
    )
    manifest_path = output_dir / spec.manifest_name
    manifest_tmp = output_dir / f".{spec.manifest_name}.tmp"
    manifest.write(manifest_tmp)
    manifest_tmp.replace(manifest_path)
    BundleKindMarker(
        kind=spec.kind,
        manifest_filename=spec.manifest_name,
        manifest_sha256=file_sha256(manifest_path),
    ).write(output_dir / KIND_MARKER_FILENAME)

    published = verify_bundle(
        output_dir,
        manifest_name=spec.manifest_name,
        expected_kind=spec.kind,
        expected=manifest,
        realise=realise,
    )
    logger.info(
        "Published %s bundle tenant=%s family=%s retriever=%s learned=%s fallback=%s into %s",
        spec.kind,
        manifest.tenant_id,
        manifest.retriever.family,
        manifest.retriever.version,
        manifest.route(RANKER_ROUTE_LEARNED).artifact.version,
        manifest.route(RANKER_ROUTE_FALLBACK).artifact.version,
        output_dir,
    )
    return PublishedBundle(
        kind=spec.kind,
        manifest=published.manifest,
        manifest_path=manifest_path,
        artifact_filenames=tuple(sorted(written)),
    )


def verify_bundle(
    bundle_dir: Path,
    *,
    manifest_name: str = DEFAULT_MANIFEST_NAME,
    expected_kind: str | None = None,
    expected: ServingManifest | None = None,
    realise: bool = False,
) -> PublishedBundle:
    """Load a published bundle back and hold it to everything it claims.

    This is the round trip, and it is deliberately the *real* loader rather than
    a re-read of the document this module just wrote. ``ServingManifest.load``
    re-hashes every artifact from the bytes on disk and runs every validator —
    the family's required roles and params, the per-route ranker feature
    contract against the booster files themselves, the cross-route permutation
    guard. A publisher that checked its own work with its own code would agree
    with itself about a bundle no sidecar could load.

    ``realise=True`` goes one step further and builds the models, which for a
    sequence bundle means rebuilding the encoder and its exact index. That is
    the only check that can catch a bundle whose two manifests disagree, so it
    is worth having — and it is off by default because it costs a torch import
    and a FAISS build that an item-item bundle has no use for.
    """
    manifest_path = bundle_dir / manifest_name
    if not manifest_path.is_file():
        raise BundlePublishError(f"no serving manifest at {manifest_path}")

    marker = read_bundle_kind(bundle_dir)
    if expected_kind is not None:
        if marker is None:
            raise BundlePublishError(
                f"{bundle_dir} carries no {KIND_MARKER_FILENAME}, so nothing says whether it is "
                f"a {expected_kind!r} bundle or the other kind under the same file layout"
            )
        if marker.kind != expected_kind:
            raise BundlePublishError(
                f"{bundle_dir} is labelled {marker.kind!r} but was verified as {expected_kind!r}"
            )
    if marker is not None:
        _verify_marker(marker, bundle_dir=bundle_dir, manifest_path=manifest_path)

    manifest = ServingManifest.load(manifest_path)
    if expected is not None and manifest.to_dict() != expected.to_dict():
        raise BundlePublishError(
            f"the bundle written to {bundle_dir} did not read back as the manifest that was "
            f"published; differing field(s) {_differences(manifest, expected)}"
        )
    if marker is not None and marker.kind == BUNDLE_KIND_SERVED:
        _require_lineage(manifest)
    _refuse_unpinned_files(manifest, bundle_dir=bundle_dir, manifest_name=manifest_name)
    if realise:
        ServingArtifactBundle.load(manifest_path)
    return PublishedBundle(
        kind=marker.kind if marker is not None else "",
        manifest=manifest,
        manifest_path=manifest_path,
        artifact_filenames=tuple(sorted(_pinned_filenames(manifest))),
    )


def read_bundle_kind(bundle_dir: Path) -> BundleKindMarker | None:
    """The directory's kind marker, or ``None`` for a bundle that carries none.

    ``None`` rather than a refusal because the committed demo fixture predates
    this module and has no marker; a caller that requires one says so by passing
    ``expected_kind``.
    """
    marker_path = bundle_dir / KIND_MARKER_FILENAME
    return BundleKindMarker.load(marker_path) if marker_path.is_file() else None


def parse_utc_timestamp(value: str) -> datetime:
    """Parse a bundle timestamp, in UTC, refusing a naive one.

    The same rule ``src/training/demo_artifacts.py`` applies to ``--as-of`` and
    for the same reason: a naive timestamp resolves against whatever zone the
    build host sits in, and two hosts disagreeing about ``trained_at`` is exactly
    what pinning it prevents. Restated rather than imported — ``src/models``
    must not depend on ``src/training``.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise BundlePublishError(f"trained_at must be an ISO-8601 timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise BundlePublishError(f"trained_at must carry a UTC offset: {value!r}")
    return parsed.astimezone(UTC)


def _validate_spec(spec: BundleSpec) -> None:
    """Everything that can be refused before anything is written.

    Ordering matters here more than it looks: each of these failures is one a
    sidecar would otherwise hit at boot, in production, on a bundle that had
    already been promoted. Catching them at publish costs nothing and is the
    difference between a bad spec and a bad deployment.
    """
    if spec.kind not in BUNDLE_KINDS:
        raise BundlePublishError(
            f"unknown bundle kind {spec.kind!r}; expected one of {list(BUNDLE_KINDS)}"
        )
    if not spec.tenant_id:
        raise BundlePublishError("bundle spec tenant_id must not be empty")

    required_roles = _required_retriever_roles(spec.retriever.family)
    missing_roles = [role for role in required_roles if role not in spec.retriever.artifacts]
    if missing_roles:
        raise BundlePublishError(
            f"retriever family {spec.retriever.family!r} needs artifact(s) {missing_roles}; "
            "a bundle missing one loads no retrieval stage at all"
        )
    missing_routes = [route for route in RANKER_ROUTES if route not in spec.rankers]
    if missing_routes:
        raise BundlePublishError(
            f"bundle spec declares no ranker for route(s) {missing_routes}; a bundle that can "
            "only rank on one route would fall through to the incumbent at request time"
        )
    unknown_routes = sorted(set(spec.rankers) - set(RANKER_ROUTES))
    if unknown_routes:
        raise BundlePublishError(f"bundle spec declares unknown ranker route(s) {unknown_routes}")

    for source in _sources(spec):
        if not source.path.is_file():
            raise BundlePublishError(f"artifact to bake is missing: {source.path}")
    _validate_destinations(spec)
    if spec.kind == BUNDLE_KIND_SERVED:
        _require_lineage_fields(spec.lineage, family=spec.retriever.family)
    _validate_family_companions(spec)


def _required_retriever_roles(family: str) -> tuple[str, ...]:
    """The roles the manifest will insist on for this family, asked of the manifest."""
    required = _REQUIRED_RETRIEVER_ARTIFACTS.get(family)
    if required is None:
        raise BundlePublishError(
            f"unsupported retriever family {family!r}; expected one of "
            f"{sorted(_REQUIRED_RETRIEVER_ARTIFACTS)}"
        )
    return tuple(required)


def _sources(spec: BundleSpec) -> list[ArtifactSource]:
    return [
        *spec.retriever.artifacts.values(),
        *(ref.artifact for ref in spec.rankers.values()),
    ]


def _validate_destinations(spec: BundleSpec) -> None:
    """Refuse two artifacts that would land on the same name with different bytes.

    Two roles pointing at one file is legitimate and expected — a SASRec
    bundle's vocabulary and config both live in ``sasrec-manifest.json``. Two
    *different* files claiming one name is a spec bug that would otherwise
    resolve to whichever was copied last, and the manifest would pin a checksum
    for one role that belongs to the other.
    """
    reserved = {spec.manifest_name, KIND_MARKER_FILENAME}
    by_name: dict[str, set[Path]] = {}
    for source in _sources(spec):
        name = source.target_name
        if name in reserved:
            raise BundlePublishError(
                f"artifact {source.path} would be baked as {name!r}, which the bundle reserves "
                "for its manifest and kind marker"
            )
        by_name.setdefault(name, set()).add(source.path.resolve())
    for name, paths in sorted(by_name.items()):
        if len(paths) > 1:
            raise BundlePublishError(
                f"artifacts {sorted(str(path) for path in paths)} would both be baked as {name!r}"
            )


def _validate_family_companions(spec: BundleSpec) -> None:
    """Hold a sequence bundle to the file layout its loader reads by fixed name.

    ``load_sequence_retriever`` requires ``sasrec-manifest.json`` beside the
    encoder archive and cross-checks the two manifests against each other. Both
    of those are boot-time failures on a bundle that has already been promoted,
    and both are answerable here from the bytes in front of us.

    The import is inside the function on purpose: ``sasrec_artifact`` imports
    torch, and this module is worth nothing if reading a spec costs a torch
    import. Only a sequence bundle pays it.
    """
    if spec.retriever.family != RETRIEVER_FAMILY_SASREC:
        return
    from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME

    names = {source.target_name: source for source in spec.retriever.artifacts.values()}
    companion = names.get(MANIFEST_FILENAME)
    if companion is None:
        raise BundlePublishError(
            f"a {RETRIEVER_FAMILY_SASREC!r} bundle must ship {MANIFEST_FILENAME}, and it must be "
            "declared as one of the retriever's artifacts so its checksum is pinned by the "
            "serving manifest; the loader reads it by that name and nothing else pins it"
        )
    encoder = spec.retriever.artifacts["encoder"]
    try:
        described = json.loads(companion.path.read_text(encoding="utf-8"))["model_filename"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise BundlePublishError(
            f"{companion.path} does not read as a SASRec artifact manifest: {error}"
        ) from error
    if str(described) != encoder.target_name:
        raise BundlePublishError(
            f"the spec bakes encoder {encoder.target_name!r} but {MANIFEST_FILENAME} describes "
            f"{str(described)!r}; the sidecar would load an encoder the serving manifest never "
            "pinned the checksum of"
        )


def _require_lineage_fields(lineage: Lineage, *, family: str) -> None:
    required = (*_REQUIRED_LINEAGE_FIELDS, *_REQUIRED_LINEAGE_FIELDS_BY_FAMILY.get(family, ()))
    missing = [name for name in required if not getattr(lineage, name)]
    if missing:
        raise BundlePublishError(
            f"a {BUNDLE_KIND_SERVED!r} bundle must record lineage {missing}; without it the "
            "promotion decision behind a bundle serving production traffic cannot be "
            "reconstructed from the bundle"
        )


def _require_lineage(manifest: ServingManifest) -> None:
    _require_lineage_fields(manifest.lineage, family=manifest.retriever.family)


def _bake_artifacts(spec: BundleSpec, output_dir: Path) -> set[str]:
    """Copy each distinct artifact in once, atomically. Returns what was written."""
    written: dict[str, Path] = {}
    for source in _sources(spec):
        name = source.target_name
        if name in written:
            # Already copied for another role — ``_validate_destinations`` has
            # proved the two roles name the same file.
            continue
        destination = output_dir / name
        temporary = output_dir / f".{name}.tmp"
        shutil.copyfile(source.path, temporary)
        temporary.replace(destination)
        written[name] = destination
    return set(written)


def _artifact_ref(source: ArtifactSource, written: Path) -> ArtifactRef:
    """Describe an artifact by the bytes that landed in the bundle.

    Hashing ``written`` rather than ``source.path`` is the whole point: the
    manifest is a claim about the file a sidecar will open, and a copy that
    truncated or a source that changed between the copy and the hash would
    otherwise produce a manifest that disagrees with its own directory.
    """
    return ArtifactRef(
        artifact_type=source.artifact_type,
        version=source.version,
        filename=written.name,
        sha256=file_sha256(written),
    )


def _verify_marker(marker: BundleKindMarker, *, bundle_dir: Path, manifest_path: Path) -> None:
    if marker.manifest_filename != manifest_path.name:
        raise BundlePublishError(
            f"{KIND_MARKER_FILENAME} in {bundle_dir} labels manifest "
            f"{marker.manifest_filename!r}, but {manifest_path.name!r} was verified"
        )
    actual = file_sha256(manifest_path)
    if actual != marker.manifest_sha256:
        raise BundlePublishError(
            f"{manifest_path.name} in {bundle_dir} is not the manifest this bundle was labelled "
            f"with: {KIND_MARKER_FILENAME} pins {marker.manifest_sha256}, the file hashes to "
            f"{actual}"
        )


def _pinned_filenames(manifest: ServingManifest) -> set[str]:
    return {
        *(ref.filename for ref in manifest.retriever.artifacts.values()),
        *(manifest.rankers[route].artifact.filename for route in manifest.rankers),
    }


def _refuse_unpinned_files(
    manifest: ServingManifest, *, bundle_dir: Path, manifest_name: str
) -> None:
    """Refuse a bundle directory holding a file no checksum covers.

    A bundle is meant to be exactly what its manifest describes. A stray file is
    either dead weight in an image or — for a family whose loader reads a file by
    fixed name — an unpinned input that the checksum machinery silently does not
    cover. Dot-prefixed entries are skipped because that is the naming this
    module and ``demo_artifacts`` both use for their atomic-write temporaries.
    """
    allowed = _pinned_filenames(manifest) | {manifest_name, KIND_MARKER_FILENAME}
    stray = sorted(
        entry.name
        for entry in bundle_dir.iterdir()
        if entry.is_file() and not entry.name.startswith(".") and entry.name not in allowed
    )
    if stray:
        raise BundlePublishError(
            f"{bundle_dir} holds file(s) {stray} that {manifest_name} does not pin; every byte in "
            "a bundle must carry a checksum the loader can check"
        )


def _differences(loaded: ServingManifest, expected: ServingManifest) -> list[str]:
    left = loaded.to_dict()
    right = expected.to_dict()
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a schema 2 serving bundle from already-trained artifacts."
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help=(
            "JSON document naming the artifacts to bake, their versions, the "
            "retrieval params and the lineage. Relative paths inside it resolve "
            "against the spec's own directory. Required unless --check."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="The bundle directory to publish into, or to check.",
    )
    parser.add_argument(
        "--manifest-name",
        default=DEFAULT_MANIFEST_NAME,
        help=f"Manifest filename inside the bundle. Defaults to {DEFAULT_MANIFEST_NAME}.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify the bundle already in --output-dir and write nothing: every "
            "artifact re-hashed against the manifest, every manifest validator "
            "re-run, the kind marker matched, and lineage required on a served "
            "bundle. This is not a retrain — a full-data bundle cannot be "
            "rebuilt from the demo tenant's ratings."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=BUNDLE_KINDS,
        help=(
            "The kind this bundle must be labelled as. On --check it is what "
            "makes a mislabelled directory fail; on a publish it overrides the "
            "spec's own kind."
        ),
    )
    parser.add_argument(
        "--realise",
        action="store_true",
        help=(
            "Also build the models from the verified bundle, which for a "
            "sequence bundle rebuilds the encoder and its exact index. Costs a "
            "torch import; catches a bundle whose two manifests disagree."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.check:
            published = verify_bundle(
                args.output_dir,
                manifest_name=args.manifest_name,
                expected_kind=args.kind,
                realise=args.realise,
            )
            logger.info(
                "Bundle in %s verifies: kind=%s schema=%s retriever=%s artifacts=%s",
                args.output_dir,
                published.kind or "unlabelled",
                published.manifest.schema_version,
                published.manifest.retriever.version,
                list(published.artifact_filenames),
            )
            return

        if args.spec is None:
            parser.error("--spec is required unless --check")
        spec = BundleSpec.from_json_file(args.spec)
        if args.kind is not None:
            spec = replace(spec, kind=args.kind)
        if args.manifest_name != DEFAULT_MANIFEST_NAME:
            spec = replace(spec, manifest_name=args.manifest_name)
        publish_bundle(spec, output_dir=args.output_dir, realise=args.realise)
    # Every refusal in this module and in the manifest vocabulary below it is a
    # ValueError. Caught rather than allowed to traceback because an operator
    # reading a failed publish wants the sentence, not the stack.
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
