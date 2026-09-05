"""Causal sequential candidate retrieval (SASRec / gSASRec, ADR 0016)."""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import faiss
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn

from . import routing
from .popularity import PopularityModel
from .sequence_data import SequenceExampleStats, build_strict_prefix_examples_with_stats
from .twotower import build_user_history


@dataclass(frozen=True)
class SASRecConfig:
    max_sequence_length: int = 50
    hidden_dim: int = 64
    num_blocks: int = 2
    num_heads: int = 2
    feedforward_dim: int = 256
    dropout: float = 0.2
    negative_count: int = 64
    loss: Literal["gbce", "bce"] = "gbce"
    calibration_t: float = 0.5
    batch_size: int = 512
    epochs: int = 3
    learning_rate: float = 1e-3
    faiss_nlist: int = 100
    faiss_nprobe: int = 10
    faiss_exact: bool = False
    seed: int = 42

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SASRecConfig:
        source = os.environ if env is None else env
        defaults = cls()

        def value(name: str, cast: Callable[[str], Any], default: Any) -> Any:
            raw = source.get(f"SASREC_{name}", "").strip()
            return default if not raw else cast(raw)

        def boolean(raw: str) -> bool:
            if raw.lower() not in {"true", "false", "1", "0"}:
                raise ValueError(f"invalid boolean: {raw}")
            return raw.lower() in {"true", "1"}

        return cls(
            max_sequence_length=value("MAX_SEQUENCE_LENGTH", int, defaults.max_sequence_length),
            hidden_dim=value("HIDDEN_DIM", int, defaults.hidden_dim),
            num_blocks=value("NUM_BLOCKS", int, defaults.num_blocks),
            num_heads=value("NUM_HEADS", int, defaults.num_heads),
            feedforward_dim=value("FEEDFORWARD_DIM", int, defaults.feedforward_dim),
            dropout=value("DROPOUT", float, defaults.dropout),
            negative_count=value("NEGATIVE_COUNT", int, defaults.negative_count),
            loss=value("LOSS", str, defaults.loss),
            calibration_t=value("CALIBRATION_T", float, defaults.calibration_t),
            batch_size=value("BATCH_SIZE", int, defaults.batch_size),
            epochs=value("EPOCHS", int, defaults.epochs),
            learning_rate=value("LEARNING_RATE", float, defaults.learning_rate),
            faiss_nlist=value("FAISS_NLIST", int, defaults.faiss_nlist),
            faiss_nprobe=value("FAISS_NPROBE", int, defaults.faiss_nprobe),
            faiss_exact=value("FAISS_EXACT", boolean, defaults.faiss_exact),
            seed=value("SEED", int, defaults.seed),
        )

    def as_params(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.max_sequence_length <= 0 or self.hidden_dim <= 0:
            raise ValueError("sequence length and hidden dimension must be positive")
        if self.num_blocks <= 0 or self.num_heads <= 0:
            raise ValueError("num_blocks and num_heads must be positive")
        if self.hidden_dim % self.num_heads:
            raise ValueError("hidden_dim must be divisible by num_heads")
        if self.negative_count <= 0:
            raise ValueError("negative_count must be positive")
        if not 0.0 <= self.calibration_t <= 1.0:
            raise ValueError("calibration_t must be in [0, 1]")


class SASRecEncoder(nn.Module):
    """Pre-normalized causal Transformer with tied input/output items."""

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
        self.transformer = nn.TransformerEncoder(layer, num_layers=config.num_blocks)
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
        encoded = self.transformer(
            values,
            mask=causal_mask,
            src_key_padding_mask=sequences.eq(0),
        )
        normalized: torch.Tensor = self.output_norm(encoded)
        normalized = normalized.masked_fill(sequences.eq(0).unsqueeze(-1), 0.0)
        return normalized

    def forward(self, sequences: torch.Tensor) -> torch.Tensor:
        """Return the representation at the final (left-padded) position."""
        return F.normalize(self.encode_positions(sequences)[:, -1, :], p=2, dim=-1)

    def training_user_vectors(self, sequences: torch.Tensor) -> torch.Tensor:
        """Unconstrained representations used by the BCE-family objective."""
        return self.encode_positions(sequences)[:, -1, :]

    def item_vectors(self, item_ids: torch.Tensor, *, normalize: bool = True) -> torch.Tensor:
        vectors = self.item_embedding(item_ids)
        return F.normalize(vectors, p=2, dim=-1) if normalize else vectors


def gbce_beta(*, negative_count: int, catalog_size: int, calibration_t: float) -> float:
    """Sampling-rate-independent gBCE beta from Petrov & Macdonald Eq. 27."""
    if catalog_size <= 1:
        return 1.0
    alpha = min(1.0, negative_count / (catalog_size - 1))
    return 1.0 - calibration_t * (1.0 - alpha)


def sampled_gbce_loss(
    positive_logits: torch.Tensor,
    negative_logits: torch.Tensor,
    *,
    beta: float,
) -> torch.Tensor:
    """Numerically stable Eq. 8; beta=1 is ordinary sampled BCE."""
    positive = beta * F.softplus(-positive_logits)
    negative = F.softplus(negative_logits).sum(dim=1)
    return ((positive + negative) / (negative_logits.shape[1] + 1)).mean()


def sample_negatives(
    histories: torch.Tensor,
    positives: torch.Tensor,
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> torch.Tensor:
    """Uniform seeded negatives excluding padding, prefix, target, and duplicates."""
    output = np.empty((len(positives), count), dtype=np.int64)
    for row, (history, positive) in enumerate(zip(histories.numpy(), positives.numpy())):
        forbidden = set(int(item) for item in history if item)
        forbidden.add(int(positive))
        if n_items - len(forbidden) < count:
            raise ValueError("not enough eligible unique negatives for requested count")
        selected: list[int] = []
        selected_set: set[int] = set()
        while len(selected) < count:
            draws = rng.integers(1, n_items + 1, size=max(8, 2 * (count - len(selected))))
            for candidate_value in draws:
                candidate = int(candidate_value)
                if candidate not in forbidden and candidate not in selected_set:
                    selected.append(candidate)
                    selected_set.add(candidate)
                    if len(selected) == count:
                        break
        output[row] = selected
    return torch.from_numpy(output)


@dataclass
class SASRecModel:
    config: SASRecConfig = field(default_factory=SASRecConfig)
    cold_start_threshold: int | None = routing.DEFAULT_COLD_START_THRESHOLD
    _encoder: SASRecEncoder | None = None
    _item_to_index: dict[int, int] = field(default_factory=dict)
    _index_to_item: dict[int, int] = field(default_factory=dict)
    _user_history: dict[int, list[int]] = field(default_factory=dict)
    _unknown_index: int = 0
    _training_stats: SequenceExampleStats | None = None
    _faiss_index: Any = None
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(
        self,
        train: pd.DataFrame,
        on_epoch: Callable[[int, float], None] | None = None,
    ) -> SASRecModel:
        self.config.validate()
        self._popularity = PopularityModel().fit(train)
        if train.empty:
            return self
        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)
        items = sorted(int(item) for item in train["movieId"].unique())
        self._item_to_index = {item: index + 1 for index, item in enumerate(items)}
        self._index_to_item = {index: item for item, index in self._item_to_index.items()}
        self._unknown_index = len(items) + 1
        self._user_history = build_user_history(train, self._item_to_index)
        histories, positives, self._training_stats = build_strict_prefix_examples_with_stats(
            train,
            item_to_index=self._item_to_index,
            max_length=self.config.max_sequence_length,
        )
        self._encoder = SASRecEncoder(len(items) + 2, self.config)
        optimizer = torch.optim.Adam(self._encoder.parameters(), lr=self.config.learning_rate)
        rng = np.random.default_rng(self.config.seed)
        beta = (
            1.0
            if self.config.loss == "bce"
            else gbce_beta(
                negative_count=self.config.negative_count,
                catalog_size=len(items),
                calibration_t=self.config.calibration_t,
            )
        )
        for epoch in range(self.config.epochs):
            self._encoder.train()
            permutation = torch.randperm(len(positives))
            epoch_loss = 0.0
            batches = 0
            for start in range(0, len(positives), self.config.batch_size):
                rows = permutation[start : start + self.config.batch_size]
                history_batch = histories[rows].long()
                positive_batch = positives[rows].long()
                negative_batch = sample_negatives(
                    history_batch,
                    positive_batch,
                    n_items=len(items),
                    count=self.config.negative_count,
                    rng=rng,
                )
                user_vectors = self._encoder.training_user_vectors(history_batch)
                positive_logits = (
                    user_vectors * self._encoder.item_vectors(positive_batch, normalize=False)
                ).sum(dim=1)
                negative_logits = torch.einsum(
                    "bd,bkd->bk",
                    user_vectors,
                    self._encoder.item_vectors(negative_batch, normalize=False),
                )
                loss = sampled_gbce_loss(positive_logits, negative_logits, beta=beta)
                optimizer.zero_grad()
                loss.backward()  # type: ignore[no-untyped-call]
                optimizer.step()
                with torch.no_grad():
                    self._encoder.item_embedding.weight[0].zero_()
                epoch_loss += float(loss.item())
                batches += 1
            if on_epoch is not None:
                on_epoch(epoch + 1, epoch_loss / max(1, batches))
        self.build_index()
        return self

    def build_index(self) -> None:
        if self._encoder is None:
            return
        # Retrieval must be deterministic: disable dropout before building the
        # item index and leave the fitted model in inference mode. ``fit``
        # explicitly restores training mode at the start of every epoch.
        self._encoder.eval()
        n_items = len(self._index_to_item)
        with torch.no_grad():
            vectors = self._encoder.item_vectors(torch.arange(1, n_items + 1)).numpy()
        if self.config.faiss_exact:
            index: Any = faiss.IndexFlatIP(self.config.hidden_dim)
        else:
            quantizer = faiss.IndexFlatIP(self.config.hidden_dim)
            nlist = min(self.config.faiss_nlist, max(1, n_items // 4))
            index = faiss.IndexIVFFlat(
                quantizer, self.config.hidden_dim, nlist, faiss.METRIC_INNER_PRODUCT
            )
            index.train(vectors)
            index.nprobe = self.config.faiss_nprobe
        index.add(vectors)
        self._faiss_index = index

    def was_served_by_sasrec(self, user_id: int) -> bool:
        history = self._user_history.get(user_id, [])
        if self._encoder is None or self._faiss_index is None or not history:
            return False
        return self.cold_start_threshold is None or len(history) >= self.cold_start_threshold

    def recommend(self, user_id: int, k: int) -> list[int]:
        if not self.was_served_by_sasrec(user_id):
            return self._popularity.recommend(user_id, k)
        assert self._encoder is not None and self._faiss_index is not None
        full_history = self._user_history[user_id]
        return self._recommend_from_dense_history(full_history, k)

    def encode_movie_history(self, movie_ids: list[int]) -> torch.Tensor:
        """Encode ordered movie ids without depending on fitted user state.

        This is the artifact/serving boundary. Unknown snapshot items receive
        the explicit unknown token and can never alias a trained title.
        """
        if self._encoder is None:
            raise RuntimeError("SASRec encoder is not fitted")
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        dense_history = [
            self._item_to_index.get(int(movie_id), self._unknown_index) for movie_id in movie_ids
        ]
        sequence = self._sequence_tensor(dense_history)
        with torch.no_grad():
            encoded: torch.Tensor = self._encoder(sequence)
        return encoded

    def recommend_from_history(
        self,
        movie_ids: list[int],
        k: int,
        *,
        excluded_movie_ids: set[int] | None = None,
    ) -> list[int]:
        """Retrieve from an ordered runtime history using an exported model."""
        if k <= 0:
            return []
        if self._encoder is None or self._faiss_index is None:
            raise RuntimeError("SASRec model and retrieval index are not loaded")
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        dense_history = [
            self._item_to_index.get(int(movie_id), self._unknown_index) for movie_id in movie_ids
        ]
        dense_exclusions = {
            dense
            for movie_id in (set(movie_ids) | (excluded_movie_ids or set()))
            if (dense := self._item_to_index.get(int(movie_id))) is not None
        }
        return self._recommend_from_dense_history(
            dense_history,
            k,
            dense_exclusions=dense_exclusions,
        )

    def retrieve_unfiltered(self, histories: Sequence[Sequence[int]], k: int) -> list[list[int]]:
        """Batch top-k over ordered movie histories with nothing excluded.

        The training-time counterpart of ``ItemItemModel.recommend(...,
        filter_seen=False)``. ``recommend_from_history`` is the serving shape and
        removes the history from its own results, which is right online and wrong
        when assembling LambdaRank groups: the positive is drawn from the user's
        train history, so a retriever that filters its own history would drop
        every positive it was asked about. Exclusions belong to the caller here,
        applied to the *negatives* pool after the positive has been kept
        (`src/training/sasrec_ranker.py`, and #126 for the rule itself).

        Batched because the ranker asks this ~154k times per run and a batch of
        one spends most of its time in PyTorch dispatch rather than arithmetic.
        Rows do not interact — causal attention plus the padding mask keeps each
        sequence independent — so a row's result depends only on its own history
        and on the batch size the caller chose, which callers pin and log.
        """
        if self._encoder is None or self._faiss_index is None:
            raise RuntimeError("SASRec model and retrieval index are not loaded")
        if not histories:
            return []
        if k <= 0:
            return [[] for _ in histories]
        if any(len(movie_ids) == 0 for movie_ids in histories):
            raise ValueError("SASRec history must contain at least one movie")

        max_length = self.config.max_sequence_length
        batch = torch.zeros((len(histories), max_length), dtype=torch.long)
        for row, movie_ids in enumerate(histories):
            dense = [
                self._item_to_index.get(int(movie_id), self._unknown_index)
                for movie_id in movie_ids[-max_length:]
            ]
            batch[row, max_length - len(dense) :] = torch.tensor(dense, dtype=torch.long)
        with torch.no_grad():
            queries = self._encoder(batch).numpy()
        search_k = min(len(self._index_to_item), k)
        _scores, indices = self._faiss_index.search(queries, search_k)
        return [
            [self._index_to_item[int(index) + 1] for index in row if index >= 0] for row in indices
        ]

    def _sequence_tensor(self, dense_history: list[int]) -> torch.Tensor:
        history = dense_history[-self.config.max_sequence_length :]
        sequence = torch.zeros((1, self.config.max_sequence_length), dtype=torch.long)
        if history:
            sequence[0, -len(history) :] = torch.tensor(history)
        return sequence

    def _recommend_from_dense_history(
        self,
        full_history: list[int],
        k: int,
        *,
        dense_exclusions: set[int] | None = None,
    ) -> list[int]:
        assert self._encoder is not None and self._faiss_index is not None
        history = full_history[-self.config.max_sequence_length :]
        sequence = self._sequence_tensor(history)
        with torch.no_grad():
            query = self._encoder(sequence).numpy()
        # Exclusions cover the full known history, not only the encoder window.
        seen = set(full_history) | (dense_exclusions or set())
        search_k = min(len(self._index_to_item), k + len(seen))
        _scores, indices = self._faiss_index.search(query, search_k)
        return [
            self._index_to_item[int(index) + 1]
            for index in indices[0]
            if index >= 0 and int(index) + 1 not in seen
        ][:k]

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        return {user_id: self.recommend(user_id, k) for user_id in user_ids}
