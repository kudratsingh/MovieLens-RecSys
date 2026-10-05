"""SASRec v1's encoder exactly as it was before WO-2, kept as a test reference only.

Rule D7 bans PyTorch's packaged Transformer classes from ``src/``; they may live
here, where their only job is to be the yardstick the hand-written encoder is
measured against. It is ``SASRecEncoder`` as of commit ``d8d58a8`` (the last
``main`` before WO-2) with two changes that leave the arithmetic alone:
``enable_nested_tensor=False`` silences the warning PyTorch emits because
``norm_first=True`` already rules nested tensors out, and the process-wide
fast-path switch is gone from ``encode_positions``. Callers that need the fast
path off (every padded eval-mode comparison) turn it off around the call with
``fastpath(False)``, which restores the previous setting afterwards.

Its ``state_dict`` names are the ones every pre-WO-2 archive was written with,
so ``legacy_state_to_hand_written`` can be exercised against the real thing
rather than a hand-written list of keys.
"""

from __future__ import annotations

import contextlib
import json
import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn

from src.models.artifacts import file_sha256
from src.models.candidates.sasrec import SASRecConfig
from src.models.candidates.sasrec_artifact import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    _read_model_archive,
    _write_model_archive,
)


class LegacySASRecEncoder(nn.Module):
    def __init__(self, n_item_rows: int, config: SASRecConfig) -> None:
        super().__init__()
        self.config = config
        self.item_embedding = nn.Embedding(n_item_rows, config.hidden_dim, padding_idx=0)
        self.position_embedding = nn.Embedding(config.max_sequence_length, config.hidden_dim)
        layer = nn.TransformerEncoderLayer(
            d_model=config.hidden_dim,
            nhead=config.num_heads,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(
            layer, num_layers=config.num_blocks, enable_nested_tensor=False
        )
        self.output_norm = nn.LayerNorm(config.hidden_dim)
        nn.init.normal_(self.item_embedding.weight, std=1.0 / math.sqrt(config.hidden_dim))
        with torch.no_grad():
            self.item_embedding.weight[0].zero_()

    def encode_positions(self, sequences: torch.Tensor) -> torch.Tensor:
        length = sequences.shape[1]
        positions = torch.arange(length, device=sequences.device).unsqueeze(0)
        values = self.item_embedding(sequences) + self.position_embedding(positions)
        causal_mask = torch.triu(
            torch.ones(length, length, dtype=torch.bool, device=sequences.device), diagonal=1
        )
        encoded = self.transformer(values, mask=causal_mask, src_key_padding_mask=sequences.eq(0))
        normalized: torch.Tensor = self.output_norm(encoded)
        return normalized.masked_fill(sequences.eq(0).unsqueeze(-1), 0.0)

    def forward(self, sequences: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.encode_positions(sequences)[:, -1, :], p=2, dim=-1)

    def training_user_vectors(self, sequences: torch.Tensor) -> torch.Tensor:
        return self.encode_positions(sequences)[:, -1, :]

    def item_vectors(self, item_ids: torch.Tensor, *, normalize: bool = True) -> torch.Tensor:
        vectors = self.item_embedding(item_ids)
        return F.normalize(vectors, p=2, dim=-1) if normalize else vectors

    def state_arrays(self) -> dict[str, np.ndarray]:
        """The state dict as the NumPy arrays a v1 archive stores."""
        return {name: tensor.detach().numpy().copy() for name, tensor in self.state_dict().items()}


@contextlib.contextmanager
def fastpath(enabled: bool) -> Iterator[None]:
    """Set PyTorch's process-wide attention fast-path flag, and put it back after."""
    previous = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(enabled)
    try:
        yield
    finally:
        torch.backends.mha.set_fastpath_enabled(previous)


def hand_written_state_to_legacy(
    state: dict[str, torch.Tensor], *, num_blocks: int
) -> dict[str, np.ndarray]:
    """The inverse of ``legacy_state_to_hand_written``: pack back into v1's names.

    Lets a test take a model trained with the hand-written encoder and run the
    very same weights through the packaged one, and lets it write a v1-layout
    archive without needing a real pre-WO-2 artifact on disk.
    """
    arrays = {name: tensor.detach().numpy().copy() for name, tensor in state.items()}
    legacy: dict[str, np.ndarray] = {
        name: arrays.pop(name)
        for name in (
            "item_embedding.weight",
            "position_embedding.weight",
            "output_norm.weight",
            "output_norm.bias",
        )
    }
    renames = {
        "attention.output.weight": "self_attn.out_proj.weight",
        "attention.output.bias": "self_attn.out_proj.bias",
        "feed_forward.expand.weight": "linear1.weight",
        "feed_forward.expand.bias": "linear1.bias",
        "feed_forward.contract.weight": "linear2.weight",
        "feed_forward.contract.bias": "linear2.bias",
        "attention_norm.weight": "norm1.weight",
        "attention_norm.bias": "norm1.bias",
        "feed_forward_norm.weight": "norm2.weight",
        "feed_forward_norm.bias": "norm2.bias",
    }
    for block in range(num_blocks):
        new = f"transformer.blocks.{block}."
        old = f"transformer.layers.{block}."
        for kind, packed in (("weight", "in_proj_weight"), ("bias", "in_proj_bias")):
            legacy[f"{old}self_attn.{packed}"] = np.concatenate(
                [arrays.pop(f"{new}attention.{part}.{kind}") for part in ("query", "key", "value")]
            )
        for hand_written, packaged in renames.items():
            legacy[old + packaged] = arrays.pop(new + hand_written)
    assert not arrays, f"unmapped tensors: {sorted(arrays)}"
    return legacy


def legacy_encoder_from(model_encoder: nn.Module, config: SASRecConfig) -> LegacySASRecEncoder:
    """A packaged-encoder twin of a hand-written encoder, carrying identical weights."""
    n_item_rows = model_encoder.state_dict()["item_embedding.weight"].shape[0]
    legacy = LegacySASRecEncoder(n_item_rows, config)
    legacy.load_state_dict(
        {
            name: torch.from_numpy(array)
            for name, array in hand_written_state_to_legacy(
                model_encoder.state_dict(), num_blocks=config.num_blocks
            ).items()
        }
    )
    return legacy.train(model_encoder.training)


def rewrite_as_legacy_artifact(directory: Path) -> None:
    """Turn a freshly exported artifact into one shaped exactly like a pre-WO-2 export.

    Legacy tensor names in the archive, no ``encoder_impl`` in its metadata or in
    the manifest, and the manifest's checksum moved to the rewritten bytes. This
    is what ``artifacts/sasrec/a11af5ed…`` looks like on disk.
    """
    archive = directory / MODEL_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    metadata, arrays = _read_model_archive(archive)
    num_blocks = int(metadata["config"]["num_blocks"])
    legacy = hand_written_state_to_legacy(
        {name: torch.from_numpy(array) for name, array in arrays.items()}, num_blocks=num_blocks
    )
    metadata.pop("encoder_impl")
    metadata["state_keys"] = sorted(legacy)
    archive.unlink()
    _write_model_archive(archive, metadata, {k: torch.from_numpy(v) for k, v in legacy.items()})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("encoder_impl")
    manifest["model_sha256"] = file_sha256(archive)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
