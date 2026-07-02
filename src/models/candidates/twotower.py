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

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import faiss
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F  # noqa: N812 — canonical PyTorch alias
from torch import nn

from .popularity import PopularityModel

logger = logging.getLogger(__name__)


@dataclass
class TwoTowerConfig:
    """Hyperparameters. Every field is logged as an MLflow param by the
    training script so a future sweep is a pure config change.
    """

    embedding_dim: int = 64
    history_window: int = 50  # N in ADR 0006 — trailing items used to encode a user
    batch_size: int = 4096
    # ADR 0006 pins num_sampled = 4 * batch_size. Kept as an explicit
    # field so a sweep can vary it independently.
    num_sampled: int = 16384
    epochs: int = 3
    learning_rate: float = 1e-3
    # FAISS IVF-Flat tuning. nlist = sqrt(n_items) rounded is the FAISS
    # rule-of-thumb; MovieLens has ~62 k items so 100 is under the
    # recommended range but keeps train time bounded and matches what the
    # ADR pins as a defensible starting point.
    faiss_nlist: int = 100
    faiss_nprobe: int = 10
    seed: int = 42


class ItemTower(nn.Module):
    """Id-only item embedding, L2-normalized on the way out.

    Index 0 is reserved as the padding id so variable-length user histories
    pack cleanly into a ``(batch_size, N)`` integer tensor. The padding row
    is kept at zero (``padding_idx=0`` freezes it), which makes it a no-op
    in a mean-pool sum — only the true items contribute, and the caller
    divides by the true item count (not by ``N``).
    """

    def __init__(self, n_items: int, embedding_dim: int) -> None:
        super().__init__()
        # +1 for the padding row at index 0.
        self.embed = nn.Embedding(n_items + 1, embedding_dim, padding_idx=0)
        # Small-variance init, standard for embedding tables trained with
        # sampled-softmax. Larger init pushes early logits toward the tails
        # of softmax and slows convergence.
        nn.init.normal_(self.embed.weight, mean=0.0, std=1.0 / math.sqrt(embedding_dim))
        with torch.no_grad():
            self.embed.weight[0].zero_()

    def forward(self, item_ids: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.embed(item_ids), p=2, dim=-1)


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
    return ordered.assign(_dense=ordered_dense).groupby("userId")["_dense"].apply(list).to_dict()


def _log_uniform_probabilities(n_items: int) -> np.ndarray:
    """Rank-based log-uniform sampling weights for negatives.

    P(rank r) ∝ log(r + 2) - log(r + 1). Same distribution TensorFlow's
    ``log_uniform_candidate_sampler`` uses. The array is over *ranks*
    (0 = most popular); callers permute by the popularity ordering before
    sampling.
    """
    ranks = np.arange(n_items, dtype=np.float64)
    weights = np.log(ranks + 2.0) - np.log(ranks + 1.0)
    return weights / weights.sum()


@dataclass
class TwoTowerModel:
    """Public model class matching the ``CandidateModel``-shaped contract.

    Same interface as PopularityModel / CFModel / ItemItemModel: ``fit``,
    ``recommend``, ``recommend_for_users``, and a ``was_served_by_twotower``
    predicate for per-policy attribution. Phase 3's serving layer treats
    these interchangeably.
    """

    config: TwoTowerConfig = field(default_factory=TwoTowerConfig)

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

    def fit(
        self,
        train: pd.DataFrame,
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
            return self

        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)

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
        history_tensor, positive_tensor = self._build_training_pairs()
        n_examples = positive_tensor.shape[0]
        logger.info(
            "Training on %d (history, positive) pairs across %d users, %d items",
            n_examples,
            len(self._user_history),
            n_items,
        )

        # ---- Training loop ----
        self._item_tower = ItemTower(n_items=n_items, embedding_dim=self.config.embedding_dim)
        optimizer = torch.optim.Adam(self._item_tower.parameters(), lr=self.config.learning_rate)

        for epoch in range(self.config.epochs):
            perm = torch.randperm(n_examples)
            history_shuf = history_tensor[perm]
            positive_shuf = positive_tensor[perm]

            epoch_loss = 0.0
            n_batches = 0
            for start in range(0, n_examples, self.config.batch_size):
                end = start + self.config.batch_size
                history_batch = history_shuf[start:end]
                positive_batch = positive_shuf[start:end]

                loss = self._compute_loss(
                    history_batch=history_batch,
                    positive_batch=positive_batch,
                    rank_probs_t=rank_probs_t,
                    rank_to_dense=rank_to_dense,
                    log_p_per_index_t=log_p_per_index_t,
                )
                optimizer.zero_grad()
                loss.backward()
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

        # ---- FAISS index ----
        self._build_faiss_index(n_items)
        return self

    def _build_training_pairs(self) -> tuple[torch.Tensor, torch.Tensor]:
        """Materialize the (history, positive) tensors from ``_user_history``.

        History is padded with 0 on the *left* so the last N slots are the
        most-recent items — mean-pool doesn't care about position, but a
        left-pad keeps the invariant readable in tests.
        """
        n = self.config.history_window
        histories: list[list[int]] = []
        positives: list[int] = []
        for hist in self._user_history.values():
            for i in range(1, len(hist)):
                window = hist[max(0, i - n) : i]
                # Left-pad to N.
                pad = [0] * (n - len(window))
                histories.append(pad + window)
                positives.append(hist[i])
        history_tensor = torch.tensor(histories, dtype=torch.long)
        positive_tensor = torch.tensor(positives, dtype=torch.long)
        return history_tensor, positive_tensor

    def _encode_user(self, history_batch: torch.Tensor) -> torch.Tensor:
        """Mean-pool over non-padding history items, then L2-normalize.

        ``history_batch`` is ``(B, N)`` with 0 marking padding. The item
        tower zeroes the padding row so the sum is over true items only;
        we divide by the true item count (clamped ≥ 1 for safety), then
        re-normalize.
        """
        assert self._item_tower is not None
        # (B, N, d) — padding rows contribute the zero vector.
        item_vecs = self._item_tower.embed(history_batch)
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
    ) -> torch.Tensor:
        """Sampled softmax with Yi et al. 2019 log-uniform correction.

        Each negative logit has ``log P(negative)`` subtracted so the
        gradient is an unbiased estimator of the full softmax over the
        catalog. The positive gets the same correction so warm items — even
        popular ones — aren't systematically penalized in the ranking. The
        result is a cross-entropy where the positive occupies column 0.
        """
        assert self._item_tower is not None
        # (num_sampled,) — sampled ranks, mapped to dense indices.
        sampled_ranks = torch.multinomial(
            rank_probs_t,
            num_samples=self.config.num_sampled,
            replacement=True,
        )
        neg_dense = torch.tensor(
            rank_to_dense[sampled_ranks.numpy()], dtype=torch.long
        )

        user_vecs = self._encode_user(history_batch)  # (B, d)
        pos_vecs = self._item_tower(positive_batch)  # (B, d)
        neg_vecs = self._item_tower(neg_dense)  # (S, d)

        pos_logits = (user_vecs * pos_vecs).sum(dim=-1, keepdim=True)  # (B, 1)
        neg_logits = user_vecs @ neg_vecs.T  # (B, S)

        # Log-uniform correction — subtract log P(item) from each logit.
        pos_correction = log_p_per_index_t[positive_batch].unsqueeze(-1)  # (B, 1)
        neg_correction = log_p_per_index_t[neg_dense].unsqueeze(0)  # (1, S)
        pos_logits = pos_logits - pos_correction
        neg_logits = neg_logits - neg_correction

        logits = torch.cat([pos_logits, neg_logits], dim=1)  # (B, 1 + S)
        target = torch.zeros(logits.shape[0], dtype=torch.long)
        return F.cross_entropy(logits, target)

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
            item_vecs = self._item_tower(
                torch.arange(1, n_items + 1, dtype=torch.long)
            ).numpy().astype(np.float32)

        d = self.config.embedding_dim
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

        True iff the tower is fitted and the user has any training history.
        Mirrors the routing condition in ``recommend`` exactly so training
        can attribute metrics to the right policy without re-deriving the
        predicate — same pattern CFModel and ItemItemModel established.
        """
        return (
            self._item_tower is not None
            and self._faiss_index is not None
            and user_id in self._user_history
            and len(self._user_history[user_id]) > 0
        )
