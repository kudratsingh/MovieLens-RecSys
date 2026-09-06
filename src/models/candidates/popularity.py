"""
Popularity baseline.

Ranks every item in the catalog by how many times it was rated in the
training window, then recommends the top-K from that ranking to each user,
excluding items the user has already seen in train. Unpersonalized — every
warm user gets the same global ordering minus their own history; cold
users get the head of the global ordering. This is the bar the
collaborative-filtering and two-tower models have to clear.

Filtering out already-seen items matters: without it, top recommendations
would be the user's existing favorites, which they're unlikely to re-rate
in the holdout window, and the metric would be artificially deflated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class PopularityModel:
    # Item ids ordered by descending training popularity. The first element is
    # the most-rated item; the list contains every item that appears in train.
    ranking: list[int] = field(default_factory=list)
    # Map from user id to the set of item ids that user rated in train.
    # Used purely to filter recommendations — never to compute popularity.
    user_history: dict[int, set[int]] = field(default_factory=dict)
    # The raw interaction counts `ranking` was sorted from. Kept because the
    # ranking alone cannot be re-ordered under a different tiebreak, and the
    # published fill order (src/models/popularity_artifact.py) needs to: this
    # model's tie order comes from a non-stable sort and is a pandas
    # implementation detail, which is fine for a ranking and not fine for bytes
    # a manifest pins by checksum.
    counts: dict[int, int] = field(default_factory=dict)

    def fit(self, train: pd.DataFrame) -> PopularityModel:
        """Build the popularity ranking and per-user history from a train slice.

        Expects columns ``userId`` and ``movieId``. An empty DataFrame produces
        an empty model — recommendation against it yields no items, which the
        eval harness handles as a 0.0 score per user.
        """
        if train.empty:
            self.ranking = []
            self.user_history = {}
            self.counts = {}
            return self

        counts = train.groupby("movieId").size().sort_values(ascending=False)
        self.ranking = counts.index.tolist()
        self.counts = {int(movie_id): int(count) for movie_id, count in counts.items()}
        self.user_history = train.groupby("userId")["movieId"].apply(set).to_dict()
        return self

    def recommend(self, user_id: int, k: int) -> list[int]:
        """Top-k popular items the given user hasn't already seen.

        Unknown users (no training history) get the head of the global
        ranking — the right behavior for cold-start, matching the popularity
        fallback specified in ADR 0001.
        """
        seen = self.user_history.get(user_id, set())
        out: list[int] = []
        for item in self.ranking:
            if item in seen:
                continue
            out.append(item)
            if len(out) == k:
                break
        return out

    def recommend_for_users(self, user_ids: Iterable[int], k: int) -> dict[int, list[int]]:
        """Batch variant — one ``list[int]`` per user, keyed by user id."""
        return {uid: self.recommend(uid, k) for uid in user_ids}
