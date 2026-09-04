"""
Unit tests for the last-item baseline trainer's run-shaping helpers.

The trainer itself needs Postgres and MLflow, so what is testable here is the
part that decides *which* run was made: the user subsample, the seed it is
drawn at, and the name the run is filed under. All three matter because this
model is a control — a control run at a different sample from the model it
controls for is worse than no control, and a control that is indistinguishable
in MLflow from a differently-routed one is not much better.
"""

from __future__ import annotations

import pytest

from src.models.candidates import routing
from src.training import last_item


def test_sample_fraction_defaults_to_the_full_dataset() -> None:
    assert last_item.resolve_sample_fraction({}) == 1.0
    assert last_item.resolve_sample_fraction({last_item.SAMPLE_FRACTION_ENV_VAR: "  "}) == 1.0


def test_sample_fraction_is_read_from_the_environment() -> None:
    assert last_item.resolve_sample_fraction({last_item.SAMPLE_FRACTION_ENV_VAR: "0.06"}) == 0.06


@pytest.mark.parametrize("raw", ["0", "-0.1", "1.5"])
def test_an_out_of_range_sample_fraction_is_refused(raw: str) -> None:
    # Named after the variable the operator actually set, not after the
    # two-tower's, which is what the shared subsample helper would report.
    with pytest.raises(ValueError, match=last_item.SAMPLE_FRACTION_ENV_VAR):
        last_item.resolve_sample_fraction({last_item.SAMPLE_FRACTION_ENV_VAR: raw})


def test_sample_seed_defaults_to_the_seed_every_other_trainer_uses() -> None:
    assert last_item.resolve_sample_seed({}) == last_item.DEFAULT_SAMPLE_SEED == 42
    assert last_item.resolve_sample_seed({last_item.SEED_ENV_VAR: "7"}) == 7


def test_the_default_policy_keeps_the_plain_run_name() -> None:
    # The name the baseline will be cited by has to stay findable, so only a
    # non-default run is renamed — the same contract routing.run_name_for holds.
    assert last_item.run_name_for(routing.DEFAULT_POLICY, "") == last_item.BASE_RUN_NAME


def test_a_non_default_policy_and_a_label_both_reach_the_run_name() -> None:
    assert last_item.run_name_for(routing.POLICY_INDEX, "") == (
        f"{last_item.BASE_RUN_NAME}-index-routing"
    )
    assert last_item.run_name_for(routing.DEFAULT_POLICY, "pilot6") == (
        f"{last_item.BASE_RUN_NAME}-pilot6"
    )
