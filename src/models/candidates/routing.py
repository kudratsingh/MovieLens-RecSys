"""Where a candidate model's learned path stops and its popularity fallback begins.

Two rules can answer that question, and until 2026-08-30 this repository used a
different one offline than it did online. Both still live here, because the
divergence was closed by choosing between them and a named alternative is worth
more than a deleted one.

**ADR 0001's threshold — the default, and the only policy anything ships on.**
`src/evaluation/protocol.COLD_START_THRESHOLD` is the one number: the warm/cold
slicing in `evaluate` uses it, every candidate model's fallback routes on it,
and `src/serving/orchestration.py` routes on it too, counted over unique watched
movie ids. Below the threshold a request is answered by the popularity fallback
rather than by retrieval, offline and online alike.

**Index membership** — the rule every offline candidate model applied before
that decision, kept as the explicit opt-out (`cold_start_threshold=None`, or
`SYNTH_COLD_ROUTING=index`). It falls back to popularity only for a user the
fitted index has never seen, and serves everybody else from the learned path. A
user with a single training interaction is in the index, so under this policy a
single interaction is enough to be served one film's cosine neighbours (or one
film's embedding) as if it were a taste profile.

ADR 0011's cohort measured the gap rather than assuming it, and the measurement
is what settled it (`docs/cold-start-routing-decision.md`): on MovieLens's
natural holdout exactly one user of 2,641 sits in the disputed band, so the
warm/cold and per-policy tables move by at most 0.08% — but on the synthetic
cohort, routing a 1-interaction user to the fallback takes item-item's h1
recall@500 from 0.144 to 0.460. The owner adopted the threshold offline on
2026-08-30 and set it to 10, so the offline metrics measure the policy the
deployed service runs.

The switch stays because a run's numbers should be attributable to a named
routing rule: `SYNTH_COLD_ROUTING=index` reproduces the pre-decision behaviour
and renames the run, and the default policy keeps the plain run name.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from src.evaluation.protocol import COLD_START_THRESHOLD

# What every candidate model is constructed with unless a caller opts out. It is
# re-exported from here rather than imported into each model so there is exactly
# one place the models depend on the protocol, and so `cold_start_threshold=None`
# reads as "the opt-out" at every call site instead of as "unset".
DEFAULT_COLD_START_THRESHOLD: int | None = COLD_START_THRESHOLD

# The environment variable the trainers read. An env var rather than a CLI
# flag because the trainers are `python -m` entrypoints driven through
# `make train-*`, and an env var threads through a Makefile target without
# every target having to grow an argument.
ROUTING_ENV_VAR = "SYNTH_COLD_ROUTING"

POLICY_INDEX = "index"
POLICY_THRESHOLD = "threshold"
POLICIES = (POLICY_INDEX, POLICY_THRESHOLD)

# ADR 0001's threshold, since the owner's 2026-08-30 decision. `index` remains
# reachable as the explicit opt-out, and a run made under it is renamed so its
# numbers can never be mistaken for a default run's.
DEFAULT_POLICY = POLICY_THRESHOLD


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
    heard of this module gets ADR 0001's threshold — the same rule the deployed
    service applies.
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
        cold_start_threshold: ADR 0001's threshold, at or above which the
            learned path serves — the default every model is constructed with —
            or ``None`` for the index-membership opt-out, where being in the
            index is the whole rule.
    """
    if cold_start_threshold is None:
        return True
    return history_size >= cold_start_threshold


def run_name_for(base: str, policy: str) -> str:
    """MLflow run name for a run under ``policy``.

    The default policy takes the plain base name, so a default run is findable
    by the name `docs/results.md` gives it; only a run under the opt-out policy
    is renamed. The default moved from `index` to `threshold` on 2026-08-30, so
    the pre-decision runs recorded under the plain name are `index` runs and the
    memo's `-threshold-routing` runs are what the plain name means now — both
    stated in that document's dated note.
    """
    if policy == DEFAULT_POLICY:
        return base
    return f"{base}-{policy}-routing"
