"""Deterministic, checksum-pinned SASRec model artifacts (M2-02)."""

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

from .sasrec import SASRecConfig, SASRecEncoder, SASRecModel

ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_TYPE = "sasrec-retriever"
MODEL_FILENAME = "sasrec-model.zip"
MANIFEST_FILENAME = "sasrec-manifest.json"
METADATA_MEMBER = "metadata.json"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class SASRecArtifactManifest:
    schema_version: int
    artifact_type: str
    model_filename: str
    model_sha256: str
    vocabulary_sha256: str
    n_items: int
    hidden_dim: int
    max_sequence_length: int
    loss: str
    negative_count: int
    calibration_t: float
    sequence_order: str = "oldest-to-newest"
    padding: str = "left-zero"
    retrieval_normalization: str = "l2"

    @classmethod
    def load(cls, path: Path) -> SASRecArtifactManifest:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("SASRec manifest must contain an object")
        try:
            manifest = cls(
                schema_version=int(raw["schema_version"]),
                artifact_type=str(raw["artifact_type"]),
                model_filename=_safe_filename(str(raw["model_filename"])),
                model_sha256=str(raw["model_sha256"]),
                vocabulary_sha256=str(raw["vocabulary_sha256"]),
                n_items=int(raw["n_items"]),
                hidden_dim=int(raw["hidden_dim"]),
                max_sequence_length=int(raw["max_sequence_length"]),
                loss=str(raw["loss"]),
                negative_count=int(raw["negative_count"]),
                calibration_t=float(raw["calibration_t"]),
                sequence_order=str(raw["sequence_order"]),
                padding=str(raw["padding"]),
                retrieval_normalization=str(raw["retrieval_normalization"]),
            )
        except KeyError as error:
            raise ValueError(f"SASRec manifest is missing {error.args[0]!r}") from error
        manifest.validate(path.parent)
        return manifest

    def validate(self, directory: Path) -> None:
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported SASRec artifact schema {self.schema_version}")
        if self.artifact_type != ARTIFACT_TYPE:
            raise ValueError(f"unexpected SASRec artifact type {self.artifact_type!r}")
        if self.sequence_order != "oldest-to-newest" or self.padding != "left-zero":
            raise ValueError("unsupported SASRec sequence contract")
        if self.retrieval_normalization != "l2":
            raise ValueError("unsupported SASRec retrieval normalization")
        model_path = directory / self.model_filename
        if not model_path.is_file():
            raise ValueError(f"SASRec model artifact is missing: {self.model_filename}")
        actual = file_sha256(model_path)
        if actual != self.model_sha256:
            raise ValueError(
                f"SASRec model checksum mismatch: expected {self.model_sha256}, got {actual}"
            )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: Path) -> None:
        _write_new(
            path,
            (json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n").encode(),
        )


def export_sasrec(model: SASRecModel, directory: Path) -> SASRecArtifactManifest:
    """Write a new immutable artifact directory; never replace an existing run."""
    if model._encoder is None or not model._index_to_item:
        raise ValueError("cannot export an unfitted SASRec model")
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / MODEL_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    if model_path.exists() or manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite SASRec artifact in {directory}")

    item_ids = [model._index_to_item[index] for index in range(1, len(model._index_to_item) + 1)]
    vocabulary_sha256 = _vocabulary_sha256(item_ids)
    metadata = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "config": model.config.as_params(),
        "cold_start_threshold": model.cold_start_threshold,
        "item_ids": item_ids,
        "unknown_index": model._unknown_index,
        "vocabulary_sha256": vocabulary_sha256,
        "state_keys": sorted(model._encoder.state_dict()),
    }
    _write_model_archive(model_path, metadata, model._encoder.state_dict())
    manifest = SASRecArtifactManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        artifact_type=ARTIFACT_TYPE,
        model_filename=MODEL_FILENAME,
        model_sha256=file_sha256(model_path),
        vocabulary_sha256=vocabulary_sha256,
        n_items=len(item_ids),
        hidden_dim=model.config.hidden_dim,
        max_sequence_length=model.config.max_sequence_length,
        loss=model.config.loss,
        negative_count=model.config.negative_count,
        calibration_t=model.config.calibration_t,
    )
    manifest.write(manifest_path)
    return manifest


def load_sasrec(manifest_path: Path) -> SASRecModel:
    """Load and validate a SASRec artifact, rebuilding its deterministic index."""
    manifest = SASRecArtifactManifest.load(manifest_path)
    metadata, arrays = _read_model_archive(manifest_path.parent / manifest.model_filename)
    _validate_metadata(metadata, manifest)

    config_raw = metadata["config"]
    if not isinstance(config_raw, dict):
        raise ValueError("SASRec artifact config must be an object")
    config = SASRecConfig(**config_raw)
    config.validate()
    item_ids = _integer_list(metadata["item_ids"], name="item_ids")
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("SASRec artifact item vocabulary contains duplicates")

    model = SASRecModel(
        config=config,
        cold_start_threshold=(
            None
            if metadata["cold_start_threshold"] is None
            else int(metadata["cold_start_threshold"])
        ),
    )
    model._item_to_index = {item_id: index + 1 for index, item_id in enumerate(item_ids)}
    model._index_to_item = {index: item_id for item_id, index in model._item_to_index.items()}
    model._unknown_index = int(metadata["unknown_index"])
    if model._unknown_index != len(item_ids) + 1:
        raise ValueError("SASRec artifact unknown index does not follow the vocabulary")
    model._encoder = SASRecEncoder(len(item_ids) + 2, config)

    expected = model._encoder.state_dict()
    if set(arrays) != set(expected):
        raise ValueError("SASRec artifact tensor names do not match the encoder")
    state: dict[str, torch.Tensor] = {}
    for name, expected_tensor in expected.items():
        array = arrays[name]
        if tuple(array.shape) != tuple(expected_tensor.shape):
            expected_shape = tuple(expected_tensor.shape)
            raise ValueError(
                f"SASRec tensor {name!r} has shape {array.shape}, expected {expected_shape}"
            )
        tensor = torch.from_numpy(array.copy())
        if tensor.dtype != expected_tensor.dtype:
            raise ValueError(
                f"SASRec tensor {name!r} has dtype {tensor.dtype}, expected {expected_tensor.dtype}"
            )
        state[name] = tensor
    model._encoder.load_state_dict(state, strict=True)
    model.build_index()
    return model


def _write_model_archive(
    path: Path,
    metadata: dict[str, Any],
    state: dict[str, torch.Tensor],
) -> None:
    buffer = io.BytesIO()
    # Stored members avoid zlib-version-dependent bytes. The state is small
    # enough that cross-environment reproducibility is worth more than saving a
    # few megabytes in an already content-addressed artifact store.
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        _zip_write(
            archive,
            METADATA_MEMBER,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
        )
        for name in sorted(state):
            tensor_buffer = io.BytesIO()
            np.save(
                tensor_buffer, state[name].detach().cpu().contiguous().numpy(), allow_pickle=False
            )
            _zip_write(archive, f"weights/{name}.npy", tensor_buffer.getvalue())
    _write_new(path, buffer.getvalue())


def _read_model_archive(path: Path) -> tuple[dict[str, Any], dict[str, np.ndarray[Any, Any]]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("SASRec artifact contains duplicate members")
            if METADATA_MEMBER not in names:
                raise ValueError("SASRec artifact has no metadata")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("SASRec artifact contains an unsafe member name")
            metadata_raw = json.loads(archive.read(METADATA_MEMBER))
            if not isinstance(metadata_raw, dict):
                raise ValueError("SASRec artifact metadata must contain an object")
            state_keys = metadata_raw.get("state_keys")
            if not isinstance(state_keys, list) or not all(
                isinstance(key, str) for key in state_keys
            ):
                raise ValueError("SASRec artifact state key list is invalid")
            expected_members = {METADATA_MEMBER} | {f"weights/{key}.npy" for key in state_keys}
            if set(names) != expected_members:
                raise ValueError("SASRec artifact members do not match its metadata")
            arrays = {
                key: np.load(io.BytesIO(archive.read(f"weights/{key}.npy")), allow_pickle=False)
                for key in state_keys
            }
            return metadata_raw, arrays
    except (zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError("SASRec model artifact is not a valid archive") from error


def _validate_metadata(metadata: dict[str, Any], manifest: SASRecArtifactManifest) -> None:
    if int(metadata.get("schema_version", -1)) != manifest.schema_version:
        raise ValueError("SASRec artifact schema does not match its manifest")
    if metadata.get("artifact_type") != manifest.artifact_type:
        raise ValueError("SASRec artifact type does not match its manifest")
    item_ids = _integer_list(metadata.get("item_ids"), name="item_ids")
    if len(item_ids) != manifest.n_items:
        raise ValueError("SASRec artifact item count does not match its manifest")
    vocabulary_sha256 = _vocabulary_sha256(item_ids)
    if (
        vocabulary_sha256 != manifest.vocabulary_sha256
        or metadata.get("vocabulary_sha256") != manifest.vocabulary_sha256
    ):
        raise ValueError("SASRec artifact vocabulary fingerprint mismatch")
    config = metadata.get("config")
    if not isinstance(config, dict):
        raise ValueError("SASRec artifact config must be an object")
    manifest_fields = {
        "hidden_dim": manifest.hidden_dim,
        "max_sequence_length": manifest.max_sequence_length,
        "loss": manifest.loss,
        "negative_count": manifest.negative_count,
        "calibration_t": manifest.calibration_t,
    }
    if any(config.get(key) != value for key, value in manifest_fields.items()):
        raise ValueError("SASRec artifact config does not match its manifest")


def _integer_list(value: Any, *, name: str) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise ValueError(f"SASRec artifact {name} must be an integer list")
    return value


def _vocabulary_sha256(item_ids: list[int]) -> str:
    canonical = json.dumps(item_ids, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}") from None


def _safe_filename(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or path.name != value or value in {".", ".."}:
        raise ValueError(f"artifact filename must be a safe basename: {value!r}")
    return value
