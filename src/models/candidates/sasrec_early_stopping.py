"""Early stopping for SASRec on a probe carved out of the training split (ADR 0020).

ADR 0020 runs its cells for 3 to 5 passes and stops on a held-out slice **of
train**, never of the holdout:

* a seeded 1% of training users each give up their last training example;
* those examples are taken out of what the model trains on and become the probe;
* after every pass recall@500 is scored on the probe, and training stops once it
  improves by less than 0.5% relative.

Everything here takes the training frame and nothing else. There is no argument
through which the 28-day holdout could reach the stopping decision, and
``tests/unit/test_sasrec_early_stopping.py`` checks that a model trained against
two different holdouts stops at the same pass with the same weights.

"Last training example" means the objective of record's last example for that
user: the final row in ``(timestamp, movieId)`` order, predicted from every item
in strictly earlier timestamp groups. Dropping that one row from the frame drops
exactly that one example and changes no other — the row is never inside another
example's prefix, because nothing comes after it. A user whose rows all share one
timestamp has no training example to give, so is never drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_PROBE_FRACTION = 0.01
DEFAULT_PROBE_SEED = 42
DEFAULT_MIN_EPOCHS = 3
DEFAULT_MIN_RELATIVE_IMPROVEMENT = 0.005


@dataclass(frozen=True)
class StoppingProbe:
    """The held-out last example of each probe user, in movie ids.

    ``histories[i]`` is user ``user_ids[i]``'s strict prefix — every movie rated at
    an earlier timestamp than ``targets[i]``, oldest first. ``timestamps[i]`` is the
    target's timestamp, kept so a caller can show the probe sits inside the
    training window.
    """

    user_ids: tuple[int, ...]
    histories: tuple[tuple[int, ...], ...]
    targets: tuple[int, ...]
    timestamps: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.user_ids)

    @property
    def latest_timestamp(self) -> int | None:
        return max(self.timestamps) if self.timestamps else None


def carve_stopping_probe(
    train: pd.DataFrame,
    *,
    fraction: float = DEFAULT_PROBE_FRACTION,
    seed: int = DEFAULT_PROBE_SEED,
) -> tuple[pd.DataFrame, StoppingProbe]:
    """Split ``train`` into (rows to train on, the probe).

    The probe users are a uniformly random ``round(fraction * eligible)`` of the
    users that have at least one training example (at least one when any are
    eligible), drawn from their sorted ids with a generator of their own. They do
    not move with the training seed, so a seed sweep varies the model and not the
    data — the same reasoning that gave the pilot subsample its own seed.

    The returned frame is ``train`` without the probe rows, in ``train``'s order.
    """
    if not 0.0 < fraction < 1.0:
        raise ValueError("probe fraction must be in (0, 1)")
    required = {"userId", "movieId", "timestamp"}
    missing = required - set(train.columns)
    if missing:
        raise ValueError(f"train is missing required columns: {sorted(missing)}")
    empty = StoppingProbe(user_ids=(), histories=(), targets=(), timestamps=())
    if train.empty:
        return train, empty

    users = train["userId"].to_numpy()
    movies = train["movieId"].to_numpy()
    timestamps = train["timestamp"].to_numpy()
    # Positions in ``train``, ordered by (user, timestamp, movie) — the order the
    # example builders use. ``lexsort`` sorts by its last key first and is stable.
    order = np.lexsort((movies, timestamps, users))
    sorted_users = users[order]
    sorted_times = timestamps[order]
    last_of_user = np.ones(len(order), dtype=bool)
    last_of_user[:-1] = sorted_users[1:] != sorted_users[:-1]
    first_of_user = np.ones(len(order), dtype=bool)
    first_of_user[1:] = sorted_users[1:] != sorted_users[:-1]
    user_start = np.maximum.accumulate(np.where(first_of_user, np.arange(len(order)), 0))
    # A user's final row is a training example only if some earlier row has a
    # strictly earlier timestamp, i.e. the user's first timestamp is smaller.
    has_example = sorted_times > sorted_times[user_start]
    final_positions = np.flatnonzero(last_of_user & has_example)
    eligible = sorted_users[final_positions]
    if len(eligible) == 0:
        return train, empty

    n_probe = min(len(eligible), max(1, int(round(fraction * len(eligible)))))
    chosen = np.sort(np.random.default_rng(seed).choice(eligible, size=n_probe, replace=False))
    chosen_positions = final_positions[np.isin(eligible, chosen)]

    probe_users: list[int] = []
    histories: list[tuple[int, ...]] = []
    targets: list[int] = []
    target_times: list[int] = []
    for position in chosen_positions:
        start = int(user_start[position])
        target_time = sorted_times[position]
        prefix = order[start:position]
        prefix = prefix[timestamps[prefix] < target_time]
        probe_users.append(int(sorted_users[position]))
        histories.append(tuple(int(movie) for movie in movies[prefix]))
        targets.append(int(movies[order[position]]))
        target_times.append(int(target_time))

    keep = np.ones(len(train), dtype=bool)
    keep[order[chosen_positions]] = False
    probe = StoppingProbe(
        user_ids=tuple(probe_users),
        histories=tuple(histories),
        targets=tuple(targets),
        timestamps=tuple(target_times),
    )
    return train[keep], probe


def should_stop(
    probe_recalls: Sequence[float],
    *,
    min_epochs: int = DEFAULT_MIN_EPOCHS,
    min_relative_improvement: float = DEFAULT_MIN_RELATIVE_IMPROVEMENT,
) -> bool:
    """Whether to stop after the pass whose probe recall is ``probe_recalls[-1]``.

    Never before ``min_epochs`` passes. From then on, stop when the latest recall
    beats the best earlier one by less than ``min_relative_improvement`` (0.5%)
    relative — so a pass that falls back also stops. The cap (5 passes in the
    cells) is the configured epoch count, which the training loop enforces.
    """
    epoch = len(probe_recalls)
    # An improvement needs something to improve on, so the first pass never stops.
    if epoch < max(2, min_epochs):
        return False
    best_before = max(probe_recalls[:-1])
    current = probe_recalls[-1]
    if best_before <= 0.0:
        # Nothing to be relative to: any recall at all is an improvement.
        return current <= 0.0
    return (current - best_before) / best_before < min_relative_improvement
