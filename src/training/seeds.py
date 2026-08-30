"""The seed a training run uses, and where it comes from.

Every trainer in this package has always been seeded — ADR 0001's
Reproducibility section requires it — but the seed was a module constant, so
"the same model at a different seed" was a code edit. That is fine while a
seed is only there to make one run repeatable, and not fine the moment
somebody needs to know how much a metric moves when *only* the seed changes.

The promotion gate needs exactly that number. `src/evaluation/gate.py` refuses
a slice regression larger than a tolerance, and a tolerance is only honest if
it was measured rather than chosen — which means running the same model, on
the same data, at three seeds, and reading the spread. This module is what
makes those three runs differ in nothing else.

`TRAIN_SEED` unset reproduces every number already in `docs/results.md`: the
default is the 42 the trainers have always used.

Two trainers read it, because two trainers have a stochastic component:
`src/training/cf.py` (ALS factor initialisation) and `src/training/ranker.py`
(positive and negative sampling, plus LightGBM's own seed). The popularity
baseline and item-item cosine have no random component at all — the same
inputs produce the same model every time — so a seed would be a parameter
that changes nothing, and they do not take one.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# An env var rather than a CLI flag for the same reason `SYNTH_COLD_ROUTING`
# is one: the trainers are `python -m` entrypoints driven through `make
# train-*`, and an env var threads through a Makefile target without every
# target having to grow an argument.
SEED_ENV_VAR = "TRAIN_SEED"

# The seed every run recorded in docs/results.md was made at.
DEFAULT_SEED = 42


class InvalidSeedError(ValueError):
    """`TRAIN_SEED` was set to something that is not a usable seed.

    Raised rather than defaulted, following `routing.resolve_policy`: a typo
    that silently produced the default seed would produce a run labelled with
    a seed it did not use, and the whole point of the variable is that a
    run's numbers can be attributed to a named seed.
    """


def resolve_seed(default: int = DEFAULT_SEED, env: Mapping[str, str] | None = None) -> int:
    """Read this run's seed out of the environment.

    Absent or empty means ``default``, so an operator who has never heard of
    this module gets the behaviour that was there before it.
    """
    raw = (env if env is not None else os.environ).get(SEED_ENV_VAR, "").strip()
    if not raw:
        return default
    try:
        seed = int(raw)
    except ValueError as exc:
        raise InvalidSeedError(
            f"{SEED_ENV_VAR}={raw!r} is not an integer. Unset it for the default ({default})."
        ) from exc
    if seed < 0:
        raise InvalidSeedError(f"{SEED_ENV_VAR}={seed} is negative; seeds are non-negative.")
    return seed


def run_name_for(base: str, seed: int, default: int = DEFAULT_SEED) -> str:
    """MLflow run name for a run at ``seed``.

    The default seed keeps the run name it has always had, so the runs already
    cited by name in `docs/results.md` stay findable; only a re-seeded run is
    renamed. Same contract as `routing.run_name_for`, and the two compose:
    a threshold-routed run at seed 7 is `<base>-threshold-routing-seed7`.
    """
    if seed == default:
        return base
    return f"{base}-seed{seed}"
