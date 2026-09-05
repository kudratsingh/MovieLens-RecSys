"""
LightGBM ranker for the Phase 2 two-stage architecture.

Per ADR 0005, the ranker scores the ~500 surviving candidates from a
candidate model and returns the top-K by predicted relevance. The
objective is ``lambdarank`` — LightGBM's listwise learning-to-rank loss
that optimizes a smooth approximation to NDCG, matching the metric
ADR 0001 pins as the recommender-end-to-end success criterion.

Contract:

  - ``fit(features_df, group_sizes, labels)`` — features come from the
    ordered schema in ``self.feature_columns``, which defaults to
    ``FEATURE_COLUMNS`` from ``src/feature_contract.py``. Since ADR 0018
    there are two of these: the fallback route reads the eight
    aggregates, the learned route reads those plus the two SASRec score
    columns, and a booster carries the contract it was fitted against.
    ``group_sizes`` is a list where each entry is the number of rows in
    that group (one group per (user, query-time) query). ``labels`` is
    1 for positives, 0 for negatives.
  - ``predict(features_df) -> np.ndarray`` — raw scores, higher = more
    relevant. Not calibrated probabilities.
  - ``rank_candidates(candidates_by_user, features_by_user, k)`` — the
    batch shape the training script's eval loop uses. Returns
    ``dict[int, list[int]]``: user id → top-K movie ids. The serving
    sidecar (``src/serving/model_server.py``) scores one user per request
    against Feast-served features, so it calls ``predict`` directly and
    does its own top-K.

Cold-start passes through cleanly — a user with no history gets features
close to zero on the user-side dimensions, and the ranker learns to
weight the item-side features (popularity, age) more heavily for those
rows. Matches the ranker's cold-start behavior noted in ADR 0005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.feature_contract import FEATURE_COLUMNS


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

    # --- Reproducibility pins (non-negotiable #5) ---------------------------
    #
    # A seed alone does not make a booster reproducible. LightGBM accumulates
    # gradients into histogram bins in whatever order its worker threads
    # finish, so floating-point addition happens in a different order on a
    # 4-core machine than on a 12-core one and the resulting trees differ.
    # Pinning one thread removes the ordering entirely; `deterministic` tells
    # LightGBM to prefer reproducible accumulation over speed wherever it
    # still has the choice.
    #
    # `force_row_wise` matters for the same reason and is easy to miss: with
    # neither strategy forced, LightGBM *times* row-wise against col-wise on
    # the first iteration and keeps the faster one, so the tree structure ends
    # up depending on how loaded the build host was. Row-wise is the
    # documented choice for a narrow feature matrix (eight columns) on a
    # single thread, and LightGBM warns that `deterministic` wants one of the
    # two forced anyway.
    num_threads: int = 1
    deterministic: bool = True
    force_row_wise: bool = True


@dataclass
class LGBMRanker:
    """Public model class. Same shape as candidate-stage models in intent:
    a ``fit``, a ``predict``, and a batch method the serving layer wants.
    """

    config: LGBMRankerConfig = field(default_factory=LGBMRankerConfig)
    #: The ordered feature schema this booster is fitted against. Defaults to
    #: the eight-column fallback contract, so every caller that predates ADR
    #: 0018 keeps the behaviour it had. A learned-route booster is constructed
    #: with ``LEARNED_ROUTE_FEATURE_COLUMNS`` and carries it through save/load,
    #: which is what makes "right model, wrong columns" a loud failure rather
    #: than a quiet misprediction.
    feature_columns: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))

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
        # candidates against the wrong feature per split. `feature_columns` is
        # this booster's schema of record; anything else on the DataFrame is
        # dropped, which is what lets one feature frame serve both routes.
        feature_matrix = features_df[self.feature_columns].to_numpy(dtype=np.float64)

        # Name the columns in the Dataset, not only in this object. A booster
        # fitted from a bare matrix saves `Column_0..N`, which means the model
        # file cannot say which contract it was fitted against and a
        # booster-vs-contract check downstream degrades to a width check —
        # exactly the "right model, wrong columns" failure that per-route
        # rankers make possible. The names are metadata: they change the saved
        # text, never the trees.
        train_set = lgb.Dataset(
            feature_matrix,
            label=labels,
            group=group_sizes,
            feature_name=list(self.feature_columns),
            free_raw_data=False,
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
            "num_threads": self.config.num_threads,
            "deterministic": self.config.deterministic,
            "force_row_wise": self.config.force_row_wise,
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
        feature_matrix = features_df[self.feature_columns].to_numpy(dtype=np.float64)
        # LGBM's predict return type is a union of ndarray / Any / list depending
        # on prediction mode; the LambdaRank objective returns dense scores, so
        # normalize to ndarray here.
        # A serving worker may score several requests concurrently. Pinning one
        # native thread per request prevents LightGBM's default all-core pool
        # from oversubscribing the host and turning modest concurrency into
        # long p95/p99 queueing tails.
        return np.asarray(
            self._booster.predict(feature_matrix, num_threads=1),
            dtype=np.float64,
        )

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
        return dict(zip(self.feature_columns, (float(v) for v in raw), strict=False))

    def save_model(self, path: Path) -> None:
        """Persist the fitted booster in LightGBM's portable text format."""
        assert self._booster is not None, "call fit() before save_model()"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._booster.save_model(str(path))

    @classmethod
    def load_model(
        cls,
        path: Path,
        *,
        config: LGBMRankerConfig | None = None,
        feature_columns: list[str] | None = None,
    ) -> LGBMRanker:
        """Load a trained booster without fitting during process startup.

        ``feature_columns`` names the contract the caller expects the booster to
        carry, and the arity check below is what turns a route/booster mix-up
        into a startup failure. It defaults to the eight-column fallback
        contract, so nothing that predates ADR 0018 changes.
        """
        columns = list(FEATURE_COLUMNS if feature_columns is None else feature_columns)
        ranker = cls(config=config or LGBMRankerConfig(), feature_columns=columns)
        ranker._booster = lgb.Booster(model_file=str(path))
        if ranker._booster.num_feature() != len(columns):
            raise ValueError(
                f"ranker has {ranker._booster.num_feature()} features; "
                f"serving contract requires {len(columns)}"
            )
        # Boosters fitted before feature names were written out carry
        # `Column_0..N`; those are still loadable and the width check above is
        # all they can support. A booster that *does* name its features must
        # name the ones the caller expects, in order — a width match with a
        # different order is the silent misprediction this exists to stop.
        saved = list(ranker._booster.feature_name())
        if saved and not all(name.startswith("Column_") for name in saved) and saved != columns:
            raise ValueError(f"ranker was fitted on {saved}; serving contract requires {columns}")
        return ranker
