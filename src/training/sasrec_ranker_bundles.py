"""Two follow-on arms for the SASRec-candidate ranker, gated against the same incumbent.

Step 1 (`src/training/sasrec_ranker.py`) established the shape of the problem:
retraining the LightGBM ranker on SASRec candidates gains **+25.96% warm NDCG@10**
and loses **−53.11% cold**, and the cold loss is not a retrieval effect at all.
Below the cold-start threshold both arms route to the same popularity fallback and
hold the same fitted fallback object, so a cold user's slate is byte-identical
between them — the whole cold move belongs to the booster. The incumbent booster
leans on `item_popularity_30d` (2.5x its next feature), which is the right rule for
a popularity slate; the SASRec-trained booster, fitted on a deep-catalog candidate
mix where popularity discriminates poorly, never learned it.

That diagnosis has two obvious repairs, and this module measures both rather than
arguing about them.

**1b, the per-route bundle.** No new weights at all: compose the two boosters step
1 already saved, keyed by the route serving already takes. A warm user gets SASRec
candidates ranked by the SASRec-trained booster; a cold user gets the popularity
fallback ranked by the incumbent booster, exactly as today. The prediction this
makes is unusually strong, which is what makes it worth running: cold must come
back **bit-identical** to the incumbent's, because it is the same slate scored by
the same booster over the same features. The run refuses to write its result if it
does not, because anything else would mean the routing or the feature path moved
under us.

**1c, the union booster.** One new booster trained on the concatenation of both
arms' training sets — the same 154,003 positives seen through both candidate
stages — then served through the same per-route composition. The hypothesis is
that one model can carry both distributions, which would be simpler to ship than
two.

Neither arm promotes anything. Both are scored through `src/evaluation/` and put
through ADR 0001's gate against step 1's item-item incumbent, and the champion
changes only by the owner's decision.

Run with ``make train-sasrec-ranker-bundles``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

from src.evaluation.gate import GateDecision, promotion_decision
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    EvalResult,
    K,
    UserMetrics,
    evaluate,
    per_user_recall_document,
)
from src.features import FeatureIndex
from src.models.candidates import routing
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training import protocol_manifest, sampling, seeds
from src.training.ranker import NEGATIVES_PER_POSITIVE, RANKER_POSITIVE_WINDOW_DAYS
from src.training.sasrec_ranker import (
    CANDIDATE_BATCH_SIZE,
    PHASE_2_RANKER_EXPERIMENT,
    CandidateSource,
    SharedInputs,
    _save_booster_create_only,
    build_ranker_training_set,
    prepare_shared,
)
from synthetic.cold_start import harness as synth_cold

logger = logging.getLogger(__name__)

PER_ROUTE_ARM = "per-route-bundle"
UNION_ARM = "union-booster"

# Step 1's two runs. Named rather than searched for: these arms are defined
# against *those* boosters, and a run that silently picked up different weights
# would answer a different question under the same name.
STEP1_INCUMBENT_RUN = "bff5f86e6ae14e6b9c19d9c426e3b6ec"
STEP1_CHALLENGER_RUN = "50d9718802f949d98c5d8d4d6315bb1a"

# The bit-identity claim 1b rests on. `_mean` sums a dict's values in insertion
# order, so re-scoring the same users with the same booster over the same features
# reproduces these to the last bit — not merely to six decimal places.
STEP1_INCUMBENT_COLD_NDCG = 0.5490019989542251
STEP1_INCUMBENT_COLD_RECALL = 0.07763845424378057
STEP1_CHALLENGER_WARM_NDCG = 0.09168799054929602
STEP1_CHALLENGER_WARM_RECALL = 0.07743102042408873

# What step 1 built, so a rebuild that drifts is caught before it is trained on.
STEP1_ITEMITEM_SHAPE = (87_794, 1_843_318)
STEP1_SASREC_SHAPE = (83_538, 1_754_298)


class BundleReproductionError(RuntimeError):
    """A rebuilt input or a re-scored slice did not reproduce step 1.

    Raised rather than reported, because every claim these arms make is a claim
    *relative to* step 1. A per-route bundle whose cold slice is not step 1's cold
    slice is not a per-route bundle; it is an unexplained third thing.
    """


@dataclass(frozen=True)
class BundleOutcome:
    name: str
    run_id: str
    result: EvalResult
    booster_sha256: str
    importances: dict[str, float]
    seconds: dict[str, float]
    detail: dict[str, object]


def step1_incumbent_result() -> EvalResult:
    """Step 1's item-item arm, as the gate reads it.

    Rebuilt from the recorded numbers rather than re-run: the gate needs six
    metrics and two counts, all of which are published, and re-running the arm to
    recover them would spend five minutes reproducing a number this file already
    refuses to disagree with.
    """
    return EvalResult(
        warm=UserMetrics(recall=0.04824392062685383, ndcg=0.07279178471502273),
        cold=UserMetrics(recall=STEP1_INCUMBENT_COLD_RECALL, ndcg=STEP1_INCUMBENT_COLD_NDCG),
        overall=UserMetrics(recall=0.056146275366731904, ndcg=0.20081497748663713),
        n_warm_users=1931,
        n_cold_users=710,
        k=K,
    )


def rank_by_route(
    warm_source: CandidateSource,
    warm_ranker: LGBMRanker,
    cold_source: CandidateSource,
    cold_ranker: LGBMRanker,
    feature_index: FeatureIndex,
    user_ids: Sequence[int],
    as_of_timestamp: int,
    warm_ranking_features: Callable[[int, list[int]], pd.DataFrame] | None = None,
) -> dict[int, list[int]]:
    """Retrieve and rank each user through the route serving would take them on.

    The route is the candidate stage's own predicate, not the evaluator's warm/cold
    slice — they coincide for holdout users, and using the model's predicate is
    what makes this a description of a servable system rather than of a scoring
    convenience.

    ``warm_ranking_features``, when given, supplies the extra columns the learned
    route's ranker reads (ADR 0018). It is called only for learned-route users,
    because the fallback booster's contract is the eight aggregates and a user
    below the threshold has no sequence to score against.
    """
    candidates_by_user: dict[int, list[int]] = {}
    features_by_user: dict[int, pd.DataFrame] = {}
    rankers: dict[int, LGBMRanker] = {}
    for user_id in user_ids:
        learned = warm_source.served_by_learned_path(user_id)
        source = warm_source if learned else cold_source
        rankers[user_id] = warm_ranker if learned else cold_ranker
        candidates = source.holdout_candidates(user_id, K_CANDIDATES)
        if not candidates:
            candidates_by_user[user_id] = []
            continue
        candidates_by_user[user_id] = candidates
        features = feature_index.features_for(
            pd.DataFrame(
                {
                    "userId": [user_id] * len(candidates),
                    "movieId": candidates,
                    "as_of_timestamp": [as_of_timestamp] * len(candidates),
                }
            )
        )
        if learned and warm_ranking_features is not None:
            extra = warm_ranking_features(user_id, candidates)
            features = pd.concat([features, extra.set_index(features.index)], axis=1)
        features_by_user[user_id] = features

    out: dict[int, list[int]] = {}
    for user_id, candidates in candidates_by_user.items():
        if not candidates:
            out[user_id] = []
            continue
        ranked: dict[int, list[int]] = rankers[user_id].rank_candidates(
            {user_id: candidates}, {user_id: features_by_user[user_id]}, k=K
        )
        out[user_id] = ranked[user_id]
    return out


def duplicate_group_count(features: pd.DataFrame, group_sizes: list[int]) -> int:
    """How many LambdaRank groups appear more than once across the union.

    Rows cannot be deduplicated — a group is the unit LightGBM segments on, and
    dropping a row from one corrupts it — so the question worth answering is
    whether whole groups collide. Two arms drawing from different candidate pools
    at different positions in the same RNG stream should essentially never build
    the same group; this counts rather than assumes it.
    """
    values = features.to_numpy(dtype=np.float64)
    seen: set[bytes] = set()
    duplicates = 0
    offset = 0
    for size in group_sizes:
        digest = hashlib.sha256(np.ascontiguousarray(values[offset : offset + size]).tobytes())
        key = digest.digest()
        if key in seen:
            duplicates += 1
        seen.add(key)
        offset += size
    return duplicates


def _log_bundle_run(
    shared: SharedInputs,
    *,
    name: str,
    result: EvalResult,
    booster_sha256: str,
    importances: dict[str, float],
    params: dict[str, object],
    tags: dict[str, str],
    run_id_holder: dict[str, str],
) -> None:
    """MLflow side of one arm, in the shape the ADR 0001 gate CLI reads."""
    protocol = protocol_manifest.build_protocol(
        split=shared.split,
        fitted_frame=shared.train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(
            routing.cold_start_threshold_for(shared.routing_policy, COLD_START_THRESHOLD)
        ),
        stage="ranking",
        k=K,
    )
    envelope = protocol_manifest.run_envelope(protocol, deterministic=False, seed=shared.seed)
    mlflow.set_experiment(PHASE_2_RANKER_EXPERIMENT)
    run_name = seeds.run_name_for(
        sampling.run_name_for(
            routing.run_name_for(f"lgbm-lambdarank-{name}", shared.routing_policy),
            shared.positive_limit,
        ),
        shared.seed,
    )
    with mlflow.start_run(run_name=run_name) as run:
        run_id_holder["run_id"] = run.info.run_id
        mlflow.set_tags(
            {
                **envelope.tags,
                "model_family": "ranker",
                "model_type": "lgbm_lambdarank",
                "phase": "2",
                "stage": "ranker",
                "sasrec_ranker_arm": name,
                "cold_start_routing_policy": shared.routing_policy,
                "train_seed": str(shared.seed),
                "ranker_serving_exclusions_applied": "true",
                "candidate_leakage_compromise": "true",
                "step1_incumbent_run_id": STEP1_INCUMBENT_RUN,
                "step1_challenger_run_id": STEP1_CHALLENGER_RUN,
                **tags,
                **shared.sasrec.identity(),
            }
        )
        mlflow.log_params(
            {
                **envelope.params,
                "k_final": K,
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cutoff_timestamp": shared.split.cutoff,
                "holdout_end_timestamp": shared.split.holdout_end,
                "n_train_rows": len(shared.split.train),
                "n_fitted_rows": len(shared.train_frame),
                "n_holdout_rows": len(shared.split.holdout),
                "n_positives_sampled": len(shared.positives),
                "negatives_per_positive": NEGATIVES_PER_POSITIVE,
                "ranker_positive_window_days": RANKER_POSITIVE_WINDOW_DAYS,
                "ranker_positive_limit": shared.positive_limit,
                "candidate_batch_size": CANDIDATE_BATCH_SIZE,
                "seed": shared.seed,
                "ranker_booster_sha256": booster_sha256,
                **params,
            }
        )
        mlflow.log_metrics(
            {
                "warm_recall_at_k": result.warm.recall,
                "warm_ndcg_at_k": result.warm.ndcg,
                "cold_recall_at_k": result.cold.recall,
                "cold_ndcg_at_k": result.cold.ndcg,
                "overall_recall_at_k": result.overall.recall,
                "overall_ndcg_at_k": result.overall.ndcg,
                "n_warm_users": result.n_warm_users,
                "n_cold_users": result.n_cold_users,
            }
        )
        mlflow.log_metrics({f"importance_{key}": value for key, value in importances.items()})
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run.info.run_id,
                model_type=f"lgbm_lambdarank_{name}",
                seed=shared.seed,
                configuration_id=f"sasrec-ranker-bundle:{name}",
                protocol=protocol.to_dict(),
            ),
            PER_USER_RECALL_ARTIFACT,
        )
        if shared.cohort is not None:
            synth_cold.log_summary(result, logger=logger, k=K)
            mlflow.log_params(synth_cold.params(shared.cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )


def _evaluate(shared: SharedInputs, recommendations: dict[int, list[int]]) -> EvalResult:
    return evaluate(
        recommendations,
        shared.split.holdout.groupby("userId")["movieId"].apply(set).to_dict(),
        shared.split.train.groupby("userId").size().to_dict(),
        k=K,
        synthetic_cold_users=(
            shared.cohort.targets_by_bucket if shared.cohort is not None else None
        ),
        synthetic_cold_served_by=(
            shared.sasrec.served_by_learned_path if shared.cohort is not None else None
        ),
    )


def _holdout_user_ids(shared: SharedInputs) -> list[int]:
    users: list[int] = list(shared.split.holdout["userId"].unique().tolist())
    if shared.cohort is not None:
        users = users + list(shared.cohort.user_ids)
    return users


def _log_slices(name: str, result: EvalResult) -> None:
    for label, metrics, n_users in (
        ("Warm", result.warm, result.n_warm_users),
        ("Cold", result.cold, result.n_cold_users),
        ("Overall", result.overall, result.n_warm_users + result.n_cold_users),
    ):
        logger.info(
            "[%s] %s (n=%d): recall@%d=%.6f ndcg@%d=%.6f",
            name,
            label,
            n_users,
            K,
            metrics.recall,
            K,
            metrics.ndcg,
        )


def run_per_route(
    shared: SharedInputs, incumbent: LGBMRanker, challenger: LGBMRanker, sha256s: dict[str, str]
) -> BundleOutcome:
    """1b — compose step 1's two boosters by route. No new weights."""
    started = time.perf_counter()
    recommendations = rank_by_route(
        warm_source=shared.sasrec,
        warm_ranker=challenger,
        cold_source=shared.itemitem,
        cold_ranker=incumbent,
        feature_index=shared.feature_index,
        user_ids=_holdout_user_ids(shared),
        as_of_timestamp=shared.split.cutoff,
    )
    rank_seconds = time.perf_counter() - started
    result = _evaluate(shared, recommendations)
    _log_slices(PER_ROUTE_ARM, result)

    # The predeclared identities. Equality, not a tolerance: the same users, the
    # same slate, the same booster and the same features summed in the same order
    # reproduce the same float. A near miss would mean something moved.
    for label, actual, expected in (
        ("cold ndcg@10", result.cold.ndcg, STEP1_INCUMBENT_COLD_NDCG),
        ("cold recall@10", result.cold.recall, STEP1_INCUMBENT_COLD_RECALL),
        ("warm ndcg@10", result.warm.ndcg, STEP1_CHALLENGER_WARM_NDCG),
        ("warm recall@10", result.warm.recall, STEP1_CHALLENGER_WARM_RECALL),
    ):
        if actual != expected:
            raise BundleReproductionError(
                f"per-route {label} is {actual!r}, expected step 1's {expected!r} exactly. "
                "The composition is meant to re-serve step 1's own slates; a difference "
                "means the routing, the features or the boosters are not what they were."
            )

    run_id_holder: dict[str, str] = {}
    _log_bundle_run(
        shared,
        name=PER_ROUTE_ARM,
        result=result,
        booster_sha256=f"warm={sha256s['challenger']};cold={sha256s['incumbent']}",
        importances=challenger.feature_importances(importance_type="gain"),
        params={
            "rank_seconds": round(rank_seconds, 1),
            "n_new_boosters": 0,
            "warm_booster_run_id": STEP1_CHALLENGER_RUN,
            "cold_booster_run_id": STEP1_INCUMBENT_RUN,
        },
        tags={"bundle_composition": "warm=sasrec+challenger;cold=popularity+incumbent"},
        run_id_holder=run_id_holder,
    )
    return BundleOutcome(
        name=PER_ROUTE_ARM,
        run_id=run_id_holder["run_id"],
        result=result,
        booster_sha256=f"warm={sha256s['challenger']};cold={sha256s['incumbent']}",
        importances=challenger.feature_importances(importance_type="gain"),
        seconds={"rank": round(rank_seconds, 1)},
        detail={
            "n_new_boosters": 0,
            "reproduces_step1_cold_exactly": True,
            "reproduces_step1_warm_exactly": True,
        },
    )


def run_union(shared: SharedInputs) -> BundleOutcome:
    """1c — one booster on the concatenation of both arms' training sets."""
    started = time.perf_counter()
    itemitem_features, itemitem_groups, itemitem_labels, _ = build_ranker_training_set(
        positives=shared.positives,
        source=shared.itemitem,
        feature_index=shared.feature_index,
        history_by_user=shared.history_by_user,
        n_negatives=NEGATIVES_PER_POSITIVE,
        rng=np.random.default_rng(shared.seed),
    )
    sasrec_features, sasrec_groups, sasrec_labels, _ = build_ranker_training_set(
        positives=shared.positives,
        source=shared.sasrec,
        feature_index=shared.feature_index,
        history_by_user=shared.history_by_user,
        n_negatives=NEGATIVES_PER_POSITIVE,
        rng=np.random.default_rng(shared.seed),
    )
    build_seconds = time.perf_counter() - started

    for label, shape, expected in (
        ("item-item", (len(itemitem_groups), sum(itemitem_groups)), STEP1_ITEMITEM_SHAPE),
        ("SASRec", (len(sasrec_groups), sum(sasrec_groups)), STEP1_SASREC_SHAPE),
    ):
        if shape != expected:
            raise BundleReproductionError(
                f"rebuilt {label} training set is {shape} groups/rows, expected {expected} "
                "from step 1. The union is only interpretable if its halves are step 1's."
            )

    features = pd.concat([itemitem_features, sasrec_features], ignore_index=True)
    groups = itemitem_groups + sasrec_groups
    labels = np.concatenate([itemitem_labels, sasrec_labels])
    duplicates = duplicate_group_count(features, groups)
    logger.info(
        "[%s] union training set: %d groups, %d rows (%d duplicated groups across the halves)",
        UNION_ARM,
        len(groups),
        sum(groups),
        duplicates,
    )

    started = time.perf_counter()
    config = LGBMRankerConfig(seed=shared.seed)
    ranker = LGBMRanker(config=config).fit(features, groups, labels)
    fit_seconds = time.perf_counter() - started
    logger.info("[%s] booster fit in %.1fs", UNION_ARM, fit_seconds)

    run_id_holder: dict[str, str] = {}
    # Saved before evaluation, and named by the clock rather than by a run id: the
    # MLflow run does not exist yet, and minting an empty one just to borrow its id
    # would leave a metric-less run in the experiment forever. The directory name
    # goes into the run's params, so the link is recorded either way.
    booster_dir = f"{UNION_ARM}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    booster_path, booster_sha256 = _save_booster_create_only(
        ranker, shared.booster_root / booster_dir
    )
    logger.info("[%s] booster %s sha256=%s", UNION_ARM, booster_path, booster_sha256)

    started = time.perf_counter()
    recommendations = rank_by_route(
        warm_source=shared.sasrec,
        warm_ranker=ranker,
        cold_source=shared.itemitem,
        cold_ranker=ranker,
        feature_index=shared.feature_index,
        user_ids=_holdout_user_ids(shared),
        as_of_timestamp=shared.split.cutoff,
    )
    rank_seconds = time.perf_counter() - started
    result = _evaluate(shared, recommendations)
    _log_slices(UNION_ARM, result)
    importances = ranker.feature_importances(importance_type="gain")
    logger.info("[%s] gain importances: %s", UNION_ARM, importances)

    _log_bundle_run(
        shared,
        name=UNION_ARM,
        result=result,
        booster_sha256=booster_sha256,
        importances=importances,
        params={
            "ranker_training_set_seconds": round(build_seconds, 1),
            "ranker_fit_seconds": round(fit_seconds, 1),
            "rank_seconds": round(rank_seconds, 1),
            "n_new_boosters": 1,
            "n_ranker_positives_used": len(groups),
            "n_ranker_training_rows": sum(groups),
            "n_duplicate_groups": duplicates,
            "num_leaves": config.num_leaves,
            "learning_rate": config.learning_rate,
            "num_boost_round": config.num_boost_round,
            "booster_directory": booster_dir,
        },
        tags={"bundle_composition": "union-booster-on-per-route-slates"},
        run_id_holder=run_id_holder,
    )
    return BundleOutcome(
        name=UNION_ARM,
        run_id=run_id_holder["run_id"],
        result=result,
        booster_sha256=booster_sha256,
        importances=importances,
        seconds={
            "training_set": round(build_seconds, 1),
            "fit": round(fit_seconds, 1),
            "rank": round(rank_seconds, 1),
        },
        detail={
            "n_new_boosters": 1,
            "groups": len(groups),
            "rows": sum(groups),
            "duplicate_groups": duplicates,
            "booster_path": str(booster_path.name),
            "booster_dir": booster_dir,
        },
    )


def _document(outcome: BundleOutcome, decision: GateDecision) -> dict[str, object]:
    return {
        "arm": outcome.name,
        "run_id": outcome.run_id,
        "incumbent_run_id": STEP1_INCUMBENT_RUN,
        "metrics": {
            "warm_recall_at_k": outcome.result.warm.recall,
            "warm_ndcg_at_k": outcome.result.warm.ndcg,
            "cold_recall_at_k": outcome.result.cold.recall,
            "cold_ndcg_at_k": outcome.result.cold.ndcg,
            "overall_recall_at_k": outcome.result.overall.recall,
            "overall_ndcg_at_k": outcome.result.overall.ndcg,
        },
        "n_warm_users": outcome.result.n_warm_users,
        "n_cold_users": outcome.result.n_cold_users,
        "feature_importance_gain": outcome.importances,
        "booster_sha256": outcome.booster_sha256,
        "seconds": outcome.seconds,
        "detail": outcome.detail,
        "gate": decision.to_dict(),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    shared = prepare_shared()

    incumbent_path = shared.booster_root / STEP1_INCUMBENT_RUN / "ranker.txt"
    challenger_path = shared.booster_root / STEP1_CHALLENGER_RUN / "ranker.txt"
    for path in (incumbent_path, challenger_path):
        if not path.is_file():
            raise BundleReproductionError(f"step 1 booster is missing: {path}")
    incumbent_booster = LGBMRanker.load_model(incumbent_path)
    challenger_booster = LGBMRanker.load_model(challenger_path)
    sha256s = {
        "incumbent": _file_sha256(incumbent_path),
        "challenger": _file_sha256(challenger_path),
    }
    logger.info("Loaded step 1 boosters: %s", sha256s)

    reference = step1_incumbent_result()
    outcomes = [
        run_per_route(shared, incumbent_booster, challenger_booster, sha256s),
        run_union(shared),
    ]
    for outcome in outcomes:
        decision = promotion_decision(outcome.result, reference)
        logger.info("ADR 0001 gate, %s vs step 1 item-item incumbent:", outcome.name)
        for line in decision.summary().splitlines():
            logger.info("  %s", line)
        output = shared.booster_root / f"step1-{outcome.name}-{outcome.run_id}.json"
        output.write_text(
            json.dumps(_document(outcome, decision), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote %s", output)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
