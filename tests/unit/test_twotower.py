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

import pandas as pd
import pytest
import torch

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
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    assert isinstance(model, TwoTowerModel)


def test_recommendations_are_valid_movie_ids() -> None:
    # Every returned id must be one that existed in train. Catches the
    # dense-index → movieId inverse map going wrong — the same bug that
    # would surface as a KeyError at serving time.
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    catalog = set(_SYNTHETIC_TRAIN["movieId"].unique())
    recs = model.recommend(user_id=1, k=5)
    assert all(item in catalog for item in recs)


def test_recommendations_exclude_already_seen_items() -> None:
    # Same leak-prevention guarantee CF and item-item carry. The FAISS
    # results are post-filtered against the user's training history, and
    # the request headroom (k + |seen|) makes sure we don't shrink below k.
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    seen = {100, 101, 102}
    recs = model.recommend(user_id=1, k=5)
    assert not (set(recs) & seen)


def test_returns_at_most_k_items() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=3)
    assert len(recs) <= 3


def test_unknown_user_falls_through_to_popularity() -> None:
    # ADR 0001 / ADR 0006 fallback path. User 999 was never in train and
    # so has no history to encode; recommend must route to the embedded
    # popularity model and return its top-k.
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    tt_recs = model.recommend(user_id=999, k=3)
    pop_recs = model._popularity.recommend(user_id=999, k=3)
    assert tt_recs == pop_recs
    assert len(tt_recs) > 0


def test_empty_train_handles_gracefully() -> None:
    # temporal_split could produce an empty train slice on edge-case data.
    # The tower can't fit but the model must still return a list (empty).
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_ratings([]))
    assert model.recommend(user_id=1, k=10) == []


def test_recommend_for_users_returns_one_list_per_user() -> None:
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    out = model.recommend_for_users(user_ids=[1, 2, 999], k=3)
    assert set(out.keys()) == {1, 2, 999}
    assert all(len(v) <= 3 for v in out.values())


def test_was_served_by_twotower_matches_recommend_routing() -> None:
    # Predicate contract the training pipeline uses for per-policy MLflow
    # attribution. Must mirror the recommend() branch exactly: True iff the
    # tower is fitted, the FAISS index exists, and the user has training
    # history.
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    assert model.was_served_by_twotower(1) is True
    assert model.was_served_by_twotower(999) is False


def test_was_served_by_twotower_false_for_empty_train() -> None:
    # No tower means every user routes to popularity, and the predicate
    # must acknowledge that even for ids the caller might assume are known.
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_ratings([]))
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
    model = TwoTowerModel(config=_FAST_CONFIG)
    # Prime the model's internal state as .fit() would, then call the
    # private builder directly so we can inspect the tensors.
    movie_ids = sorted(_SYNTHETIC_TRAIN["movieId"].unique())
    model._item_to_index = {mid: i + 1 for i, mid in enumerate(movie_ids)}
    model._index_to_item = {v: k for k, v in model._item_to_index.items()}
    model._user_history = build_user_history(_SYNTHETIC_TRAIN, model._item_to_index)

    histories, positives = model._build_training_pairs()

    # Every row's positive must not appear in that row's history slice.
    # (Padding is 0 and never equals a positive since dense indices start
    # at 1.)
    for hist_row, pos in zip(histories.tolist(), positives.tolist()):
        assert pos not in hist_row, f"positive {pos} leaked into its own history {hist_row}"


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
    model = TwoTowerModel(config=config).fit(_SYNTHETIC_TRAIN)

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
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    assert model._item_tower is not None
    padding_vec = model._item_tower.embed.weight[0]
    assert torch.allclose(padding_vec, torch.zeros_like(padding_vec))


@pytest.mark.parametrize("k", [1, 5, 10])
def test_recommend_length_bounded_by_k(k: int) -> None:
    """Basic parametric sanity — recommend never returns more than k."""
    model = TwoTowerModel(config=_FAST_CONFIG).fit(_SYNTHETIC_TRAIN)
    recs = model.recommend(user_id=1, k=k)
    assert len(recs) <= k
