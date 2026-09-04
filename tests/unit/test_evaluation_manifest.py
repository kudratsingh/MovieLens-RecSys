"""The protocol manifest is the comparability boundary, not descriptive metadata."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.evaluation.manifest import (
    MLFLOW_PROTOCOL_HASH_PARAM,
    MLFLOW_PROTOCOL_SCHEMA_PARAM,
    MLFLOW_PROTOCOL_TAG,
    PROTOCOL_SCHEMA_VERSION,
    ProtocolManifest,
    ProtocolManifestError,
    ProtocolMismatchError,
    RunIdentity,
    validate_metric_value,
)


def _manifest(**changes: object) -> ProtocolManifest:
    values: dict[str, object] = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "raw_data_revision": "md5:c3ce6309.dir",
        "derived_snapshot_hash": "sha256:split-v1",
        "event_schema_version": "movielens-rating-v1",
        "train_cutoff": 1_000,
        "holdout_start": 1_000,
        "holdout_end": 2_000,
        "backtest_window_id": "fixed-holdout-v1",
        "timestamp_unit": "unix-seconds",
        "timezone": "UTC",
        "label_contract_version": "implicit-positive-v1",
        "relevance_definition": "every-rating-is-positive",
        "eligible_user_policy": "at-least-one-holdout-positive",
        "catalog_fingerprint": "sha256:catalog-v1",
        "unknown_item_policy": "exclude",
        "cold_start_threshold": 10,
        "learned_routing_policy": "history-count-gte-threshold",
        "fallback_policy": "training-popularity",
        "positive_history_filter": "strictly-before-prediction",
        "seen_item_filter": "exclude-all-positive-history",
        "dismissal_filter": "exclude-known-dismissals",
        "target_filter": "exclude-target-from-context",
        "candidate_filter": "eligible-training-catalog",
        "feature_contract_version": "candidate-v1",
        "point_in_time_semantics": "strictly-earlier-event-time",
        "stage": "retrieval",
        "primary_metric": "recall",
        "metric_contract_version": "evaluation-v1",
        "metric_aggregation": "unweighted-user-mean",
        "k": 500,
        "slice_definition": "warm-gte-10;cold-lt-10;overall=union",
    }
    values.update(changes)
    return ProtocolManifest(**values)  # type: ignore[arg-type]


def test_canonical_json_and_hash_ignore_mapping_order():
    manifest = _manifest()
    reversed_payload = dict(reversed(list(manifest.to_dict().items())))

    rebuilt = ProtocolManifest.from_dict(reversed_payload)

    assert rebuilt.canonical_json() == manifest.canonical_json()
    assert rebuilt.semantic_hash == manifest.semantic_hash
    assert manifest.semantic_hash.startswith("sha256:")


def test_round_trip_is_exact_and_rejects_unknown_or_missing_fields():
    manifest = _manifest()
    assert ProtocolManifest.from_json(manifest.canonical_json()) == manifest

    missing = manifest.to_dict()
    del missing["catalog_fingerprint"]
    with pytest.raises(ProtocolManifestError, match="missing fields: catalog_fingerprint"):
        ProtocolManifest.from_dict(missing)

    unknown = {**manifest.to_dict(), "display_name": "not semantic"}
    with pytest.raises(ProtocolManifestError, match="unknown fields: display_name"):
        ProtocolManifest.from_dict(unknown)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": 2}, "unsupported protocol schema_version"),
        ({"stage": "ranking", "primary_metric": "recall"}, "requires primary_metric='ndcg'"),
        ({"k": 0}, "k must be a positive integer"),
        ({"cold_start_threshold": -1}, "cold_start_threshold"),
        ({"holdout_start": 2_000}, "holdout_start must be before holdout_end"),
        ({"train_cutoff": 1_001}, "train_cutoff must not be after holdout_start"),
        ({"catalog_fingerprint": ""}, "catalog_fingerprint must be a non-empty string"),
        ({"catalog_fingerprint": " hash "}, "leading or trailing whitespace"),
    ],
)
def test_invalid_semantics_fail_closed(changes: dict[str, object], message: str):
    with pytest.raises(ProtocolManifestError, match=message):
        _manifest(**changes)


def test_mismatch_lists_every_different_semantic_field():
    left = _manifest()
    right = replace(left, catalog_fingerprint="sha256:new", seen_item_filter="none")

    assert left.mismatches(right) == {
        "catalog_fingerprint": ("sha256:catalog-v1", "sha256:new"),
        "seen_item_filter": ("exclude-all-positive-history", "none"),
    }
    with pytest.raises(ProtocolMismatchError) as error:
        left.assert_compatible(right)
    assert set(error.value.mismatches) == {"catalog_fingerprint", "seen_item_filter"}


def test_every_semantic_field_moves_the_hash():
    manifest = _manifest()
    baseline = manifest.semantic_hash
    for name, value in manifest.to_dict().items():
        if name == "schema_version":
            continue
        if isinstance(value, int):
            replacement: object = value + 1
            if name == "holdout_start":
                replacement = value + 1
        elif name == "stage":
            replacement = "ranking"
        elif name == "primary_metric":
            replacement = "ndcg"
        else:
            replacement = f"{value}-changed"
        changes = {name: replacement}
        if name == "stage":
            changes["primary_metric"] = "ndcg"
        if name == "primary_metric":
            changes["stage"] = "ranking"
        if name == "train_cutoff":
            changes["holdout_start"] = int(replacement)
        if name == "holdout_end":
            changes["holdout_end"] = int(value) + 1
        assert replace(manifest, **changes).semantic_hash != baseline


def test_run_identity_does_not_move_the_semantic_hash():
    manifest = _manifest()
    first = RunIdentity(
        run_id="run-1",
        code_sha="abc123",
        dirty_worktree=False,
        environment_fingerprint="sha256:env-a",
        model_config_hash="sha256:config",
        seed=42,
        hardware="m3-pro",
        started_at="2026-09-04T00:00:00Z",
    )
    second = replace(first, run_id="run-2", seed=7, hardware="cloud-gpu")

    assert first != second
    assert manifest.semantic_hash == _manifest().semantic_hash


def test_mlflow_fields_are_self_verifying():
    manifest = _manifest()

    assert manifest.mlflow_params() == {
        MLFLOW_PROTOCOL_HASH_PARAM: manifest.semantic_hash,
        MLFLOW_PROTOCOL_SCHEMA_PARAM: str(PROTOCOL_SCHEMA_VERSION),
    }
    assert ProtocolManifest.from_json(manifest.mlflow_tags()[MLFLOW_PROTOCOL_TAG]) == manifest


def test_non_object_or_non_json_payload_is_rejected():
    with pytest.raises(ProtocolManifestError, match="one object"):
        ProtocolManifest.from_json(json.dumps([1, 2, 3]))
    with pytest.raises(ProtocolManifestError, match="not valid JSON"):
        ProtocolManifest.from_json("{")


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_metric_validation_rejects_invalid_values(value: object):
    with pytest.raises(ProtocolManifestError):
        validate_metric_value("warm recall", value)  # type: ignore[arg-type]
