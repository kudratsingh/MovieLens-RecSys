"""
Unit tests for the two-tower candidate generator.

Two-tower has learned parameters and stochastic training, so unlike the
item-item tests we can't assert exact orderings — but the shape guarantees
match the ``CandidateModel`` contract every candidate generator in the
lineage upholds. Tests mirror test_itemitem.py where the contract is the
same; two extra tests carry the load-bearing invariants ADR 0006's Risks
section named:

  1. ``test_history_is_strictly_past`` — the point-in-time correctness
     canary. A hand-built fixture where the expected history at each
     position is precomputed; any drift in ``build_user_history`` or the
     training-pair construction flips this test.
  2. ``test_converges_on_two_cluster_synthetic`` — smoke test that the
     sampled-softmax loss actually pulls same-cluster items together.
     Guards against the loss being wired up with the wrong sign for the
     log-uniform correction (the failure mode where popularity gets
     inverted, per ADR 0006 Risk #1).
"""

from __future__ import annotations

import faiss
import numpy as np
import pandas as pd
import pytest
import torch

from src.evaluation.protocol import evaluate
from src.models.candidates.twotower import (
    TwoTowerConfig,
    TwoTowerModel,
    build_user_history,
)


def _ratings(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    """Rows are (userId, movieId, timestamp)."""
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


# Two-cluster synthetic train set. Same shape as test_itemitem's fixture —
# an "action" cluster ({100..104}) and a "drama" cluster ({200..204}) —
# augmented with timestamps because the two-tower is time-aware.
# Timestamps are per-user increasing so the (userId, timestamp) sort in
# build_user_history produces a well-defined chronological order.
# Every user in the fixture below has three interactions, which is under
# ADR 0001's `COLD_START_THRESHOLD` — the constructor default since the owner's
# 2026-08-30 decision. A model built with the default answers every one of them
# from its popularity fallback, so these tests pass `cold_start_threshold=None`,
# the documented index-membership opt-out, and keep measuring retrieval rather
# than the fallback in front of it. Where the *default* sends this fixture is
# asserted by the routing test below, and exhaustively in
# `tests/unit/test_candidate_routing.py`.
_SYNTHETIC_TRAIN = _ratings(
    [
        (1, 100, 10),
        (1, 101, 20),
        (1, 102, 30),
        (2, 100, 11),
        (2, 101, 21),
        (2, 103, 31),
        (3, 100, 12),
        (3, 102, 22),
        (3, 104, 32),
        (4, 200, 13),
        (4, 201, 23),
        (4, 202, 33),
        (5, 200, 14),
        (5, 201, 24),
        (5, 203, 34),
        (6, 200, 15),
        (6, 202, 25),
        (6, 204, 35),
        (7, 100, 16),
        (7, 200, 26),
        (8, 101, 17),
        (8, 201, 27),
    ]
)


# Small config that trains fast enough for CI — the point of the smoke
# tests isn't recall quality, it's that the loss actually descends and
# the model class doesn't crash. faiss_nlist gets capped internally to
# n_items // 4 so IVF-Flat trains cleanly on a 10-item fixture.
_FAST_CONFIG = TwoTowerConfig(
    embedding_dim=16,
    history_window=5,
    batch_size=8,
    num_sampled=16,
    epochs=1,
    learning_rate=1e-2,
    faiss_nlist=4,
    faiss_nprobe=2,
    seed=42,
)


def test_fit_returns_self_for_chaining() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert isinstance(model, TwoTowerModel)


def test_recommendations_are_valid_movie_ids() -> None:
    # Every returned id must be one that existed in train. Catches the
    # dense-index → movieId inverse map going wrong — the same bug that
    # would surface as a KeyError at serving time.
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    catalog = set(_SYNTHETIC_TRAIN["movieId"].unique())
    recs = model.recommend(user_id=1, k=5)
    assert all(item in catalog for item in recs)


def test_recommendations_exclude_already_seen_items() -> None:
    # Same leak-prevention guarantee CF and item-item carry. The FAISS
    # results are post-filtered against the user's training history, and
    # the request headroom (k + |seen|) makes sure we don't shrink below k.
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    seen = {100, 101, 102}
    recs = model.recommend(user_id=1, k=5)
    assert not (set(recs) & seen)


def _sasrec_style_exact_recommend(
    model: TwoTowerModel,
    history_movie_ids: list[int],
    excluded_movie_ids: set[int],
    k: int,
) -> list[int]:
    """Independent exact-FAISS path with SASRec's row-to-dense conversion."""
    assert model._item_tower is not None
    n_items = len(model._index_to_item)
    with torch.no_grad():
        item_vectors = (
            model._item_tower(torch.arange(1, n_items + 1, dtype=torch.long))
            .numpy()
            .astype(np.float32)
        )
    index = faiss.IndexFlatIP(model.config.embedding_dim)
    index.add(item_vectors)
    dense_history = [model._item_to_index[movie_id] for movie_id in history_movie_ids]
    window = dense_history[-model.config.history_window :]
    padded = [0] * (model.config.history_window - len(window)) + window
    with torch.no_grad():
        query = model._encode_user(torch.tensor([padded], dtype=torch.long)).numpy()
    request = min(k + len(excluded_movie_ids), n_items)
    _scores, row_indices = index.search(query.astype(np.float32), request)
    return [
        movie_id
        for row_index in row_indices[0]
        if row_index >= 0
        and (movie_id := int(model._index_to_item[int(row_index) + 1])) not in excluded_movie_ids
    ][:k]


def test_exact_faiss_path_matches_sasrec_row_mapping_and_evaluator() -> None:
    config = TwoTowerConfig(**{**_FAST_CONFIG.as_params(), "faiss_exact": True})
    model = TwoTowerModel(config=config, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    user_ids = [1, 4, 7]
    direct = model.recommend_for_users(user_ids, k=5)
    reference = {
        user_id: _sasrec_style_exact_recommend(
            model,
            [model._index_to_item[dense] for dense in model._user_history[user_id]],
            set(model._popularity.user_history[user_id]),
            5,
        )
        for user_id in user_ids
    }

    assert direct == reference
    holdout = {user_id: {reference[user_id][0]} for user_id in user_ids}
    counts = {user_id: len(model._user_history[user_id]) for user_id in user_ids}
    assert evaluate(direct, holdout, counts, k=5) == evaluate(reference, holdout, counts, k=5)


def test_tiny_model_overfits_recall_at_10() -> None:
    n_users = 200
    rows = [
        (user_id, 1_000 + 2 * user_id + offset, 100 + 100 * offset)
        for user_id in range(n_users)
        for offset in range(2)
    ]
    train = _ratings(rows)
    losses: list[float] = []
    config = TwoTowerConfig(
        embedding_dim=128,
        history_window=1,
        batch_size=n_users,
        num_sampled=1,
        epochs=100,
        learning_rate=0.05,
        logit_temperature=0.02,
        correct_positive_logit=True,
        use_item_features=False,
        hard_negative_count=0,
        early_stopping_patience=0,
        faiss_exact=True,
        seed=42,
    )
    model = TwoTowerModel(config=config, cold_start_threshold=None).fit(
        train, on_epoch=lambda _epoch, loss: losses.append(loss)
    )
    recommendations = {
        user_id: _sasrec_style_exact_recommend(
            model,
            [1_000 + 2 * user_id],
            {1_000 + 2 * user_id},
            10,
        )
        for user_id in range(n_users)
    }
    holdout = {user_id: {1_001 + 2 * user_id} for user_id in range(n_users)}
    counts = {user_id: 10 for user_id in range(n_users)}
    result = evaluate(recommendations, holdout, counts, k=10)

    # CPU kernels differ slightly across supported Torch releases; both the
    # local 0.000498 and CI's 0.00231 are effectively zero for this objective.
    assert min(losses) < 0.01
    assert result.warm.recall == 1.0


def test_returns_at_most_k_items() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=3)
    assert len(recs) <= 3


def test_unknown_user_falls_through_to_popularity() -> None:
    # ADR 0001 / ADR 0006 fallback path. User 999 was never in train and
    # so has no history to encode; recommend must route to the embedded
    # popularity model and return its top-k.
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    tt_recs = model.recommend(user_id=999, k=3)
    pop_recs = model._popularity.recommend(user_id=999, k=3)
    assert tt_recs == pop_recs
    assert len(tt_recs) > 0


def test_empty_train_handles_gracefully() -> None:
    # temporal_split could produce an empty train slice on edge-case data.
    # The tower can't fit but the model must still return a list (empty).
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_ratings([]))
    assert model.recommend(user_id=1, k=10) == []


def test_recommend_for_users_returns_one_list_per_user() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    out = model.recommend_for_users(user_ids=[1, 2, 999], k=3)
    assert set(out.keys()) == {1, 2, 999}
    assert all(len(v) <= 3 for v in out.values())


def test_was_served_by_twotower_matches_recommend_routing() -> None:
    # Predicate contract the training pipeline uses for per-policy MLflow
    # attribution. Must mirror the recommend() branch exactly, under whichever
    # policy the model was built with.
    index_model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(
        _SYNTHETIC_TRAIN
    )
    assert index_model.was_served_by_twotower(1) is True
    assert index_model.was_served_by_twotower(999) is False

    # And under the shipped default, this fixture's three-interaction users are
    # below the threshold, so the fallback answers them and the predicate says
    # so rather than reporting a learned serve the model did not make.
    default_model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    assert default_model.was_served_by_twotower(1) is False
    assert default_model.recommend(user_id=1, k=3) == default_model._popularity.recommend(
        user_id=1, k=3
    )


def test_was_served_by_twotower_false_for_empty_train() -> None:
    # No tower means every user routes to popularity, and the predicate
    # must acknowledge that even for ids the caller might assume are known.
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_ratings([]))
    assert model.was_served_by_twotower(1) is False


def test_history_is_strictly_past() -> None:
    """Point-in-time correctness canary — ADR 0006's severity-highest test.

    Hand-built fixture where each user's chronological history is known
    exactly. ``build_user_history`` must return the items in ascending
    timestamp order and never include the current or future positions.
    This is a strict-equality check against a precomputed expected list,
    not a "history is small enough" heuristic — because "small enough"
    is what silently allows leakage back in.
    """
    train = _ratings(
        [
            (42, 900, 100),
            (42, 901, 200),
            (42, 902, 300),
            (42, 903, 400),
            # Deliberately out-of-order rows to exercise the sort:
            (43, 950, 500),
            (43, 951, 400),  # earlier timestamp than the row above
            (43, 952, 600),
        ]
    )
    # movieId → dense index; 0 reserved for padding.
    item_to_index = {900: 1, 901: 2, 902: 3, 903: 4, 950: 5, 951: 6, 952: 7}
    history = build_user_history(train, item_to_index)

    # User 42's chronological history is 900, 901, 902, 903 (already in
    # increasing timestamp order). Slicing at position i must yield the
    # dense indices for the strictly-earlier items.
    assert history[42] == [1, 2, 3, 4]

    # User 43's rows are out of timestamp order in the input; a correct
    # sort produces 951 (t=400), 950 (t=500), 952 (t=600) → [6, 5, 7].
    assert history[43] == [6, 5, 7]


def test_training_pair_history_excludes_positive() -> None:
    """The (history, positive) pairs must never include the positive in the
    history — the invariant that keeps offline recall from being inflated by
    trivial self-reconstruction. Position 0 (no history) is dropped."""
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None)
    # Prime the model's internal state as .fit() would, then call the
    # private builder directly so we can inspect the tensors.
    movie_ids = sorted(_SYNTHETIC_TRAIN["movieId"].unique())
    model._item_to_index = {mid: i + 1 for i, mid in enumerate(movie_ids)}
    model._index_to_item = {v: k for k, v in model._item_to_index.items()}
    model._user_history = build_user_history(_SYNTHETIC_TRAIN, model._item_to_index)

    histories, positives = model._build_training_pairs(_SYNTHETIC_TRAIN)

    # Every row's positive must not appear in that row's history slice.
    # (Padding is 0 and never equals a positive since dense indices start
    # at 1.)
    for hist_row, pos in zip(histories.tolist(), positives.tolist()):
        assert pos not in hist_row, f"positive {pos} leaked into its own history {hist_row}"


def test_equal_timestamp_items_do_not_enter_each_others_history() -> None:
    """Only strictly earlier timestamps are visible to a training target."""
    train = _ratings(
        [
            (1, 100, 10),
            (1, 101, 20),
            (1, 102, 20),
            (1, 103, 30),
            (2, 200, 40),
            (2, 201, 40),  # no earlier timestamp: neither row is trainable
        ]
    )
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None)
    movie_ids = sorted(train["movieId"].unique())
    model._item_to_index = {movie_id: i + 1 for i, movie_id in enumerate(movie_ids)}
    model._index_to_item = {dense: movie for movie, dense in model._item_to_index.items()}
    model._user_history = build_user_history(train, model._item_to_index)

    histories, positives = model._build_training_pairs(train)
    rows = list(zip(histories.tolist(), positives.tolist()))

    dense_100 = model._item_to_index[100]
    dense_101 = model._item_to_index[101]
    dense_102 = model._item_to_index[102]
    dense_103 = model._item_to_index[103]
    assert len(rows) == 3
    assert [positive for _history, positive in rows] == [dense_101, dense_102, dense_103]
    assert [value for value in rows[0][0] if value] == [dense_100]
    assert [value for value in rows[1][0] if value] == [dense_100]
    assert [value for value in rows[2][0] if value] == [dense_100, dense_101, dense_102]


def test_converges_on_two_cluster_synthetic() -> None:
    """Smoke test that sampled softmax pulls same-cluster items together.

    After a short training run, the mean cosine similarity between items
    inside the action cluster should exceed the mean cosine similarity
    between action items and drama items. Guards against the log-uniform
    correction being wired with the wrong sign (ADR 0006 Risk #1) — an
    inverted correction pushes popular items apart, and same-cluster
    items in the synthetic set are all "popular" within their cluster,
    so the test flips.
    """
    config = TwoTowerConfig(
        embedding_dim=16,
        history_window=5,
        batch_size=8,
        num_sampled=16,
        epochs=5,  # a bit more than the fast fixture — we need actual convergence
        learning_rate=5e-2,
        faiss_nlist=4,
        faiss_nprobe=2,
        seed=0,
    )
    model = TwoTowerModel(config=config, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)

    action_ids = [100, 101, 102, 103, 104]
    drama_ids = [200, 201, 202, 203, 204]
    action_dense = torch.tensor([model._item_to_index[m] for m in action_ids], dtype=torch.long)
    drama_dense = torch.tensor([model._item_to_index[m] for m in drama_ids], dtype=torch.long)

    assert model._item_tower is not None
    with torch.no_grad():
        action_vecs = model._item_tower(action_dense)  # (5, d), L2-normalized
        drama_vecs = model._item_tower(drama_dense)

    within_action = (action_vecs @ action_vecs.T).mean().item()
    across = (action_vecs @ drama_vecs.T).mean().item()
    assert within_action > across, (
        f"expected same-cluster > cross-cluster similarity; "
        f"got within_action={within_action:.4f} across={across:.4f}"
    )


def test_padding_row_stays_zero_after_training() -> None:
    """Padding index 0 must be all-zero after training — the mean-pool
    masking trick assumes it. If padding drifts (e.g. an optimizer with
    weight decay applied to the whole embedding table without a
    padding-aware exclusion), variable-length users get a spurious
    padding contribution to their user vector.
    """
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert model._item_tower is not None
    padding_vec = model._item_tower.embed.weight[0]
    assert torch.allclose(padding_vec, torch.zeros_like(padding_vec))


@pytest.mark.parametrize("k", [1, 5, 10])
def test_recommend_length_bounded_by_k(k: int) -> None:
    """Basic parametric sanity — recommend never returns more than k."""
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=k)
    assert len(recs) <= k


def test_hard_negative_mining_starts_after_warmup_and_fills_slots() -> None:
    config = TwoTowerConfig(
        embedding_dim=16,
        history_window=5,
        batch_size=8,
        num_sampled=16,
        epochs=2,
        learning_rate=1e-2,
        hard_negative_count=3,
        hard_negative_pool_size=12,
        hard_negative_warmup_epochs=1,
        faiss_nlist=4,
        faiss_nprobe=2,
        seed=42,
    )
    model = TwoTowerModel(config=config, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    stats = model.hard_negative_stats()
    assert stats["hard_negative_slots"] > 0
    assert 0 < stats["hard_negative_selected"] <= stats["hard_negative_slots"]
    assert 0.0 < stats["hard_negative_fill_rate"] <= 1.0


def test_fit_records_deterministic_item_feature_schema() -> None:
    movies = pd.DataFrame(
        {
            "movieId": sorted(_SYNTHETIC_TRAIN["movieId"].unique()),
            "title": [f"Movie {index} ({2000 + index})" for index in range(10)],
            "genres": ["Action|Comedy"] * 5 + ["Drama"] * 5,
        }
    )
    model = TwoTowerModel(config=_FAST_CONFIG, cold_start_threshold=None).fit(
        _SYNTHETIC_TRAIN, movies=movies
    )
    params = model.item_feature_params()
    assert params["item_features_fitted"] is True
    assert params["item_feature_genre_count"] == 3
    assert params["item_feature_count"] == 6
    assert len(str(params["item_feature_schema_sha256"])) == 64


def test_v2_fit_is_bitwise_deterministic_for_same_seed() -> None:
    config = TwoTowerConfig(
        embedding_dim=16,
        history_window=5,
        batch_size=8,
        num_sampled=16,
        epochs=2,
        learning_rate=1e-2,
        hard_negative_count=3,
        hard_negative_pool_size=12,
        hard_negative_warmup_epochs=1,
        faiss_exact=True,
        seed=42,
    )
    first = TwoTowerModel(config=config, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    second = TwoTowerModel(config=config, cold_start_threshold=None).fit(_SYNTHETIC_TRAIN)
    assert first._item_tower is not None
    assert second._item_tower is not None
    for name, first_value in first._item_tower.state_dict().items():
        assert torch.equal(first_value, second._item_tower.state_dict()[name])
    assert first.hard_negative_stats() == second.hard_negative_stats()
    assert first.recommend_for_users([1, 4, 7], k=3) == second.recommend_for_users([1, 4, 7], k=3)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hard_negative_count": -1}, "non-negative"),
        (
            {"hard_negative_count": 5, "hard_negative_pool_size": 4},
            "pool_size",
        ),
        ({"hard_negative_warmup_epochs": -1}, "warmup"),
    ],
)
def test_invalid_hard_negative_config_fails_loudly(overrides: dict[str, int], message: str) -> None:
    config = TwoTowerConfig(**overrides)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        TwoTowerModel(config=config).fit(_SYNTHETIC_TRAIN)
