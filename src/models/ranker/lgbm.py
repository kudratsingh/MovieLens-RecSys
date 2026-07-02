"""
LightGBM ranker for the Phase 2 two-stage architecture.

Per ADR 0005, the ranker scores the ~500 surviving candidates from a
candidate model and returns the top-K by predicted relevance. The
objective is ``lambdarank`` — LightGBM's listwise learning-to-rank loss
that optimizes a smooth approximation to NDCG, matching the metric
ADR 0001 pins as the recommender-end-to-end success criterion.

Contract:

  - ``fit(features_df, group_sizes, labels)`` — features come from the
    ``FEATURE_COLUMNS`` schema pinned in ``src/features/pipeline.py``.
    ``group_sizes`` is a list where each entry is the number of rows in
    that group (one group per (user, query-time) query). ``labels`` is
    1 for positives, 0 for negatives.
  - ``predict(features_df) -> np.ndarray`` — raw scores, higher = more
    relevant. Not calibrated probabilities.
  - ``rank_candidates(candidates_by_user, features_by_user, k)`` — the
    shape both the training script's eval loop and Phase 3's serving
    handler will call. Returns ``dict[int, list[int]]``: user id → top-K
    movie ids.

Cold-start passes through cleanly — a user with no history gets features
close to zero on the user-side dimensions, and the ranker learns to
weight the item-side features (popularity, age) more heavily for those
rows. Matches the ranker's cold-start behavior noted in ADR 0005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.features import FEATURE_COLUMNS


@dataclass
class LGBMRankerConfig:
    """Hyperparameters. Every field is logged as an MLflow param by the
    training script so a future sweep is a pure config change.

    The defaults are the "sensible starting point" set — chosen to train
    fast enough on the ranker's ~10⁵-row training set (< 5 min on CPU)
    and to avoid the most common LambdaRank failure modes (too-shallow
    trees under-fit the group structure; too-deep trees overfit).
    """

    num_leaves: int = 63
    learning_rate: float = 0.05
    min_data_in_leaf: int = 20
    num_boost_round: int = 200
    feature_fraction: float = 0.9
    bagging_fraction: float = 0.9
    bagging_freq: int = 5
    lambda_l2: float = 1.0
    seed: int = 42
    # NDCG cutoff LGBM's internal metric reports during training. Aligned
    # with ADR 0001's K=10.
    ndcg_eval_at: tuple[int, ...] = (10,)


@dataclass
class LGBMRanker:
    """Public model class. Same shape as candidate-stage models in intent:
    a ``fit``, a ``predict``, and a batch method the serving layer wants.
    """

    config: LGBMRankerConfig = field(default_factory=LGBMRankerConfig)

    _booster: lgb.Booster | None = None

    def fit(
        self,
        features_df: pd.DataFrame,
        group_sizes: list[int],
        labels: np.ndarray,
    ) -> LGBMRanker:
        """Train a LambdaRank booster on the (features, group, label) tuple.

        ``sum(group_sizes)`` must equal ``len(features_df)`` — LightGBM
        segments the rows by walking ``group_sizes`` in order. Assertion
        catches the off-by-one that would otherwise train silently on the
        wrong groupings.
        """
        assert sum(group_sizes) == len(
            features_df
        ), f"group sizes ({sum(group_sizes)}) must sum to n_rows ({len(features_df)})"
        assert len(labels) == len(features_df)

        # Enforce column order — the ranker learns splits by column index,
        # and a downstream caller reordering columns would silently score
        # candidates against the wrong feature per split. FEATURE_COLUMNS
        # is the schema of record; anything else on the DataFrame is dropped.
        feature_matrix = features_df[FEATURE_COLUMNS].to_numpy(dtype=np.float64)

        train_set = lgb.Dataset(
            feature_matrix, label=labels, group=group_sizes, free_raw_data=False
        )
        params: dict[str, Any] = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": list(self.config.ndcg_eval_at),
            "num_leaves": self.config.num_leaves,
            "learning_rate": self.config.learning_rate,
            "min_data_in_leaf": self.config.min_data_in_leaf,
            "feature_fraction": self.config.feature_fraction,
            "bagging_fraction": self.config.bagging_fraction,
            "bagging_freq": self.config.bagging_freq,
            "lambda_l2": self.config.lambda_l2,
            "seed": self.config.seed,
            "verbose": -1,
        }
        self._booster = lgb.train(
            params,
            train_set,
            num_boost_round=self.config.num_boost_round,
        )
        return self

    def predict(self, features_df: pd.DataFrame) -> np.ndarray:
        """Score each row. Higher score = more relevant.

        Callers rank within a group (user); scores across users are not
        comparable — LambdaRank optimizes per-group ordering, not
        cross-group calibration.
        """
        assert self._booster is not None, "call fit() before predict()"
        feature_matrix = features_df[FEATURE_COLUMNS].to_numpy(dtype=np.float64)
        scores: np.ndarray = self._booster.predict(feature_matrix)
        return scores

    def rank_candidates(
        self,
        candidates_by_user: dict[int, list[int]],
        features_by_user: dict[int, pd.DataFrame],
        k: int,
    ) -> dict[int, list[int]]:
        """End-to-end re-ranking for a batch of users.

        For each user, ``candidates_by_user[uid]`` is the ordered list of
        candidate movie ids from the candidate stage and
        ``features_by_user[uid]`` is a DataFrame with one row per
        candidate in the same order. Returns top-K movie ids per user
        after re-ranking by predicted score. The invariant that
        ``len(candidates_by_user[uid]) == len(features_by_user[uid])`` is
        checked — a length mismatch would return a top-K over the wrong
        rows.
        """
        assert self._booster is not None, "call fit() before rank_candidates()"
        out: dict[int, list[int]] = {}
        for user_id, candidates in candidates_by_user.items():
            if not candidates:
                out[user_id] = []
                continue
            user_features = features_by_user[user_id]
            assert len(user_features) == len(candidates), (
                f"user {user_id}: {len(candidates)} candidates but "
                f"{len(user_features)} feature rows"
            )
            scores = self.predict(user_features)
            # Descending sort; np.argsort is ascending, so negate.
            order = np.argsort(-scores, kind="stable")
            top_k_indices = order[:k]
            out[user_id] = [candidates[int(i)] for i in top_k_indices]
        return out

    def feature_importances(self, importance_type: str = "gain") -> dict[str, float]:
        """Map feature name → importance. ``gain`` is total loss reduction
        the feature contributed; ``split`` is number of splits it appears
        in. Used by the training script to log per-feature importance to
        MLflow so a Phase 4 SHAP-based explainer has grounded priors.
        """
        assert self._booster is not None, "call fit() before feature_importances()"
        raw = self._booster.feature_importance(importance_type=importance_type)
        return dict(zip(FEATURE_COLUMNS, (float(v) for v in raw), strict=False))
