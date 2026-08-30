from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .metrics import ndcg_at_k, recall_at_k

# Matches ADR 0001 as amended 2026-08-30: users with fewer than this many
# training interactions are cold. One number for the whole system — the warm/cold
# slicing here, the offline candidate models' fallback routing, and the deployed
# path in `src/serving/orchestration.py` all read it, so "is this user cold?" has
# a single answer offline and online (`docs/cold-start-routing-decision.md`).
COLD_START_THRESHOLD = 10

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
class SyntheticColdSlice:
    """One history bucket of ADR 0011's synthetic cold-start cohort.

    ``metrics`` is the bucket's recall and NDCG over its single held-out target
    per user. ``n_fallback_served`` is the attribution that makes the bucket
    worth having: how many of its users the model routed to its popularity
    fallback rather than to its learned path. It is ``None`` when the caller
    passed no routing predicate — an unmeasured count and a measured zero are
    very different claims and must not share a representation.
    """

    history_size: int
    metrics: UserMetrics
    n_users: int
    n_fallback_served: int | None = None


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
    # ADR 0011's per-bucket cold-start coverage, keyed by history size. Empty
    # unless the caller passed ``synthetic_cold_users`` — the natural state for
    # every call site that predates the cohort.
    synthetic_cold_slices: dict[int, SyntheticColdSlice] = field(default_factory=dict)


def evaluate(
    recommendations: dict[int, list[int]],
    holdout: dict[int, set[int]],
    train_interaction_counts: dict[int, int],
    k: int = K,
    *,
    synthetic_cold_users: Mapping[int, Mapping[int, set[int]]] | None = None,
    synthetic_cold_served_by: Callable[[int], bool] | None = None,
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
        synthetic_cold_users: ADR 0011's cohort as
                                  ``{history_size: {user_id: {target_item}}}``.
                                  Scored into ``synthetic_cold_slices`` and
                                  nowhere else — these users are not in
                                  ``holdout``, so they never enter the warm,
                                  cold or overall numbers and cannot move a
                                  metric anyone is already comparing across runs.
        synthetic_cold_served_by: predicate answering "did the model's learned
                                  path serve this user?" — in practice one of the
                                  models' ``was_served_by_*`` methods. Supplying
                                  it is what turns the buckets from a recall
                                  report into a routing assertion.

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
        synthetic_cold_slices=_synthetic_cold_slices(
            recommendations,
            synthetic_cold_users,
            synthetic_cold_served_by,
            k,
        ),
    )


def _synthetic_cold_slices(
    recommendations: dict[int, list[int]],
    synthetic_cold_users: Mapping[int, Mapping[int, set[int]]] | None,
    served_by: Callable[[int], bool] | None,
    k: int,
) -> dict[int, SyntheticColdSlice]:
    """Score ADR 0011's cohort one history bucket at a time.

    Each user holds out exactly one item, so their recall is 0 or 1 and the
    bucket mean reads directly as "fraction of users whose target appeared in
    top-k" — the per-bucket cleanliness the ADR trades per-user signal density
    for.
    """
    slices: dict[int, SyntheticColdSlice] = {}
    for history_size, targets in sorted((synthetic_cold_users or {}).items()):
        recalls: list[float] = []
        ndcgs: list[float] = []
        fallback_served = 0
        for user_id, relevant in targets.items():
            retrieved = recommendations.get(user_id, [])
            recalls.append(recall_at_k(relevant, retrieved, k))
            ndcgs.append(ndcg_at_k(relevant, retrieved, k))
            if served_by is not None and not served_by(user_id):
                fallback_served += 1
        slices[history_size] = SyntheticColdSlice(
            history_size=history_size,
            metrics=UserMetrics(recall=_mean(recalls), ndcg=_mean(ndcgs)),
            n_users=len(targets),
            n_fallback_served=None if served_by is None else fallback_served,
        )
    return slices


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
