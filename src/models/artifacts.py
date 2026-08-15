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

    def retrieve(self, history_movie_ids: list[int], *, limit: int) -> list[int]:
        """Aggregate neighbor similarity and exclude every live seen item."""
        if limit <= 0:
            return []
        seen = set(history_movie_ids)
        scores: dict[int, float] = defaultdict(float)
        for source_id in history_movie_ids:
            for candidate_id, similarity in self.neighbors.get(source_id, ()):
                if candidate_id not in seen:
                    scores[candidate_id] += similarity
        ranked = [
            item_id
            for item_id, _ in sorted(scores.items(), key=lambda value: (-value[1], value[0]))
        ]
        selected = ranked[:limit]
        selected_set = set(selected)
        for item_id in self.popularity:
            if len(selected) >= limit:
                break
            if item_id not in seen and item_id not in selected_set:
                selected.append(item_id)
                selected_set.add(item_id)
        return selected

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
