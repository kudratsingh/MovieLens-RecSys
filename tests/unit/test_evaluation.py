import math
import pytest

from src.evaluation.metrics import recall_at_k, ndcg_at_k
from src.evaluation.protocol import evaluate, COLD_START_THRESHOLD, K


# --- metrics ---


def test_recall_perfect():
    assert recall_at_k({1, 2, 3}, [1, 2, 3, 4, 5], k=3) == 1.0


def test_recall_partial():
    assert recall_at_k({1, 2, 3}, [1, 4, 5, 6, 7], k=5) == pytest.approx(1 / 3)


def test_recall_miss():
    assert recall_at_k({1, 2, 3}, [4, 5, 6], k=3) == 0.0


def test_recall_empty_relevant():
    assert recall_at_k(set(), [1, 2, 3], k=3) == 0.0


def test_recall_truncates_at_k():
    # item 2 is relevant but at position 4, beyond k=3
    assert recall_at_k({2}, [1, 3, 4, 2], k=3) == 0.0


def test_ndcg_perfect():
    # Only one relevant item, ranked first — should be 1.0
    assert ndcg_at_k({1}, [1, 2, 3], k=3) == pytest.approx(1.0)


def test_ndcg_relevant_at_second_position():
    # Relevant item at rank 2: DCG = 1/log2(3), IDCG = 1/log2(2) = 1.0
    expected = (1.0 / math.log2(3)) / (1.0 / math.log2(2))
    assert ndcg_at_k({2}, [1, 2, 3], k=3) == pytest.approx(expected)


def test_ndcg_no_hits():
    assert ndcg_at_k({99}, [1, 2, 3], k=3) == 0.0


def test_ndcg_empty_relevant():
    assert ndcg_at_k(set(), [1, 2, 3], k=3) == 0.0


# --- protocol ---


def _make_eval_inputs(
    warm_users: list[int],
    cold_users: list[int],
    hit: bool = True,
) -> tuple[dict, dict, dict]:
    """
    Build minimal evaluate() inputs.
    If hit=True every user gets a recommendation that matches their holdout item.
    """
    all_users = warm_users + cold_users
    recommendations = {u: [u * 100] if hit else [9999] for u in all_users}
    holdout = {u: {u * 100} for u in all_users}
    train_counts = {}
    for u in warm_users:
        train_counts[u] = COLD_START_THRESHOLD  # exactly at threshold = warm
    for u in cold_users:
        train_counts[u] = COLD_START_THRESHOLD - 1  # below threshold = cold
    return recommendations, holdout, train_counts


def test_evaluate_perfect_warm():
    recs, holdout, counts = _make_eval_inputs(warm_users=[1, 2], cold_users=[])
    result = evaluate(recs, holdout, counts)
    assert result.warm.recall == pytest.approx(1.0)
    assert result.warm.ndcg == pytest.approx(1.0)
    assert result.n_warm_users == 2
    assert result.n_cold_users == 0


def test_evaluate_cold_users_separated():
    recs, holdout, counts = _make_eval_inputs(warm_users=[1], cold_users=[2])
    result = evaluate(recs, holdout, counts)
    assert result.n_warm_users == 1
    assert result.n_cold_users == 1
    # both get hits so all metrics should be 1.0
    assert result.cold.recall == pytest.approx(1.0)
    assert result.warm.recall == pytest.approx(1.0)


def test_evaluate_miss_returns_zero():
    recs, holdout, counts = _make_eval_inputs(warm_users=[1], cold_users=[], hit=False)
    result = evaluate(recs, holdout, counts)
    assert result.warm.recall == 0.0
    assert result.warm.ndcg == 0.0


def test_evaluate_no_recommendation_for_user():
    holdout = {1: {100}}
    train_counts = {1: 10}
    result = evaluate({}, holdout, train_counts)
    assert result.warm.recall == 0.0


def test_evaluate_overall_is_average_of_all_users():
    # warm user gets a hit, cold user misses — overall should be 0.5 recall
    recs = {1: [100], 2: [9999]}
    holdout = {1: {100}, 2: {200}}
    train_counts = {1: COLD_START_THRESHOLD, 2: 0}
    result = evaluate(recs, holdout, train_counts)
    assert result.overall.recall == pytest.approx(0.5)
