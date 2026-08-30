"""Where a candidate model's learned path stops and its popularity fallback begins.

Two rules are in use in this repository and they do not agree, which is the
whole reason this module exists.

**Index membership** — the rule every offline candidate model has always
applied. `CFModel`, `ItemItemModel` and `TwoTowerModel` each fall back to
popularity for a user their fitted index has never seen, and serve everybody
else from the learned path. A user with a single training interaction is in
the index, so a single interaction is enough to be served one film's cosine
neighbours (or one film's embedding) as if it were a taste profile.

**ADR 0001's threshold** — the rule the protocol and the deployed serving path
apply. `src/evaluation/protocol.COLD_START_THRESHOLD` is 5, the warm/cold
slicing in `evaluate` uses it, and `src/serving/recommendations.py` routes on
the same number counted over unique watched movie ids. Below five signals a
request is answered by the popularity fallback, not by retrieval.

ADR 0011's cohort measured the gap rather than assuming it: buckets h1 and h3
are `expected_fallback_served = 500` and every learned model served 0 of them
from the fallback, so `synth_cold_routing_ok` is `false` on every run
(`docs/results.md`, 2026-08-29).

Closing the gap is a decision for the owner — either the offline models move
onto the threshold, or ADR 0001 records that offline retrieval routes on index
membership by design. This module is the instrument that lets both be
measured before that decision is made, so it is deliberately **opt-in and
non-default**: `cold_start_threshold=None` on every model reproduces the
behaviour on `main` exactly, and nothing changes unless a trainer is run with
`SYNTH_COLD_ROUTING=threshold` in its environment.

See `docs/cold-start-routing-decision.md` for the measurements and the options.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# The environment variable the trainers read. An env var rather than a CLI
# flag because the trainers are `python -m` entrypoints driven through
# `make train-*`, and an env var threads through a Makefile target without
# every target having to grow an argument.
ROUTING_ENV_VAR = "SYNTH_COLD_ROUTING"

# `index` is the default and is what `main` does today.
POLICY_INDEX = "index"
POLICY_THRESHOLD = "threshold"
POLICIES = (POLICY_INDEX, POLICY_THRESHOLD)

DEFAULT_POLICY = POLICY_INDEX


class UnknownRoutingPolicyError(ValueError):
    """`SYNTH_COLD_ROUTING` was set to something this module does not implement.

    Raised rather than defaulted: a typo that silently produced the default
    policy would produce a run labelled with the policy it did not use, and
    the whole point of the switch is that a run's numbers can be attributed
    to a named routing rule.
    """


def resolve_policy(env: Mapping[str, str] | None = None) -> str:
    """Read the routing policy for this run out of the environment.

    Absent or empty means :data:`DEFAULT_POLICY`, so an operator who has never
    heard of this module gets the behaviour that was there before it.
    """
    raw = (env if env is not None else os.environ).get(ROUTING_ENV_VAR, "").strip().lower()
    if not raw:
        return DEFAULT_POLICY
    if raw not in POLICIES:
        raise UnknownRoutingPolicyError(
            f"{ROUTING_ENV_VAR}={raw!r} is not one of {POLICIES}. "
            f"Unset it for the default ({DEFAULT_POLICY!r})."
        )
    return raw


def cold_start_threshold_for(policy: str, threshold: int) -> int | None:
    """Translate a policy name into the value a model's constructor takes.

    ``None`` is not "no threshold configured" — it is the index-membership
    policy stated in the only vocabulary the models have.
    """
    if policy not in POLICIES:
        raise UnknownRoutingPolicyError(f"{policy!r} is not one of {POLICIES}")
    return None if policy == POLICY_INDEX else threshold


def learned_path_serves(*, history_size: int, cold_start_threshold: int | None) -> bool:
    """Given a user the model's index already knows, may the learned path serve them?

    Callers check index membership first — a user the fit never saw has no
    learned path to take, under either policy — so this answers only the part
    the two policies disagree about.

    Args:
        history_size: how many distinct items the user interacted with in
            train. Distinct rather than raw rows so it means the same thing
            here as in `src/serving/recommendations.py`, which counts unique
            watched movie ids. On MovieLens the two are the same number.
        cold_start_threshold: ``None`` for index membership (the default —
            being in the index is the whole rule), or ADR 0001's threshold,
            at or above which the learned path serves.
    """
    if cold_start_threshold is None:
        return True
    return history_size >= cold_start_threshold


def run_name_for(base: str, policy: str) -> str:
    """MLflow run name for a run under ``policy``.

    The default policy keeps the run name it has always had, so the runs
    already recorded in `docs/results.md` stay findable by the name that
    document gives them; only an experimental run is renamed.
    """
    if policy == DEFAULT_POLICY:
        return base
    return f"{base}-{policy}-routing"
