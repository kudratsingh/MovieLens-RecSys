"""Canonical identity for an offline evaluation question.

Metric values are comparable only when the data, time window, labels, routing,
filtering, catalog, features, stage and metric all mean the same thing.  This
module gives that semantic contract one versioned, content-addressed shape.

Run-specific provenance (code, environment, hardware and seed) is deliberately
separate.  It is necessary to reproduce a run, but changing hardware must not
make two otherwise identical evaluation questions semantically different.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Final, Literal

PROTOCOL_SCHEMA_VERSION: Final = 1
PROTOCOL_HASH_ALGORITHM: Final = "sha256"
MLFLOW_PROTOCOL_TAG: Final = "evaluation_protocol"
MLFLOW_PROTOCOL_HASH_PARAM: Final = "evaluation_protocol_hash"
MLFLOW_PROTOCOL_SCHEMA_PARAM: Final = "evaluation_protocol_schema_version"

EvaluationStage = Literal["retrieval", "ranking", "reranking"]
PrimaryMetric = Literal["recall", "ndcg"]


class ProtocolManifestError(ValueError):
    """A protocol is incomplete, malformed, or semantically inconsistent."""


class ProtocolMismatchError(ProtocolManifestError):
    """Two valid manifests describe different evaluation questions."""

    def __init__(self, mismatches: Mapping[str, tuple[object, object]]) -> None:
        self.mismatches = dict(mismatches)
        detail = ", ".join(
            f"{name}={left!r}/{right!r}" for name, (left, right) in self.mismatches.items()
        )
        super().__init__(f"evaluation protocols are not comparable: {detail}")


@dataclass(frozen=True)
class ProtocolManifest:
    """Every semantic field required to decide whether results are comparable.

    Identifiers are values, not filesystem paths.  For example,
    ``raw_data_revision`` is the immutable DVC object hash and
    ``catalog_fingerprint`` is a content hash of the eligible item IDs.
    """

    schema_version: int
    raw_data_revision: str
    derived_snapshot_hash: str
    event_schema_version: str
    train_cutoff: int
    holdout_start: int
    holdout_end: int
    backtest_window_id: str
    timestamp_unit: str
    timezone: str
    label_contract_version: str
    relevance_definition: str
    eligible_user_policy: str
    catalog_fingerprint: str
    unknown_item_policy: str
    cold_start_threshold: int
    learned_routing_policy: str
    fallback_policy: str
    positive_history_filter: str
    seen_item_filter: str
    dismissal_filter: str
    target_filter: str
    candidate_filter: str
    feature_contract_version: str
    point_in_time_semantics: str
    stage: EvaluationStage
    primary_metric: PrimaryMetric
    metric_contract_version: str
    metric_aggregation: str
    k: int
    slice_definition: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PROTOCOL_SCHEMA_VERSION:
            raise ProtocolManifestError(
                f"unsupported protocol schema_version {self.schema_version!r}; "
                f"expected {PROTOCOL_SCHEMA_VERSION}"
            )
        if self.stage not in ("retrieval", "ranking", "reranking"):
            raise ProtocolManifestError(f"unsupported evaluation stage {self.stage!r}")
        if self.primary_metric not in ("recall", "ndcg"):
            raise ProtocolManifestError(f"unsupported primary metric {self.primary_metric!r}")
        expected_metric = "recall" if self.stage == "retrieval" else "ndcg"
        if self.primary_metric != expected_metric:
            raise ProtocolManifestError(
                f"stage {self.stage!r} requires primary_metric={expected_metric!r}, "
                f"got {self.primary_metric!r}"
            )
        if type(self.k) is not int or self.k <= 0:
            raise ProtocolManifestError(f"k must be a positive integer, got {self.k!r}")
        if type(self.cold_start_threshold) is not int or self.cold_start_threshold < 0:
            raise ProtocolManifestError(
                "cold_start_threshold must be a non-negative integer, "
                f"got {self.cold_start_threshold!r}"
            )
        for name in ("train_cutoff", "holdout_start", "holdout_end"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ProtocolManifestError(
                    f"{name} must be a non-negative integer timestamp, got {value!r}"
                )
        if self.train_cutoff > self.holdout_start:
            raise ProtocolManifestError("train_cutoff must not be after holdout_start")
        if self.holdout_start >= self.holdout_end:
            raise ProtocolManifestError("holdout_start must be before holdout_end")

        integer_fields = {
            "schema_version",
            "train_cutoff",
            "holdout_start",
            "holdout_end",
            "cold_start_threshold",
            "k",
        }
        for manifest_field in fields(self):
            if manifest_field.name in integer_fields:
                continue
            value = getattr(self, manifest_field.name)
            if not isinstance(value, str) or not value.strip():
                raise ProtocolManifestError(
                    f"{manifest_field.name} must be a non-empty string, got {value!r}"
                )
            if value != value.strip():
                raise ProtocolManifestError(
                    f"{manifest_field.name} has leading or trailing whitespace"
                )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe semantic payload in schema field order."""
        return asdict(self)

    def canonical_json(self) -> str:
        """Serialize deterministically; NaN/Infinity and display whitespace are illegal."""
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def semantic_hash(self) -> str:
        """Content identity of the complete semantic protocol."""
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return f"{PROTOCOL_HASH_ALGORITHM}:{digest}"

    def mismatches(self, other: ProtocolManifest) -> dict[str, tuple[object, object]]:
        """Return every semantic field that differs, never just the first one."""
        return {
            manifest_field.name: (
                getattr(self, manifest_field.name),
                getattr(other, manifest_field.name),
            )
            for manifest_field in fields(self)
            if getattr(self, manifest_field.name) != getattr(other, manifest_field.name)
        }

    def assert_compatible(self, other: ProtocolManifest) -> None:
        """Raise with all differences when ``other`` asks a different question."""
        mismatches = self.mismatches(other)
        if mismatches:
            raise ProtocolMismatchError(mismatches)

    def mlflow_params(self) -> dict[str, str]:
        """Minimal indexed fields; the complete contract is stored as a tag."""
        return {
            MLFLOW_PROTOCOL_HASH_PARAM: self.semantic_hash,
            MLFLOW_PROTOCOL_SCHEMA_PARAM: str(self.schema_version),
        }

    def mlflow_tags(self) -> dict[str, str]:
        """The canonical payload required to recalculate and verify the hash."""
        return {MLFLOW_PROTOCOL_TAG: self.canonical_json()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProtocolManifest:
        """Load strictly: missing and unknown fields are both invalid."""
        expected = {manifest_field.name for manifest_field in fields(cls)}
        supplied = set(payload)
        missing = sorted(expected - supplied)
        unknown = sorted(supplied - expected)
        if missing or unknown:
            problems = []
            if missing:
                problems.append(f"missing fields: {', '.join(missing)}")
            if unknown:
                problems.append(f"unknown fields: {', '.join(unknown)}")
            raise ProtocolManifestError("; ".join(problems))
        try:
            return cls(**dict(payload))
        except TypeError as exc:
            raise ProtocolManifestError(f"invalid protocol field types: {exc}") from exc

    @classmethod
    def from_json(cls, value: str) -> ProtocolManifest:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProtocolManifestError(f"protocol is not valid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ProtocolManifestError("protocol JSON must contain one object")
        return cls.from_dict(payload)


@dataclass(frozen=True)
class RunIdentity:
    """Reproduction identity deliberately excluded from ``semantic_hash``."""

    run_id: str
    code_sha: str
    dirty_worktree: bool
    environment_fingerprint: str
    model_config_hash: str
    seed: int | None
    hardware: str
    started_at: str

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "code_sha",
            "environment_fingerprint",
            "model_config_hash",
            "hardware",
            "started_at",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ProtocolManifestError(f"{name} must be a normalized non-empty string")
        if type(self.dirty_worktree) is not bool:
            raise ProtocolManifestError("dirty_worktree must be a boolean")
        if self.seed is not None and type(self.seed) is not int:
            raise ProtocolManifestError("seed must be an integer or null")


def validate_metric_value(name: str, value: float) -> None:
    """Shared strict validation for bounded offline metrics."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProtocolManifestError(f"{name} must be numeric, got {value!r}")
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ProtocolManifestError(f"{name} must be finite and in [0, 1], got {value!r}")
