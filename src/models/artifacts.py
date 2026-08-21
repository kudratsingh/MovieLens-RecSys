"""Versioned serving artifacts for learned two-stage recommendations.

The manifest is the compatibility boundary between offline training and the
model sidecar. It pins artifact hashes and the exact Feast/LightGBM feature
order so a mismatched deployment fails at startup instead of silently scoring
the wrong columns.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from src.features import FEATURE_COLUMNS
from src.models.ranker.lgbm import LGBMRanker

MANIFEST_SCHEMA_VERSION = 1


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
class ServingManifest:
    tenant_id: str
    candidate: ArtifactRef
    ranker: ArtifactRef
    feature_version: str
    trained_at: str
    feature_columns: tuple[str, ...] = tuple(FEATURE_COLUMNS)
    schema_version: int = MANIFEST_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> ServingManifest:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("serving manifest must contain an object")
        manifest = cls(
            schema_version=int(value["schema_version"]),
            tenant_id=str(value["tenant_id"]),
            candidate=ArtifactRef.from_dict(value["candidate"]),
            ranker=ArtifactRef.from_dict(value["ranker"]),
            feature_version=str(value["feature_version"]),
            trained_at=str(value["trained_at"]),
            feature_columns=tuple(str(column) for column in value["feature_columns"]),
        )
        manifest.validate(path.parent)
        return manifest

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def validate(self, artifact_dir: Path) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported serving manifest schema {self.schema_version}; "
                f"expected {MANIFEST_SCHEMA_VERSION}"
            )
        if not self.tenant_id:
            raise ValueError("manifest tenant_id must not be empty")
        if self.feature_columns != tuple(FEATURE_COLUMNS):
            raise ValueError("manifest feature order does not match the serving feature contract")
        for artifact in (self.candidate, self.ranker):
            artifact_path = artifact_dir / artifact.filename
            if not artifact_path.is_file():
                raise ValueError(f"artifact is missing: {artifact.filename}")
            actual = file_sha256(artifact_path)
            if actual != artifact.sha256:
                raise ValueError(
                    f"artifact checksum mismatch for {artifact.filename}: "
                    f"expected {artifact.sha256}, got {actual}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "candidate": self.candidate.to_dict(),
            "ranker": self.ranker.to_dict(),
            "feature_version": self.feature_version,
            "trained_at": self.trained_at,
            "feature_columns": list(self.feature_columns),
        }


CANDIDATE_SOURCE_SIMILARITY = "item-item-cosine"
CANDIDATE_SOURCE_POPULARITY_FILL = "popularity-fill"


@dataclass(frozen=True)
class CandidateContribution:
    """Where one retrieved candidate came from, for prediction audits."""

    movie_id: int
    source: str
    seed_movie_id: int | None
    contribution: float


@dataclass(frozen=True)
class CandidateRetrieval:
    """Retrieved candidates plus the provenance needed to explain them."""

    contributions: tuple[CandidateContribution, ...]
    seed_count: int
    excluded_count: int

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
    ) -> CandidateRetrieval:
        """Walk neighbors from positive history only, suppressing exclusions.

        The two inputs are deliberately not interchangeable. Positive history
        both seeds the walk and hides its own items; an excluded id only hides.
        A dismissal must never pull in more of the same thing, so a dismissed
        id is dropped from the seed set even if it also carries watched state
        (ADR 0012).
        """
        excluded = set(excluded_movie_ids)
        if limit <= 0:
            return CandidateRetrieval(contributions=(), seed_count=0, excluded_count=len(excluded))
        seeds = [movie_id for movie_id in positive_history_movie_ids if movie_id not in excluded]
        blocked = excluded | set(positive_history_movie_ids)
        scores: dict[int, float] = {}
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
            seed_count=len(seeds),
            excluded_count=len(excluded),
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
    manifest: ServingManifest
    candidates: CandidateIndex
    ranker: LGBMRanker

    @classmethod
    def load(cls, manifest_path: Path) -> ServingArtifactBundle:
        manifest = ServingManifest.load(manifest_path)
        artifact_dir = manifest_path.parent
        return cls(
            manifest=manifest,
            candidates=CandidateIndex.load(artifact_dir / manifest.candidate.filename),
            ranker=LGBMRanker.load_model(artifact_dir / manifest.ranker.filename),
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or path.name != value or value in {".", ".."}:
        raise ValueError(f"artifact filename must be a safe basename: {value!r}")
    return value
