"""Unit tests for `RANKER_POSITIVE_LIMIT`, the knob that decides how much the ranker sees.

Two contracts. The first is the same boring one `TRAIN_SEED` has: a malformed
value raises rather than falling back, because a run labelled with a sample size
it did not use would make the variance measurement in `docs/results.md` a
fiction. The second is the one the measurement turns on — that a limit at or
above the trailing window's row count draws the *whole* window, so the sampling
stops being random and the seed stops choosing the training set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.training import sampling
from src.training.ranker import _sample_training_positives

_SECONDS_PER_DAY = 24 * 3600


def _window_frame(n_rows: int = 100) -> pd.DataFrame:
    """A train frame whose rows all sit inside a 30-day trailing window."""
    base = 1_500_000_000
    return pd.DataFrame(
        {
            "userId": [i % 10 for i in range(n_rows)],
            "movieId": list(range(n_rows)),
            "timestamp": [base + i * 60 for i in range(n_rows)],
        }
    )


def test_an_unset_variable_is_the_limit_the_results_page_documents():
    assert sampling.DEFAULT_POSITIVE_LIMIT == 200_000
    assert sampling.resolve_positive_limit(env={}) == 200_000


def test_an_empty_or_whitespace_value_is_treated_as_unset():
    assert sampling.resolve_positive_limit(env={sampling.POSITIVE_LIMIT_ENV_VAR: ""}) == 200_000
    assert sampling.resolve_positive_limit(env={sampling.POSITIVE_LIMIT_ENV_VAR: "  "}) == 200_000


def test_an_integer_value_is_used():
    assert sampling.resolve_positive_limit(env={sampling.POSITIVE_LIMIT_ENV_VAR: "20000"}) == 20_000
    padded = {sampling.POSITIVE_LIMIT_ENV_VAR: " 60000 "}
    assert sampling.resolve_positive_limit(env=padded) == 60_000


def test_a_callers_own_default_is_respected():
    assert sampling.resolve_positive_limit(1_000, env={}) == 1_000
    assert sampling.resolve_positive_limit(1_000, env={sampling.POSITIVE_LIMIT_ENV_VAR: "7"}) == 7


def test_a_non_integer_value_raises_rather_than_defaulting():
    with pytest.raises(sampling.InvalidPositiveLimitError, match="not an integer"):
        sampling.resolve_positive_limit(env={sampling.POSITIVE_LIMIT_ENV_VAR: "all"})


def test_a_non_positive_limit_raises():
    for value in ("0", "-1"):
        with pytest.raises(sampling.InvalidPositiveLimitError, match="not positive"):
            sampling.resolve_positive_limit(env={sampling.POSITIVE_LIMIT_ENV_VAR: value})


def test_the_default_limit_keeps_the_run_name_the_results_page_cites():
    base = "lgbm-lambdarank-itemitem-candidates"
    assert sampling.run_name_for(base, sampling.DEFAULT_POSITIVE_LIMIT) == base
    assert sampling.run_name_for(base, 20_000) == f"{base}-pos20000"


def test_the_sample_suffix_composes_with_the_routing_and_seed_suffixes():
    """A 20,000-positive index-routed run at seed 7 has to be findable as all three."""
    from src.models.candidates import routing
    from src.training import seeds

    name = seeds.run_name_for(
        sampling.run_name_for(
            routing.run_name_for("lgbm", routing.POLICY_INDEX),
            20_000,
        ),
        7,
    )
    assert name == "lgbm-index-routing-pos20000-seed7"


def test_a_binding_limit_draws_a_random_subset_that_the_seed_chooses():
    """Below the window's size the seed picks the training set — the finding."""
    train = _window_frame(100)
    a = _sample_training_positives(train, n_days=30, limit=40, rng=np.random.default_rng(42))
    b = _sample_training_positives(train, n_days=30, limit=40, rng=np.random.default_rng(7))
    assert len(a) == len(b) == 40
    assert set(a["movieId"]) != set(b["movieId"])


def test_a_limit_at_or_above_the_window_draws_the_whole_window_at_every_seed():
    """At or above the ceiling the sample is the window, so the seed cannot move it.

    This is what the default buys: `len(window) > limit` is false, the
    `rng.choice` never runs, and two seeds produce the identical training set
    rather than two different ones.
    """
    train = _window_frame(100)
    at_ceiling = _sample_training_positives(
        train, n_days=30, limit=100, rng=np.random.default_rng(42)
    )
    above_ceiling = _sample_training_positives(
        train, n_days=30, limit=10_000, rng=np.random.default_rng(7)
    )
    assert len(at_ceiling) == len(above_ceiling) == 100
    pd.testing.assert_frame_equal(at_ceiling, above_ceiling)


def test_only_the_trailing_window_is_eligible_however_large_the_limit():
    """A limit above the window's size never reaches back past the window."""
    base = 1_500_000_000
    train = pd.DataFrame(
        {
            "userId": [0, 0, 0],
            "movieId": [1, 2, 3],
            # Two rows inside the trailing 30 days, one a year earlier.
            "timestamp": [base - 365 * _SECONDS_PER_DAY, base - _SECONDS_PER_DAY, base],
        }
    )
    sampled = _sample_training_positives(
        train, n_days=30, limit=1_000_000, rng=np.random.default_rng(42)
    )
    assert sorted(sampled["movieId"]) == [2, 3]
