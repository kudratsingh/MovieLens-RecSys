"""
Item-item collaborative filtering candidate generator.

The third candidate model in the candidate-stage lineage (after popularity
and CF/ALS). Per ADR 0004, item-item lands first in Phase 2 as the
zero-learned-parameters baseline the two-tower has to beat to take over the
candidate-stage champion slot.

The recommender precomputes, for each item, the top-K most similar items
under cosine similarity over the binary user-item interaction matrix. At
recommend time, for a user with history H, item i is scored as the sum of
its similarities to the items in H; the top-N by score are returned with
already-seen items filtered out. Cold users — those absent from the
training matrix — fall through to the embedded popularity baseline, the
same fallback pattern CFModel established and ADR 0001 locked in.

The implicit library's CosineRecommender does exactly this precomputation
and aggregation, including a sparse top-K storage of the similarity matrix
(K_neighbors here), which is the production pattern — a dense 62 k × 62 k
matrix would be ~15 GB and infeasible. As with CFModel, we own the
bidirectional MovieLens-id ↔ dense-index mapping because MovieLens ids are
not contiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from implicit.nearest_neighbours import CosineRecommender
from scipy.sparse import csr_matrix

from .popularity import PopularityModel


@dataclass
class ItemItemModel:
    # Number of nearest-neighbor items retained per item in the precomputed
    # similarity index. The implicit library defaults to 20; we raise to 200
    # to capture more of the long-tail signal MovieLens has (median item
    # popularity is 6 ratings per docs/eda.md section 4) at the cost of a
    # larger but still bounded sparse matrix. Logged as an MLflow param so
    # future sweeps can sweep it.
    k_neighbors: int = 200

    # Populated by fit:
    _knn: CosineRecommender | None = None
    _user_to_index: dict[int, int] = field(default_factory=dict)
    _index_to_item: dict[int, int] = field(default_factory=dict)
    _user_items: csr_matrix | None = None
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(self, train: pd.DataFrame) -> ItemItemModel:
        """Train the cosine item-item index; also fit the popularity fallback.

        Expects ``userId`` and ``movieId`` columns. Rating values are ignored
        (every interaction has weight 1.0 per ADR 0002).
        """
        # Fit the fallback first so it's ready even if the KNN step is skipped.
        self._popularity = PopularityModel().fit(train)

        if train.empty:
            self._knn = None
            self._user_to_index = {}
            self._index_to_item = {}
            self._user_items = None
            return self

        # Same dense-index assignment trick used by CFModel — pandas
        # categoricals build forward + inverse maps in one pass and are
        # faster than a python dict comprehension on 25 M rows.
        users = pd.Categorical(train["userId"])
        items = pd.Categorical(train["movieId"])
        self._user_to_index = {uid: i for i, uid in enumerate(users.categories)}
        self._index_to_item = dict(enumerate(items.categories))

        n_users = len(users.categories)
        n_items = len(items.categories)
        data = np.ones(len(train), dtype=np.float32)
        self._user_items = csr_matrix(
            (data, (users.codes, items.codes)),
            shape=(n_users, n_items),
        )

        self._knn = CosineRecommender(K=self.k_neighbors)
        # implicit's progress bar lights up the terminal; quiet it.
        self._knn.fit(self._user_items, show_progress=False)
        return self

    def recommend(self, user_id: int, k: int, filter_seen: bool = True) -> list[int]:
        """Top-k items for one user.

        Unknown user (no training history) → popularity fallback. Known user →
        cosine-aggregated scores over their history.

        ``filter_seen`` defaults to True (serving shape — items the user has
        already interacted with in train are excluded from candidates). The
        ranker training pipeline passes ``filter_seen=False`` so a sampled
        positive (which is always in the user's train history) can appear in
        the candidate list and become a LambdaRank positive; otherwise every
        such positive is silently dropped.
        """
        if self._knn is None or user_id not in self._user_to_index:
            return self._popularity.recommend(user_id, k)

        user_idx = self._user_to_index[user_id]
        assert self._user_items is not None  # implied by self._knn is not None

        if filter_seen:
            # Same post-filter pattern CFModel uses: implicit's
            # filter_already_liked_items pushes seen items to the bottom but
            # does not drop them; when N approaches the catalog size, they
            # leak into top-K. Ask for k + |seen| and filter explicitly.
            seen = self._popularity.user_history.get(user_id, set())
            n_request = min(k + len(seen), len(self._index_to_item))
        else:
            seen = set()
            n_request = min(k, len(self._index_to_item))

        item_indices, _scores = self._knn.recommend(
            user_idx,
            self._user_items[user_idx],
            N=n_request,
            filter_already_liked_items=filter_seen,
        )

        out: list[int] = []
        for idx in item_indices:
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

    def was_served_by_itemitem(self, user_id: int) -> bool:
        """Predicate: would ``recommend(user_id, …)`` go through item-item or popularity?

        True iff this user has any training history and the KNN index is
        fitted. Mirrors the routing condition inside ``recommend`` exactly so
        the training pipeline can attribute metrics to the right policy
        without re-deriving the predicate — same pattern CFModel established
        for ALS-vs-fallback attribution.
        """
        return self._knn is not None and user_id in self._user_to_index
