"""One vocabulary for retrieval, across the families that implement it.

Item-item and SASRec both retrieve, and until now they said so differently: the
routing predicate was named after the model (``was_served_by_itemitem`` /
``was_served_by_sasrec``), the seen-filter argument existed on one and not the
other, and SASRec alone could retrieve from a history handed to it. Nothing
could hold "a retriever" without first knowing which one it had.

The contract is split in two, because retrieval is asked two different
questions in this system and merging them would force one of the callers to
lie:

* :class:`UserRetriever` is the **offline** question — "top-k for user 7", where
  7 is a row in a table the model built at fit time. It is what ``src/training``
  and ``src/evaluation`` ask, and both model classes already answer it in
  exactly this shape, so they satisfy it structurally with no edits.
  :class:`CandidateRetriever` adds the two things that were missing: a family
  name, and the routing predicate under a name that is not one family's.

* :class:`HistoryRetriever` is the **serving** question. A rank request carries
  the user's ordered positive history plus an exclusion set and a dismissal set
  (``src/serving/model_server.py``); the sidecar holds no fit-time user table,
  and the user it answers for need not have existed when the bundle was
  trained. A contract that could only look a user up by id would not survive
  contact with that request, so this one takes the history as its argument.
  ``CandidateIndex.retrieve`` — the shipped item-item artifact — already has
  precisely this signature, which is why it was copied rather than invented.

The two do not collapse into one protocol, because the item-item *family* has
no single object that answers both: ``ItemItemModel`` retrieves only for users
its fit saw, and the history-shaped retrieval lives in the exported
``CandidateIndex``. SASRec carries both in the fitted model. That asymmetry is a
property of the families, not of the contract, and papering over it would mean
shipping an adapter method that exists only to raise.

Both protocols describe *serving* semantics for already-seen items: what the
caller has seen is never returned. The training pipelines deliberately need the
opposite (``ItemItemModel.recommend(..., filter_seen=False)`` and
``SASRecModel.retrieve_unfiltered``, so that a sampled positive can survive into
a LambdaRank group) and that variant is left off the contract rather than
smuggled in as a flag — a retriever that could be asked to return seen items is
one a serving caller can misuse.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from src.models.artifacts import (
    RETRIEVER_FAMILY_ITEM_ITEM,
    RETRIEVER_FAMILY_SASREC,
    CandidateContribution,
    CandidateRetrieval,
)


class UserRetriever(Protocol):
    """Retrieval keyed by a user id the fit already saw — the offline shape.

    Both fitted model classes satisfy this today without being touched, which
    is the point: the protocol was written against what they already agree on
    rather than as a target they have to be edited towards.
    """

    def recommend(self, user_id: int, k: int) -> list[int]:
        """Top-k unseen items for one user, learned path or fallback."""

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        """Batch variant — one ``list[int]`` per user, keyed by user id."""


class CandidateRetriever(UserRetriever, Protocol):
    """A user-keyed retriever that can also name itself and its routing.

    ``serves_from_learned_path`` is the family-neutral spelling of
    ``was_served_by_itemitem`` / ``was_served_by_sasrec``. The name matters more
    than it looks: every caller of the old predicates had to know the family to
    ask the question, so the evaluation harness, the cold-start slicing and the
    ranker's group assembly all carried a family name they had no other use for.
    """

    @property
    def family(self) -> str:
        """The manifest's name for this retrieval family (``RETRIEVER_FAMILY_*``)."""

    def serves_from_learned_path(self, user_id: int) -> bool:
        """Would ``recommend`` answer this user from retrieval, or from the fallback?"""


class HistoryRetriever(Protocol):
    """Retrieval keyed by the history that arrives on the request — the serving shape.

    The three inputs are not interchangeable, and the difference between the
    last two is the bug this signature exists to prevent. Positive history both
    seeds retrieval and hides its own items; ``excluded_movie_ids`` is the
    caller's complete "never show this" set, which necessarily *contains* the
    watched history and so may only hide, never narrow the seed set; and
    ``dismissed_movie_ids`` is the one input that also drops a seed, because a
    "not for me" must not pull in more of the same thing (ADR 0012).
    """

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        """At most ``limit`` candidates, with the provenance an audit needs."""


# The adapters below take structural inner protocols rather than the concrete
# model classes, and that is a packaging decision rather than a stylistic one:
# importing ``ItemItemModel`` or ``SASRecModel`` here would put implicit, torch
# and faiss into this module's own import graph, and the model sidecar's image
# installs none of the three (``infra/features/requirements.txt``). The static
# conformance assertions in ``tests/unit/test_candidate_retriever.py`` are what
# prove the real classes still fit the shapes named here.
#
# That is necessary and not yet sufficient: ``src/models/candidates/__init__.py``
# imports ``.cf``, which imports implicit, so importing *any* module in this
# package still drags a fit-time-only library into the importer. The sidecar
# therefore cannot adopt this contract until the protocols live outside this
# package — nothing in them depends on anything under
# ``src/models/candidates/``, so that move is a rename.


class _ItemItemLike(UserRetriever, Protocol):
    def was_served_by_itemitem(self, user_id: int) -> bool: ...


class _SequenceConfig(Protocol):
    @property
    def max_sequence_length(self) -> int: ...


class _SASRecLike(UserRetriever, Protocol):
    @property
    def config(self) -> _SequenceConfig: ...

    def was_served_by_sasrec(self, user_id: int) -> bool: ...

    def recommend_from_history(
        self,
        movie_ids: list[int],
        k: int,
        *,
        excluded_movie_ids: set[int] | None = None,
    ) -> list[int]: ...


@dataclass(frozen=True)
class ItemItemRetriever:
    """The item-item family under the neutral contract.

    Thin on purpose — the only thing it reconciles is the predicate's name.
    Notably it does *not* expose ``filter_seen``: the contract is the serving
    semantics, and the training pipeline that wants the unfiltered variant holds
    the concrete model, not this.
    """

    model: _ItemItemLike

    @property
    def family(self) -> str:
        return RETRIEVER_FAMILY_ITEM_ITEM

    def recommend(self, user_id: int, k: int) -> list[int]:
        return self.model.recommend(user_id, k)

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        return self.model.recommend_for_users(user_ids, k)

    def serves_from_learned_path(self, user_id: int) -> bool:
        return self.model.was_served_by_itemitem(user_id)


@dataclass(frozen=True)
class SASRecRetriever:
    """The SASRec family under both halves of the contract.

    The history side does real work rather than forwarding: ``recommend_from_history``
    knows about exclusions but not about dismissals, and the two are not the
    same input. A dismissal has to drop the seed *before* the encoder sees it —
    passing the merged set as exclusions alone would suppress the dismissed
    title itself while still letting it steer the query vector towards more of
    the same, which is the failure ADR 0012 is about.
    """

    model: _SASRecLike

    @property
    def family(self) -> str:
        return RETRIEVER_FAMILY_SASREC

    def recommend(self, user_id: int, k: int) -> list[int]:
        return self.model.recommend(user_id, k)

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        return self.model.recommend_for_users(user_ids, k)

    def serves_from_learned_path(self, user_id: int) -> bool:
        return self.model.was_served_by_sasrec(user_id)

    def retrieve(
        self,
        positive_history_movie_ids: list[int],
        *,
        limit: int,
        excluded_movie_ids: Iterable[int] = (),
        dismissed_movie_ids: Iterable[int] = (),
    ) -> CandidateRetrieval:
        excluded = set(excluded_movie_ids)
        dismissed = set(dismissed_movie_ids)
        hidden = excluded | dismissed
        # The wire order is *newest first*: the coordinator builds this list with
        # `ORDER BY watched_at DESC` because item-item's seed attribution wants
        # the most recent title first. SASRec is trained oldest-to-newest, so the
        # window has to be reversed before the encoder sees it. Getting this
        # wrong is silent in both directions — the model would read a user's
        # oldest title as their most recent, and a history longer than the window
        # would keep the *oldest* `max_sequence_length` items instead of the
        # newest. Neither shows up as an error, only as worse recommendations.
        seeds = [
            movie_id
            for movie_id in reversed(positive_history_movie_ids)
            if movie_id not in dismissed
        ]
        # An empty seed set is an ordinary outcome online — a user whose whole
        # history was dismissed — and ``CandidateIndex`` answers it with an
        # empty retrieval. ``recommend_from_history`` raises on it instead, so
        # the adapter absorbs the difference rather than letting the caller
        # discover that two retrievers disagree about what "no seeds" means.
        if limit <= 0 or not seeds:
            return CandidateRetrieval(
                contributions=(),
                seed_count=0,
                excluded_count=len(hidden),
            )
        movie_ids = self.model.recommend_from_history(seeds, limit, excluded_movie_ids=hidden)
        contributions = tuple(
            CandidateContribution(
                movie_id=movie_id,
                source=RETRIEVER_FAMILY_SASREC,
                # SASRec encodes the whole window into one query vector, so no
                # candidate is attributable to a single seed. ``None`` is the
                # honest answer and the field already carries it for
                # popularity fill; inventing a seed here would put a fabricated
                # "because you watched…" into the audit.
                seed_movie_id=None,
                # The model returns ids without their scores, so there is no
                # mass to report. Do not compare this field across families
                # until scored retrieval exists.
                contribution=0.0,
            )
            for movie_id in movie_ids
        )
        # Only the seeds inside the encoder window drove the query — the model
        # truncates to ``max_sequence_length`` — so a longer history must not be
        # counted whole. ``seed_count`` is the number of seeds that reached a
        # candidate, and a retrieval that returned nothing was reached by none.
        window = seeds[-self.model.config.max_sequence_length :]
        return CandidateRetrieval(
            contributions=contributions,
            seed_count=len(window) if contributions else 0,
            excluded_count=len(hidden),
        )
