"""
Two-tower candidate generator.

Fourth entry in the candidate-stage lineage (popularity → CF → item-item →
two-tower). Per ADR 0006 the tower shape is fixed:

  - User tower: history-based encoder — mean-pool over the last N=50 items
    strictly before the query timestamp. No per-user-id embedding, so the
    model has a defined answer for users who arrive after training.
  - Item tower: id-only ``nn.Embedding``, dim 64.
  - Loss: sampled softmax with log-uniform negative correction (Yi et al.
    2019).
  - Retrieval: FAISS-CPU IVF-Flat over L2-normalized item embeddings.
  - Cold-start: embedded ``PopularityModel`` fallback, same pattern
    CFModel and ItemItemModel use.

The MovieLens-id ↔ dense-index bookkeeping mirrors CFModel and ItemItemModel
— pandas categoricals build the forward and inverse maps in one pass over
the 25 M-row training frame. The one twist is that dense index 0 is
reserved for a padding item so variable-length histories can be packed into
a (batch, N) tensor cleanly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any

import faiss
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F  # noqa: N812 — canonical PyTorch alias
from torch import nn

from . import routing
from .hard_negatives import select_hard_negatives
from .item_features import (
    ItemFeatureSchema,
    build_item_feature_matrix,
    fit_item_feature_schema,
)
from .popularity import PopularityModel

logger = logging.getLogger(__name__)


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    return default if not raw else int(raw)


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    return default if not raw else float(raw)


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{key}={raw!r} is not a boolean")


@dataclass
class TwoTowerConfig:
    """Hyperparameters. Every field is logged as an MLflow param by the
    training script so a future sweep is a pure config change.

    Defaults follow the currently accepted architecture. ADR 0015 changes the
    v2 temperature to 0.05; a v1 reproduction must explicitly set 1.0. Every
    value is logged, so the distinction is visible in MLflow.
    """

    embedding_dim: int = 64
    history_window: int = 50  # N in ADR 0006 — trailing items used to encode a user
    batch_size: int = 4096
    # ADR 0006 pins num_sampled = 4 * batch_size. Kept as an explicit
    # field so a sweep can vary it independently.
    num_sampled: int = 16384
    epochs: int = 3
    learning_rate: float = 1e-3
    # Divides the cosine similarity before the log-uniform correction is
    # applied. ADR 0015 adopts 0.05 for v2 from the best full-data sweep cell.
    # 1.0 remains v1's explicit reproduction value.
    logit_temperature: float = 0.05
    # Whether the positive's logit gets the same -log P correction the
    # negatives get. True is v1's behaviour and matches TensorFlow's
    # `sampled_softmax_loss`, which corrects the true logit by its expected
    # sample count; ADR 0006's own wording says "each negative's logit",
    # so the flag exists to let the sweep measure the difference rather
    # than argue about which reading was meant.
    correct_positive_logit: bool = True
    # ADR 0015 v2. When catalog metadata is supplied, fuse deterministic
    # genre/year features into the item representation. Direct model callers
    # may omit metadata to reproduce the id-only v1 ablation.
    use_item_features: bool = True
    hard_negative_count: int = 8
    hard_negative_pool_size: int = 256
    hard_negative_warmup_epochs: int = 1
    # Epochs to keep going after the best mean loss seen so far. 0 disables
    # early stopping entirely, which is v1's behaviour (it always ran
    # exactly `epochs` passes).
    early_stopping_patience: int = 0
    # How much a loss has to improve to count as an improvement at all.
    early_stopping_min_delta: float = 1e-4
    # FAISS IVF-Flat tuning. nlist = sqrt(n_items) rounded is the FAISS
    # rule-of-thumb; MovieLens has ~62 k items so 100 is under the
    # recommended range but keeps train time bounded and matches what the
    # ADR pins as a defensible starting point.
    faiss_nlist: int = 100
    faiss_nprobe: int = 10
    # Replace IVF-Flat with exact inner-product search. Off by default —
    # ADR 0006 pins IVF-Flat as the shape that ships. The switch exists so a
    # run can separate "the embeddings are bad" from "the ANN index is
    # losing neighbours", which are indistinguishable from a recall number.
    faiss_exact: bool = False
    seed: int = 42

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TwoTowerConfig:
        """Build a config from ``TWOTOWER_*`` environment variables.

        An unset or empty variable takes the ADR 0006 default above, so an
        operator who sets nothing gets exactly the run that is already
        recorded in ``docs/results.md``.
        """
        e = env if env is not None else os.environ
        return cls(
            embedding_dim=_env_int(e, "TWOTOWER_EMBEDDING_DIM", cls.embedding_dim),
            history_window=_env_int(e, "TWOTOWER_HISTORY_WINDOW", cls.history_window),
            batch_size=_env_int(e, "TWOTOWER_BATCH_SIZE", cls.batch_size),
            num_sampled=_env_int(e, "TWOTOWER_NUM_SAMPLED", cls.num_sampled),
            epochs=_env_int(e, "TWOTOWER_EPOCHS", cls.epochs),
            learning_rate=_env_float(e, "TWOTOWER_LEARNING_RATE", cls.learning_rate),
            logit_temperature=_env_float(e, "TWOTOWER_LOGIT_TEMPERATURE", cls.logit_temperature),
            correct_positive_logit=_env_bool(
                e, "TWOTOWER_CORRECT_POSITIVE_LOGIT", cls.correct_positive_logit
            ),
            use_item_features=_env_bool(e, "TWOTOWER_USE_ITEM_FEATURES", cls.use_item_features),
            hard_negative_count=_env_int(
                e, "TWOTOWER_HARD_NEGATIVE_COUNT", cls.hard_negative_count
            ),
            hard_negative_pool_size=_env_int(
                e, "TWOTOWER_HARD_NEGATIVE_POOL_SIZE", cls.hard_negative_pool_size
            ),
            hard_negative_warmup_epochs=_env_int(
                e,
                "TWOTOWER_HARD_NEGATIVE_WARMUP_EPOCHS",
                cls.hard_negative_warmup_epochs,
            ),
            early_stopping_patience=_env_int(
                e, "TWOTOWER_EARLY_STOPPING_PATIENCE", cls.early_stopping_patience
            ),
            early_stopping_min_delta=_env_float(
                e, "TWOTOWER_EARLY_STOPPING_MIN_DELTA", cls.early_stopping_min_delta
            ),
            faiss_nlist=_env_int(e, "TWOTOWER_FAISS_NLIST", cls.faiss_nlist),
            faiss_nprobe=_env_int(e, "TWOTOWER_FAISS_NPROBE", cls.faiss_nprobe),
            faiss_exact=_env_bool(e, "TWOTOWER_FAISS_EXACT", cls.faiss_exact),
            seed=_env_int(e, "TWOTOWER_SEED", cls.seed),
        )

    def as_params(self) -> dict[str, str | int | float | bool]:
        """Every field, flat, for ``mlflow.log_params``."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


class ItemTower(nn.Module):
    """Item-id embedding with an optional structured-feature residual.

    Index 0 is reserved as the padding id so variable-length user histories
    pack cleanly into a ``(batch_size, N)`` integer tensor. The padding row
    is kept at zero (``padding_idx=0`` freezes it), which makes it a no-op
    in a mean-pool sum — only the true items contribute, and the caller
    divides by the true item count (not by ``N``).
    """

    def __init__(
        self,
        n_items: int,
        embedding_dim: int,
        side_features: torch.Tensor | None = None,
    ) -> None:
        super().__init__()
        self.side_features: torch.Tensor | None
        # +1 for the padding row at index 0.
        self.embed = nn.Embedding(n_items + 1, embedding_dim, padding_idx=0)
        # Small-variance init, standard for embedding tables trained with
        # sampled-softmax. Larger init pushes early logits toward the tails
        # of softmax and slows convergence.
        nn.init.normal_(self.embed.weight, mean=0.0, std=1.0 / math.sqrt(embedding_dim))
        with torch.no_grad():
            self.embed.weight[0].zero_()
        if side_features is not None:
            expected_rows = n_items + 1
            if side_features.ndim != 2 or side_features.shape[0] != expected_rows:
                raise ValueError(
                    "side_features must have shape "
                    f"({expected_rows}, feature_width), got {tuple(side_features.shape)}"
                )
            side_features = side_features.detach().to(dtype=torch.float32).clone()
            side_features[0].zero_()
            self.register_buffer("side_features", side_features)
            self.side_projection: nn.Linear | None = nn.Linear(
                int(side_features.shape[1]), embedding_dim, bias=False
            )
            # Start close to the id-only model while leaving a gradient into
            # both paths. sigmoid(2) ~= 0.88 id / 0.12 structured features.
            self.side_gate: nn.Parameter | None = nn.Parameter(torch.tensor(2.0))
        else:
            self.register_buffer("side_features", None)
            self.side_projection = None
            self.register_parameter("side_gate", None)

    def forward(self, item_ids: torch.Tensor) -> torch.Tensor:
        item_vectors = self.embed(item_ids)
        if self.side_projection is not None and self.side_features is not None:
            side_vectors = self.side_projection(self.side_features[item_ids])
            assert self.side_gate is not None
            gate = torch.sigmoid(self.side_gate)
            item_vectors = gate * item_vectors + (1.0 - gate) * side_vectors
            item_vectors = item_vectors.masked_fill((item_ids == 0).unsqueeze(-1), 0.0)
        return F.normalize(item_vectors, p=2, dim=-1)


def build_user_history(
    train: pd.DataFrame,
    item_to_index: dict[int, int],
) -> dict[int, list[int]]:
    """Per-user chronological list of dense item indices from train.

    Sorted by ``(userId, timestamp)`` in-place at the DataFrame level, then
    materialized per user as a list of dense indices. The list order *is*
    the point-in-time invariant that ADR 0006's canary test enforces —
    downstream training code must slice `history[max(0, i-N):i]` at
    position `i` and never look at `history[i:]`.
    """
    ordered = train.sort_values(["userId", "timestamp"], kind="stable")
    ordered_dense = ordered["movieId"].map(item_to_index).astype("int64")
    # groupby preserves the sorted order of rows within each group.
    grouped = ordered.assign(_dense=ordered_dense).groupby("userId")["_dense"].apply(list)
    return dict(grouped)


def _log_uniform_probabilities(n_items: int) -> np.ndarray:
    """Rank-based log-uniform sampling weights for negatives.

    P(rank r) ∝ log(r + 2) - log(r + 1). Same distribution TensorFlow's
    ``log_uniform_candidate_sampler`` uses. The array is over *ranks*
    (0 = most popular); callers permute by the popularity ordering before
    sampling.
    """
    ranks = np.arange(n_items, dtype=np.float64)
    weights = np.log(ranks + 2.0) - np.log(ranks + 1.0)
    normalized: np.ndarray = weights / weights.sum()
    return normalized


@dataclass
class TwoTowerModel:
    """Public model class matching the ``CandidateModel``-shaped contract.

    Same interface as PopularityModel / CFModel / ItemItemModel: ``fit``,
    ``recommend``, ``recommend_for_users``, and a ``was_served_by_twotower``
    predicate for per-policy attribution. The training scripts and the
    eval harness treat these interchangeably.
    """

    config: TwoTowerConfig = field(default_factory=TwoTowerConfig)

    # Where the learned path stops. ADR 0001's threshold by default, which is
    # the rule the deployed service routes on; None opts out to the
    # index-membership rule this model used before 2026-08-30. See
    # src/models/candidates/routing.py.
    cold_start_threshold: int | None = routing.DEFAULT_COLD_START_THRESHOLD

    # Populated by fit:
    _item_tower: ItemTower | None = None
    # movieId → dense index in [1, n_items]. 0 is padding.
    _item_to_index: dict[int, int] = field(default_factory=dict)
    # dense index → movieId; length n_items + 1, position 0 unused.
    _index_to_item: dict[int, int] = field(default_factory=dict)
    # User → chronological list of dense item indices from train.
    _user_history: dict[int, list[int]] = field(default_factory=dict)
    _faiss_index: Any = None  # faiss.Index; typed loose because faiss stubs are partial
    _popularity: PopularityModel = field(default_factory=PopularityModel)
    _item_feature_schema: ItemFeatureSchema | None = None
    _hard_negative_selected: int = 0
    _hard_negative_slots: int = 0

    def fit(
        self,
        train: pd.DataFrame,
        movies: pd.DataFrame | None = None,
        on_epoch: Callable[[int, float], None] | None = None,
    ) -> TwoTowerModel:
        """Train both towers, then build the FAISS retrieval index.

        Expects columns ``userId``, ``movieId``, ``timestamp``. Rating
        values are ignored — every interaction has weight 1.0 per ADR 0002.
        ``on_epoch`` is called with ``(epoch, mean_loss)`` after each epoch
        so the training script can log per-epoch loss to MLflow without
        the model class depending on MLflow directly.
        """
        # Popularity fallback first so it's ready for cold users no matter
        # what happens next (an interrupted training still returns a valid
        # recommend path).
        self._popularity = PopularityModel().fit(train)

        if train.empty:
            self._item_tower = None
            self._item_to_index = {}
            self._index_to_item = {}
            self._user_history = {}
            self._faiss_index = None
            self._item_feature_schema = None
            return self

        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)
        if self.config.hard_negative_count < 0:
            raise ValueError("hard_negative_count must be non-negative")
        if self.config.hard_negative_pool_size < self.config.hard_negative_count:
            raise ValueError("hard_negative_pool_size must be >= hard_negative_count")
        if self.config.hard_negative_warmup_epochs < 0:
            raise ValueError("hard_negative_warmup_epochs must be non-negative")
        self._hard_negative_selected = 0
        self._hard_negative_slots = 0

        # ---- Vocabulary and history construction ----
        # Item ids are re-indexed starting at 1; index 0 is padding.
        item_categorical = pd.Categorical(train["movieId"])
        self._item_to_index = {mid: i + 1 for i, mid in enumerate(item_categorical.categories)}
        self._index_to_item = {i + 1: mid for i, mid in enumerate(item_categorical.categories)}
        n_items = len(item_categorical.categories)

        self._user_history = build_user_history(train, self._item_to_index)

        # ---- Popularity ranking for log-uniform sampling ----
        # Sort items by descending frequency; the sampler draws ranks under
        # log-uniform and maps them back to dense indices via this table.
        item_freq = train["movieId"].value_counts()
        popular_movie_ids = item_freq.index.tolist()
        rank_to_dense = np.array(
            [self._item_to_index[mid] for mid in popular_movie_ids],
            dtype=np.int64,
        )
        rank_probs = _log_uniform_probabilities(n_items)
        # log P per dense index (aligned to indices 1..n_items).
        log_p_per_index = np.zeros(n_items + 1, dtype=np.float64)
        log_p_per_index[rank_to_dense] = np.log(rank_probs)
        log_p_per_index_t = torch.tensor(log_p_per_index, dtype=torch.float32)
        # Sampling table for torch.multinomial (over ranks).
        rank_probs_t = torch.tensor(rank_probs, dtype=torch.float32)

        # ---- Training pairs ----
        # For each user, position i > 0 yields (history[:i][-N:], history[i]).
        # Position 0 is dropped — a user's first interaction has no history
        # to encode, so it can't feed the mean-pool. That's per ADR 0006's
        # point-in-time rule: the encoder never runs on an empty history.
        history_tensor, positive_tensor = self._build_training_pairs(train)
        n_examples = positive_tensor.shape[0]
        logger.info(
            "Training on %d (history, positive) pairs across %d users, %d items",
            n_examples,
            len(self._user_history),
            n_items,
        )

        # ---- Training loop ----
        side_features: torch.Tensor | None = None
        self._item_feature_schema = None
        if self.config.use_item_features and movies is not None:
            training_catalog = movies[movies["movieId"].isin(self._item_to_index)].copy()
            self._item_feature_schema = fit_item_feature_schema(training_catalog)
            side_features = build_item_feature_matrix(
                training_catalog,
                item_to_index=self._item_to_index,
                schema=self._item_feature_schema,
            )
        self._item_tower = ItemTower(
            n_items=n_items,
            embedding_dim=self.config.embedding_dim,
            side_features=side_features,
        )
        optimizer = torch.optim.Adam(self._item_tower.parameters(), lr=self.config.learning_rate)

        best_loss = math.inf
        epochs_since_best = 0

        for epoch in range(self.config.epochs):
            perm = torch.randperm(n_examples)

            epoch_loss = 0.0
            n_batches = 0
            for start in range(0, n_examples, self.config.batch_size):
                end = start + self.config.batch_size
                # Gather the batch straight out of the unshuffled tensors.
                # `A[perm][start:end]` and `A[perm[start:end]]` are the same
                # rows in the same order, but the first materializes a whole
                # second copy of a (19.9 M, 50) tensor every epoch — about
                # 4 GB that this machine does not have spare. The pairs are
                # stored as int32 for the same reason and cast per batch,
                # since `nn.Embedding` wants int64 indices.
                rows = perm[start:end]
                history_batch = history_tensor[rows].long()
                positive_batch = positive_tensor[rows].long()

                loss = self._compute_loss(
                    history_batch=history_batch,
                    positive_batch=positive_batch,
                    rank_probs_t=rank_probs_t,
                    rank_to_dense=rank_to_dense,
                    log_p_per_index_t=log_p_per_index_t,
                    mine_hard_negatives=(
                        self.config.hard_negative_count > 0
                        and epoch >= self.config.hard_negative_warmup_epochs
                    ),
                )
                optimizer.zero_grad()
                loss.backward()  # type: ignore[no-untyped-call]
                optimizer.step()
                # Padding row must remain zero for the mask trick to hold.
                with torch.no_grad():
                    self._item_tower.embed.weight[0].zero_()

                epoch_loss += float(loss.item())
                n_batches += 1

            mean_loss = epoch_loss / max(n_batches, 1)
            logger.info("Epoch %d/%d loss=%.4f", epoch + 1, self.config.epochs, mean_loss)
            if on_epoch is not None:
                on_epoch(epoch + 1, mean_loss)

            # Early stopping on a loss plateau. Off by default (patience 0),
            # which is what v1 did — it always ran exactly `epochs` passes.
            if self.config.early_stopping_patience > 0:
                if mean_loss < best_loss - self.config.early_stopping_min_delta:
                    best_loss = mean_loss
                    epochs_since_best = 0
                else:
                    epochs_since_best += 1
                    if epochs_since_best >= self.config.early_stopping_patience:
                        logger.info(
                            "Early stop after epoch %d: %d epoch(s) without a %.1e "
                            "improvement on best loss %.4f",
                            epoch + 1,
                            epochs_since_best,
                            self.config.early_stopping_min_delta,
                            best_loss,
                        )
                        break

        # ---- FAISS index ----
        self.build_index()
        return self

    def embedding_spread(self, sample_size: int = 2000) -> dict[str, float]:
        """Mean and spread of pairwise cosine among a fixed sample of items.

        The diagnostic for the failure mode a loss curve cannot show. Both
        towers are L2-normalized, so retrieval can only order items by angle;
        if training drives the item embeddings into one direction (or into two
        antipodal clumps) the loss can keep improving while the top-500 for
        every user becomes the same near-tied set, and recall collapses toward
        chance. High ``abs_mean`` with low ``std`` is that state.

        The sample is the first ``sample_size`` dense indices — deterministic,
        and drawing no random numbers, so calling this between epochs cannot
        perturb the run it is measuring.
        """
        if self._item_tower is None:
            return {}
        n_items = len(self._index_to_item)
        take = min(sample_size, n_items)
        if take < 2:
            return {}
        with torch.no_grad():
            vecs = self._item_tower(torch.arange(1, take + 1, dtype=torch.long))
            sims = vecs @ vecs.T
            off_diagonal = ~torch.eye(take, dtype=torch.bool)
            values = sims[off_diagonal]
            return {
                "item_cosine_mean": float(values.mean()),
                "item_cosine_abs_mean": float(values.abs().mean()),
                "item_cosine_std": float(values.std()),
            }

    def build_index(self) -> None:
        """(Re)build the retrieval index over the current item embeddings.

        Public because the training script calls it between epochs to score
        recall mid-run — a loss curve says the model stopped moving, but only
        recall says whether where it stopped is any good. Each call constructs
        a fresh FAISS index and trains it from scratch at FAISS's default
        clustering seed, so an intermediate call cannot perturb the final
        index: the result is a function of the embeddings alone.
        """
        if self._item_tower is None:
            return
        self._build_faiss_index(len(self._index_to_item))

    def _build_training_pairs(
        self, train: pd.DataFrame | None = None
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Materialize the (history, positive) tensors from ``_user_history``.

        History is padded with 0 on the *left* so the last N slots are the
        most-recent items — mean-pool doesn't care about position, but a
        left-pad keeps the invariant readable in tests.

        Filled into a preallocated int32 array rather than accumulated as
        lists of Python ints. On the full 25 M split that is 19.9 M rows of
        50 columns: as a list-of-lists it is roughly 9 GB of list objects
        before the tensor is even built, and this machine has 16 GB. The
        rows and their order are identical either way.

        When ``train`` is supplied, timestamps are authoritative: every item
        sharing a timestamp receives the same prefix containing only items at
        strictly earlier timestamps. The history-only fallback exists for
        small callers that have no timestamps; production training always
        supplies the frame.
        """
        n = self.config.history_window
        if train is not None:
            ordered = train.sort_values(["userId", "timestamp", "movieId"], kind="stable")
            total = 0
            for _user_id, group in ordered.groupby("userId", sort=False):
                first_timestamp = group["timestamp"].min()
                total += int((group["timestamp"] > first_timestamp).sum())
        else:
            ordered = None
            total = sum(max(0, len(hist) - 1) for hist in self._user_history.values())
        histories = np.zeros((total, n), dtype=np.int32)
        positives = np.empty(total, dtype=np.int32)

        row = 0
        if ordered is not None:
            for _user_id, group in ordered.groupby("userId", sort=False):
                prefix: list[int] = []
                for _timestamp, simultaneous in group.groupby("timestamp", sort=False):
                    dense_targets = [
                        self._item_to_index[int(movie_id)]
                        for movie_id in simultaneous["movieId"].tolist()
                    ]
                    if prefix:
                        window = np.asarray(prefix[-n:], dtype=np.int32)
                        for target in dense_targets:
                            histories[row, n - len(window) :] = window
                            positives[row] = target
                            row += 1
                    # Same-time targets become visible only to later timestamp
                    # groups, never to one another.
                    prefix.extend(dense_targets)
        else:
            for hist in self._user_history.values():
                dense = np.asarray(hist, dtype=np.int32)
                for i in range(1, len(dense)):
                    window = dense[max(0, i - n) : i]
                    histories[row, n - len(window) :] = window
                    positives[row] = dense[i]
                    row += 1

        return torch.from_numpy(histories), torch.from_numpy(positives)

    def _encode_user(self, history_batch: torch.Tensor) -> torch.Tensor:
        """Mean-pool over non-padding history items, then L2-normalize.

        ``history_batch`` is ``(B, N)`` with 0 marking padding. The item
        tower zeroes the padding row so the sum is over true items only;
        we divide by the true item count (clamped ≥ 1 for safety), then
        re-normalize.
        """
        assert self._item_tower is not None
        # (B, N, d) — padding rows contribute the zero vector.
        item_vecs = self._item_tower(history_batch)
        # (B, N) 1.0 where history is non-padding, 0.0 elsewhere.
        mask = (history_batch != 0).float().unsqueeze(-1)
        summed = (item_vecs * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1.0)
        user_vecs = summed / counts
        return F.normalize(user_vecs, p=2, dim=-1)

    def _compute_loss(
        self,
        history_batch: torch.Tensor,
        positive_batch: torch.Tensor,
        rank_probs_t: torch.Tensor,
        rank_to_dense: np.ndarray,
        log_p_per_index_t: torch.Tensor,
        mine_hard_negatives: bool = False,
    ) -> torch.Tensor:
        """Sampled softmax with Yi et al. 2019 log-uniform correction.

        Each negative logit has ``log P(negative)`` subtracted so the
        gradient is an unbiased estimator of the full softmax over the
        catalog. The positive gets the same correction by default — the
        reading TensorFlow's ``sampled_softmax_loss`` takes, where the true
        class is treated as if it too could have been drawn — but ADR 0006's
        own wording says "each negative's logit", so
        ``config.correct_positive_logit`` makes the difference measurable
        rather than assumed. The result is a cross-entropy where the
        positive occupies column 0.
        """
        assert self._item_tower is not None
        # (num_sampled,) — sampled ranks, mapped to dense indices.
        sampled_ranks = torch.multinomial(
            rank_probs_t,
            num_samples=self.config.num_sampled,
            replacement=True,
        )
        neg_dense = torch.tensor(rank_to_dense[sampled_ranks.numpy()], dtype=torch.long)

        user_vecs = self._encode_user(history_batch)  # (B, d)
        pos_vecs = self._item_tower(positive_batch)  # (B, d)
        neg_vecs = self._item_tower(neg_dense)  # (S, d)

        pos_logits = (user_vecs * pos_vecs).sum(dim=-1, keepdim=True)  # (B, 1)
        neg_logits = user_vecs @ neg_vecs.T  # (B, S)

        # Temperature. Both towers are L2-normalized, so these dot products
        # are cosines in [-1, 1] — two nats of range for the model to say
        # anything in, against a correction term below that spans about ten.
        # Dividing by a temperature is what lets the learned score compete
        # with the prior. 1.0 is v1's implicit value and leaves the arithmetic
        # exactly as it was.
        temperature = self.config.logit_temperature
        if temperature != 1.0:
            pos_logits = pos_logits / temperature
            neg_logits = neg_logits / temperature

        # Log-uniform correction — subtract log P(item) from each logit.
        neg_correction = log_p_per_index_t[neg_dense].unsqueeze(0)  # (1, S)
        neg_logits = neg_logits - neg_correction
        if self.config.correct_positive_logit:
            pos_correction = log_p_per_index_t[positive_batch].unsqueeze(-1)  # (B, 1)
            pos_logits = pos_logits - pos_correction

        logit_blocks = [pos_logits, neg_logits]
        if mine_hard_negatives:
            hard_pool_ranks = torch.multinomial(
                rank_probs_t,
                num_samples=self.config.hard_negative_pool_size,
                replacement=True,
            )
            hard_pool_dense = torch.tensor(rank_to_dense[hard_pool_ranks.numpy()], dtype=torch.long)
            with torch.no_grad():
                pool_vectors = self._item_tower(hard_pool_dense)
                pool_scores = user_vecs.detach() @ pool_vectors.T
                hard_dense, hard_valid = select_hard_negatives(
                    candidate_ids=hard_pool_dense,
                    candidate_scores=pool_scores,
                    positive_ids=positive_batch,
                    history_ids=history_batch,
                    k=self.config.hard_negative_count,
                )
            hard_vectors = self._item_tower(hard_dense)
            hard_logits = torch.einsum("bd,bhd->bh", user_vecs, hard_vectors)
            if temperature != 1.0:
                hard_logits = hard_logits / temperature
            # These rows are chosen by model score after proposal sampling,
            # so their effective selection probability is not the original
            # log-uniform proposal. Applying that proposal correction again
            # would over-weight popular mined items. Only the independently
            # sampled random block above receives sampled-softmax correction.
            hard_logits = hard_logits.masked_fill(~hard_valid, float("-inf"))
            logit_blocks.append(hard_logits)
            self._hard_negative_selected += int(hard_valid.sum().item())
            self._hard_negative_slots += int(hard_valid.numel())

        logits = torch.cat(logit_blocks, dim=1)  # positive, random, then hard
        target = torch.zeros(logits.shape[0], dtype=torch.long)
        return F.cross_entropy(logits, target)

    def hard_negative_stats(self) -> dict[str, float]:
        """Coverage of requested hard-negative slots in the latest fit."""
        fill_rate = (
            self._hard_negative_selected / self._hard_negative_slots
            if self._hard_negative_slots
            else 0.0
        )
        return {
            "hard_negative_selected": float(self._hard_negative_selected),
            "hard_negative_slots": float(self._hard_negative_slots),
            "hard_negative_fill_rate": float(fill_rate),
        }

    def item_feature_params(self) -> dict[str, str | int | float | bool]:
        """Compact, reproducible metadata for MLflow and future artifacts."""
        schema = self._item_feature_schema
        if schema is None:
            return {"item_features_fitted": False, "item_feature_count": 0}
        payload = json.dumps(schema.to_dict(), sort_keys=True, separators=(",", ":"))
        return {
            "item_features_fitted": True,
            "item_feature_count": len(schema.feature_names),
            "item_feature_genre_count": len(schema.genres),
            "item_feature_release_year_mean": schema.release_year_mean,
            "item_feature_release_year_std": schema.release_year_std,
            "item_feature_schema_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }

    def _build_faiss_index(self, n_items: int) -> None:
        """Train and populate a FAISS IVF-Flat index over item embeddings.

        Inner product metric on unit-normalized vectors — equivalent to
        cosine similarity, which is the space the two towers train in.
        Trained on the item embeddings themselves so the coarse quantizer
        matches the distribution actually queried at recommend time.
        """
        assert self._item_tower is not None
        with torch.no_grad():
            # Skip the padding row (index 0).
            item_vecs = (
                self._item_tower(torch.arange(1, n_items + 1, dtype=torch.long))
                .numpy()
                .astype(np.float32)
            )

        d = self.config.embedding_dim
        if self.config.faiss_exact:
            # Diagnostic path: brute-force inner product, no quantization and
            # no recall loss of its own. A run that gains here and nowhere
            # else was losing neighbours to the index, not to the embeddings.
            exact = faiss.IndexFlatIP(d)
            exact.add(item_vecs)
            self._faiss_index = exact
            return

        quantizer = faiss.IndexFlatIP(d)
        # nlist must not exceed n_train. FAISS complains loudly on small
        # synthetic datasets otherwise; cap defensively.
        effective_nlist = min(self.config.faiss_nlist, max(1, n_items // 4))
        index = faiss.IndexIVFFlat(quantizer, d, effective_nlist, faiss.METRIC_INNER_PRODUCT)
        index.train(item_vecs)
        index.add(item_vecs)
        index.nprobe = self.config.faiss_nprobe
        self._faiss_index = index

    def recommend(self, user_id: int, k: int) -> list[int]:
        """Top-k items for one user.

        Cold user (no training history or tower not fitted) → popularity
        fallback. Warm user → mean-pool their history through the trained
        item embeddings, L2-normalize, query FAISS for ``k + |seen|``
        candidates, filter already-seen items.
        """
        if not self.was_served_by_twotower(user_id):
            return self._popularity.recommend(user_id, k)

        assert self._item_tower is not None
        assert self._faiss_index is not None

        history = self._user_history[user_id]
        # Take the trailing N items — same window training used, so a warm
        # user with a long history is encoded from the same slice at
        # inference as during their most-recent training example.
        window = history[-self.config.history_window :]
        pad = [0] * (self.config.history_window - len(window))
        history_tensor = torch.tensor([pad + window], dtype=torch.long)

        with torch.no_grad():
            user_vec = self._encode_user(history_tensor).numpy().astype(np.float32)

        seen = self._popularity.user_history.get(user_id, set())
        # FAISS returns dense-index results; we request headroom so the
        # already-seen filter doesn't shrink us below k.
        n_request = min(k + len(seen), len(self._index_to_item))
        _scores, dense_indices = self._faiss_index.search(user_vec, n_request)

        out: list[int] = []
        for idx in dense_indices[0]:
            if idx < 1:  # -1 = FAISS's "no neighbor" sentinel; 0 = padding
                continue
            movie = int(self._index_to_item[int(idx)])
            if movie in seen:
                continue
            out.append(movie)
            if len(out) == k:
                break
        return out

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        """Batch variant — one ``list[int]`` per user, keyed by user id."""
        return {uid: self.recommend(uid, k) for uid in user_ids}

    def was_served_by_twotower(self, user_id: int) -> bool:
        """Predicate: would ``recommend(user_id, …)`` go through the tower or popularity?

        By default: true iff the tower is fitted and the user has at least
        ``cold_start_threshold`` distinct training items, which is the rule
        `src/serving/orchestration.py` applies to a live request. ``recommend``
        already calls this rather than restating the condition, so the two
        cannot drift — which matters because the condition has two forms. With
        ``cold_start_threshold=None`` the index-membership opt-out applies and
        any training history at all is enough.
        """
        if (
            self._item_tower is None
            or self._faiss_index is None
            or user_id not in self._user_history
            or len(self._user_history[user_id]) == 0
        ):
            return False
        return routing.learned_path_serves(
            history_size=len(self._popularity.user_history.get(user_id, ())),
            cold_start_threshold=self.cold_start_threshold,
        )
