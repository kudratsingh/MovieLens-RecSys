"""
Unit tests for the LightGBM ranker.

Follows the same shape as ``test_twotower.py`` — contract tests plus a
converges-on-synthetic smoke test. The ranker's behavioral guarantee is
narrower than the two-tower's (no ANN index to worry about, no PyTorch
training loop to break), so the test set is proportionally smaller.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features import FEATURE_COLUMNS
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig


def _synthetic_features(n_groups: int, group_size: int, seed: int = 0) -> pd.DataFrame:
    """Generate a synthetic feature block. The first FEATURE_COLUMNS
    dimensions are filled with random values in [0, 1]; the ranker
    can't learn anything from these alone. The label-generation logic
    in each test decides which dimension carries signal.
    """
    rng = np.random.default_rng(seed)
    n_rows = n_groups * group_size
    data = rng.random((n_rows, len(FEATURE_COLUMNS)))
    return pd.DataFrame(data, columns=FEATURE_COLUMNS)


def _labels_from_signal_column(
    features: pd.DataFrame, group_size: int, signal_col: str
) -> np.ndarray:
    """Assign label 1 to the row with the highest signal-column value in
    each group, 0 elsewhere. This creates a learnable ranking signal —
    a well-behaved ranker should assign the top score to the row with
    the highest signal value.
    """
    n_rows = len(features)
    n_groups = n_rows // group_size
    labels = np.zeros(n_rows, dtype=np.float64)
    signal = features[signal_col].to_numpy()
    for g in range(n_groups):
        start = g * group_size
        end = start + group_size
        best = start + int(np.argmax(signal[start:end]))
        labels[best] = 1.0
    return labels


def test_fit_returns_self_for_chaining() -> None:
    features = _synthetic_features(n_groups=10, group_size=5)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    group_sizes = [5] * 10
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10)).fit(
        features, group_sizes, labels
    )
    assert isinstance(ranker, LGBMRanker)


def test_predict_returns_one_score_per_row() -> None:
    features = _synthetic_features(n_groups=10, group_size=5)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    group_sizes = [5] * 10
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10)).fit(
        features, group_sizes, labels
    )
    scores = ranker.predict(features)
    assert scores.shape == (len(features),)


def test_fit_asserts_on_mismatched_group_sizes() -> None:
    """sum(group_sizes) must equal len(features) — mismatch would train
    on the wrong groupings silently. The ranker asserts before training.
    """
    features = _synthetic_features(n_groups=10, group_size=5)  # 50 rows
    labels = np.zeros(50, dtype=np.float64)
    with pytest.raises(AssertionError):
        LGBMRanker(config=LGBMRankerConfig(num_boost_round=1)).fit(
            features, group_sizes=[4] * 10, labels=labels  # sums to 40, not 50
        )


def test_predict_requires_fit_first() -> None:
    ranker = LGBMRanker()
    features = _synthetic_features(n_groups=1, group_size=5)
    with pytest.raises(AssertionError):
        ranker.predict(features)


def test_converges_on_synthetic_signal_column() -> None:
    """Smoke test: given a clear per-group signal in one feature, the
    ranker should score the group's true positive above the group's
    negatives at least most of the time. Guards against a completely
    broken training path — a genuinely broken ranker would score at
    chance (~1/group_size = 20 % here).
    """
    signal_col = FEATURE_COLUMNS[0]  # user_interaction_count as arbitrary signal
    features = _synthetic_features(n_groups=100, group_size=5, seed=1)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=signal_col)
    group_sizes = [5] * 100
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=50, seed=0))
    ranker.fit(features, group_sizes, labels)

    scores = ranker.predict(features)
    # For each group, check whether the ranker's top-scored row is the
    # true positive. On synthetic data with one clean signal column, a
    # working LambdaRank ranker should be well above chance.
    correct = 0
    for g in range(100):
        start = g * 5
        end = start + 5
        pred_top = start + int(np.argmax(scores[start:end]))
        true_top = start + int(np.argmax(labels[start:end]))
        if pred_top == true_top:
            correct += 1
    assert correct >= 60, f"expected >=60% top-1 accuracy, got {correct}%"


def test_rank_candidates_returns_top_k_per_user() -> None:
    """rank_candidates is the end-to-end shape the training script and
    Phase 3 serving both use. Contract: dict[user_id, list[movie_id]]
    with at most k entries per user, containing only ids from that
    user's candidate list.
    """
    features = _synthetic_features(n_groups=20, group_size=5, seed=2)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=20, seed=0))
    ranker.fit(features, [5] * 20, labels)

    # Two users, each with 5 candidates.
    candidates_by_user = {1: [100, 101, 102, 103, 104], 2: [200, 201, 202, 203, 204]}
    features_by_user = {
        1: _synthetic_features(n_groups=1, group_size=5, seed=3),
        2: _synthetic_features(n_groups=1, group_size=5, seed=4),
    }
    top3 = ranker.rank_candidates(candidates_by_user, features_by_user, k=3)
    assert set(top3.keys()) == {1, 2}
    assert len(top3[1]) == 3
    assert len(top3[2]) == 3
    assert set(top3[1]) <= set(candidates_by_user[1])
    assert set(top3[2]) <= set(candidates_by_user[2])


def test_rank_candidates_handles_empty_candidate_list() -> None:
    """A user with no candidates (cold-cold: no candidate model output)
    must not crash the batch. Return an empty list for them and press on.
    """
    features = _synthetic_features(n_groups=5, group_size=5, seed=5)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10, seed=0))
    ranker.fit(features, [5] * 5, labels)

    result = ranker.rank_candidates({1: []}, {1: pd.DataFrame(columns=FEATURE_COLUMNS)}, k=5)
    assert result == {1: []}


def test_determinism_under_fixed_seed() -> None:
    """Same seed + same data → same booster. Reproducibility (non-
    negotiable #5). LambdaRank has bagging and feature-fraction subsample
    steps that must be seeded consistently."""
    features = _synthetic_features(n_groups=20, group_size=5, seed=6)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    group_sizes = [5] * 20
    a = LGBMRanker(config=LGBMRankerConfig(num_boost_round=15, seed=0)).fit(
        features, group_sizes, labels
    )
    b = LGBMRanker(config=LGBMRankerConfig(num_boost_round=15, seed=0)).fit(
        features, group_sizes, labels
    )
    np.testing.assert_array_equal(a.predict(features), b.predict(features))


def test_feature_importances_schema_matches_features() -> None:
    """Every feature column should have an importance entry after
    training — no silent dropping of columns."""
    features = _synthetic_features(n_groups=20, group_size=5, seed=7)
    labels = _labels_from_signal_column(features, group_size=5, signal_col=FEATURE_COLUMNS[0])
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10, seed=0))
    ranker.fit(features, [5] * 20, labels)
    importances = ranker.feature_importances()
    assert set(importances.keys()) == set(FEATURE_COLUMNS)
