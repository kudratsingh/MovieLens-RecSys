"""
Unit tests for the two-tower sweep parameterisation (2026-08-30).

The sweep added knobs — temperature, early stopping, an exact index, a
seeded user subsample, an env-driven config, a grid runner. The whole point
of the tests below is that **none of it moved a default**: a run that sets
nothing must still be the run ADR 0006 specifies and `docs/results.md`
reports, or the sweep's comparison against v1 is not a comparison at all.

`tests/unit/test_twotower.py` keeps the model contract itself. This file
keeps the parameterisation around it.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest
import torch

from src.models.candidates.twotower import (
    TwoTowerConfig,
    TwoTowerModel,
    build_user_history,
)
from src.training.twotower import resolve_sample_fraction, run_name_for, subsample_users
from src.training.twotower_sweep import parse_grid


def _ratings(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


_SYNTHETIC_TRAIN = _ratings(
    [
        (1, 100, 10),
        (1, 101, 20),
        (1, 102, 30),
        (2, 100, 11),
        (2, 101, 21),
        (2, 103, 31),
        (3, 100, 12),
        (3, 102, 22),
        (3, 104, 32),
        (4, 200, 13),
        (4, 201, 23),
        (4, 202, 33),
        (5, 200, 14),
        (5, 201, 24),
        (5, 203, 34),
        (6, 200, 15),
        (6, 202, 25),
        (6, 204, 35),
        (7, 100, 16),
        (7, 200, 26),
        (8, 101, 17),
        (8, 201, 27),
    ]
)

_FAST_CONFIG = TwoTowerConfig(
    embedding_dim=16,
    history_window=5,
    batch_size=8,
    num_sampled=16,
    epochs=1,
    learning_rate=1e-2,
    faiss_nlist=4,
    faiss_nprobe=2,
    seed=42,
)


# --- the defaults did not move --------------------------------------------


def test_adr_0006_defaults_are_unchanged() -> None:
    """The load-bearing test for the sweep.

    Every field below is either ADR 0006's stated configuration or the
    "behave exactly as v1 did" value for a knob v1 did not have. If a sweep
    result talks anyone into editing one of these, this test is what makes
    them do it deliberately and in a diff that says so.
    """
    config = TwoTowerConfig()
    assert config.embedding_dim == 64
    assert config.history_window == 50
    assert config.batch_size == 4096
    assert config.num_sampled == 4 * config.batch_size
    assert config.epochs == 3
    assert config.learning_rate == 1e-3
    assert config.faiss_nlist == 100
    assert config.faiss_nprobe == 10
    assert config.seed == 42
    # Knobs the sweep added, at their v1-equivalent values.
    assert config.logit_temperature == 1.0
    assert config.correct_positive_logit is True
    assert config.early_stopping_patience == 0
    assert config.faiss_exact is False


def test_from_env_with_an_empty_environment_is_the_default_config() -> None:
    assert TwoTowerConfig.from_env({}) == TwoTowerConfig()


def test_from_env_treats_a_blank_value_as_unset() -> None:
    """An exported-but-empty variable is "unset", not "zero".

    `TWOTOWER_EPOCHS=` left in a shell profile should not quietly train for
    no epochs at all and report the result as a run.
    """
    assert TwoTowerConfig.from_env({"TWOTOWER_EPOCHS": "  "}) == TwoTowerConfig()


def test_from_env_reads_every_field() -> None:
    config = TwoTowerConfig.from_env(
        {
            "TWOTOWER_EMBEDDING_DIM": "128",
            "TWOTOWER_HISTORY_WINDOW": "20",
            "TWOTOWER_BATCH_SIZE": "512",
            "TWOTOWER_NUM_SAMPLED": "2048",
            "TWOTOWER_EPOCHS": "9",
            "TWOTOWER_LEARNING_RATE": "0.05",
            "TWOTOWER_LOGIT_TEMPERATURE": "0.05",
            "TWOTOWER_CORRECT_POSITIVE_LOGIT": "false",
            "TWOTOWER_EARLY_STOPPING_PATIENCE": "2",
            "TWOTOWER_EARLY_STOPPING_MIN_DELTA": "0.01",
            "TWOTOWER_FAISS_NLIST": "256",
            "TWOTOWER_FAISS_NPROBE": "32",
            "TWOTOWER_FAISS_EXACT": "yes",
            "TWOTOWER_SEED": "7",
        }
    )
    assert config == TwoTowerConfig(
        embedding_dim=128,
        history_window=20,
        batch_size=512,
        num_sampled=2048,
        epochs=9,
        learning_rate=0.05,
        logit_temperature=0.05,
        correct_positive_logit=False,
        early_stopping_patience=2,
        early_stopping_min_delta=0.01,
        faiss_nlist=256,
        faiss_nprobe=32,
        faiss_exact=True,
        seed=7,
    )


def test_from_env_rejects_a_non_boolean() -> None:
    with pytest.raises(ValueError, match="TWOTOWER_FAISS_EXACT"):
        TwoTowerConfig.from_env({"TWOTOWER_FAISS_EXACT": "maybe"})


def test_as_params_covers_every_field() -> None:
    """MLflow has to receive the whole config, or a run is not reproducible
    from its own params — which is the claim this sweep rests on."""
    params = TwoTowerConfig().as_params()
    assert set(params) == {f.name for f in dataclasses.fields(TwoTowerConfig)}


# --- the new knobs actually reach the loss ---------------------------------


def _fit_weights(**overrides: object) -> torch.Tensor:
    config = dataclasses.replace(_FAST_CONFIG, **overrides)  # type: ignore[arg-type]
    model = TwoTowerModel(config=config).fit(_SYNTHETIC_TRAIN)
    assert model._item_tower is not None
    return model._item_tower.embed.weight.clone()


def test_temperature_of_one_is_bit_identical_to_itself() -> None:
    """Determinism check, and the baseline the next test compares against.

    v1's numbers in `docs/results.md` came from code with no temperature at
    all, so the sweep's comparison against them is only honest if τ = 1.0 is
    a genuine no-op rather than a small perturbation.
    """
    assert torch.equal(_fit_weights(logit_temperature=1.0), _fit_weights(logit_temperature=1.0))


def test_temperature_changes_the_learned_embeddings() -> None:
    assert not torch.equal(
        _fit_weights(logit_temperature=1.0), _fit_weights(logit_temperature=0.05)
    )


def test_positive_correction_flag_reaches_the_loss() -> None:
    assert not torch.equal(
        _fit_weights(correct_positive_logit=True), _fit_weights(correct_positive_logit=False)
    )


def test_early_stopping_is_off_by_default() -> None:
    """Patience 0 must run every epoch asked for, plateau or not — v1's rule."""
    seen: list[int] = []
    config = dataclasses.replace(_FAST_CONFIG, epochs=4)
    TwoTowerModel(config=config).fit(_SYNTHETIC_TRAIN, on_epoch=lambda e, _loss: seen.append(e))
    assert seen == [1, 2, 3, 4]


def test_early_stopping_stops_on_a_plateau() -> None:
    """A min-delta no epoch can clear makes the second epoch the last one."""
    seen: list[int] = []
    config = dataclasses.replace(
        _FAST_CONFIG,
        epochs=6,
        early_stopping_patience=1,
        early_stopping_min_delta=1e9,
    )
    TwoTowerModel(config=config).fit(_SYNTHETIC_TRAIN, on_epoch=lambda e, _loss: seen.append(e))
    assert seen == [1, 2]


def test_exact_index_serves_valid_recommendations() -> None:
    model = TwoTowerModel(config=dataclasses.replace(_FAST_CONFIG, faiss_exact=True))
    model.fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=5)
    assert recs
    assert set(recs) <= set(_SYNTHETIC_TRAIN["movieId"])
    assert len(set(recs)) == len(recs)


def test_rebuilding_the_index_does_not_change_what_is_served() -> None:
    """The trainer calls build_index() between epochs to score recall.

    That is only safe if a rebuild over unchanged embeddings is a no-op —
    otherwise mid-run scoring would perturb the final model, and every
    metric in the sweep would describe a run other than the one it names.
    """
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    before = model.recommend(user_id=1, k=5)
    model.build_index()
    assert model.recommend(user_id=1, k=5) == before


def test_training_pairs_match_the_list_built_reference() -> None:
    """Guards the preallocated-array rewrite of _build_training_pairs.

    The array version exists only to keep a 19.9 M-pair fit inside 16 GB, so
    it has to produce the identical pairs in the identical order. This is the
    original list construction, kept as the reference.
    """
    model = TwoTowerModel(config=_FAST_CONFIG)
    model._item_to_index = {
        mid: i + 1 for i, mid in enumerate(sorted(_SYNTHETIC_TRAIN["movieId"].unique()))
    }
    model._user_history = build_user_history(_SYNTHETIC_TRAIN, model._item_to_index)

    n = _FAST_CONFIG.history_window
    expected_histories: list[list[int]] = []
    expected_positives: list[int] = []
    for hist in model._user_history.values():
        for i in range(1, len(hist)):
            window = hist[max(0, i - n) : i]
            expected_histories.append([0] * (n - len(window)) + window)
            expected_positives.append(hist[i])

    histories, positives = model._build_training_pairs()
    assert histories.tolist() == expected_histories
    assert positives.tolist() == expected_positives


# --- the pilot subsample ---------------------------------------------------


def test_full_fraction_is_the_identity() -> None:
    out = subsample_users(_SYNTHETIC_TRAIN, 1.0, seed=42)
    assert out is _SYNTHETIC_TRAIN


def test_subsample_keeps_whole_histories() -> None:
    """Users, not rows. A row-thinned sample shortens every history, which
    is the one thing a history-based encoder cannot be pilot-tested on."""
    out = subsample_users(_SYNTHETIC_TRAIN, 0.5, seed=42)
    kept = set(out["userId"])
    original = _SYNTHETIC_TRAIN[_SYNTHETIC_TRAIN["userId"].isin(kept)]
    assert len(out) == len(original)
    for user in kept:
        assert sorted(out[out["userId"] == user]["movieId"]) == sorted(
            original[original["userId"] == user]["movieId"]
        )


def test_subsample_is_deterministic_for_a_seed() -> None:
    first = subsample_users(_SYNTHETIC_TRAIN, 0.5, seed=42)
    second = subsample_users(_SYNTHETIC_TRAIN, 0.5, seed=42)
    assert sorted(first["userId"].unique()) == sorted(second["userId"].unique())


def test_subsample_rejects_a_fraction_outside_the_unit_interval() -> None:
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="TWOTOWER_USER_SAMPLE_FRACTION"):
            subsample_users(_SYNTHETIC_TRAIN, bad, seed=42)


def test_resolve_sample_fraction_defaults_to_the_whole_dataset() -> None:
    assert resolve_sample_fraction({}) == 1.0
    assert resolve_sample_fraction({"TWOTOWER_USER_SAMPLE_FRACTION": ""}) == 1.0
    assert resolve_sample_fraction({"TWOTOWER_USER_SAMPLE_FRACTION": "0.06"}) == 0.06


def test_default_run_keeps_the_name_results_md_cites() -> None:
    """docs/results.md finds v1 by this name; a sweep must not rename it."""
    assert run_name_for("index", "") == "twotower-sampled-softmax"
    assert run_name_for("index", "lr1e-2") == "twotower-sampled-softmax-lr1e-2"
    assert run_name_for("threshold", "") == "twotower-sampled-softmax-threshold-routing"


# --- the grid runner -------------------------------------------------------


def test_parse_grid_applies_overrides_over_adr_defaults() -> None:
    fraction, cells = parse_grid(
        {
            "sample_fraction": 0.06,
            "cells": [{"label": "hot", "learning_rate": 0.01, "logit_temperature": 0.05}],
        }
    )
    assert fraction == 0.06
    label, config = cells[0]
    assert label == "hot"
    assert config.learning_rate == 0.01
    assert config.logit_temperature == 0.05
    # Everything unnamed stays at ADR 0006's value.
    assert config.epochs == TwoTowerConfig.epochs
    assert config.num_sampled == TwoTowerConfig.num_sampled


def test_parse_grid_defaults_to_the_full_dataset() -> None:
    fraction, _ = parse_grid({"cells": [{"label": "a"}]})
    assert fraction == 1.0


def test_parse_grid_rejects_an_unknown_field() -> None:
    """A cell naming `temperature` instead of `logit_temperature` would
    otherwise run at the default and be written up as if it had not."""
    with pytest.raises(ValueError, match="temperature"):
        parse_grid({"cells": [{"label": "typo", "temperature": 0.05}]})


def test_parse_grid_labels_unlabelled_cells_positionally() -> None:
    _, cells = parse_grid({"cells": [{"learning_rate": 0.01}, {"learning_rate": 0.02}]})
    assert [label for label, _ in cells] == ["cell0", "cell1"]


def test_embedding_spread_reports_the_three_statistics() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    spread = model.embedding_spread()
    assert set(spread) == {"item_cosine_mean", "item_cosine_abs_mean", "item_cosine_std"}
    assert -1.0 <= spread["item_cosine_mean"] <= 1.0
    assert 0.0 <= spread["item_cosine_abs_mean"] <= 1.0
    assert spread["item_cosine_std"] >= 0.0


def test_embedding_spread_is_empty_before_fitting() -> None:
    """An unmeasured spread and a measured zero are different claims."""
    assert TwoTowerModel(config=_FAST_CONFIG).embedding_spread() == {}


def test_embedding_spread_draws_no_random_numbers() -> None:
    """It runs between epochs, so it must not perturb the run it measures."""
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    torch.manual_seed(123)
    before = torch.rand(4)
    torch.manual_seed(123)
    model.embedding_spread()
    assert torch.equal(torch.rand(4), before)


def test_a_collapsed_embedding_table_is_visible_in_the_spread() -> None:
    """The state the diagnostic exists to name: every item pointing one way.

    A loss curve cannot show this, and it is the difference between "the model
    stopped learning" and "the model learned something that cannot be
    retrieved from".
    """
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    assert model._item_tower is not None
    with torch.no_grad():
        model._item_tower.embed.weight[1:] = 1.0
    collapsed = model.embedding_spread()
    assert collapsed["item_cosine_mean"] == pytest.approx(1.0, abs=1e-5)
    assert collapsed["item_cosine_std"] == pytest.approx(0.0, abs=1e-5)


@pytest.mark.parametrize("name", ["pilot", "full", "full2"])
def test_committed_grids_parse(name: str) -> None:
    """The grids that produced the numbers in docs/results.md are committed;
    they have to stay loadable by the runner that consumed them."""
    import json
    from pathlib import Path

    spec = json.loads(Path(f"docs/experiments/twotower-sweep/{name}.json").read_text())
    fraction, cells = parse_grid(spec)
    assert 0.0 < fraction <= 1.0
    assert cells
    assert len({label for label, _ in cells}) == len(cells), "duplicate cell labels"
