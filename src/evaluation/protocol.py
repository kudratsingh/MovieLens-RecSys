from dataclasses import dataclass

from .metrics import ndcg_at_k, recall_at_k

# Matches ADR 0001: users with fewer than this many training interactions are cold.
COLD_START_THRESHOLD = 5

# K for the recommender end-to-end (top-K returned to the user).
K = 10

# K for the candidate stage's recall metric. Per ADR 0003, the candidate stage
# retrieves ~500 items from the catalog before the ranker scores them, so the
# candidate-stage success criterion is "did the relevant items survive into
# the top-500?" rather than "into the top-10." Item-item, two-tower, and any
# future candidate generator are scored on recall@K_CANDIDATES; the ranker
# stage is scored on NDCG@K against its output. Both use the same evaluate()
# entrypoint with a different k.
K_CANDIDATES = 500


@dataclass
class UserMetrics:
    recall: float
    ndcg: float


@dataclass
class EvalResult:
    """
    Structured result from a single evaluation run.

    Metrics are split by warm vs. cold users so cold-start failure modes
    don't get masked by the warm-user majority.
    """

    warm: UserMetrics
    cold: UserMetrics
    overall: UserMetrics
    n_warm_users: int
    n_cold_users: int
    k: int = K


def evaluate(
    recommendations: dict[int, list[int]],
    holdout: dict[int, set[int]],
    train_interaction_counts: dict[int, int],
    k: int = K,
) -> EvalResult:
    """
    Evaluate a set of recommendations against holdout interactions.

    Args:
        recommendations: mapping of user_id -> ordered list of recommended item_ids.
        holdout: mapping of user_id -> set of item_ids the user interacted with
                 in the holdout window. Only users present here are evaluated.
        train_interaction_counts: mapping of user_id -> number of interactions in
                                  the training window, used to classify warm vs. cold.
        k: top-K cutoff for recall and NDCG. Defaults to ``K`` (10) for the
                                  recommender end-to-end; callers evaluating the
                                  candidate stage in isolation should pass
                                  ``K_CANDIDATES`` (500) instead.

    Returns:
        EvalResult with per-slice and overall metrics, ``k`` stamped on the result
        so a downstream consumer (MLflow tags, plots) can never confuse a
        candidate-stage recall@500 with a recommender-end-to-end recall@10.
    """
    warm_recalls, warm_ndcgs = [], []
    cold_recalls, cold_ndcgs = [], []

    for user_id, relevant in holdout.items():
        retrieved = recommendations.get(user_id, [])
        r = recall_at_k(relevant, retrieved, k)
        n = ndcg_at_k(relevant, retrieved, k)

        if train_interaction_counts.get(user_id, 0) < COLD_START_THRESHOLD:
            cold_recalls.append(r)
            cold_ndcgs.append(n)
        else:
            warm_recalls.append(r)
            warm_ndcgs.append(n)

    warm = UserMetrics(
        recall=_mean(warm_recalls),
        ndcg=_mean(warm_ndcgs),
    )
    cold = UserMetrics(
        recall=_mean(cold_recalls),
        ndcg=_mean(cold_ndcgs),
    )
    all_recalls = warm_recalls + cold_recalls
    all_ndcgs = warm_ndcgs + cold_ndcgs
    overall = UserMetrics(
        recall=_mean(all_recalls),
        ndcg=_mean(all_ndcgs),
    )

    return EvalResult(
        warm=warm,
        cold=cold,
        overall=overall,
        n_warm_users=len(warm_recalls),
        n_cold_users=len(cold_recalls),
        k=k,
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
