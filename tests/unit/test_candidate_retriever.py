"""The retrieval contract, and the two families that have to keep it.

Two kinds of test live here and they prove different things. The conformance
tests are *typed assignments*: they pass at runtime trivially, and the work is
done by mypy, which is why they name the real ``ItemItemModel``,
``SASRecModel`` and ``CandidateIndex`` rather than doubles. Run them with
``mypy tests/unit/test_candidate_retriever.py`` — the repository's own gate is
``mypy src/``, so a green pytest here is not on its own evidence that the
protocol still fits.

The behavioural tests use a SASRec-shaped stub instead of a fitted model. A real
fit would be a training job to prove three lines of set arithmetic, and the
adapter's signature compatibility with the real class is already settled
statically by the conformance test above it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.models.artifacts import (
    RETRIEVER_FAMILY_ITEM_ITEM,
    RETRIEVER_FAMILY_SASREC,
    CandidateIndex,
)
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.retriever import (
    CandidateRetriever,
    HistoryRetriever,
    ItemItemRetriever,
    SASRecRetriever,
    UserRetriever,
)
from src.models.candidates.sasrec import SASRecConfig, SASRecModel

# Three items apiece across two taste clusters, which is under ADR 0001's
# threshold, so these models are built with the documented index-membership
# opt-out to keep the tests measuring retrieval rather than the fallback in
# front of it.
_TRAIN = pd.DataFrame(
    [
        (1, 100),
        (1, 101),
        (1, 102),
        (2, 100),
        (2, 101),
        (2, 103),
        (3, 100),
        (3, 102),
        (3, 104),
        (4, 200),
        (4, 201),
        (4, 202),
    ],
    columns=["userId", "movieId"],
)


@dataclass(frozen=True)
class _StubConfig:
    max_sequence_length: int


class _StubSASRec:
    """A SASRec-shaped stand-in for the parts of the model the adapter touches.

    Mirrors the real model where it matters to the adapter: it excludes its own
    history as well as the caller's exclusions, and it raises rather than
    answering an empty history.
    """

    def __init__(self, catalog: list[int], *, max_sequence_length: int = 3) -> None:
        self.catalog = catalog
        self.max_sequence_length = max_sequence_length
        self.calls: list[tuple[list[int], int, set[int]]] = []

    @property
    def config(self) -> _StubConfig:
        return _StubConfig(max_sequence_length=self.max_sequence_length)

    def recommend(self, user_id: int, k: int) -> list[int]:
        return self.catalog[:k]

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        return {user_id: self.recommend(user_id, k) for user_id in user_ids}

    def was_served_by_sasrec(self, user_id: int) -> bool:
        return user_id > 0

    def recommend_from_history(
        self,
        movie_ids: list[int],
        k: int,
        *,
        excluded_movie_ids: set[int] | None = None,
    ) -> list[int]:
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        excluded = set(excluded_movie_ids or set()) | set(movie_ids)
        self.calls.append((list(movie_ids), k, set(excluded_movie_ids or set())))
        return [movie_id for movie_id in self.catalog if movie_id not in excluded][:k]


class TestStructuralConformance:
    """What mypy checks. The assertions are incidental; the annotations are the test."""

    def test_both_fitted_model_families_are_user_retrievers_unedited(self) -> None:
        # Neither class inherits from anything in the contract, and neither was
        # touched to satisfy it: the protocol was written to what they already
        # agreed on. ``ItemItemModel.recommend`` carries an extra defaulted
        # ``filter_seen`` and is still a valid implementation.
        item_item: UserRetriever = ItemItemModel()
        sasrec: UserRetriever = SASRecModel()

        assert item_item.recommend(1, 3) == []
        assert sasrec.recommend_for_users([1], 3) == {1: []}

    def test_the_adapters_complete_the_neutral_contract(self) -> None:
        item_item: CandidateRetriever = ItemItemRetriever(ItemItemModel())
        sasrec: CandidateRetriever = SASRecRetriever(SASRecModel())

        assert item_item.family == RETRIEVER_FAMILY_ITEM_ITEM
        assert sasrec.family == RETRIEVER_FAMILY_SASREC

    def test_the_shipped_item_item_artifact_is_already_the_serving_shape(self) -> None:
        # ``CandidateIndex.retrieve`` is where the ``HistoryRetriever`` signature
        # came from, so this is the assertion that the contract was copied from
        # the serving path rather than imposed on it.
        index: HistoryRetriever = CandidateIndex.build({1: {10, 20}, 2: {10, 30}})

        assert index.retrieve([10], limit=2).movie_ids == [20, 30]

    def test_the_sasrec_adapter_answers_both_halves_of_the_contract(self) -> None:
        # One object, both shapes — which is what the sidecar will hold in W8.
        retriever = SASRecRetriever(SASRecModel(config=SASRecConfig()))
        neutral: CandidateRetriever = retriever
        serving: HistoryRetriever = retriever

        assert neutral.family == RETRIEVER_FAMILY_SASREC
        assert serving.retrieve([], limit=10).movie_ids == []


class TestItemItemAdapter:
    def test_the_neutral_predicate_agrees_with_the_family_named_one(self) -> None:
        model = ItemItemModel(cold_start_threshold=None).fit(_TRAIN)
        retriever = ItemItemRetriever(model)

        assert retriever.serves_from_learned_path(1) is model.was_served_by_itemitem(1)
        assert retriever.serves_from_learned_path(1) is True
        # A user the fit never saw is the fallback's, under either policy.
        assert retriever.serves_from_learned_path(999) is False

    def test_the_predicate_follows_the_configured_routing_policy(self) -> None:
        # Same fixture, ADR 0001's threshold instead of the opt-out: every user
        # here has three interactions, so the learned path serves none of them.
        model = ItemItemModel(cold_start_threshold=10).fit(_TRAIN)

        assert ItemItemRetriever(model).serves_from_learned_path(1) is False

    def test_retrieval_forwards_to_the_model_with_serving_semantics(self) -> None:
        model = ItemItemModel(cold_start_threshold=None).fit(_TRAIN)
        retriever = ItemItemRetriever(model)

        recommendations = retriever.recommend(1, 3)

        # The contract has no ``filter_seen``, so the adapter always takes the
        # serving default and the user's own history cannot come back.
        assert not set(recommendations) & {100, 101, 102}
        assert retriever.recommend_for_users([1, 4], 3) == {
            1: recommendations,
            4: model.recommend(4, 3),
        }


class TestSASRecAdapter:
    def test_a_dismissal_drops_the_seed_and_still_hides_the_title(self) -> None:
        model = _StubSASRec(catalog=[10, 20, 30, 40])
        retriever = SASRecRetriever(model)

        retrieval = retriever.retrieve([1, 2], limit=4, dismissed_movie_ids=[2])

        seeds, _limit, excluded = model.calls[0]
        # The dismissed title must not steer the query vector...
        assert seeds == [1]
        # ...and must still be suppressed in the results.
        assert 2 in excluded
        assert retrieval.excluded_count == 1

    def test_exclusions_only_hide_and_never_narrow_the_seed_set(self) -> None:
        model = _StubSASRec(catalog=[10, 20, 30, 40])
        retriever = SASRecRetriever(model)

        retrieval = retriever.retrieve([1, 2], limit=4, excluded_movie_ids=[2, 10])

        seeds, _limit, excluded = model.calls[0]
        # ``excluded_movie_ids`` contains the watched history by construction,
        # so using it to filter seeds is what would silently empty retrieval.
        assert seeds == [1, 2]
        assert excluded == {2, 10}
        assert 10 not in retrieval.movie_ids
        assert retrieval.excluded_count == 2

    def test_an_empty_seed_set_answers_the_way_the_item_item_index_does(self) -> None:
        # The one signature difference the adapter absorbs: the model raises on
        # an empty history, the index returns an empty retrieval. Online, a user
        # whose whole history was dismissed is ordinary, not exceptional.
        model = _StubSASRec(catalog=[10, 20])
        index = CandidateIndex.build({1: {10, 20}})

        adapted = SASRecRetriever(model).retrieve([1], limit=4, dismissed_movie_ids=[1])
        indexed = index.retrieve([1], limit=4, dismissed_movie_ids=[1])

        assert adapted.contributions == ()
        assert adapted.seed_count == indexed.seed_count == 0
        assert adapted.excluded_count == indexed.excluded_count == 1
        assert model.calls == []

    def test_a_non_positive_limit_asks_the_model_nothing(self) -> None:
        model = _StubSASRec(catalog=[10, 20])

        retrieval = SASRecRetriever(model).retrieve([1], limit=0, excluded_movie_ids=[7])

        assert retrieval.movie_ids == []
        assert retrieval.excluded_count == 1
        assert model.calls == []

    def test_seed_count_is_bounded_by_the_encoder_window(self) -> None:
        # A history longer than the window is truncated inside the model, so the
        # seeds that fell off it drove nothing and counting them would let the
        # audit claim a retrieval that never happened.
        model = _StubSASRec(catalog=[10, 20, 30], max_sequence_length=2)

        retrieval = SASRecRetriever(model).retrieve([1, 2, 3, 4], limit=3)

        assert retrieval.seed_count == 2

    def test_contributions_carry_the_family_and_claim_no_seed(self) -> None:
        model = _StubSASRec(catalog=[10, 20, 30])

        retrieval = SASRecRetriever(model).retrieve([1], limit=2)

        assert retrieval.movie_ids == [10, 20]
        assert {c.source for c in retrieval.contributions} == {RETRIEVER_FAMILY_SASREC}
        # SASRec encodes the whole window into one query, so no candidate is
        # attributable to a single watched title and none claims to be.
        assert all(c.seed_movie_id is None for c in retrieval.contributions)
        # Unscored retrieval: the mass is a placeholder, not a similarity.
        assert all(c.contribution == 0.0 for c in retrieval.contributions)
