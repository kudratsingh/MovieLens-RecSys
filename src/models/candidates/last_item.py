"""
Last-item transition baseline — the control a sequential retriever has to beat.

ADR 0016 proposes SASRec on the argument that order is the largest unused
signal in the data. That argument only becomes evidence if the gain survives
comparison with the cheapest thing that also uses order: "the item you just
watched is commonly followed by these ones". Popularity is not a sufficient
control on its own, because a sequential model that clears popularity may have
learned nothing beyond the obvious successors of a user's most recent title.
This model is the missing control — zero learned parameters, one pass over the
training slice, and the same retrieval contract every other candidate generator
honours, so a comparison against it changes the model and nothing else.

**The model.** Sort the training slice by ``(userId, timestamp, movieId)`` and
cut each user's rows into *timestamp groups* — maximal runs of interactions
sharing one second. For every pair of consecutive groups of one user, each item
in the earlier group is an antecedent of each item in the later one, and
``count[antecedent][successor]`` gains one per such pair. A user's *last items*
are the items of their final training timestamp group. A warm user's score for
a candidate is the sum of ``count[a][candidate]`` over their last items ``a``;
candidates already in that user's training history are dropped, and what
remains is ordered by descending score, then descending training popularity,
then ascending movie id. All three keys are read off the training slice, so the
ordering is a total one and the model is reproducible down to the tie.

**Why timestamp groups.** MovieLens records whole seconds, and a user who rated
six films inside one second did not watch them in the order their ids happen to
sort in. Letting co-timestamped items transition to one another would have an
item predict a sibling it has no claim to precede — exactly the
sequence-construction leakage CLAUDE.md singles out. Items inside a group
therefore never form a transition with each other; they are jointly the
antecedents of the next group, which is the only ordering the timestamps
actually support. ADR 0016's sequence builder applies the same rule to its
prefixes, so the control and the model it controls for read the data the same
way.

**Where popularity enters, and why it is named twice.** Below ADR 0001's
``COLD_START_THRESHOLD`` a user is answered by the embedded popularity
fallback, identically to item-item, CF and SASRec — this baseline earns no
special routing for being cheap. Above it, two further uses are deliberate and
both are measurable rather than assumed:

  1. *Tie-break.* Most successors of a given antecedent are seen exactly once,
     so a tie-break decides most of the ordering. Movie id alone would rank by
     nothing at all; training popularity makes the control as strong as a
     control should be, since a sequential model that cannot beat "transitions,
     popularity where transitions are silent" has not shown that order bought
     anything. Movie id remains as the third key so that two items of equal
     count and equal popularity still have a defined order — the popularity
     model's own ranking leaves that case to a sort implementation, and a
     control should not.
  2. *Backfill.* A rare last item has far fewer than ``K_CANDIDATES`` distinct
     successors, and a retriever that returns forty candidates is not being
     compared at the same K as one that returns five hundred. The remaining
     slots are filled from the popularity ranking, skipping seen and already
     emitted items. :meth:`transition_candidates` returns the un-backfilled
     prefix so the two contributions can be reported apart; the trainer logs
     the fill rate for that reason.

**The recency half of the name.** The backlog item calls this a "last-item
transition / recency" baseline. Recency is expressed here as *only the most
recent interaction is consulted* — the purest form of the signal and one with
no free parameter. A decay-weighted variant over the whole history was
deliberately not added: it is a second model with a hyperparameter to tune, and
this is a control.

**Cost.** Fitting counts one pair per (antecedent, successor) occurrence, so the
work is the sum of ``|g|·|g-1|`` over consecutive same-user timestamp groups.
That is close to linear in rows while groups are small, which is the shape
MovieLens's per-second timestamps produce, but it is quadratic in the size of a
bulk-rated batch. :attr:`TransitionStats.n_transition_events` and
``max_timestamp_group_size`` are recorded for exactly that reason — an
expensive fit should be legible from the run rather than inferred from a
wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import routing
from .popularity import PopularityModel

_REQUIRED_COLUMNS = ("userId", "movieId", "timestamp")


@dataclass(frozen=True)
class TransitionStats:
    """What the fit saw, in the terms that explain both its cost and its reach."""

    # Total (antecedent, successor) occurrences counted — the size of the work,
    # and the denominator behind every count in the table.
    n_transition_events: int
    # Distinct pairs retained. Far smaller than the events on real data; the
    # ratio is how much repetition there was to learn from.
    n_transition_pairs: int
    # Antecedents with at least one recorded successor. An item that never had
    # one contributes nothing at recommend time and the user falls to backfill.
    n_antecedents: int
    # Largest run of one user's interactions sharing a timestamp. One means the
    # data is strictly ordered; a large number is where the fit's cost went and
    # how ambiguous "the last item" was.
    max_timestamp_group_size: int


_EMPTY_STATS = TransitionStats(
    n_transition_events=0,
    n_transition_pairs=0,
    n_antecedents=0,
    max_timestamp_group_size=0,
)


@dataclass
class LastItemTransitionModel:
    # Where the learned path stops, in the same vocabulary every other candidate
    # model uses. ADR 0001's threshold by default; None is the index-membership
    # opt-out. See src/models/candidates/routing.py.
    cold_start_threshold: int | None = routing.DEFAULT_COLD_START_THRESHOLD

    # Fill the tail of a short candidate list from the popularity ranking. On by
    # default because a candidate generator is compared at a fixed K and a
    # partially filled list is not the same measurement. Off is the transitions
    # only view, which the trainer also reports.
    backfill_with_popularity: bool = True

    stats: TransitionStats = _EMPTY_STATS

    # Populated by fit. The successor table is stored CSR-style — one sorted
    # run per antecedent inside three parallel arrays — because a dict of
    # per-item Counters over 25M interactions costs several gigabytes in
    # object overhead alone, and the arrays slice straight into the vectorized
    # scoring below.
    _successor_items: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _successor_counts: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _successor_popularity: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    _successor_span: dict[int, tuple[int, int]] = field(default_factory=dict)
    _last_items: dict[int, tuple[int, ...]] = field(default_factory=dict)
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(self, train: pd.DataFrame) -> LastItemTransitionModel:
        """Count transitions over a training slice.

        Expects ``userId``, ``movieId`` and ``timestamp``. Rating values are
        ignored — every interaction has weight 1.0 per ADR 0002. Nothing but
        the rows handed in is ever consulted, which is what keeps the model
        point-in-time: the caller passes the ``t < cutoff`` slice, so no
        holdout interaction can enter a transition or become a last item.
        """
        # Fitted first so the fallback and the seen-item history are ready even
        # when there is nothing to count.
        self._popularity = PopularityModel().fit(train)
        self._reset_transitions()

        if train.empty:
            return self

        missing = [column for column in _REQUIRED_COLUMNS if column not in train.columns]
        if missing:
            raise KeyError(f"train is missing required columns: {missing}")

        ordered = train.sort_values(["userId", "timestamp", "movieId"], kind="stable")
        users = ordered["userId"].to_numpy()
        timestamps = ordered["timestamp"].to_numpy()
        items = ordered["movieId"].to_numpy()

        # A row opens a new timestamp group when its user or its second differs
        # from the previous row's. Groups are numbered globally and in order, so
        # group g-1 is g's predecessor whenever the two share a user.
        opens_group = np.empty(len(ordered), dtype=bool)
        opens_group[0] = True
        opens_group[1:] = (users[1:] != users[:-1]) | (timestamps[1:] != timestamps[:-1])
        group_of_row = np.cumsum(opens_group) - 1
        group_starts = np.flatnonzero(opens_group)
        group_users = users[group_starts]
        group_sizes = np.bincount(group_of_row)

        follows_same_user = np.zeros(len(group_users), dtype=bool)
        follows_same_user[1:] = group_users[1:] == group_users[:-1]

        self._last_items = self._collect_last_items(
            items, group_users, group_starts, group_sizes, follows_same_user
        )
        self._build_successor_table(items, group_of_row, follows_same_user)
        self.stats = TransitionStats(
            # Computed from the group sizes rather than from the materialized
            # pairs: it is exact either way, and this way the number is known
            # even if the join below is what a future reader is trying to
            # explain the cost of.
            n_transition_events=int(
                (group_sizes[1:] * group_sizes[:-1] * follows_same_user[1:]).sum()
            ),
            n_transition_pairs=len(self._successor_items),
            n_antecedents=len(self._successor_span),
            max_timestamp_group_size=int(group_sizes.max()),
        )
        return self

    def recommend(self, user_id: int, k: int) -> list[int]:
        """Top-k items for one user.

        Cold user (below the routing threshold, or unknown) → popularity
        fallback. Warm user → transition-scored candidates, topped up from the
        popularity ranking when the last item has too few distinct successors
        to fill k.
        """
        if not self.was_served_by_last_item(user_id):
            return self._popularity.recommend(user_id, k)

        candidates = self.transition_candidates(user_id, k)
        if not self.backfill_with_popularity or len(candidates) >= k:
            return candidates

        emitted = set(candidates)
        seen = self._popularity.user_history.get(user_id, set())
        for item in self._popularity.ranking:
            if len(candidates) == k:
                break
            if item in seen or item in emitted:
                continue
            candidates.append(item)
        return candidates

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        """Batch variant — one ``list[int]`` per user, keyed by user id."""
        return {user_id: self.recommend(user_id, k) for user_id in user_ids}

    def transition_candidates(self, user_id: int, k: int) -> list[int]:
        """The transition-scored part of ``recommend``, without the backfill.

        Always a prefix of what ``recommend`` returns for the same user and k,
        which is what lets the trainer report a transitions-only recall without
        scoring the model twice. Empty for a user the fallback serves, and for a
        warm user whose last items have no recorded successors — those are
        different situations and the trainer counts them separately.
        """
        if not self.was_served_by_last_item(user_id):
            return []

        spans = [
            span
            for item in self._last_items.get(user_id, ())
            if (span := self._successor_span.get(item)) is not None
        ]
        if not spans:
            return []

        candidates = np.concatenate([self._successor_items[start:end] for start, end in spans])
        counts = np.concatenate([self._successor_counts[start:end] for start, end in spans])
        popularity = np.concatenate([self._successor_popularity[start:end] for start, end in spans])

        # One user can have several last items, and they can share successors —
        # collapse duplicates and sum their counts. Popularity is a property of
        # the item, so taking it from the first occurrence is exact.
        unique_items, first_seen, inverse = np.unique(
            candidates, return_index=True, return_inverse=True
        )
        totals = np.bincount(inverse, weights=counts, minlength=len(unique_items)).astype(np.int64)
        unique_popularity = popularity[first_seen]

        seen = self._popularity.user_history.get(user_id, set())
        if seen:
            keep = ~np.isin(unique_items, np.fromiter(seen, dtype=np.int64, count=len(seen)))
            unique_items = unique_items[keep]
            totals = totals[keep]
            unique_popularity = unique_popularity[keep]

        # lexsort reads its keys last-to-first: score descending, then the more
        # popular item, then the lower id. The third key is what makes the
        # ordering total, and so the whole model reproducible.
        order = np.lexsort((unique_items, -unique_popularity, -totals))
        return [int(item) for item in unique_items[order[:k]]]

    def was_served_by_last_item(self, user_id: int) -> bool:
        """Predicate: does ``recommend`` route this user to transitions or to popularity?

        ``recommend`` calls this rather than restating the condition, so the two
        cannot drift — the same contract ``ItemItemModel.was_served_by_itemitem``
        holds, and the same one every per-policy metric and ADR 0011 bucket count
        is computed from. Deliberately says nothing about whether the user's last
        items have any successors: routing and reach are separate claims, and
        conflating them would make the cohort's fallback counts disagree with
        ADR 0001's threshold for reasons that have nothing to do with routing.
        """
        if user_id not in self._last_items:
            return False
        return routing.learned_path_serves(
            history_size=len(self._popularity.user_history.get(user_id, ())),
            cold_start_threshold=self.cold_start_threshold,
        )

    def _reset_transitions(self) -> None:
        self._successor_items = np.empty(0, dtype=np.int64)
        self._successor_counts = np.empty(0, dtype=np.int64)
        self._successor_popularity = np.empty(0, dtype=np.int64)
        self._successor_span = {}
        self._last_items = {}
        self.stats = _EMPTY_STATS

    @staticmethod
    def _collect_last_items(
        items: np.ndarray,
        group_users: np.ndarray,
        group_starts: np.ndarray,
        group_sizes: np.ndarray,
        follows_same_user: np.ndarray,
    ) -> dict[int, tuple[int, ...]]:
        """Each user's final timestamp group, in movie-id order.

        A tie at the end of a history is not resolved into a single winner. Every
        item of that last second is equally "the last item", and scoring sums
        over all of them — the same treatment ties get during counting, so the
        query side and the fit side cannot disagree about what "last" means.
        """
        closes_user = np.empty(len(group_users), dtype=bool)
        closes_user[-1] = True
        closes_user[:-1] = ~follows_same_user[1:]
        last_items: dict[int, tuple[int, ...]] = {}
        for group in np.flatnonzero(closes_user):
            start = int(group_starts[group])
            end = start + int(group_sizes[group])
            last_items[int(group_users[group])] = tuple(int(item) for item in items[start:end])
        return last_items

    def _build_successor_table(
        self,
        items: np.ndarray,
        group_of_row: np.ndarray,
        follows_same_user: np.ndarray,
    ) -> None:
        """Count every (antecedent, successor) pair and store it CSR-style.

        The join is the whole of the counting: each row that opens no new user
        is joined, on the id of the group before its own, against every row of
        that group. It materializes one row per transition event, which is the
        memory high-water mark of the fit and why the event count is recorded.
        """
        antecedents = pd.DataFrame({"group": group_of_row, "antecedent": items})
        is_successor = follows_same_user[group_of_row]
        successors = pd.DataFrame(
            {
                "group": group_of_row[is_successor] - 1,
                "successor": items[is_successor],
            }
        )
        if successors.empty:
            return

        pairs = successors.merge(antecedents, on="group", how="inner")
        # ``sort=True`` is load-bearing rather than cosmetic: the CSR spans built
        # below assume one contiguous run of rows per antecedent.
        counts = (
            pairs.groupby(["antecedent", "successor"], sort=True).size().reset_index(name="count")
        )
        if counts.empty:
            return

        antecedent_values = counts["antecedent"].to_numpy()
        self._successor_items = counts["successor"].to_numpy(dtype=np.int64)
        self._successor_counts = counts["count"].to_numpy(dtype=np.int64)
        # The tie-break key, carried alongside the counts so it comes along with
        # a slice for free instead of being looked up per candidate per user. It
        # is the raw training interaction count rather than a position in
        # ``PopularityModel.ranking``: that ranking orders equally popular items
        # by whatever the sort happened to do, and a control's ordering should
        # be explainable rather than merely repeatable.
        catalog, catalog_counts = np.unique(items, return_counts=True)
        self._successor_popularity = catalog_counts[
            np.searchsorted(catalog, self._successor_items)
        ].astype(np.int64)

        opens_run = np.empty(len(antecedent_values), dtype=bool)
        opens_run[0] = True
        opens_run[1:] = antecedent_values[1:] != antecedent_values[:-1]
        starts = np.flatnonzero(opens_run)
        ends = np.append(starts[1:], len(antecedent_values))
        self._successor_span = {
            int(antecedent_values[start]): (int(start), int(end))
            for start, end in zip(starts, ends, strict=True)
        }
