"""Unit tests for `TRAIN_SEED`, the knob the promotion gate's noise floor was measured with.

The contract that matters is the boring half: with the variable unset, every
trainer must behave exactly as it did before this module existed, because
every number in `docs/results.md` was produced that way and none of them
should quietly change meaning. The interesting half is that a malformed value
raises rather than falling back — a run labelled with a seed it did not use
would make the whole seed-to-seed measurement a fiction.
"""

from __future__ import annotations

import pytest

from src.training import seeds


def test_an_unset_variable_is_the_seed_every_published_run_used():
    assert seeds.DEFAULT_SEED == 42
    assert seeds.resolve_seed(env={}) == 42


def test_an_empty_or_whitespace_value_is_treated_as_unset():
    assert seeds.resolve_seed(env={seeds.SEED_ENV_VAR: ""}) == 42
    assert seeds.resolve_seed(env={seeds.SEED_ENV_VAR: "   "}) == 42


def test_an_integer_value_is_used():
    assert seeds.resolve_seed(env={seeds.SEED_ENV_VAR: "7"}) == 7
    assert seeds.resolve_seed(env={seeds.SEED_ENV_VAR: " 13 "}) == 13
    assert seeds.resolve_seed(env={seeds.SEED_ENV_VAR: "0"}) == 0


def test_a_callers_own_default_is_respected():
    """`src/training/ranker.py` passes RANKER_SEED rather than assuming 42."""
    assert seeds.resolve_seed(99, env={}) == 99
    assert seeds.resolve_seed(99, env={seeds.SEED_ENV_VAR: "7"}) == 7


def test_a_non_integer_value_raises_rather_than_defaulting():
    with pytest.raises(seeds.InvalidSeedError, match="not an integer"):
        seeds.resolve_seed(env={seeds.SEED_ENV_VAR: "fourty-two"})


def test_a_negative_seed_raises():
    with pytest.raises(seeds.InvalidSeedError, match="non-negative"):
        seeds.resolve_seed(env={seeds.SEED_ENV_VAR: "-1"})


def test_the_default_seed_keeps_the_run_name_the_results_page_cites():
    assert seeds.run_name_for("cf-als-baseline", 42) == "cf-als-baseline"
    assert seeds.run_name_for("cf-als-baseline", 7) == "cf-als-baseline-seed7"


def test_the_seed_suffix_composes_with_the_routing_suffix():
    """A re-seeded threshold-routed run has to be findable as both."""
    from src.models.candidates import routing

    base = routing.run_name_for("cf-als-baseline", routing.POLICY_THRESHOLD)
    assert seeds.run_name_for(base, 7) == "cf-als-baseline-threshold-routing-seed7"
