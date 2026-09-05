"""Deterministic, checksum-pinned two-tower model artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from src.models.artifacts import file_sha256

from .item_features import ItemFeatureSchema
from .twotower import ItemTower, TwoTowerConfig, TwoTowerModel

ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_TYPE = "two-tower-retriever"
MODEL_FILENAME = "two-tower-model.zip"
MANIFEST_FILENAME = "two-tower-manifest.json"
METADATA_MEMBER = "metadata.json"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class TwoTowerArtifactManifest:
    schema_version: int
    artifact_type: str
    model_filename: str
    model_sha256: str
    vocabulary_sha256: str
    n_items: int
    embedding_dim: int
    history_window: int
    item_features_fitted: bool
    retrieval_normalization: str = "l2"

    @classmethod
    def load(cls, path: Path) -> TwoTowerArtifactManifest:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("two-tower manifest must contain an object")
        try:
            item_features_fitted = raw["item_features_fitted"]
            if type(item_features_fitted) is not bool:
                raise ValueError("two-tower manifest item_features_fitted must be a boolean")
            manifest = cls(
                schema_version=int(raw["schema_version"]),
                artifact_type=str(raw["artifact_type"]),
                model_filename=_safe_filename(str(raw["model_filename"])),
                model_sha256=str(raw["model_sha256"]),
                vocabulary_sha256=str(raw["vocabulary_sha256"]),
                n_items=int(raw["n_items"]),
                embedding_dim=int(raw["embedding_dim"]),
                history_window=int(raw["history_window"]),
                item_features_fitted=item_features_fitted,
                retrieval_normalization=str(raw["retrieval_normalization"]),
            )
        except KeyError as error:
            raise ValueError(f"two-tower manifest is missing {error.args[0]!r}") from error
        manifest.validate(path.parent)
        return manifest

    def validate(self, directory: Path) -> None:
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported two-tower artifact schema {self.schema_version}")
        if self.artifact_type != ARTIFACT_TYPE:
            raise ValueError(f"unexpected two-tower artifact type {self.artifact_type!r}")
        if self.retrieval_normalization != "l2":
            raise ValueError("unsupported two-tower retrieval normalization")
        if self.n_items <= 0 or self.embedding_dim <= 0 or self.history_window <= 0:
            raise ValueError("two-tower manifest dimensions and item count must be positive")
        model_path = directory / self.model_filename
        if not model_path.is_file():
            raise ValueError(f"two-tower model artifact is missing: {self.model_filename}")
        actual = file_sha256(model_path)
        if actual != self.model_sha256:
            raise ValueError(
                f"two-tower model checksum mismatch: expected {self.model_sha256}, got {actual}"
            )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: Path) -> None:
        _write_new(path, (json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n").encode())


def export_twotower(model: TwoTowerModel, directory: Path) -> TwoTowerArtifactManifest:
    """Write a complete new model bundle without replacing any prior bytes."""
    if model._item_tower is None or not model._index_to_item:
        raise ValueError("cannot export an unfitted two-tower model")
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / MODEL_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    if model_path.exists() or manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite two-tower artifact in {directory}")

    item_ids = [model._index_to_item[index] for index in range(1, len(model._index_to_item) + 1)]
    vocabulary_sha256 = _vocabulary_sha256(item_ids)
    metadata = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "config": model.config.as_params(),
        "cold_start_threshold": model.cold_start_threshold,
        "item_ids": item_ids,
        "item_feature_schema": (
            None if model._item_feature_schema is None else model._item_feature_schema.to_dict()
        ),
        "vocabulary_sha256": vocabulary_sha256,
        "state_keys": sorted(model._item_tower.state_dict()),
    }
    _write_model_archive(model_path, metadata, model._item_tower.state_dict())
    manifest = TwoTowerArtifactManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        artifact_type=ARTIFACT_TYPE,
        model_filename=MODEL_FILENAME,
        model_sha256=file_sha256(model_path),
        vocabulary_sha256=vocabulary_sha256,
        n_items=len(item_ids),
        embedding_dim=model.config.embedding_dim,
        history_window=model.config.history_window,
        item_features_fitted=model._item_feature_schema is not None,
    )
    manifest.write(manifest_path)
    return manifest


def load_twotower(manifest_path: Path) -> TwoTowerModel:
    """Checksum-load a two-tower artifact and rebuild its retrieval index."""
    manifest = TwoTowerArtifactManifest.load(manifest_path)
    metadata, arrays = _read_model_archive(manifest_path.parent / manifest.model_filename)
    _validate_metadata(metadata, manifest)

    config_raw = metadata["config"]
    if not isinstance(config_raw, dict):
        raise ValueError("two-tower artifact config must be an object")
    config = TwoTowerConfig(**config_raw)
    if config.embedding_dim != manifest.embedding_dim:
        raise ValueError("two-tower manifest embedding dimension does not match the archive")
    if config.history_window != manifest.history_window:
        raise ValueError("two-tower manifest history window does not match the archive")
    item_ids = _integer_list(metadata["item_ids"], name="item_ids")
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("two-tower artifact item vocabulary contains duplicates")
    schema = _feature_schema(metadata["item_feature_schema"])
    side_features = (
        torch.from_numpy(arrays["side_features"].copy()) if "side_features" in arrays else None
    )
    if (schema is None) != (side_features is None):
        raise ValueError("two-tower feature schema and feature tensor disagree")
    if (schema is not None) != manifest.item_features_fitted:
        raise ValueError("two-tower manifest feature flag does not match the archive")

    model = TwoTowerModel(
        config=config,
        cold_start_threshold=(
            None
            if metadata["cold_start_threshold"] is None
            else int(metadata["cold_start_threshold"])
        ),
    )
    model._item_to_index = {item_id: index + 1 for index, item_id in enumerate(item_ids)}
    model._index_to_item = {index: item_id for item_id, index in model._item_to_index.items()}
    model._item_feature_schema = schema
    model._item_tower = ItemTower(len(item_ids), config.embedding_dim, side_features=side_features)

    expected = model._item_tower.state_dict()
    if set(arrays) != set(expected):
        raise ValueError("two-tower artifact tensor names do not match the item tower")
    if set(metadata["state_keys"]) != set(arrays):
        raise ValueError("two-tower declared tensor names do not match the archive")
    state: dict[str, torch.Tensor] = {}
    for name, expected_tensor in expected.items():
        array = arrays[name]
        if tuple(array.shape) != tuple(expected_tensor.shape):
            raise ValueError(
                f"two-tower tensor {name!r} has shape {array.shape}, "
                f"expected {tuple(expected_tensor.shape)}"
            )
        tensor = torch.from_numpy(array.copy())
        if tensor.dtype != expected_tensor.dtype:
            raise ValueError(
                f"two-tower tensor {name!r} has dtype {tensor.dtype}, "
                f"expected {expected_tensor.dtype}"
            )
        state[name] = tensor
    model._item_tower.load_state_dict(state, strict=True)
    model._build_faiss_index(len(item_ids))
    return model


def _feature_schema(raw: object) -> ItemFeatureSchema | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("two-tower item feature schema must be an object or null")
    try:
        genres_raw = raw["genres"]
        if not isinstance(genres_raw, list):
            raise ValueError("two-tower item feature genres must be a list")
        schema = ItemFeatureSchema(
            genres=tuple(str(value) for value in genres_raw),
            release_year_mean=float(raw["release_year_mean"]),
            release_year_std=float(raw["release_year_std"]),
        )
        feature_names = raw.get("feature_names")
        if feature_names is not None and feature_names != list(schema.feature_names):
            raise ValueError("two-tower item feature names do not match the fitted schema")
        return schema
    except KeyError as error:
        raise ValueError(f"two-tower item feature schema is missing {error.args[0]!r}") from error


def _write_model_archive(
    path: Path,
    metadata: dict[str, Any],
    state: dict[str, torch.Tensor],
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        _zip_write(
            archive,
            METADATA_MEMBER,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
        )
        for name in sorted(state):
            tensor_buffer = io.BytesIO()
            np.save(
                tensor_buffer,
                state[name].detach().cpu().contiguous().numpy(),
                allow_pickle=False,
            )
            _zip_write(archive, f"weights/{name}.npy", tensor_buffer.getvalue())
    _write_new(path, buffer.getvalue())


def _read_model_archive(path: Path) -> tuple[dict[str, Any], dict[str, np.ndarray[Any, Any]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("two-tower artifact contains duplicate members")
            if METADATA_MEMBER not in names:
                raise ValueError("two-tower artifact has no metadata")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("two-tower artifact contains an unsafe member name")
            metadata_raw = json.loads(archive.read(METADATA_MEMBER))
            if not isinstance(metadata_raw, dict):
                raise ValueError("two-tower artifact metadata must contain an object")
            arrays = {
                name.removeprefix("weights/").removesuffix(".npy"): np.load(
                    io.BytesIO(archive.read(name)), allow_pickle=False
                )
                for name in names
                if name.startswith("weights/") and name.endswith(".npy")
            }
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read two-tower artifact: {error}") from error
    return metadata_raw, arrays


def _validate_metadata(metadata: dict[str, Any], manifest: TwoTowerArtifactManifest) -> None:
    if int(metadata.get("schema_version", -1)) != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("two-tower model archive schema does not match the loader")
    if metadata.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("two-tower model archive has the wrong artifact type")
    items = _integer_list(metadata.get("item_ids"), name="item_ids")
    if len(items) != manifest.n_items:
        raise ValueError("two-tower manifest item count does not match the model archive")
    if _vocabulary_sha256(items) != manifest.vocabulary_sha256:
        raise ValueError("two-tower vocabulary fingerprint does not match the manifest")
    state_keys = metadata.get("state_keys")
    if not isinstance(state_keys, list) or not all(isinstance(value, str) for value in state_keys):
        raise ValueError("two-tower model archive state_keys must be a list of strings")


def _integer_list(raw: object, *, name: str) -> list[int]:
    if not isinstance(raw, list) or any(type(value) is not int for value in raw):
        raise ValueError(f"two-tower artifact {name} must be a list of integers")
    return [int(value) for value in raw]


def _vocabulary_sha256(item_ids: list[int]) -> str:
    payload = json.dumps(item_ids, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _zip_write(archive: zipfile.ZipFile, name: str, value: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    archive.writestr(info, value)


def _safe_filename(value: str) -> str:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ValueError(f"unsafe two-tower artifact filename {value!r}")
    return value


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
