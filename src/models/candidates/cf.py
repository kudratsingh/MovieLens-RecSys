"""
Collaborative-filtering baseline via implicit ALS.

Matrix factorization on the (user, item) interaction sparse matrix. Every
rating counts as a positive interaction — see ADR 0002 for why. Cold users
(no training history) fall through to the popularity baseline, which is the
ADR 0001 fallback path. That keeps the recommender well-defined for every
user the eval harness might hand us; the metric we report is the metric for
the deployed policy, not just for ALS in isolation.

The implicit library uses dense 0..N indices internally; MovieLens ids are
not contiguous (62 423 movies span ids up to ~209 000), so this module owns
the bidirectional id↔index mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from implicit.als import AlternatingLeastSquares
from scipy.sparse import csr_matrix

from .popularity import PopularityModel


@dataclass
class CFModel:
    # ALS hyperparameters. Logged as MLflow params so future runs can be
    # swept; the Phase 1 defaults are not claimed to be optimal — they are
    # the standard implicit-ALS starting point.
    factors: int = 64
    regularization: float = 0.01
    iterations: int = 15
    random_state: int = 42

    # Populated by fit:
    _als: AlternatingLeastSquares | None = None
    _user_to_index: dict[int, int] = field(default_factory=dict)
    _index_to_item: dict[int, int] = field(default_factory=dict)
    _user_items: csr_matrix | None = None
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(self, train: pd.DataFrame) -> CFModel:
        """Train ALS on the interaction matrix; also fit the popularity fallback.

        Expects ``userId`` and ``movieId`` columns. Rating values are ignored
        (every interaction has weight 1.0 — see ADR 0002).
        """
        # Fit the fallback first so it's ready even if ALS is somehow skipped.
        self._popularity = PopularityModel().fit(train)

        if train.empty:
            self._als = None
            self._user_to_index = {}
            self._index_to_item = {}
            self._user_items = None
            return self

        # Dense index assignment. We use pandas categoricals because they
        # build the forward + inverse maps in one pass and are faster than a
        # python dict comprehension on 20 M rows.
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

        self._als = AlternatingLeastSquares(
            factors=self.factors,
            regularization=self.regularization,
            iterations=self.iterations,
            random_state=self.random_state,
        )
        # implicit's progress bar lights up the terminal; quiet it.
        self._als.fit(self._user_items, show_progress=False)
        return self

    def recommend(self, user_id: int, k: int) -> list[int]:
        """Top-k items for one user.

        Unknown user (no training history) → popularity fallback. Known user →
        ALS scores, with items the user already saw in train filtered out.
        """
        if self._als is None or user_id not in self._user_to_index:
            return self._popularity.recommend(user_id, k)

        user_idx = self._user_to_index[user_id]
        assert self._user_items is not None  # implied by self._als is not None

        # implicit's filter_already_liked_items pushes seen items to the
        # bottom of the returned ranking but does not remove them — when
        # N exceeds the count of unseen items in the catalog, the seen
        # items come back at the tail. Ask for k + |seen| so we have
        # headroom, then filter explicitly. In production with a 34 k-item
        # catalog and ~150 items per user this is a tiny ask; the test's
        # 10-item synthetic catalog is what surfaced the edge case.
        seen = self._popularity.user_history.get(user_id, set())
        n_request = min(k + len(seen), len(self._index_to_item))
        item_indices, _scores = self._als.recommend(
            user_idx,
            self._user_items[user_idx],
            N=n_request,
            filter_already_liked_items=True,
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
