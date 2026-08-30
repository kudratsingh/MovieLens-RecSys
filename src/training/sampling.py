"""How many positives the ranker trains on, and where the number comes from.

[ADR 0005](../../docs/adr/0005-lightgbm-over-neural-ranker.md) pins the ranker's
training-data construction and calls the sample size *"a config knob"* — "the
full 20 M train interactions are more than the ranker needs to converge, and the
initial config caps the training set at a fixed sample so an iteration cycle
stays minutes, not hours." It was a module constant, so exercising that knob was
a code edit; this module makes it an environment variable, on the same pattern
as `src/training/seeds.py` and `src/models/candidates/routing.py`.

The knob stopped being cosmetic on 2026-08-30. Measuring the promotion gate's
noise floor found the ranker's warm NDCG@10 moving 28.68% of its own mean across
three seeds and its *overall* NDCG@10 moving 5.81% — wider than ADR 0001's own
+3% promotion threshold — because the seed selects which ≤20,000 positives are
drawn from the trailing window, so a re-seed is a different training set rather
than a different tie-break. The named fix was a larger sample, and a larger
sample needed a knob.

The trailing window is what bounds it. `RANKER_POSITIVE_WINDOW_DAYS` is 30, and
on MovieLens 25M the last 30 days of train hold **154,003** interactions — so
any limit at or above that draws the whole window and the sampling stops being
random at all. That is why the default is a limit the data does not reach rather
than a sample size: the trainer records `ranker_positive_limit_binding` on every
run, so "the positives were the entire window" is a logged fact and not an
assumption that would quietly stop being true on a larger dataset.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# An env var rather than a CLI flag for the same reason `TRAIN_SEED` and
# `SYNTH_COLD_ROUTING` are: the trainers are `python -m` entrypoints driven
# through `make train-*`, and an env var threads through a Makefile target
# without every target having to grow an argument.
POSITIVE_LIMIT_ENV_VAR = "RANKER_POSITIVE_LIMIT"

# 200,000 against a 154,003-row window: deliberately above the ceiling, so the
# default trains on every positive the window holds. See the module docstring
# and `docs/results.md`'s 2026-08-30 sample-size section for the measurement
# that moved this from 20,000.
DEFAULT_POSITIVE_LIMIT = 200_000


class InvalidPositiveLimitError(ValueError):
    """`RANKER_POSITIVE_LIMIT` was set to something that is not a usable limit.

    Raised rather than defaulted, following `seeds.resolve_seed` and
    `routing.resolve_policy`: a typo that silently produced the default would
    produce a run labelled with a sample size it did not use, and the whole
    point of the variable is that a run's numbers can be attributed to one.
    """


def resolve_positive_limit(
    default: int = DEFAULT_POSITIVE_LIMIT, env: Mapping[str, str] | None = None
) -> int:
    """Read this run's positive-sample cap out of the environment.

    Absent or empty means ``default``, so an operator who has never heard of
    this module gets the size the docs describe.
    """
    raw = (env if env is not None else os.environ).get(POSITIVE_LIMIT_ENV_VAR, "").strip()
    if not raw:
        return default
    try:
        limit = int(raw)
    except ValueError as exc:
        raise InvalidPositiveLimitError(
            f"{POSITIVE_LIMIT_ENV_VAR}={raw!r} is not an integer. "
            f"Unset it for the default ({default:,})."
        ) from exc
    if limit <= 0:
        raise InvalidPositiveLimitError(
            f"{POSITIVE_LIMIT_ENV_VAR}={limit} is not positive; a run with no positives "
            "trains on nothing."
        )
    return limit


def run_name_for(base: str, limit: int, default: int = DEFAULT_POSITIVE_LIMIT) -> str:
    """MLflow run name for a run capped at ``limit``.

    The default takes the plain base name so a default run stays findable under
    the name `docs/results.md` gives it; only a run at another size is renamed.
    Same contract as `seeds.run_name_for` and `routing.run_name_for`, and the
    three compose — a 20,000-positive run at seed 7 under index routing is
    `<base>-index-routing-pos20000-seed7`.
    """
    if limit == default:
        return base
    return f"{base}-pos{limit}"
