"""Correctness tests for the SASRec-candidate ranker runner.

Four claims are worth a test here, because all four are claims a reviewer would
otherwise have to take on trust from a six-figure-positive training run:

1. **The item-item arm is `ranker.py`.** If the incumbent this run gates against
   is not the incumbent trainer, the comparison measures the runner rather than
   the retriever. The equivalence is asserted on real objects, not by inspection.
2. **The SASRec query is point-in-time.** The prefix a positive is scored from
   holds only items strictly before its timestamp, and events sharing a timestamp
   are never context for one another — asserted against
   `build_strict_prefix_examples`, which is where that rule is defined for
   SASRec's own training.
3. **Exclusions match serving (#126).** No negative in a LambdaRank group is an
   item the user had already watched at that moment.
4. **The construction is deterministic.** Same seed, same fixture, identical
   training set to the row.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.evaluation.protocol import COLD_START_THRESHOLD
from src.features import FeatureIndex
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec import SASRecConfig, SASRecModel
from src.models.candidates.sequence_data import build_strict_prefix_examples
from src.models.candidates.twotower import build_user_history
from src.training import ranker as ranker_module
from src.training import sasrec_ranker
from src.training.ranker import _sample_training_positives
from src.training.sasrec_ranker import (
    CandidateSource,
    ExclusionPolicyError,
    ItemItemSource,
    RoutingCounts,
    SasrecSource,
    TrainingQuery,
    build_ranker_training_set,
    history_index,
    strict_prefix,
)

_BASE_TS = 1_500_000_000


def _train_frame(seed: int = 0) -> pd.DataFrame:
    """20 users × 40 items, 15 interactions each, strictly increasing timestamps."""
    rng = np.random.default_rng(seed)
    rows = []
    for user in range(20):
        items = rng.choice(40, size=15, replace=False)
        for position, movie in enumerate(items):
            rows.append(
                {
                    "userId": int(user),
                    "movieId": int(movie),
                    "timestamp": _BASE_TS + user * 1000 + position,
                }
            )
    return pd.DataFrame(rows)


def _tied_frame() -> pd.DataFrame:
    """Two users whose events collide on a timestamp, which is the hard case."""
    return pd.DataFrame(
        [
            # user 1: three events at t, then two more later
            {"userId": 1, "movieId": 5, "timestamp": _BASE_TS},
            {"userId": 1, "movieId": 3, "timestamp": _BASE_TS},
            {"userId": 1, "movieId": 9, "timestamp": _BASE_TS},
            {"userId": 1, "movieId": 7, "timestamp": _BASE_TS + 10},
            {"userId": 1, "movieId": 2, "timestamp": _BASE_TS + 10},
            {"userId": 1, "movieId": 4, "timestamp": _BASE_TS + 20},
            # user 2: strictly increasing
            {"userId": 2, "movieId": 1, "timestamp": _BASE_TS + 1},
            {"userId": 2, "movieId": 6, "timestamp": _BASE_TS + 2},
            {"userId": 2, "movieId": 8, "timestamp": _BASE_TS + 3},
        ]
    )


def _movies(n_items: int = 40) -> pd.DataFrame:
    return pd.DataFrame({"movieId": list(range(n_items)), "genres": ["Action|Drama"] * n_items})


def _sasrec_config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 5,
        "hidden_dim": 8,
        "num_blocks": 1,
        "num_heads": 2,
        "feedforward_dim": 16,
        "dropout": 0.0,
        "negative_count": 2,
        "batch_size": 8,
        "epochs": 1,
        "faiss_exact": True,
        "seed": 42,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _sasrec_source(train: pd.DataFrame, *, threshold: int | None = 1) -> SasrecSource:
    model = SASRecModel(config=_sasrec_config(), cold_start_threshold=threshold).fit(train)
    popularity = PopularityModel().fit(train)
    model._popularity = popularity
    return SasrecSource(
        model=model,
        popularity=popularity,
        manifest=None,  # type: ignore[arg-type]
    )


# --- 1. the incumbent arm is `ranker.py` ------------------------------------


def test_itemitem_arm_reproduces_ranker_py_training_set() -> None:
    """The incumbent must be the incumbent trainer, not a lookalike.

    Both constructions are driven from the same fixture, the same positives and
    the same seeded generator, and the resulting (features, groups, labels) triple
    is compared exactly. If this ever diverges, the gate verdict is measuring the
    runner and not the candidate stage.
    """
    train = _train_frame()
    movies = _movies()
    model = ItemItemModel().fit(train)
    feature_index = FeatureIndex.build(train, movies)
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )

    reference = ranker_module._build_ranker_training_set(
        positives=positives,
        candidate_model=model,
        feature_index=feature_index,
        training_history=train,
        n_negatives=5,
        rng=np.random.default_rng(7),
    )
    features, groups, labels, _dropped = build_ranker_training_set(
        positives=positives,
        source=ItemItemSource(model=model),
        feature_index=feature_index,
        history_by_user=history_index(train),
        n_negatives=5,
        rng=np.random.default_rng(7),
        # `ranker.py` hard-codes K_CANDIDATES, so the equivalence only holds when
        # this one does too — the candidate pool is what the negatives are drawn
        # from, and a smaller pool is a different draw.
        batch_size=7,
    )

    assert groups == reference[1]
    assert np.array_equal(labels, reference[2])
    pd.testing.assert_frame_equal(features, reference[0])


def test_batching_does_not_change_the_item_item_training_set() -> None:
    """The chunk size is an efficiency knob, not a parameter of the result."""
    train = _train_frame()
    model = ItemItemModel().fit(train)
    feature_index = FeatureIndex.build(train, _movies())
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )
    history = history_index(train)

    def build(batch_size: int) -> tuple[pd.DataFrame, list[int], np.ndarray, int]:
        return build_ranker_training_set(
            positives=positives,
            source=ItemItemSource(model=model),
            feature_index=feature_index,
            history_by_user=history,
            n_negatives=5,
            rng=np.random.default_rng(11),
            k_candidates=30,
            batch_size=batch_size,
        )

    one_at_a_time = build(1)
    in_chunks = build(9)
    assert one_at_a_time[1] == in_chunks[1]
    assert np.array_equal(one_at_a_time[2], in_chunks[2])
    pd.testing.assert_frame_equal(one_at_a_time[0], in_chunks[0])


# --- 2. the SASRec query is point-in-time -----------------------------------


def test_strict_prefix_agrees_with_the_sequence_builder() -> None:
    """The runner's slice is SASRec's own training rule, not a near-miss of it.

    `build_strict_prefix_examples` is what produced the sequences the pinned model
    was fitted on. Every (prefix, target) pair it emits for the tied fixture must
    be reproduced by ``strict_prefix`` at that target's timestamp — which is what
    makes "the ranker asks the model the kind of question it was trained on" a
    checked statement.
    """
    frame = _tied_frame()
    item_to_index = {item: index + 1 for index, item in enumerate(sorted(frame["movieId"]))}
    index_to_item = {index: item for item, index in item_to_index.items()}
    max_length = 10
    histories, positives = build_strict_prefix_examples(
        frame, item_to_index=item_to_index, max_length=max_length
    )

    by_user = history_index(frame)
    ordered = frame.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    # `build_strict_prefix_examples` walks users in that same order and emits one
    # row per event outside a user's first timestamp group, so the two can be
    # zipped by construction.
    emitted = [
        row
        for _user, group in ordered.groupby("userId", sort=False)
        for row in group[group["timestamp"] > group["timestamp"].min()].itertuples(index=False)
    ]
    assert len(emitted) == len(positives)

    for row, event in enumerate(emitted):
        timestamps, movie_ids = by_user[int(event.userId)]
        expected = [
            int(movie) for movie in strict_prefix(timestamps, movie_ids, int(event.timestamp))
        ]
        built = [index_to_item[int(dense)] for dense in histories[row].tolist() if int(dense) != 0]
        assert built == expected[-max_length:]
        assert index_to_item[int(positives[row])] == int(event.movieId)


def test_strict_prefix_excludes_the_positive_and_its_timestamp_twins() -> None:
    frame = _tied_frame()
    timestamps, movie_ids = history_index(frame)[1]
    # The three events at _BASE_TS are context for nothing: the prefix at that
    # instant is empty, and none of them can see the other two.
    assert strict_prefix(timestamps, movie_ids, _BASE_TS).tolist() == []
    # At the next timestamp group they are all visible, and its own two events
    # remain invisible to each other.
    assert sorted(strict_prefix(timestamps, movie_ids, _BASE_TS + 10).tolist()) == [3, 5, 9]
    assert sorted(strict_prefix(timestamps, movie_ids, _BASE_TS + 20).tolist()) == [2, 3, 5, 7, 9]


def test_sasrec_source_never_sees_an_item_from_the_future() -> None:
    """The leakage canary, driven through the source rather than around it.

    Two frames: the real one, and one truncated immediately before each positive.
    A retriever that only reads the strict prefix cannot tell them apart, so the
    candidate lists must be identical. A retriever that reached forward would
    differ on the first user with a later interaction.
    """
    train = _train_frame()
    source = _sasrec_source(train)
    by_user = history_index(train)

    as_of = _BASE_TS + 3 * 1000 + 9
    user_id = 3
    timestamps, movie_ids = by_user[user_id]
    query = TrainingQuery(
        user_id=user_id,
        as_of=as_of,
        prior_movie_ids=strict_prefix(timestamps, movie_ids, as_of),
    )
    truncated = train[train["timestamp"] < as_of]
    truncated_ts, truncated_ids = history_index(truncated)[user_id]
    truncated_query = TrainingQuery(
        user_id=user_id,
        as_of=as_of,
        prior_movie_ids=strict_prefix(truncated_ts, truncated_ids, as_of),
    )

    assert query.prior_movie_ids.tolist() == truncated_query.prior_movie_ids.tolist()
    assert source.training_candidates([query], 10) == source.training_candidates(
        [truncated_query], 10
    )


def test_sasrec_training_retrieval_is_unfiltered() -> None:
    """`filter_seen=False`'s counterpart: the history is *not* removed.

    The positive is drawn from the user's own train history, so a source that
    filtered its history out of its results would drop every positive it was asked
    about — the exact bug #26 fixed for item-item. The exclusion belongs to the
    negatives pool, applied afterwards.
    """
    train = _train_frame()
    source = _sasrec_source(train)
    by_user = history_index(train)
    user_id = 4
    timestamps, movie_ids = by_user[user_id]
    as_of = int(timestamps[-1])
    prefix = strict_prefix(timestamps, movie_ids, as_of)

    candidates = source.training_candidates(
        [TrainingQuery(user_id=user_id, as_of=as_of, prior_movie_ids=prefix)], 40
    )[0]
    assert set(candidates) & set(int(m) for m in prefix), (
        "training retrieval must be willing to return already-watched items; "
        "otherwise every positive is dropped"
    )
    # Serving retrieval, by contrast, still excludes them.
    assert set(source.holdout_candidates(user_id, 40)).isdisjoint(
        set(train.loc[train["userId"] == user_id, "movieId"].astype(int))
    )


def test_sasrec_routes_an_empty_prefix_to_the_shared_popularity_fallback() -> None:
    """A user with no causal context yet gets what serving would give them."""
    train = _train_frame()
    source = _sasrec_source(train)
    user_id = 6
    timestamps, movie_ids = history_index(train)[user_id]
    as_of = int(timestamps[0])

    candidates = source.training_candidates(
        [
            TrainingQuery(
                user_id=user_id,
                as_of=as_of,
                prior_movie_ids=strict_prefix(timestamps, movie_ids, as_of),
            )
        ],
        10,
    )[0]
    assert candidates == source.popularity.recommend(user_id, 10)
    assert source.routing_counts.empty_prefix == 1
    assert source.routing_counts.learned == 0


def test_runtime_history_matches_the_canonical_construction() -> None:
    """The rebuilt runtime state is `build_user_history`, not a variant of it.

    The artifact carries no histories, so the run rebuilds them; if that rebuild
    differed from the one the retrieval run used, the holdout numbers here would
    not be comparable with the recorded ones. Equality is asserted on a frame the
    model's vocabulary fully covers, which is the full-data case.
    """
    train = _train_frame()
    model = SASRecModel(config=_sasrec_config(), cold_start_threshold=1).fit(train)
    rebuilt, n_unknown = sasrec_ranker.runtime_user_history(train, model)
    assert n_unknown == 0
    assert rebuilt == build_user_history(train, model._item_to_index)


def test_runtime_history_gives_an_unseen_item_the_unknown_token() -> None:
    """A subsampled smoke run may hold an item the pinned model never saw."""
    train = _train_frame()
    model = SASRecModel(config=_sasrec_config(), cold_start_threshold=1).fit(train)
    stranger = pd.DataFrame(
        [{"userId": 0, "movieId": 9_999, "timestamp": _BASE_TS + 500_000}],
    )
    rebuilt, n_unknown = sasrec_ranker.runtime_user_history(
        pd.concat([train, stranger], ignore_index=True), model
    )
    assert n_unknown == 1
    assert rebuilt[0][-1] == model._unknown_index


def test_a_cold_user_gets_the_same_candidates_from_both_arms() -> None:
    """Below the threshold the two arms are the same retriever.

    This is what makes the cold slice interpretable in the step-1 comparison: a
    user under `COLD_START_THRESHOLD` routes to the popularity fallback in both
    arms, and both arms hold the *same* fitted fallback object, so their candidate
    lists are identical and any difference in the cold numbers belongs entirely to
    the booster. Asserted rather than reasoned about, because the whole reading of
    that result rests on it.
    """
    train = _train_frame()
    itemitem_model = ItemItemModel(cold_start_threshold=COLD_START_THRESHOLD).fit(train)
    shared_popularity = itemitem_model._popularity
    sasrec_model = SASRecModel(
        config=_sasrec_config(), cold_start_threshold=COLD_START_THRESHOLD
    ).fit(train)
    sasrec_model._popularity = shared_popularity
    itemitem = ItemItemSource(model=itemitem_model)
    sasrec = SasrecSource(
        model=sasrec_model,
        popularity=shared_popularity,
        manifest=None,  # type: ignore[arg-type]
    )

    # Every fixture user has 15 interactions, so thin one out below the threshold.
    cold_user = 11
    keep = ~((train["userId"] == cold_user) & (train.groupby("userId").cumcount() >= 5))
    thinned = train[keep].reset_index(drop=True)
    itemitem_model.fit(thinned)
    thinned_popularity = itemitem_model._popularity
    sasrec_model._popularity = thinned_popularity
    sasrec_model._user_history, _ = sasrec_ranker.runtime_user_history(thinned, sasrec_model)
    sasrec.popularity = thinned_popularity

    assert not itemitem.served_by_learned_path(cold_user)
    assert not sasrec.served_by_learned_path(cold_user)
    assert itemitem.holdout_candidates(cold_user, 15) == sasrec.holdout_candidates(cold_user, 15)


def test_batched_and_single_retrieval_agree() -> None:
    train = _train_frame()
    model = SASRecModel(config=_sasrec_config(), cold_start_threshold=1).fit(train)
    histories = [[3, 7, 11], [1, 2], [5]]
    batched = model.retrieve_unfiltered(histories, 12)
    singly = [model.retrieve_unfiltered([history], 12)[0] for history in histories]
    assert batched == singly


def test_retrieve_unfiltered_rejects_an_empty_history() -> None:
    train = _train_frame()
    model = SASRecModel(config=_sasrec_config(), cold_start_threshold=1).fit(train)
    with pytest.raises(ValueError, match="at least one movie"):
        model.retrieve_unfiltered([[]], 5)


# --- 3. exclusions match serving (#126) -------------------------------------


def test_no_negative_is_an_item_the_user_had_already_watched() -> None:
    """#126's rule, asserted on the assembled groups of both arms."""
    train = _train_frame()
    movies = _movies()
    feature_index = FeatureIndex.build(train, movies)
    by_user = history_index(train)
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )

    for source in (
        ItemItemSource(model=ItemItemModel().fit(train)),
        _sasrec_source(train),
    ):
        recorder = _RecordingSource(source)
        build_ranker_training_set(
            positives=positives,
            source=recorder,
            feature_index=feature_index,
            history_by_user=by_user,
            n_negatives=5,
            rng=np.random.default_rng(3),
            k_candidates=30,
            batch_size=8,
        )
        assert recorder.queries, "fixture produced no groups"
        for query in recorder.queries:
            watched = {int(movie) for movie in query.prior_movie_ids}
            timestamps, movie_ids = by_user[query.user_id]
            expected = {
                int(movie)
                for movie, stamp in zip(movie_ids, timestamps, strict=True)
                if int(stamp) < query.as_of
            }
            assert watched == expected


def test_every_group_holds_exactly_one_positive() -> None:
    train = _train_frame()
    feature_index = FeatureIndex.build(train, _movies())
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )
    features, groups, labels, dropped = build_ranker_training_set(
        positives=positives,
        source=_sasrec_source(train),
        feature_index=feature_index,
        history_by_user=history_index(train),
        n_negatives=5,
        rng=np.random.default_rng(3),
        k_candidates=30,
        batch_size=8,
    )
    assert groups
    assert sum(groups) == len(features) == len(labels)
    assert int(labels.sum()) == len(groups)
    assert dropped + len(groups) == len(positives)
    offset = 0
    for size in groups:
        assert labels[offset] == 1.0
        assert not labels[offset + 1 : offset + size].any()
        offset += size


# --- 4. determinism ----------------------------------------------------------


def test_construction_is_deterministic_for_a_fixed_seed() -> None:
    train = _train_frame()
    feature_index = FeatureIndex.build(train, _movies())
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )
    history = history_index(train)
    source = _sasrec_source(train)

    def build() -> tuple[pd.DataFrame, list[int], np.ndarray, int]:
        return build_ranker_training_set(
            positives=positives,
            source=source,
            feature_index=feature_index,
            history_by_user=history,
            n_negatives=5,
            rng=np.random.default_rng(5),
            k_candidates=30,
            batch_size=8,
        )

    first, second = build(), build()
    assert first[1] == second[1]
    assert np.array_equal(first[2], second[2])
    pd.testing.assert_frame_equal(first[0], second[0])


# --- guards ------------------------------------------------------------------


def test_runner_refuses_an_exclusion_mismatched_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The #126 toggle is an ablation for `ranker.py`, never for a gated pair."""
    monkeypatch.setattr(sasrec_ranker, "RANKER_APPLY_SERVING_EXCLUSIONS", False)
    with pytest.raises(ExclusionPolicyError):
        sasrec_ranker.main()


def test_booster_directory_is_create_only(tmp_path: Path) -> None:
    """A run never overwrites another run's weights."""
    from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig

    train = _train_frame()
    feature_index = FeatureIndex.build(train, _movies())
    positives = _sample_training_positives(
        train, n_days=365, limit=40, rng=np.random.default_rng(1)
    )
    features, groups, labels, _dropped = build_ranker_training_set(
        positives=positives,
        source=ItemItemSource(model=ItemItemModel().fit(train)),
        feature_index=feature_index,
        history_by_user=history_index(train),
        n_negatives=5,
        rng=np.random.default_rng(2),
        k_candidates=30,
        batch_size=8,
    )
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=2)).fit(features, groups, labels)

    path, digest = sasrec_ranker._save_booster_create_only(ranker, tmp_path / "run")
    assert path.is_file() and len(digest) == 64
    with pytest.raises(FileExistsError):
        sasrec_ranker._save_booster_create_only(ranker, tmp_path / "run")


def test_routing_counts_are_reported_per_arm() -> None:
    counts = RoutingCounts(
        learned=3,
        fallback=1,
        empty_prefix=2,
        learned_with_short_prefix=1,
        learned_below_encoder_window=2,
    )
    assert counts.as_params("training_positives") == {
        "training_positives_learned": 3,
        "training_positives_fallback": 1,
        "training_positives_empty_prefix": 2,
        "training_positives_learned_short_prefix": 1,
        "training_positives_learned_below_encoder_window": 2,
    }


class _RecordingSource:
    """Wraps a source and keeps the queries it was handed, for assertions."""

    def __init__(self, inner: CandidateSource) -> None:
        self._inner = inner
        self.queries: list[TrainingQuery] = []
        self.name = inner.name
        self.candidate_model_tag = inner.candidate_model_tag
        self.routing_counts = inner.routing_counts

    def training_candidates(self, queries: list[TrainingQuery], k: int) -> list[list[int]]:
        self.queries.extend(queries)
        return self._inner.training_candidates(queries, k)

    def holdout_candidates(self, user_id: int, k: int) -> list[int]:
        return self._inner.holdout_candidates(user_id, k)

    def served_by_learned_path(self, user_id: int) -> bool:
        return self._inner.served_by_learned_path(user_id)

    def identity(self) -> dict[str, str]:
        return self._inner.identity()

    def ranking_features(self, row: int, movie_ids: Sequence[int]) -> pd.DataFrame | None:
        return self._inner.ranking_features(row, movie_ids)


def test_cold_start_threshold_is_the_one_the_system_uses() -> None:
    """A guard, not a tautology: both arms read the same module constant."""
    assert sasrec_ranker.COLD_START_THRESHOLD == COLD_START_THRESHOLD
