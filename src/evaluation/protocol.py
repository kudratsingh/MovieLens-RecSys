from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

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

# The slices a per-user recall vector is published for. "overall" is the union
# of the other two rather than a third partition of the users, which is why its
# vector has as many entries as the warm and cold ones together.
RECALL_SLICES = ("warm", "cold", "overall")

# Where a trainer writes its recall vectors inside an MLflow run. One name for
# every model, because the tolerance study's evidence document is assembled by
# hand out of several runs and the operator should not have to guess at four.
PER_USER_RECALL_ARTIFACT = "per_user_recall.json"

# Versions this artifact's own envelope. The `per_user_recall` object inside it
# is the study's shape, not ours, and carries the study's schema version.
PER_USER_RECALL_ARTIFACT_VERSION = 1


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
    # The recall behind each slice mean, keyed by the dataset's own user id:
    # ``{slice_name: {user_id: recall}}`` over ``RECALL_SLICES``. Kept because
    # it cannot be recovered later — a finished run publishes means, and the
    # retrieval tolerance study's population term needs the *paired* per-user
    # differences between two runs scored on the same users
    # (docs/model-planning/contracts/retrieval-tolerance-measurement.md).
    # Empty on an ``EvalResult`` that was not produced by ``evaluate()``: a
    # mean across seeds has no per-user vector, and pretending otherwise would
    # hand the study a vector belonging to no run.
    per_user_recall: dict[str, dict[int, float]] = field(default_factory=dict)


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
        candidate-stage recall@500 with a recommender-end-to-end recall@10,
        and ``per_user_recall`` carrying the recall of every evaluated user
        behind those means.
    """
    # Recall is accumulated per user rather than as a bare list: the means below
    # are unchanged (a dict preserves insertion order, so they are summed in the
    # same order and to the same bits), and keeping the user id is what lets a
    # later study pair this run against another one user by user. One float per
    # evaluated user per slice — the whole holdout is thousands of users, not
    # millions, and no candidate list is retained.
    warm_recalls: dict[int, float] = {}
    cold_recalls: dict[int, float] = {}
    warm_ndcgs: list[float] = []
    cold_ndcgs: list[float] = []

    for user_id, relevant in holdout.items():
        retrieved = recommendations.get(user_id, [])
        r = recall_at_k(relevant, retrieved, k)
        n = ndcg_at_k(relevant, retrieved, k)

        if train_interaction_counts.get(user_id, 0) < COLD_START_THRESHOLD:
            cold_recalls[user_id] = r
            cold_ndcgs.append(n)
        else:
            warm_recalls[user_id] = r
            warm_ndcgs.append(n)

    warm = UserMetrics(
        recall=_mean(warm_recalls.values()),
        ndcg=_mean(warm_ndcgs),
    )
    cold = UserMetrics(
        recall=_mean(cold_recalls.values()),
        ndcg=_mean(cold_ndcgs),
    )
    # Warm and cold partition the holdout, so the union is every evaluated user
    # exactly once and its mean is the overall recall by construction.
    all_recalls = {**warm_recalls, **cold_recalls}
    all_ndcgs = warm_ndcgs + cold_ndcgs
    overall = UserMetrics(
        recall=_mean(all_recalls.values()),
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
        per_user_recall={"warm": warm_recalls, "cold": cold_recalls, "overall": all_recalls},
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


def per_user_recall_document(
    result: EvalResult,
    *,
    run_id: str,
    model_type: str,
    seed: int | None,
    configuration_id: str,
    protocol: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Serialize one evaluated run into the tolerance study's run-object shape.

    ``src/evaluation/tolerance_study.py`` consumes an evidence document holding
    one incumbent run object and three or more study run objects. This returns
    exactly one such object, so assembling the document is a matter of
    collecting artifacts rather than reformatting them.

    Two details are load-bearing rather than incidental. JSON has no integer
    keys, so user ids are stringified here and parsed back by the study's
    loader — they remain the dataset's own ids, never positions, which is what
    makes the study's user-by-user pairing well defined. And no value is
    rounded: the study checks that each vector's mean reproduces the slice
    recall the run published, and rounding would break that check for a vector
    that is in fact the right one.

    ``protocol`` is the canonical ``ProtocolManifest`` payload, taken as a
    mapping so this module keeps its distance from the manifest's schema. No
    candidate trainer emits one today, so the artifact is a complete run object
    except for that field, and whoever assembles the evidence fills it from the
    run's ``evaluation_protocol`` tag.

    Raises:
        ValueError: the result carries no per-user vectors, or vectors that
            disagree with its own slice counts — evidence the study would
            refuse later, reported here instead where the run can be re-made.
    """
    for name, value in (
        ("run_id", run_id),
        ("model_type", model_type),
        ("configuration_id", configuration_id),
    ):
        # The study requires all three and refuses on a blank one, which is an
        # expensive way to discover a typo three runs into a study.
        if not value.strip() or value != value.strip():
            raise ValueError(f"{name} must be a non-empty string without surrounding whitespace")

    missing = [name for name in RECALL_SLICES if name not in result.per_user_recall]
    if missing:
        raise ValueError(
            f"result carries no per-user recall for {', '.join(missing)}; only a result from "
            "evaluate() can be exported as study evidence"
        )
    expected_users = {
        "warm": result.n_warm_users,
        "cold": result.n_cold_users,
        "overall": result.n_warm_users + result.n_cold_users,
    }
    for name, expected in expected_users.items():
        actual = len(result.per_user_recall[name])
        if actual != expected:
            raise ValueError(
                f"{name} vector holds {actual} users but the result reports {expected}"
            )

    document: dict[str, Any] = {
        "artifact_schema_version": PER_USER_RECALL_ARTIFACT_VERSION,
        "run_id": run_id,
        "model_type": model_type,
        "seed": seed,
        "configuration_id": configuration_id,
        # Not read by the study (it takes K from the protocol), but a recall@10
        # vector and a recall@500 one look identical on the page, and pasting
        # the wrong one into an evidence document is the mistake to make.
        "k": result.k,
        "metrics": {
            "warm_recall": result.warm.recall,
            "cold_recall": result.cold.recall,
            "overall_recall": result.overall.recall,
            "n_warm_users": result.n_warm_users,
            "n_cold_users": result.n_cold_users,
        },
        "per_user_recall": {
            name: {str(user_id): value for user_id, value in result.per_user_recall[name].items()}
            for name in RECALL_SLICES
        },
    }
    if protocol is not None:
        document["protocol"] = dict(protocol)
    return document


def _mean(values: Collection[float]) -> float:
    return sum(values) / len(values) if values else 0.0
