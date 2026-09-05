"""Correctness tests for ADR 0018 increment 1's two ranker features.

The features are two scalars, which makes them easy to compute and easy to
compute *wrongly* in ways no aggregate metric would reveal. Five claims are worth
asserting, because a six-figure-positive training run cannot show any of them:

1. **The score is the score FAISS ranked on.** Not a re-derivation that agrees
   today: the same normalised user vector, the same item embedding, the same
   inner product, and the retrieved top-1 is the argmax of the column.
2. **The query is point-in-time.** The vector behind a positive's features is
   encoded from the strict prefix and nothing later, and events sharing a
   timestamp are never context for one another.
3. **The construction is deterministic.** Same seed, same fixture, identical
   feature frame to the bit.
4. **The fallback route is untouched.** The item-item source contributes no
   extra columns and its frame is still the eight aggregates in order.
5. **Missingness is explicit.** No encodable prefix, or a candidate outside the
   encoder's vocabulary, yields NaN rather than a fabricated similarity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.feature_contract import (
    FEATURE_COLUMNS,
    LEARNED_ROUTE_FEATURE_COLUMNS,
    SASREC_SCORE_COLUMNS,
)
from src.features import FeatureIndex
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec import SASRecConfig, SASRecModel
from src.models.candidates.sasrec_ranking_features import SasrecScoreFeatures, missing_frame
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training.ranker import _sample_training_positives
from src.training.sasrec_ranker import (
    ItemItemSource,
    SasrecSource,
    TrainingQuery,
    build_ranker_training_set,
    history_index,
    strict_prefix,
)

_BASE_TS = 1_500_000_000


def _train_frame(seed: int = 0) -> pd.DataFrame:
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


def _movies(n_items: int = 40) -> pd.DataFrame:
    return pd.DataFrame({"movieId": list(range(n_items)), "genres": ["Action|Drama"] * n_items})


def _config() -> SASRecConfig:
    return SASRecConfig(
        max_sequence_length=5,
        hidden_dim=8,
        num_blocks=1,
        num_heads=2,
        feedforward_dim=16,
        dropout=0.0,
        negative_count=2,
        batch_size=8,
        epochs=1,
        faiss_exact=True,
        seed=42,
    )


def _sasrec_source(train: pd.DataFrame, *, threshold: int | None = 1) -> SasrecSource:
    model = SASRecModel(config=_config(), cold_start_threshold=threshold).fit(train)
    popularity = PopularityModel().fit(train)
    model._popularity = popularity
    source = SasrecSource(
        model=model,
        popularity=popularity,
        manifest=None,  # type: ignore[arg-type]
    )
    source.score_features = SasrecScoreFeatures(model)
    return source


# --- 1. the score is the score FAISS ranked on -------------------------------


def test_score_is_the_retrieval_score() -> None:
    """The feature reproduces the inner product the index searched with.

    Both sides are L2-normalised at the retrieval boundary, so the score is the
    cosine — and the candidate SASRec put first must be the one that maximises
    it. If the feature were computed from a differently-shaped encode, or against
    the unnormalised item matrix, this is where it would show.
    """
    train = _train_frame()
    source = _sasrec_source(train)
    model = source.model
    features = SasrecScoreFeatures(model)

    history = [int(movie) for movie in train[train["userId"] == 3]["movieId"].tolist()]
    normalized, unnormalized = model.encode_histories([history])
    candidates = model.retrieve_from_queries(normalized, 10)[0]

    score, logit = features.scores_for(normalized[0], unnormalized[0], candidates)
    assert not np.isnan(score).any()
    # Exact-index retrieval is by descending inner product, so the column is
    # non-increasing over the returned order.
    assert np.all(np.diff(score) <= 1e-6)
    assert score[0] == pytest.approx(score.max())

    # And the value itself is the normalised inner product, recomputed by hand.
    dense = model.dense_index_for(candidates[0])
    assert dense is not None
    normalized_items, unnormalized_items = model.item_matrices()
    assert score[0] == pytest.approx(float(normalized_items[dense - 1] @ normalized[0]), abs=1e-6)
    assert logit[0] == pytest.approx(
        float(unnormalized_items[dense - 1] @ unnormalized[0]), abs=1e-5
    )


def test_logit_is_not_the_score() -> None:
    """The unnormalised quantity carries the magnitude the cosine discards.

    Worth asserting rather than assuming: if `item_matrices` returned the same
    matrix twice, or the encoder's normalisation leaked into the raw vector, the
    two columns would be a monotone rescaling of one another and one of them
    would be dead weight in the booster.
    """
    train = _train_frame()
    model = _sasrec_source(train).model
    features = SasrecScoreFeatures(model)
    history = [int(movie) for movie in train[train["userId"] == 5]["movieId"].tolist()]
    normalized, unnormalized = model.encode_histories([history])
    candidates = model.retrieve_from_queries(normalized, 20)[0]

    score, logit = features.scores_for(normalized[0], unnormalized[0], candidates)
    assert not np.allclose(score, logit)
    # A pure rescaling would leave the ratio constant across candidates.
    ratios = logit / score
    assert float(ratios.max() - ratios.min()) > 1e-6


def test_batched_and_single_encodes_agree_within_the_adr_tolerance() -> None:
    """The offline path batches and the online path does not.

    ADR 0018 names the tolerances precisely because float32 matmul is not
    associative, so this asserts the skew is inside them rather than pretending
    it is zero: 1e-5 absolute on the bounded score, 1e-4 relative on the logit.
    """
    train = _train_frame()
    model = _sasrec_source(train).model
    features = SasrecScoreFeatures(model)
    histories = [
        [int(movie) for movie in train[train["userId"] == user]["movieId"].tolist()]
        for user in (1, 2, 3, 4)
    ]
    batched_norm, batched_raw = model.encode_histories(histories)

    for row, history in enumerate(histories):
        dense = [model.dense_index_for(movie) or model._unknown_index for movie in history]
        single_norm, single_raw = model.encode_dense_history(dense)
        candidates = model.retrieve_from_queries(batched_norm[row : row + 1], 10)[0]

        batch_score, batch_logit = features.scores_for(
            batched_norm[row], batched_raw[row], candidates
        )
        single_score, single_logit = features.scores_for(single_norm[0], single_raw[0], candidates)
        assert np.allclose(batch_score, single_score, atol=1e-5, rtol=0.0)
        assert np.allclose(batch_logit, single_logit, rtol=1e-4, atol=0.0)


# --- 2. the query is point-in-time -------------------------------------------


def test_training_features_use_only_the_strict_prefix() -> None:
    """A positive's features are computed from history strictly before it.

    The assertion is against a *recomputation from the prefix*, not against an
    inspection of the code path: the group's score column must equal what the
    encoder says about the items available at that moment, and nothing about the
    items that came after would reproduce it.
    """
    train = _train_frame()
    movies = _movies()
    source = _sasrec_source(train)
    feature_index = FeatureIndex.build(train, movies)
    history = history_index(train)
    positives = _sample_training_positives(train, n_days=365, limit=8, rng=np.random.default_rng(3))

    features_df, group_sizes, _labels, _dropped = build_ranker_training_set(
        positives=positives,
        source=source,
        feature_index=feature_index,
        history_by_user=history,
        n_negatives=4,
        rng=np.random.default_rng(5),
        k_candidates=30,
        batch_size=64,
    )
    assert list(features_df.columns) == LEARNED_ROUTE_FEATURE_COLUMNS

    # Rebuild the first group's expected score column from its own strict prefix.
    first = positives.iloc[0]
    timestamps, movie_ids = history[int(first.userId)]
    prefix = strict_prefix(timestamps, movie_ids, int(first.timestamp))
    normalized, unnormalized = source.model.encode_histories([[int(m) for m in prefix]])
    scorer = SasrecScoreFeatures(source.model)

    group_items = _first_group_items(
        source, feature_index, history, positives, seed=5, k_candidates=30
    )
    expected, _ = scorer.scores_for(normalized[0], unnormalized[0], group_items)
    actual = features_df[SASREC_SCORE_COLUMNS[0]].to_numpy()[: group_sizes[0]]
    assert np.allclose(actual, expected, atol=1e-5, rtol=0.0)


def _first_group_items(
    source: SasrecSource,
    feature_index: FeatureIndex,
    history: dict[int, tuple[np.ndarray, np.ndarray]],
    positives: pd.DataFrame,
    *,
    seed: int,
    k_candidates: int,
) -> list[int]:
    """Replay the first group's item order so its features can be recomputed.

    The negatives are drawn from a seeded stream in positive order, so the only
    way to know which items landed in group one is to draw them the same way.
    """
    rng = np.random.default_rng(seed)
    first = positives.iloc[0]
    user_id = int(first.userId)
    as_of = int(first.timestamp)
    timestamps, movie_ids = history[user_id]
    prefix = strict_prefix(timestamps, movie_ids, as_of)
    candidates = source.training_candidates(
        [TrainingQuery(user_id=user_id, as_of=as_of, prior_movie_ids=prefix)], k_candidates
    )[0]
    positive = int(first.movieId)
    excluded = {int(movie) for movie in prefix}
    pool = [c for c in candidates if c != positive and c not in excluded]
    if len(pool) < 4:
        return [positive, *pool]
    index = rng.choice(len(pool), size=4, replace=False)
    return [positive, *[pool[int(i)] for i in index]]


def test_equal_timestamp_events_are_not_context_for_one_another() -> None:
    """A positive whose entire prior history shares its timestamp has no prefix.

    `searchsorted(..., "left")` cuts *before* a run of equal timestamps, so a
    user whose first three events land in the same second genuinely has no causal
    context for any of them — and the feature says so with the missing sentinel
    instead of quietly encoding the siblings.
    """
    train = _train_frame()
    source = _sasrec_source(train)
    assert source.score_features is not None

    timestamps = np.array([_BASE_TS, _BASE_TS, _BASE_TS], dtype=np.int64)
    movie_ids = np.array([5, 3, 9], dtype=np.int64)
    prefix = strict_prefix(timestamps, movie_ids, _BASE_TS)
    assert prefix.size == 0

    query = TrainingQuery(user_id=0, as_of=_BASE_TS, prior_movie_ids=prefix)
    source.training_candidates([query], 10)
    frame = source.ranking_features(0, [1, 2, 3])
    assert frame is not None
    assert frame[SASREC_SCORE_COLUMNS[0]].isna().all()
    assert frame[SASREC_SCORE_COLUMNS[1]].isna().all()
    assert source.routing_counts.empty_prefix == 1


# --- 3. determinism ----------------------------------------------------------


def test_feature_construction_is_deterministic() -> None:
    train = _train_frame()
    movies = _movies()
    feature_index = FeatureIndex.build(train, movies)
    history = history_index(train)
    positives = _sample_training_positives(train, n_days=365, limit=8, rng=np.random.default_rng(3))

    def build() -> pd.DataFrame:
        return build_ranker_training_set(
            positives=positives,
            source=_sasrec_source(train),
            feature_index=feature_index,
            history_by_user=history,
            n_negatives=4,
            rng=np.random.default_rng(5),
            k_candidates=30,
            batch_size=64,
        )[0]

    pd.testing.assert_frame_equal(build(), build())


# --- 4. the fallback route is untouched --------------------------------------


def test_item_item_source_contributes_no_extra_columns() -> None:
    """The fallback contract is still the eight aggregates, in order.

    A user below the threshold routes to popularity precisely because their
    history is too thin to encode; giving that booster a sequence column it can
    never populate online is the "right model, wrong columns" failure ADR 0018
    names as a new class.
    """
    train = _train_frame()
    movies = _movies()
    source = ItemItemSource(model=ItemItemModel().fit(train))
    assert source.ranking_features(0, [1, 2, 3]) is None

    features_df, _groups, _labels, _dropped = build_ranker_training_set(
        positives=_sample_training_positives(
            train, n_days=365, limit=8, rng=np.random.default_rng(3)
        ),
        source=source,
        feature_index=FeatureIndex.build(train, movies),
        history_by_user=history_index(train),
        n_negatives=4,
        rng=np.random.default_rng(5),
        k_candidates=30,
    )
    assert list(features_df.columns) == FEATURE_COLUMNS


def test_sasrec_source_without_score_features_is_pr_151_behaviour() -> None:
    """Leaving `score_features` unset reproduces the eight-column arm exactly."""
    train = _train_frame()
    model = SASRecModel(config=_config(), cold_start_threshold=1).fit(train)
    popularity = PopularityModel().fit(train)
    model._popularity = popularity
    source = SasrecSource(model=model, popularity=popularity, manifest=None)  # type: ignore[arg-type]

    features_df, _groups, _labels, _dropped = build_ranker_training_set(
        positives=_sample_training_positives(
            train, n_days=365, limit=8, rng=np.random.default_rng(3)
        ),
        source=source,
        feature_index=FeatureIndex.build(train, _movies()),
        history_by_user=history_index(train),
        n_negatives=4,
        rng=np.random.default_rng(5),
        k_candidates=30,
    )
    assert list(features_df.columns) == FEATURE_COLUMNS


# --- 5. missingness is explicit ----------------------------------------------


def test_unknown_candidate_takes_the_missing_sentinel() -> None:
    """A candidate the encoder never saw gets no opinion, not a zero.

    Zero is a real cosine — orthogonality — and would be a claim about the pair.
    NaN is the absence of a claim, which is what LightGBM's missing handling is
    for.
    """
    train = _train_frame()
    model = _sasrec_source(train).model
    features = SasrecScoreFeatures(model)
    history = [int(movie) for movie in train[train["userId"] == 2]["movieId"].tolist()]
    normalized, unnormalized = model.encode_histories([history])

    known = model.retrieve_from_queries(normalized, 3)[0]
    score, logit = features.scores_for(normalized[0], unnormalized[0], [*known, 999_999])
    assert not np.isnan(score[:-1]).any()
    assert np.isnan(score[-1]) and np.isnan(logit[-1])


def test_missing_frame_is_the_contract_shape() -> None:
    frame = missing_frame(3)
    assert list(frame.columns) == SASREC_SCORE_COLUMNS
    assert frame.isna().all().all()


def test_no_user_vector_means_no_opinion() -> None:
    train = _train_frame()
    features = SasrecScoreFeatures(_sasrec_source(train).model)
    score, logit = features.scores_for(None, None, [1, 2, 3])
    assert np.isnan(score).all() and np.isnan(logit).all()


# --- the booster carries its own contract ------------------------------------


def test_learned_route_booster_reads_ten_columns() -> None:
    """A ten-column booster fits, scores and reports importances for ten."""
    rng = np.random.default_rng(0)
    rows = 200
    frame = pd.DataFrame(
        rng.random((rows, len(LEARNED_ROUTE_FEATURE_COLUMNS))),
        columns=LEARNED_ROUTE_FEATURE_COLUMNS,
    )
    groups = [5] * (rows // 5)
    labels = np.tile([1.0, 0.0, 0.0, 0.0, 0.0], rows // 5)

    ranker = LGBMRanker(
        config=LGBMRankerConfig(num_boost_round=10),
        feature_columns=list(LEARNED_ROUTE_FEATURE_COLUMNS),
    ).fit(frame, groups, labels)
    assert set(ranker.feature_importances()) == set(LEARNED_ROUTE_FEATURE_COLUMNS)
    assert len(ranker.predict(frame)) == rows


def test_extra_columns_are_dropped_by_the_fallback_contract() -> None:
    """One feature frame can serve both routes, because each selects by name.

    This is what lets the per-route composition hand a single frame to whichever
    booster the route chose without building it twice.
    """
    rng = np.random.default_rng(1)
    rows = 100
    frame = pd.DataFrame(
        rng.random((rows, len(LEARNED_ROUTE_FEATURE_COLUMNS))),
        columns=LEARNED_ROUTE_FEATURE_COLUMNS,
    )
    groups = [5] * (rows // 5)
    labels = np.tile([1.0, 0.0, 0.0, 0.0, 0.0], rows // 5)

    eight = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10)).fit(
        frame[FEATURE_COLUMNS], groups, labels
    )
    # Scoring the wider frame with the narrower booster must work and must give
    # the same answer as scoring the narrow one.
    assert np.array_equal(eight.predict(frame), eight.predict(frame[FEATURE_COLUMNS]))


def test_a_saved_booster_names_its_contract(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The model file says which contract it was fitted against.

    Without this a booster saves `Column_0..N` and every downstream
    booster-vs-contract check degrades to a width check — which cannot tell a
    ten-column learned-route booster from any other ten-column model, and cannot
    catch a reordering at all.
    """
    rng = np.random.default_rng(3)
    rows = 100
    frame = pd.DataFrame(
        rng.random((rows, len(LEARNED_ROUTE_FEATURE_COLUMNS))),
        columns=LEARNED_ROUTE_FEATURE_COLUMNS,
    )
    groups = [5] * (rows // 5)
    labels = np.tile([1.0, 0.0, 0.0, 0.0, 0.0], rows // 5)
    ranker = LGBMRanker(
        config=LGBMRankerConfig(num_boost_round=10),
        feature_columns=list(LEARNED_ROUTE_FEATURE_COLUMNS),
    ).fit(frame, groups, labels)

    path = tmp_path / "learned.txt"
    ranker.save_model(path)
    assert "feature_names=" + " ".join(LEARNED_ROUTE_FEATURE_COLUMNS) in path.read_text()

    reloaded = LGBMRanker.load_model(path, feature_columns=list(LEARNED_ROUTE_FEATURE_COLUMNS))
    assert list(reloaded._booster.feature_name()) == LEARNED_ROUTE_FEATURE_COLUMNS
    assert np.array_equal(reloaded.predict(frame), ranker.predict(frame))


def test_load_model_rejects_a_booster_whose_names_are_the_other_contract(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A named booster loaded under the wrong contract fails at load.

    The width check alone would let an eight-column fallback booster be loaded
    as an eight-column learned-route booster if the contracts ever matched in
    size; the names are what make the two distinguishable.
    """
    rng = np.random.default_rng(4)
    rows = 100
    frame = pd.DataFrame(rng.random((rows, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    groups = [5] * (rows // 5)
    labels = np.tile([1.0, 0.0, 0.0, 0.0, 0.0], rows // 5)
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10)).fit(frame, groups, labels)
    path = tmp_path / "fallback.txt"
    ranker.save_model(path)

    reversed_contract = list(reversed(FEATURE_COLUMNS))
    with pytest.raises(ValueError, match="serving contract requires"):
        LGBMRanker.load_model(path, feature_columns=reversed_contract)


def test_load_model_rejects_a_booster_with_the_wrong_arity(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Right model, wrong columns fails loudly at load, not quietly at predict."""
    rng = np.random.default_rng(2)
    rows = 100
    frame = pd.DataFrame(
        rng.random((rows, len(FEATURE_COLUMNS))),
        columns=FEATURE_COLUMNS,
    )
    groups = [5] * (rows // 5)
    labels = np.tile([1.0, 0.0, 0.0, 0.0, 0.0], rows // 5)
    ranker = LGBMRanker(config=LGBMRankerConfig(num_boost_round=10)).fit(frame, groups, labels)
    path = tmp_path / "ranker.txt"
    ranker.save_model(path)

    assert LGBMRanker.load_model(path).feature_columns == FEATURE_COLUMNS
    with pytest.raises(ValueError, match="serving contract requires 10"):
        LGBMRanker.load_model(path, feature_columns=list(LEARNED_ROUTE_FEATURE_COLUMNS))
