"""
Feature engineering for the LightGBM ranker.

Per ADR 0005, features are the ranker's decisive lever — the model itself
is a well-understood GBDT and the win margin comes from what we feed it.
Point-in-time correctness is the module's central contract: any feature
value for a query at timestamp ``t`` must be computable from data with
``timestamp < t`` only. Non-negotiable per CLAUDE.md; enforced by the
canary test in ``tests/unit/test_features.py``.

Feature set (kept small in this first pass; expandable without touching
the call sites):

  User-side (input: train ratings):
    - ``user_interaction_count`` — # of the user's train interactions
      strictly before ``as_of``.
    - ``user_days_active`` — (last - first) in the user's pre-``as_of``
      slice, in days. Captures how long a user has been engaged.
    - ``user_days_since_last_interaction`` — ``as_of`` minus the user's
      last strictly-earlier interaction timestamp, in days.

  Item-side (input: train ratings + ``movies`` table):
    - ``item_popularity_all_time`` — # interactions with this item
      strictly before ``as_of``.
    - ``item_popularity_30d`` — same over the trailing 30-day window.
    - ``item_popularity_7d`` — same over the trailing 7-day window.
    - ``item_age_days`` — ``as_of`` minus the item's first observed
      interaction timestamp. Genuinely new items have age 0.

  User × Item (input: user history + item genres):
    - ``user_genre_affinity`` — fraction of the user's pre-``as_of`` train
      history whose genre set intersects this item's genre set. Cheap
      proxy for taste match without exploding into one-hot features.

Efficiency: the ``FeatureIndex`` class precomputes per-user and per-item
sorted timestamp arrays so each query is O(log N_user + log N_item) via
``bisect``. Naive implementations would be O(N_train) per query, which
becomes M × N_train — untenable for the ranker's training set of ~10⁵
queries against a 25 M-row train.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Ordered feature column names — used both by the ranker's fit/predict
# path (to select columns from the DataFrame) and by the test suite (to
# assert the schema doesn't drift silently).
FEATURE_COLUMNS: list[str] = [
    "user_interaction_count",
    "user_days_active",
    "user_days_since_last_interaction",
    "item_popularity_all_time",
    "item_popularity_30d",
    "item_popularity_7d",
    "item_age_days",
    "user_genre_affinity",
]

_SECONDS_PER_DAY = 24 * 3600
_THIRTY_DAYS = 30 * _SECONDS_PER_DAY
_SEVEN_DAYS = 7 * _SECONDS_PER_DAY


def _parse_genre_set(raw: str) -> frozenset[str]:
    """Turn MovieLens's pipe-separated ``genres`` string into a set.

    MovieLens uses ``"(no genres listed)"`` as a sentinel for missing
    genres; treat that as the empty set so it never matches — a genre
    match with a genre-less item would be a false signal.
    """
    if not raw or raw == "(no genres listed)":
        return frozenset()
    return frozenset(raw.split("|"))


@dataclass
class FeatureIndex:
    """Precomputed structures for point-in-time feature lookup.

    Built once from ``(train_ratings, movies)`` and reused across every
    query batch. Attribute types are explicit so mypy strict has a firm
    grip on the tensor shapes the training script downstream operates on.
    """

    # Per-user sorted timestamps (ascending). Length == user's train
    # interaction count.
    _user_timestamps: dict[int, np.ndarray] = field(default_factory=dict)
    # Per-user sorted movie ids, aligned to ``_user_timestamps`` (i.e.
    # ``_user_movies[u][i]`` was consumed at ``_user_timestamps[u][i]``).
    _user_movies: dict[int, np.ndarray] = field(default_factory=dict)
    # Per-item sorted timestamps (ascending).
    _item_timestamps: dict[int, np.ndarray] = field(default_factory=dict)
    # movieId -> frozenset of genres from ``movies``.
    _item_genres: dict[int, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, train_ratings: pd.DataFrame, movies: pd.DataFrame) -> FeatureIndex:
        """Materialize the per-user / per-item sorted arrays from train.

        Expects columns ``userId``, ``movieId``, ``timestamp`` on
        ``train_ratings`` and ``movieId``, ``genres`` on ``movies``.
        Rating values are ignored — every interaction has weight 1.0 per
        ADR 0002.
        """
        index = cls()

        # Genre map. Materialize the full movies table so an item that
        # appears only as a *candidate* (never yet in any user's train
        # history) still has known genres.
        for row in movies[["movieId", "genres"]].itertuples(index=False):
            index._item_genres[int(row.movieId)] = _parse_genre_set(str(row.genres))

        if train_ratings.empty:
            return index

        # Stable-sort so the per-user / per-item slices are chronological.
        ordered = train_ratings.sort_values(["timestamp"], kind="stable")

        for user_id, group in ordered.groupby("userId", sort=False):
            index._user_timestamps[int(user_id)] = group["timestamp"].to_numpy(dtype=np.int64)
            index._user_movies[int(user_id)] = group["movieId"].to_numpy(dtype=np.int64)

        for movie_id, group in ordered.groupby("movieId", sort=False):
            index._item_timestamps[int(movie_id)] = group["timestamp"].to_numpy(dtype=np.int64)

        return index

    # ---- User-side features ---------------------------------------------

    def _user_features(self, user_id: int, as_of: int) -> tuple[float, float, float]:
        """Return (interaction_count, days_active, days_since_last).

        A user with no strictly-earlier interactions gets
        ``(0, 0, days_since_last=-1)`` — the sentinel −1 lets the ranker
        distinguish "no history" from "history ends exactly at as_of"
        without collapsing the two into a single 0.
        """
        timestamps = self._user_timestamps.get(user_id)
        if timestamps is None or len(timestamps) == 0:
            return 0.0, 0.0, -1.0

        cutoff = bisect_left(timestamps, as_of)
        if cutoff == 0:
            return 0.0, 0.0, -1.0

        past = timestamps[:cutoff]
        count = float(cutoff)
        days_active = float((past[-1] - past[0]) / _SECONDS_PER_DAY)
        days_since_last = float((as_of - past[-1]) / _SECONDS_PER_DAY)
        return count, days_active, days_since_last

    # ---- Item-side features ---------------------------------------------

    def _item_features(self, movie_id: int, as_of: int) -> tuple[float, float, float, float]:
        """Return (pop_all_time, pop_30d, pop_7d, age_days).

        An item with no strictly-earlier interactions is treated as
        brand-new: popularity 0 across all windows, age 0. This is the
        cold-item case; the ranker learns to weight it with popularity =
        0 rather than being handed a missing value.
        """
        timestamps = self._item_timestamps.get(movie_id)
        if timestamps is None or len(timestamps) == 0:
            return 0.0, 0.0, 0.0, 0.0

        cutoff = bisect_left(timestamps, as_of)
        if cutoff == 0:
            return 0.0, 0.0, 0.0, 0.0

        past = timestamps[:cutoff]
        pop_all = float(cutoff)
        # Trailing-window popularity — count interactions with
        # as_of - window <= t < as_of.
        left_30 = bisect_left(past, as_of - _THIRTY_DAYS)
        left_7 = bisect_left(past, as_of - _SEVEN_DAYS)
        pop_30d = float(cutoff - left_30)
        pop_7d = float(cutoff - left_7)
        age_days = float((as_of - past[0]) / _SECONDS_PER_DAY)
        return pop_all, pop_30d, pop_7d, age_days

    # ---- User × item features -------------------------------------------

    def _user_item_features(self, user_id: int, movie_id: int, as_of: int) -> float:
        """Genre affinity: fraction of the user's strictly-past history
        whose genre set intersects the query item's genre set.

        Returns 0.0 if either the user has no past history or the query
        item has no listed genres. Both cases are honest zero signals —
        no data to compute the affinity, no false-positive match.
        """
        query_genres = self._item_genres.get(movie_id, frozenset())
        if not query_genres:
            return 0.0

        movies = self._user_movies.get(user_id)
        timestamps = self._user_timestamps.get(user_id)
        if movies is None or timestamps is None or len(timestamps) == 0:
            return 0.0

        cutoff = bisect_left(timestamps, as_of)
        if cutoff == 0:
            return 0.0

        past_movies = movies[:cutoff]
        matches = 0
        for past_movie in past_movies:
            past_genres = self._item_genres.get(int(past_movie), frozenset())
            if past_genres & query_genres:
                matches += 1
        return float(matches) / float(cutoff)

    # ---- Public entrypoint ----------------------------------------------

    def features_for(self, queries: pd.DataFrame) -> pd.DataFrame:
        """Compute the full feature block for a batch of queries.

        ``queries`` must have columns ``userId``, ``movieId``,
        ``as_of_timestamp``. Returns a DataFrame with one row per query
        preserving the query order — the ranker's LambdaRank groups
        depend on this ordering.
        """
        n = len(queries)
        # Allocate once so we can pass numpy slices back into pandas
        # without the append-per-row overhead.
        matrix = np.zeros((n, len(FEATURE_COLUMNS)), dtype=np.float64)

        user_ids = queries["userId"].to_numpy(dtype=np.int64)
        movie_ids = queries["movieId"].to_numpy(dtype=np.int64)
        as_ofs = queries["as_of_timestamp"].to_numpy(dtype=np.int64)

        for i in range(n):
            user_id = int(user_ids[i])
            movie_id = int(movie_ids[i])
            as_of = int(as_ofs[i])
            uc, ua, ul = self._user_features(user_id, as_of)
            ip_all, ip_30, ip_7, ia = self._item_features(movie_id, as_of)
            ga = self._user_item_features(user_id, movie_id, as_of)
            matrix[i, 0] = uc
            matrix[i, 1] = ua
            matrix[i, 2] = ul
            matrix[i, 3] = ip_all
            matrix[i, 4] = ip_30
            matrix[i, 5] = ip_7
            matrix[i, 6] = ia
            matrix[i, 7] = ga

        return pd.DataFrame(matrix, columns=FEATURE_COLUMNS)


def build_features(
    train_ratings: pd.DataFrame,
    movies: pd.DataFrame,
    queries: pd.DataFrame,
) -> pd.DataFrame:
    """Convenience: build the index and materialize the feature block.

    Callers with multiple query batches should build the ``FeatureIndex``
    once and call ``.features_for(...)`` per batch — this function
    rebuilds the index every call.
    """
    return FeatureIndex.build(train_ratings, movies).features_for(queries)
