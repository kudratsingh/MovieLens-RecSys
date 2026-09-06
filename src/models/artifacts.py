"""Versioned serving artifacts for learned two-stage recommendations.

The manifest is the compatibility boundary between offline training and the
model sidecar. It pins artifact hashes and the exact Feast/LightGBM feature
order so a mismatched deployment fails at startup instead of silently scoring
the wrong columns.

Two schema versions are live at once and both load. Schema 1 could name exactly
one retrieval artifact and exactly one ranker, and both assumptions have since
failed: retrieval is now a *family* whose artifacts differ per family (a JSON
item-item index; or SASRec encoder weights plus the vocabulary and config needed
to rebuild the encoder), and ranking is a *route table* whose two routes may
carry different feature contracts. Schema 2 says that, adds the lineage a bundle
needs to be traced back to the experiment that produced it, and is what this
module works in. A schema 1 document is normalised into that shape on load.

Refusing schema 1 was never an option. Rollback is by image (ADR 0013), so a
sidecar that only understood schema 2 could not be rolled back onto a bundle
published before this change without rolling the bundle back in the same motion
— which is exactly the coupling the image-tagged rollback exists to avoid.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import lightgbm as lgb

from src.features import FEATURE_COLUMNS
from src.models.ranker.lgbm import LGBMRanker
from src.serving.policy import (
    CANDIDATE_SOURCE_POPULARITY_FILL,
    CANDIDATE_SOURCE_SIMILARITY,
)

# Deliberate re-exports, spelled the way mypy requires one: every caller that
# reads a manifest already imports these from here, and a second spelling of a
# route name is how a manifest and an audit row end up disagreeing.
from src.serving.policy import RANKER_ROUTE_FALLBACK as RANKER_ROUTE_FALLBACK
from src.serving.policy import RANKER_ROUTE_LEARNED as RANKER_ROUTE_LEARNED
from src.serving.policy import RANKER_ROUTES as RANKER_ROUTES

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Imported for the annotation alone. ``src.serving.sequence_retrieval``
    # imports this module, so a runtime import here would be a cycle; ``load``
    # reaches into it lazily instead, which is also what keeps torch and FAISS
    # out of the import graph of anything that merely reads a manifest.
    from src.serving.sequence_retrieval import SidecarRetriever

MANIFEST_SCHEMA_VERSION = 2
LEGACY_MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA_VERSIONS: tuple[int, ...] = (
    LEGACY_MANIFEST_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)

# Retrieval families. The family — not an artifact's ``artifact_type`` — is what
# tells a sidecar which loader to reach for and which parameters have to be
# present, so it is a first-class field rather than something inferred.
RETRIEVER_FAMILY_ITEM_ITEM = CANDIDATE_SOURCE_SIMILARITY
RETRIEVER_FAMILY_SASREC = "sasrec"

# The roles a family's artifacts play. SASRec ships the encoder, the item
# vocabulary and the config needed to rebuild the encoder rather than a built
# ANN index — see ``INDEX_TYPE_FLAT_IP_EXACT`` for why the index is rebuilt
# rather than shipped.
_REQUIRED_RETRIEVER_ARTIFACTS: Mapping[str, tuple[str, ...]] = {
    RETRIEVER_FAMILY_ITEM_ITEM: ("index",),
    RETRIEVER_FAMILY_SASREC: ("encoder", "vocabulary", "config"),
}

# The one artifact whose version *is* the retriever's version. A bundle is
# promoted, audited and champion-matched as a single version string, and this is
# where that string comes from.
_PRIMARY_RETRIEVER_ARTIFACT: Mapping[str, str] = {
    RETRIEVER_FAMILY_ITEM_ITEM: "index",
    RETRIEVER_FAMILY_SASREC: "encoder",
}

# What a family must state about how it retrieves. These are not decoration: a
# SASRec bundle that does not say how long a sequence it was trained on, at what
# history length it declines to serve, or what it considers already-seen would
# be served under whatever the sidecar happened to default to.
_REQUIRED_RETRIEVER_PARAMS: Mapping[str, tuple[str, ...]] = {
    RETRIEVER_FAMILY_ITEM_ITEM: (),
    RETRIEVER_FAMILY_SASREC: (
        "max_sequence_length",
        "cold_start_threshold",
        "exclusion_policy",
        "index_type",
    ),
}

# The only ANN index a rebuilt-at-load retriever may declare. An IVF index is
# trained on the vectors it is built from, so two loads of the same weights can
# disagree on what they return; and the SASRec numbers that earned the model its
# evaluation were measured under exact search. Accepting anything else here
# would mean serving a retriever nobody has measured.
INDEX_TYPE_FLAT_IP_EXACT = "flat-ip-exact"

# The two ranking routes — imported above rather than declared here, and still
# named in this module's namespace so every existing `from src.models.artifacts
# import RANKER_ROUTE_*` keeps working. They moved to ``src.serving.policy``
# because the audit column that records which route ran is written by the slim
# API image, which installs none of this module's dependencies. Both routes must
# be declared by a manifest: a bundle with a ranker on one route and nothing on
# the other is refused at startup rather than discovered at request time, when
# the only thing left to do is fall through to the incumbent.

_LINEAGE_FIELDS: tuple[str, ...] = (
    "protocol_hash",
    "raw_data_revision",
    "run_id",
    "code_sha",
    "faiss_version",
    "torch_version",
)

# What LightGBM writes into a model file when it was trained from a bare matrix
# rather than a named frame. Not a feature order — a placeholder for one.
_LGBM_PLACEHOLDER_NAME = "Column_{index}"


@dataclass(frozen=True)
class ArtifactRef:
    artifact_type: str
    version: str
    filename: str
    sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> ArtifactRef:
        if not isinstance(value, dict):
            raise ValueError("artifact reference must be an object")
        return cls(
            artifact_type=str(value["artifact_type"]),
            version=str(value["version"]),
            filename=_safe_filename(str(value["filename"])),
            sha256=str(value["sha256"]),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_type": self.artifact_type,
            "version": self.version,
            "filename": self.filename,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RetrieverRef:
    """The retrieval stage of a bundle, whatever family produced it.

    ``artifacts`` is keyed by role rather than being a list because the roles
    are what a loader asks for by name ("give me the encoder"), and ``params``
    carries the retrieval-time settings the artifacts themselves do not encode.
    Both are read-only views: a manifest is a claim about what was published,
    and nothing downstream should be able to edit it after validation.
    """

    family: str
    artifacts: Mapping[str, ArtifactRef]
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @classmethod
    def from_dict(cls, value: Any) -> RetrieverRef:
        if not isinstance(value, dict):
            raise ValueError("retriever reference must be an object")
        artifacts = value["artifacts"]
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError("retriever must declare at least one artifact")
        params = value.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("retriever params must be an object")
        return cls(
            family=str(value["family"]),
            artifacts={str(role): ArtifactRef.from_dict(ref) for role, ref in artifacts.items()},
            params={str(name): setting for name, setting in params.items()},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "artifacts": {role: self.artifacts[role].to_dict() for role in sorted(self.artifacts)},
            "params": dict(self.params),
        }

    @property
    def primary(self) -> ArtifactRef:
        """The artifact whose version names this retriever."""
        role = _PRIMARY_RETRIEVER_ARTIFACT.get(self.family)
        if role is None:
            raise ValueError(f"unsupported retriever family {self.family!r}")
        artifact = self.artifacts.get(role)
        if artifact is None:
            raise ValueError(f"retriever family {self.family!r} declares no {role!r} artifact")
        return artifact

    @property
    def version(self) -> str:
        return self.primary.version

    def validate(self, artifact_dir: Path) -> None:
        required = _REQUIRED_RETRIEVER_ARTIFACTS.get(self.family)
        if required is None:
            raise ValueError(
                f"unsupported retriever family {self.family!r}; expected one of "
                f"{sorted(_REQUIRED_RETRIEVER_ARTIFACTS)}"
            )
        missing = [role for role in required if role not in self.artifacts]
        if missing:
            raise ValueError(f"retriever family {self.family!r} is missing artifact(s) {missing}")
        for role in sorted(self.artifacts):
            _verify_artifact(artifact_dir, self.artifacts[role])
        missing_params = [
            name for name in _REQUIRED_RETRIEVER_PARAMS[self.family] if name not in self.params
        ]
        if missing_params:
            raise ValueError(f"retriever family {self.family!r} does not declare {missing_params}")
        # Checked for any family that declares one, not only the families that
        # are required to: a bundle that names an index type this build does not
        # rebuild exactly must not be served under a measurement taken with a
        # different one.
        if "index_type" in self.params:
            index_type = self.params["index_type"]
            if index_type != INDEX_TYPE_FLAT_IP_EXACT:
                raise ValueError(
                    f"retriever declares index_type {index_type!r}; only "
                    f"{INDEX_TYPE_FLAT_IP_EXACT!r} is accepted, because an IVF rebuild is not "
                    "deterministic and the evaluated model used exact search"
                )
        if self.family == RETRIEVER_FAMILY_SASREC:
            self._validate_sequence_params()

    def _validate_sequence_params(self) -> None:
        max_sequence_length = self.params["max_sequence_length"]
        if not _is_positive_int(max_sequence_length):
            raise ValueError(
                f"max_sequence_length must be a positive integer, got {max_sequence_length!r}"
            )
        # ``None`` is a real setting, not a missing one: it is how a SASRec model
        # says it will serve every user with any history at all.
        threshold = self.params["cold_start_threshold"]
        if threshold is not None and not _is_positive_int(threshold):
            raise ValueError(
                f"cold_start_threshold must be a positive integer or null, got {threshold!r}"
            )
        policy = self.params["exclusion_policy"]
        if not isinstance(policy, str) or not policy:
            raise ValueError(f"exclusion_policy must be a non-empty string, got {policy!r}")


@dataclass(frozen=True)
class RankerRef:
    """One ranker, and the feature order *that* ranker was trained on.

    The order is per route rather than global because the two routes are allowed
    to disagree from day one — the learned route can move to a wider contract
    while the fallback stays on the one the incumbent booster was fit against.
    The global ``FEATURE_COLUMNS`` is the default, not a constraint.
    """

    artifact: ArtifactRef
    feature_columns: tuple[str, ...] = tuple(FEATURE_COLUMNS)

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_columns", tuple(self.feature_columns))

    @classmethod
    def from_dict(cls, value: Any) -> RankerRef:
        if not isinstance(value, dict):
            raise ValueError("ranker route must be an object")
        columns = value.get("feature_columns")
        return cls(
            artifact=ArtifactRef.from_dict(value["artifact"]),
            feature_columns=(
                tuple(FEATURE_COLUMNS)
                if columns is None
                else tuple(str(column) for column in columns)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact.to_dict(),
            "feature_columns": list(self.feature_columns),
        }


@dataclass(frozen=True)
class Lineage:
    """Which experiment produced a bundle.

    Schema 1 could say what a bundle *is* — hashes, versions, a feature order —
    but not where it came from, so a promoted bundle could not be traced back to
    the run that earned it, the data revision it saw, or the native libraries
    whose numerics it was measured under. Every field is optional so a bundle
    published from a context that genuinely lacks one (a local rebuild has no
    MLflow run) can still say the rest.
    """

    protocol_hash: str | None = None
    raw_data_revision: str | None = None
    run_id: str | None = None
    code_sha: str | None = None
    faiss_version: str | None = None
    torch_version: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> Lineage:
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("manifest lineage must be an object")
        # Refused rather than ignored: a field this build drops on write would
        # disappear from the bundle the next time anything republishes it.
        unknown = sorted(set(value) - set(_LINEAGE_FIELDS))
        if unknown:
            raise ValueError(f"manifest lineage carries unknown field(s) {unknown}")
        return cls(
            **{
                name: (None if value.get(name) is None else str(value[name]))
                for name in _LINEAGE_FIELDS
            }
        )

    def to_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in _LINEAGE_FIELDS}


@dataclass(frozen=True, init=False)
class ServingManifest:
    """What a sidecar loads, in the schema 2 shape, whichever version it was written in.

    The constructor is hand-written for one reason: a schema 1 manifest is still
    built in code — by ``src/training/demo_artifacts.py``, and by anything
    republishing a bundle from before the split — as ``candidate=`` plus
    ``ranker=``, and that spelling has to keep working. Passing it normalises
    into a single-artifact retriever and a route table whose two routes point at
    the same booster; the manifest then remembers it is schema 1 so
    ``to_dict`` writes back the bytes it was read from.
    """

    tenant_id: str
    retriever: RetrieverRef
    rankers: Mapping[str, RankerRef]
    feature_version: str
    trained_at: str
    feature_columns: tuple[str, ...]
    lineage: Lineage
    schema_version: int

    def __init__(
        self,
        *,
        tenant_id: str,
        feature_version: str,
        trained_at: str,
        retriever: RetrieverRef | None = None,
        rankers: Mapping[str, RankerRef] | None = None,
        candidate: ArtifactRef | None = None,
        ranker: ArtifactRef | None = None,
        feature_columns: Iterable[str] = tuple(FEATURE_COLUMNS),
        lineage: Lineage = Lineage(),
        schema_version: int | None = None,
    ) -> None:
        declared_v1 = candidate is not None or ranker is not None
        declared_v2 = retriever is not None or rankers is not None
        if declared_v1 and declared_v2:
            raise ValueError(
                "a serving manifest declares either the schema 1 candidate/ranker pair or the "
                "schema 2 retriever/rankers pair, never both"
            )
        if not declared_v1 and not declared_v2:
            raise ValueError("a serving manifest must declare a retriever and its rankers")

        columns = tuple(feature_columns)
        if declared_v1:
            if candidate is None or ranker is None:
                raise ValueError(
                    "a schema 1 serving manifest declares both a candidate and a ranker"
                )
            if schema_version not in (None, LEGACY_MANIFEST_SCHEMA_VERSION):
                raise ValueError(
                    f"the candidate/ranker pair is schema {LEGACY_MANIFEST_SCHEMA_VERSION}, "
                    f"not schema {schema_version}"
                )
            if lineage != Lineage():
                raise ValueError(
                    f"lineage arrived on schema {LEGACY_MANIFEST_SCHEMA_VERSION}, which has "
                    "nowhere to write it back to"
                )
            resolved_version = LEGACY_MANIFEST_SCHEMA_VERSION
            resolved_retriever = RetrieverRef(
                family=candidate.artifact_type,
                artifacts={_v1_retriever_role(candidate.artifact_type): candidate},
            )
            # Both routes, one booster. A schema 1 bundle only ever had one, and
            # leaving the fallback route empty would make every such bundle fail
            # the route check that exists to stop a silent fall-through.
            route = RankerRef(artifact=ranker, feature_columns=columns)
            resolved_rankers: Mapping[str, RankerRef] = {name: route for name in RANKER_ROUTES}
        else:
            if retriever is None or rankers is None:
                raise ValueError(
                    "a schema 2 serving manifest declares both a retriever and its rankers"
                )
            if schema_version not in (None, MANIFEST_SCHEMA_VERSION):
                raise ValueError(
                    f"the retriever/rankers pair is schema {MANIFEST_SCHEMA_VERSION}, "
                    f"not schema {schema_version}"
                )
            resolved_version = MANIFEST_SCHEMA_VERSION
            resolved_retriever = retriever
            resolved_rankers = dict(rankers)

        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "retriever", resolved_retriever)
        object.__setattr__(self, "rankers", MappingProxyType(dict(resolved_rankers)))
        object.__setattr__(self, "feature_version", feature_version)
        object.__setattr__(self, "trained_at", trained_at)
        object.__setattr__(self, "feature_columns", columns)
        object.__setattr__(self, "lineage", lineage)
        object.__setattr__(self, "schema_version", resolved_version)

    @property
    def candidate(self) -> ArtifactRef:
        """The schema 1 spelling of the retrieval artifact.

        Kept because callers written against schema 1 still read it, and because
        it is what ``to_dict`` writes back for a schema 1 bundle. For a family
        that ships several artifacts this is the primary one — a caller that
        wants the encoder *and* the vocabulary wants ``retriever.artifacts``.
        """
        return self.retriever.primary

    @property
    def ranker(self) -> ArtifactRef:
        """The schema 1 spelling of the ranker: the learned route's artifact."""
        return self.route(RANKER_ROUTE_LEARNED).artifact

    @property
    def ranker_version(self) -> str:
        """The version a champion row and an audit line mean by "the ranker"."""
        return self.ranker.version

    def route(self, name: str) -> RankerRef:
        ref = self.rankers.get(name)
        if ref is None:
            raise ValueError(f"serving manifest declares no ranker for route {name!r}")
        return ref

    @classmethod
    def load(cls, path: Path) -> ServingManifest:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("serving manifest must contain an object")
        try:
            schema_version = int(value["schema_version"])
            if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
                raise ValueError(
                    f"unsupported serving manifest schema {schema_version}; expected one of "
                    f"{list(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
                )
            tenant_id = str(value["tenant_id"])
            feature_version = str(value["feature_version"])
            trained_at = str(value["trained_at"])
            feature_columns = tuple(str(column) for column in value["feature_columns"])
            if schema_version == LEGACY_MANIFEST_SCHEMA_VERSION:
                manifest = cls(
                    schema_version=schema_version,
                    tenant_id=tenant_id,
                    candidate=ArtifactRef.from_dict(value["candidate"]),
                    ranker=ArtifactRef.from_dict(value["ranker"]),
                    feature_version=feature_version,
                    trained_at=trained_at,
                    feature_columns=feature_columns,
                )
            else:
                routes = value["rankers"]
                if not isinstance(routes, dict):
                    raise ValueError("serving manifest rankers must be an object")
                manifest = cls(
                    schema_version=schema_version,
                    tenant_id=tenant_id,
                    retriever=RetrieverRef.from_dict(value["retriever"]),
                    rankers={str(name): RankerRef.from_dict(ref) for name, ref in routes.items()},
                    feature_version=feature_version,
                    trained_at=trained_at,
                    feature_columns=feature_columns,
                    lineage=Lineage.from_dict(value.get("lineage")),
                )
        except KeyError as error:
            raise ValueError(f"serving manifest is missing {error.args[0]!r}") from error
        manifest.validate(path.parent)
        return manifest

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def validate(self, artifact_dir: Path) -> None:
        if self.schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported serving manifest schema {self.schema_version}; expected one of "
                f"{list(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
            )
        if not self.tenant_id:
            raise ValueError("manifest tenant_id must not be empty")
        if self.feature_columns != tuple(FEATURE_COLUMNS):
            raise ValueError("manifest feature order does not match the serving feature contract")
        self.retriever.validate(artifact_dir)
        missing = [name for name in RANKER_ROUTES if name not in self.rankers]
        if missing:
            raise ValueError(
                f"serving manifest declares no ranker for route(s) {missing}; a bundle that can "
                "only rank on one route would fall through to the incumbent at request time"
            )
        unknown = sorted(set(self.rankers) - set(RANKER_ROUTES))
        if unknown:
            raise ValueError(f"serving manifest declares unknown ranker route(s) {unknown}")
        for name in RANKER_ROUTES:
            ref = self.rankers[name]
            _verify_artifact(artifact_dir, ref.artifact)
            _validate_ranker_contract(name, ref, artifact_dir)
        _validate_route_permutation(self.rankers)

    def to_dict(self) -> dict[str, object]:
        if self.schema_version == LEGACY_MANIFEST_SCHEMA_VERSION:
            # Written back exactly as schema 1 always was. The release build
            # rebuilds the committed bundle and compares hashes (non-negotiable
            # #5), so a manifest that gained a key here would read as drift.
            return {
                "schema_version": self.schema_version,
                "tenant_id": self.tenant_id,
                "candidate": self.candidate.to_dict(),
                "ranker": self.ranker.to_dict(),
                "feature_version": self.feature_version,
                "trained_at": self.trained_at,
                "feature_columns": list(self.feature_columns),
            }
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "retriever": self.retriever.to_dict(),
            "rankers": {name: self.rankers[name].to_dict() for name in sorted(self.rankers)},
            "feature_version": self.feature_version,
            "trained_at": self.trained_at,
            "feature_columns": list(self.feature_columns),
            "lineage": self.lineage.to_dict(),
        }


@dataclass(frozen=True)
class CandidateContribution:
    """Where one retrieved candidate came from, for prediction audits."""

    movie_id: int
    source: str
    seed_movie_id: int | None
    contribution: float


@dataclass(frozen=True)
class CandidateRetrieval:
    """Retrieved candidates plus the provenance needed to explain them.

    ``seed_count`` is the number of positive seeds that actually reached at
    least one candidate, not the number the caller offered. A seed the index
    has never scored, or one whose every neighbor is filtered out, contributed
    nothing to this result, and counting it would let a response claim a
    retrieval it never performed.

    ``encoder_ms`` is the part of this retrieval spent inside a sequence
    encoder's forward pass, and nothing else — not the ANN search, not the
    exclusion filtering. Zero is the right answer for a family that runs no
    encoder rather than a missing measurement, which is why it defaults to 0.0
    instead of ``None``: item-item genuinely spends none.
    """

    contributions: tuple[CandidateContribution, ...]
    seed_count: int
    excluded_count: int
    encoder_ms: float = 0.0

    @property
    def movie_ids(self) -> list[int]:
        return [contribution.movie_id for contribution in self.contributions]

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for contribution in self.contributions:
            counts[contribution.source] = counts.get(contribution.source, 0) + 1
        return counts


@dataclass(frozen=True)
class CandidateIndex:
    """Deterministic item-item cosine index plus a warm-user fill order."""

    neighbors: dict[int, tuple[tuple[int, float], ...]]
    popularity: tuple[int, ...]

    @classmethod
    def build(
        cls,
        histories: dict[int, set[int]],
        *,
        max_neighbors: int = 100,
    ) -> CandidateIndex:
        item_counts: Counter[int] = Counter()
        pair_counts: Counter[tuple[int, int]] = Counter()
        for items in histories.values():
            ordered = sorted(items)
            item_counts.update(ordered)
            pair_counts.update(combinations(ordered, 2))

        by_item: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for (left, right), count in pair_counts.items():
            similarity = float(count) / math.sqrt(item_counts[left] * item_counts[right])
            by_item[left].append((right, similarity))
            by_item[right].append((left, similarity))

        neighbors = {
            item_id: tuple(sorted(values, key=lambda value: (-value[1], value[0]))[:max_neighbors])
            for item_id, values in sorted(by_item.items())
        }
        popularity = tuple(
            item_id
            for item_id, _ in sorted(item_counts.items(), key=lambda value: (-value[1], value[0]))
        )
        return cls(neighbors=neighbors, popularity=popularity)

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        """Walk neighbors from positive history only, suppressing exclusions.

        The three inputs are deliberately not interchangeable, and the
        difference between the last two is what this method gets wrong if they
        are merged. Positive history both seeds the walk and hides its own
        items. ``excluded_movie_ids`` is the caller's complete "never show
        this" set — it necessarily *contains* the watched history, so it may
        only hide, never narrow the seed set. ``dismissed_movie_ids`` is the
        one input that also drops a seed: a "not for me" must never pull in
        more of the same thing, even when the same title also carries watched
        state (ADR 0012).
        """
        excluded = set(excluded_movie_ids)
        dismissed = set(dismissed_movie_ids)
        hidden = excluded | dismissed
        if limit <= 0:
            return CandidateRetrieval(contributions=(), seed_count=0, excluded_count=len(hidden))
        seeds = [movie_id for movie_id in positive_history_movie_ids if movie_id not in dismissed]
        blocked = hidden | set(positive_history_movie_ids)
        scores: dict[int, float] = {}
        # Seeds that reached at least one candidate. Reported instead of the
        # offered count so the audit and the response reason can never claim a
        # retrieval that no seed actually drove.
        used_seeds: set[int] = set()
        # Callers pass seeds newest-first, so the first seed to reach a
        # candidate is the most recently watched title behind it — the honest
        # answer to "why am I seeing this". Recording it here costs one branch
        # on a loop that is already on the p99 path; a max-similarity argmax
        # measured roughly 60% slower on a 500-item history, which is not worth
        # it for an explanation field.
        first_seed: dict[int, int] = {}
        for source_id in seeds:
            for candidate_id, similarity in self.neighbors.get(source_id, ()):
                if candidate_id in blocked:
                    continue
                used_seeds.add(source_id)
                previous = scores.get(candidate_id)
                if previous is None:
                    scores[candidate_id] = similarity
                    first_seed[candidate_id] = source_id
                else:
                    scores[candidate_id] = previous + similarity
        ranked = sorted(scores.items(), key=lambda value: (-value[1], value[0]))[:limit]
        contributions = [
            CandidateContribution(
                movie_id=item_id,
                source=CANDIDATE_SOURCE_SIMILARITY,
                seed_movie_id=first_seed[item_id],
                contribution=mass,
            )
            for item_id, mass in ranked
        ]
        selected_set = {contribution.movie_id for contribution in contributions}
        for item_id in self.popularity:
            if len(contributions) >= limit:
                break
            if item_id in blocked or item_id in selected_set:
                continue
            contributions.append(
                CandidateContribution(
                    movie_id=item_id,
                    source=CANDIDATE_SOURCE_POPULARITY_FILL,
                    seed_movie_id=None,
                    contribution=0.0,
                )
            )
            selected_set.add(item_id)
        return CandidateRetrieval(
            contributions=tuple(contributions),
            seed_count=len(used_seeds),
            excluded_count=len(hidden),
        )

    @classmethod
    def load(cls, path: Path) -> CandidateIndex:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("candidate index must contain an object")
        raw_neighbors = value.get("neighbors")
        raw_popularity = value.get("popularity")
        if not isinstance(raw_neighbors, dict) or not isinstance(raw_popularity, list):
            raise ValueError("candidate index has an invalid shape")
        neighbors = {
            int(item_id): tuple((int(pair[0]), float(pair[1])) for pair in values)
            for item_id, values in raw_neighbors.items()
        }
        return cls(neighbors=neighbors, popularity=tuple(int(item) for item in raw_popularity))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "neighbors": {
                str(item_id): [[neighbor, score] for neighbor, score in values]
                for item_id, values in sorted(self.neighbors.items())
            },
            "popularity": list(self.popularity),
        }
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class ServingArtifactBundle:
    """Everything a sidecar worker needs, fully realised or not constructed at all.

    ``candidates`` is the item-item index and is ``None`` for any other family;
    ``retriever`` is the family-neutral retrieval stage the sidecar actually
    serves from. Both are kept rather than collapsed because the item-item index
    has readers that want the index itself — the baked-bundle test asserts on its
    neighbour and popularity tables — and narrowing them to the retrieval
    protocol would lose that.
    """

    manifest: ServingManifest
    candidates: CandidateIndex | None
    ranker: LGBMRanker
    rankers: Mapping[str, LGBMRanker] = field(default_factory=dict)
    retriever: SidecarRetriever | None = None

    def __post_init__(self) -> None:
        # A bundle handed a single booster is the normalised schema 1 shape:
        # both routes are that booster. Spelling it out here means every reader
        # can go through ``rankers`` without caring which shape it came from.
        routes = dict(self.rankers) or {name: self.ranker for name in RANKER_ROUTES}
        object.__setattr__(self, "rankers", MappingProxyType(routes))

    @classmethod
    def load(cls, manifest_path: Path) -> ServingArtifactBundle:
        manifest = ServingManifest.load(manifest_path)
        artifact_dir = manifest_path.parent
        # One booster per distinct file. The normalised schema 1 shape points
        # both routes at the same artifact, and loading it twice would double a
        # sidecar's resident model memory to hold two copies of one model.
        by_filename: dict[str, LGBMRanker] = {}
        rankers: dict[str, LGBMRanker] = {}
        for name in RANKER_ROUTES:
            filename = manifest.route(name).artifact.filename
            if filename not in by_filename:
                by_filename[filename] = LGBMRanker.load_model(artifact_dir / filename)
            rankers[name] = by_filename[filename]
        candidates, retriever = _load_retrieval_stage(manifest, artifact_dir, manifest_path.name)
        return cls(
            manifest=manifest,
            candidates=candidates,
            ranker=rankers[RANKER_ROUTE_LEARNED],
            rankers=rankers,
            retriever=retriever,
        )


def _load_retrieval_stage(
    manifest: ServingManifest, artifact_dir: Path, manifest_name: str
) -> tuple[CandidateIndex | None, SidecarRetriever | None]:
    """Realise the retrieval stage a manifest declares, or refuse the whole bundle.

    Still fail-closed, and for the same reason it was when this refused SASRec
    outright: a sidecar that cannot serve the family it was handed must not boot
    and answer with something else. What changed is only *which* families can be
    realised — the refusal below is now about families this build has no loader
    for at all, and it is the last line rather than the first.

    A SASRec bundle is rebuilt by ``src.serving.sequence_retrieval``, imported
    here and not at module scope. That module owns the torch and FAISS imports,
    so an item-item deployment never pays for them, and anything that only wants
    to read a manifest — the release verifier, the reproducibility check — keeps
    working in an environment that has neither library installed.
    """
    if manifest.retriever.family == RETRIEVER_FAMILY_ITEM_ITEM:
        return CandidateIndex.load(artifact_dir / manifest.retriever.primary.filename), None
    if manifest.retriever.family == RETRIEVER_FAMILY_SASREC:
        from src.serving.sequence_retrieval import load_sequence_retriever

        return None, load_sequence_retriever(manifest.retriever, artifact_dir)
    raise ValueError(
        f"this build has no loader for the {manifest.retriever.family!r} retriever family; the "
        f"manifest at {manifest_name} declares it and every family this build serves is "
        f"{[RETRIEVER_FAMILY_ITEM_ITEM, RETRIEVER_FAMILY_SASREC]}"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_artifact(artifact_dir: Path, artifact: ArtifactRef) -> None:
    """Prove the file on disk is the one the manifest was written against."""
    artifact_path = artifact_dir / artifact.filename
    if not artifact_path.is_file():
        raise ValueError(f"artifact is missing: {artifact.filename}")
    actual = file_sha256(artifact_path)
    if actual != artifact.sha256:
        raise ValueError(
            f"artifact checksum mismatch for {artifact.filename}: "
            f"expected {artifact.sha256}, got {actual}"
        )


def _validate_ranker_contract(route: str, ref: RankerRef, artifact_dir: Path) -> None:
    """Check a declared feature order against the booster file itself.

    The declaration in a manifest is a claim about a file somebody else wrote,
    and the failure it exists to catch — a booster scored against columns in an
    order it was not fit on — produces no error of its own, just quietly wrong
    rankings. LightGBM stores both the feature count and the feature names, so
    the file can be asked directly.

    The names are only usable when the booster was trained from a named frame.
    Trained from a bare matrix — which is what ``LGBMRanker.fit`` does today, so
    it is what every booster in this repo looks like — LightGBM stores
    ``Column_0 … Column_{n-1}``, which is a placeholder rather than a feature
    order. In that case the count is the only thing the file can be held to, and
    a same-length reordering is undetectable here. That is a real gap and the
    reason to prefer training from a named frame.
    """
    declared = ref.feature_columns
    if not declared:
        raise ValueError(f"ranker route {route!r} declares an empty feature order")
    duplicates = sorted({column for column in declared if declared.count(column) > 1})
    if duplicates:
        raise ValueError(f"ranker route {route!r} declares duplicate feature(s) {duplicates}")
    names, count = _booster_feature_contract(artifact_dir / ref.artifact.filename)
    if count != len(declared):
        raise ValueError(
            f"ranker route {route!r} declares {len(declared)} features but "
            f"{ref.artifact.filename} was trained on {count}"
        )
    if names and names != declared:
        raise ValueError(
            f"ranker route {route!r} declares feature order {list(declared)} but "
            f"{ref.artifact.filename} was trained on {list(names)}"
        )


def _validate_route_permutation(rankers: Mapping[str, RankerRef]) -> None:
    """Refuse two routes that declare the same features in a different order.

    TEMPORARY, and deliberately not a check on names. ``_validate_ranker_contract``
    above can only hold a booster to its *width* whenever LightGBM recorded
    placeholder names, which is every booster this repo trains today — so a
    learned route carrying the fallback's contract, permuted, passes every other
    check in this module and then scores each column against the splits learned
    for a different one. Comparing the two routes to each other needs no names
    at all, which is why it can be done now rather than after the training-side
    fix.

    Only an equal multiset in a different order is refused. A strict superset is
    the expected shape once the learned route grows sequence features while the
    fallback stays on the incumbent contract, so widening must stay legal; and a
    schema 1 bundle points both routes at one ``RankerRef``, so the two orders
    are identical there and this never fires.

    Delete it once every booster is trained from a named frame — at that point
    the name check is strictly stronger, because it catches a permutation on a
    single route as well as one between two.
    """
    learned = rankers[RANKER_ROUTE_LEARNED].feature_columns
    fallback = rankers[RANKER_ROUTE_FALLBACK].feature_columns
    if learned != fallback and Counter(learned) == Counter(fallback):
        raise ValueError(
            f"ranker routes {RANKER_ROUTE_LEARNED!r} and {RANKER_ROUTE_FALLBACK!r} declare the "
            f"same {len(learned)} features in a different order, which no booster in this repo "
            f"carries the names to detect: {RANKER_ROUTE_LEARNED} is {list(learned)} and "
            f"{RANKER_ROUTE_FALLBACK} is {list(fallback)}"
        )


def _booster_feature_contract(path: Path) -> tuple[tuple[str, ...], int]:
    """Return the feature names and count LightGBM recorded in a model file.

    Placeholder names are returned as an empty tuple so the caller can tell
    "the file disagrees" from "the file has nothing to disagree with".
    """
    try:
        booster = lgb.Booster(model_file=str(path))
    except (lgb.basic.LightGBMError, OSError) as error:
        raise ValueError(
            f"ranker artifact is not a readable LightGBM model: {path.name}"
        ) from error
    count = int(booster.num_feature())
    names = tuple(str(name) for name in booster.feature_name())
    placeholders = tuple(_LGBM_PLACEHOLDER_NAME.format(index=index) for index in range(count))
    return ((), count) if names == placeholders else (names, count)


def _v1_retriever_role(family: str) -> str:
    """The artifact role a schema 1 candidate fills within its family.

    Schema 1 wrote the family into ``artifact_type`` and never named a role,
    because there was only ever one artifact to name. An unrecognised family
    falls back to the item-item role so that construction stays total —
    ``validate`` is what refuses an unknown family, and it should do so with a
    message about the family rather than about a missing role.
    """
    return _PRIMARY_RETRIEVER_ARTIFACT.get(family, "index")


def _is_positive_int(value: Any) -> bool:
    # ``bool`` is an ``int`` in Python and is never a sequence length.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _safe_filename(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or path.name != value or value in {".", ".."}:
        raise ValueError(f"artifact filename must be a safe basename: {value!r}")
    return value
