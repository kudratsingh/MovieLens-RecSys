"""ADR 0018 increment 1: give the learned route's ranker the SASRec score.

PR #151 established what the ranker is worth on SASRec's candidates and what it
is not. Retraining it on that slate moved warm NDCG@10 **+25.96%**; composing the
two boosters by route (`566f5309…`, arm 1b) turned that into a passing gate at
**+6.88% overall** with the cold slice bit-identical. But the whole warm gain in
that number was bought by the *retriever*. The booster still reads eight
aggregate columns — three user counters, three popularity windows, item age, a
genre affinity — and its importances went flat on SASRec's deep-catalog mix,
which is what a model with nothing informative to lean on looks like.

This module measures the first thing that could change that: two point-in-time
columns carrying the encoder's own opinion of the (user, candidate) pair.

**Two boosters, one training set.** The frame, the groups and the labels are
built once and fitted twice: an eight-column *control* and the ten-column *arm*,
which select their own columns from the same rows. That is the ablation, and it
is here rather than implied because O-9's repair changed the candidate
distribution — the incumbent bundle's warm booster was fitted before the repair
and is now slightly stale against the slate it ranks, so "arm beats bundle"
alone would confound two columns with a retrain. The control removes that doubt
by being retrained under conditions identical to the arm's.

**The fallback route is untouched.** It keeps the incumbent booster
`05610e60…` on the popularity slate, exactly as 1b composed it, so the cold slice
is bit-identical by construction and the run refuses to write anything if it is
not. Everything else is 1b's: `prepare_shared()`'s prologue, the same positives,
#126's exclusions, seed 42, the same protocol hash.

**The incumbent is 1b, not item-item.** Gating against item-item plus LightGBM
would let this arm bank SASRec's retrieval gain a second time and call it a
ranker result. ADR 0018 says so explicitly and this module hard-codes it. Since
O-9's repair the incumbent is 1b **re-measured on the fixed encoder**
(`c1d742c8…`, PR #162), because holding the retriever constant across a
comparison means holding the *corrected* retriever constant.

**Both readings are reported.** Owner decision O-1 — whether retrieval-driven
changes are judged on the warm slice or on the aggregate — is open. With cold
frozen at 0.549002, the 1,931 warm users carry 31.2% of the bundle's NDCG mass,
so ADR 0001's +3% overall needs warm +9.6% while a warm-primary reading needs
+3%. The run prints both and the record carries both; neither is chosen here.

Run with ``make train-sasrec-ranker-scores``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.evaluation.gate import GateDecision, promotion_decision
from src.evaluation.protocol import EvalResult, K, UserMetrics
from src.feature_contract import (
    FEATURE_COLUMNS,
    LEARNED_ROUTE_FEATURE_COLUMNS,
    SASREC_SCORE_COLUMNS,
)
from src.models.candidates.sasrec_ranking_features import SasrecScoreFeatures
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training.ranker import NEGATIVES_PER_POSITIVE
from src.training.sasrec_ranker import (
    SharedInputs,
    _save_booster_create_only,
    build_ranker_training_set,
    prepare_shared,
)
from src.training.sasrec_ranker_bundles import (
    STEP1_INCUMBENT_COLD_NDCG,
    STEP1_INCUMBENT_COLD_RECALL,
    STEP1_INCUMBENT_RUN,
    STEP1_SASREC_SHAPE,
    BundleReproductionError,
    _evaluate,
    _file_sha256,
    _holdout_user_ids,
    _log_bundle_run,
    _log_slices,
    rank_by_route,
)

logger = logging.getLogger(__name__)

ARM = "sasrec-score-features"

#: The eight-column control, trained on the *same* frame, groups and labels as
#: the arm and differing from it only in which columns it selects.
#:
#: It exists because O-9's repair changed the candidate distribution. The
#: incumbent bundle's warm booster was fitted before that repair and is now
#: slightly stale against the slate it ranks, while a freshly retrained arm is
#: not — so "arm beats bundle" would confound the two features with the
#: retrain. This control is retrained under exactly the same conditions as the
#: arm, so the difference between them is the feature set and nothing else.
CONTROL_ARM = "sasrec-aggregates-control"

#: PR #162's per-route bundle, re-measured on the O-9-fixed encoder
#: (`c1d742c8…`, zero new boosters — the same two checksum-pinned boosters
#: recomposed over the corrected retrieval). This arm's incumbent, per ADR
#: 0018's "How it is judged": the comparison is a ranker comparison, so the
#: retriever is held constant on both sides of it, and post-O-9 that means the
#: *fixed* retriever on both sides.
INCUMBENT_RUN = "c1d742c8485d4e54b66746a65f7705d0"
INCUMBENT_WARM_NDCG = 0.10144112985227004
INCUMBENT_WARM_RECALL = 0.08466299482991889
INCUMBENT_OVERALL_NDCG = 0.2217623025377634
INCUMBENT_OVERALL_RECALL = 0.08277453446787489

#: The pre-O-9 measurement the above supersedes, kept because this module's own
#: first result was gated against it and the record has to stay legible.
#: PR #151's bundle `566f5309…`: warm 0.091688 / cold 0.549002 / overall
#: 0.214631. Cold is identical in both, and deliberately so — the fallback
#: route never touches the encoder, which is why the assertion below still
#: reads the same float.
SUPERSEDED_INCUMBENT_RUN = "566f5309767a4076a4f5e8151be16645"


def per_route_bundle_result() -> EvalResult:
    """1b as the gate reads it, from the numbers PR #162 published.

    Rebuilt rather than re-run for the same reason ``step1_incumbent_result``
    is: every figure here is recorded, re-running it would spend ten minutes
    reproducing a number another session already measured under the standing
    "one run per configuration" rule, and this module's own cold assertion
    already refuses to proceed if the cold half of it has moved.
    """
    return EvalResult(
        warm=UserMetrics(recall=INCUMBENT_WARM_RECALL, ndcg=INCUMBENT_WARM_NDCG),
        cold=UserMetrics(recall=STEP1_INCUMBENT_COLD_RECALL, ndcg=STEP1_INCUMBENT_COLD_NDCG),
        overall=UserMetrics(recall=INCUMBENT_OVERALL_RECALL, ndcg=INCUMBENT_OVERALL_NDCG),
        n_warm_users=1931,
        n_cold_users=710,
        k=K,
    )


def holdout_feature_builder(
    shared: SharedInputs, features: SasrecScoreFeatures
) -> Callable[[int, list[int]], pd.DataFrame]:
    """The learned route's extra columns at holdout time, per user.

    The holdout cutoff sits after every training interaction, so a warm user's
    whole train history *is* their strict prefix at ``as_of`` and no truncation
    is needed here — the point-in-time guarantee is the split's, not this
    function's. The encode is batch-of-one to match
    ``_recommend_from_dense_history``, which is the call that produced the
    candidates being scored.
    """
    model = shared.sasrec.model

    def build(user_id: int, movie_ids: list[int]) -> pd.DataFrame:
        dense_history = model._user_history.get(user_id, [])
        if not dense_history:
            return features.frame_for(None, None, movie_ids)
        normalized, unnormalized = model.encode_dense_history(dense_history)
        return features.frame_for(normalized[0], unnormalized[0], movie_ids)

    return build


def missing_row_count(features_df: pd.DataFrame) -> int:
    """Rows whose sequence columns are the missing sentinel.

    ADR 0018 lists missingness as a consequence to report rather than absorb:
    these are the fallback-route positives the learned booster still trains on,
    and the count is how large that compromise is.
    """
    return int(features_df[SASREC_SCORE_COLUMNS[0]].isna().sum())


def warm_primary_reading(candidate: EvalResult, incumbent: EvalResult) -> dict[str, object]:
    """The O-1 reading ADR 0001's gate does not currently take.

    Warm relative change with an explicit cold non-regression clause, reported
    beside the gate rather than instead of it. This is a *record* of what the
    other reading would say, not a second gate: the threshold and the tolerance
    are ADR 0001's own, unchanged.
    """
    warm_change = (candidate.warm.ndcg - incumbent.warm.ndcg) / incumbent.warm.ndcg
    cold_change = (candidate.cold.ndcg - incumbent.cold.ndcg) / incumbent.cold.ndcg
    return {
        "reading": "warm-primary (O-1, undecided)",
        "warm_relative_change": warm_change,
        "required_warm_gain": 0.03,
        "warm_passed": warm_change >= 0.03,
        "cold_relative_change": cold_change,
        "cold_tolerance": 0.05,
        "cold_passed": cold_change >= -0.05,
        "would_promote": warm_change >= 0.03 and cold_change >= -0.05,
    }


@dataclass(frozen=True)
class ArmRecord:
    """What one fitted booster produced, in the shape the record wants."""

    name: str
    booster_dir: str
    booster_sha256: str
    fit_seconds: float
    rank_seconds: float
    importances: dict[str, float]
    feature_columns: list[str]


def _fit_compose_and_score(
    shared: SharedInputs,
    *,
    name: str,
    feature_columns: list[str],
    features_df: pd.DataFrame,
    group_sizes: list[int],
    labels: np.ndarray,
    fallback_booster: LGBMRanker,
    holdout_features: Callable[[int, list[int]], pd.DataFrame] | None,
    full_data: bool,
) -> tuple[EvalResult, ArmRecord]:
    """Fit one booster on the shared frame, compose it per route, and score it.

    ``holdout_features`` is ``None`` for the eight-column control: its contract
    has no sequence columns, so building them for it would be work whose result
    the booster discards. The arm passes the builder because its contract does.
    """
    started = time.perf_counter()
    config = LGBMRankerConfig(seed=shared.seed)
    ranker = LGBMRanker(config=config, feature_columns=feature_columns).fit(
        features_df, group_sizes, labels
    )
    fit_seconds = time.perf_counter() - started
    logger.info("[%s] booster fit in %.1fs on %d columns", name, fit_seconds, len(feature_columns))

    # Saved before it is scored, so the weights are a fact independent of
    # whatever the holdout says about them.
    booster_dir = f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    booster_path, booster_sha256 = _save_booster_create_only(
        ranker, shared.booster_root / booster_dir
    )
    logger.info("[%s] booster %s sha256=%s", name, booster_path, booster_sha256)

    started = time.perf_counter()
    recommendations = rank_by_route(
        warm_source=shared.sasrec,
        warm_ranker=ranker,
        cold_source=shared.itemitem,
        cold_ranker=fallback_booster,
        feature_index=shared.feature_index,
        user_ids=_holdout_user_ids(shared),
        as_of_timestamp=shared.split.cutoff,
        warm_ranking_features=holdout_features,
    )
    rank_seconds = time.perf_counter() - started
    result = _evaluate(shared, recommendations)
    _log_slices(name, result)

    # The fallback route is untouched by construction — it never reads the
    # encoder — so its slice must come back as the same float the bundle
    # produced, not to six places but to the last bit. That held before O-9's
    # repair and it holds after it, which is exactly why it is worth asserting:
    # a near miss would mean the routing or the feature path moved, and the warm
    # number would then not be attributable to the columns under test.
    for label, actual, expected in (
        ("cold ndcg@10", result.cold.ndcg, STEP1_INCUMBENT_COLD_NDCG),
        ("cold recall@10", result.cold.recall, STEP1_INCUMBENT_COLD_RECALL),
    ):
        if full_data and actual != expected:
            raise BundleReproductionError(
                f"{name} {label} is {actual!r}, expected the per-route bundle's {expected!r} "
                "exactly. Only the learned route is under test; a different cold slice "
                "means something else moved with it."
            )

    importances = ranker.feature_importances(importance_type="gain")
    logger.info("[%s] gain importances: %s", name, importances)

    return result, ArmRecord(
        name=name,
        booster_dir=booster_dir,
        booster_sha256=booster_sha256,
        fit_seconds=round(fit_seconds, 1),
        rank_seconds=round(rank_seconds, 1),
        importances=importances,
        feature_columns=feature_columns,
    )


def run(shared: SharedInputs) -> tuple[EvalResult, dict[str, object]]:
    """Train the ten-column learned-route booster, compose it, and score it."""
    features = SasrecScoreFeatures(shared.sasrec.model)
    shared.sasrec.score_features = features
    logger.info(
        "[%s] item embedding matrices for %d items; learned-route contract is %d columns",
        ARM,
        features.n_items,
        len(LEARNED_ROUTE_FEATURE_COLUMNS),
    )

    started = time.perf_counter()
    features_df, group_sizes, labels, dropped = build_ranker_training_set(
        positives=shared.positives,
        source=shared.sasrec,
        feature_index=shared.feature_index,
        history_by_user=shared.history_by_user,
        n_negatives=NEGATIVES_PER_POSITIVE,
        rng=np.random.default_rng(shared.seed),
    )
    build_seconds = time.perf_counter() - started

    # Step 1's shape is a *pre-O-9* fact, so it is no longer the thing to assert.
    # The repair gives a slate to every positive whose strict prefix was shorter
    # than the encoder's window — 34,190 of them — so the training set must grow.
    # It need not grow by exactly that many: the fix also moves full-length
    # queries by ~6e-08, which can reorder the tail of a top-500 and drop the
    # occasional positive that was sitting at rank 500. So the invariant worth
    # holding is directional, and a *shrinking* set would mean something other
    # than the repair moved.
    #
    # A subsampled smoke run splits at its own cutoff and legitimately has its
    # own shape, so this applies to the full run only — the same rule
    # `prepare_shared` applies to the ADR 0011 cohort. A smoke run proves the
    # pipeline; it never produces a number anybody records.
    full_data = shared.sample_fraction == 1.0
    shape = (len(group_sizes), sum(group_sizes))
    if full_data and shape[0] <= STEP1_SASREC_SHAPE[0]:
        raise BundleReproductionError(
            f"rebuilt SASRec training set is {shape} groups/rows, which is not larger than "
            f"step 1's pre-O-9 {STEP1_SASREC_SHAPE}. The fast-path repair gives a slate to "
            "34,190 previously empty positives, so the set has to grow; it did not."
        )
    if full_data:
        logger.info(
            "[%s] training set grew from step 1's pre-O-9 %s to %s (+%d groups) — the "
            "positives O-9 was silently dropping",
            ARM,
            STEP1_SASREC_SHAPE,
            shape,
            shape[0] - STEP1_SASREC_SHAPE[0],
        )
    else:
        logger.warning(
            "[%s] smoke run at sample fraction %s: %s groups/rows, and the reproduction "
            "guards are skipped. Nothing here is a result.",
            ARM,
            shared.sample_fraction,
            shape,
        )
    if list(features_df.columns) != LEARNED_ROUTE_FEATURE_COLUMNS:
        raise BundleReproductionError(
            f"training frame columns are {list(features_df.columns)}, expected the "
            f"learned-route contract {LEARNED_ROUTE_FEATURE_COLUMNS}"
        )
    missing_rows = missing_row_count(features_df)
    logger.info(
        "[%s] training set: %d groups, %d rows, %d dropped, %d rows on the missing sentinel",
        ARM,
        len(group_sizes),
        sum(group_sizes),
        dropped,
        missing_rows,
    )

    incumbent_path = shared.booster_root / STEP1_INCUMBENT_RUN / "ranker.txt"
    if not incumbent_path.is_file():
        raise BundleReproductionError(f"step 1 fallback booster is missing: {incumbent_path}")
    fallback_booster = LGBMRanker.load_model(incumbent_path)
    fallback_sha256 = _file_sha256(incumbent_path)
    logger.info("[%s] fallback booster sha256=%s", ARM, fallback_sha256)

    # Two boosters from one training set. `LGBMRanker` selects its own columns by
    # name, so the control and the arm see the identical frame, the identical
    # groups and the identical labels, drawn once from one RNG stream — the only
    # difference between them is whether the two SASRec columns are in the
    # contract. That is the ablation; running it as a second `build_ranker_
    # training_set` would reintroduce the doubt it exists to remove.
    control_result, control = _fit_compose_and_score(
        shared,
        name=CONTROL_ARM,
        feature_columns=list(FEATURE_COLUMNS),
        features_df=features_df,
        group_sizes=group_sizes,
        labels=labels,
        fallback_booster=fallback_booster,
        holdout_features=None,
        full_data=full_data,
    )
    result, arm = _fit_compose_and_score(
        shared,
        name=ARM,
        feature_columns=list(LEARNED_ROUTE_FEATURE_COLUMNS),
        features_df=features_df,
        group_sizes=group_sizes,
        labels=labels,
        fallback_booster=fallback_booster,
        holdout_features=holdout_feature_builder(shared, features),
        full_data=full_data,
    )

    run_id_holder: dict[str, str] = {}
    _log_bundle_run(
        shared,
        name=ARM,
        result=result,
        booster_sha256=arm.booster_sha256,
        importances=arm.importances,
        params={
            "ranker_training_set_seconds": round(build_seconds, 1),
            "ranker_fit_seconds": arm.fit_seconds,
            "rank_seconds": arm.rank_seconds,
            "n_new_boosters": 1,
            "n_ranker_positives_used": len(group_sizes),
            "n_ranker_positives_dropped": dropped,
            "n_ranker_training_rows": sum(group_sizes),
            "n_missing_sentinel_rows": missing_rows,
            "learned_route_feature_columns": len(LEARNED_ROUTE_FEATURE_COLUMNS),
            "fallback_route_feature_columns": len(fallback_booster.feature_columns),
            "fallback_booster_sha256": fallback_sha256,
            "num_leaves": LGBMRankerConfig(seed=shared.seed).num_leaves,
            "learning_rate": LGBMRankerConfig(seed=shared.seed).learning_rate,
            "num_boost_round": LGBMRankerConfig(seed=shared.seed).num_boost_round,
            "booster_directory": arm.booster_dir,
            "n_control_boosters": 1,
            "control_booster_sha256": control.booster_sha256,
            "control_booster_directory": control.booster_dir,
            "control_warm_ndcg_at_k": control_result.warm.ndcg,
            "control_overall_ndcg_at_k": control_result.overall.ndcg,
        },
        tags={
            "bundle_composition": "warm=sasrec+score-features;cold=popularity+incumbent",
            "adr_0018_increment": "1",
            "sasrec_ranker_feature_set": "aggregates-plus-sasrec-score-logit-v1",
            "sasrec_ranker_missing_sentinel": "nan",
            "incumbent_bundle_run_id": INCUMBENT_RUN,
        },
        run_id_holder=run_id_holder,
    )

    detail: dict[str, object] = {
        "n_new_boosters": 1,
        "groups": len(group_sizes),
        "rows": sum(group_sizes),
        "dropped_positives": dropped,
        "missing_sentinel_rows": missing_rows,
        "booster_dir": arm.booster_dir,
        "booster_sha256": arm.booster_sha256,
        "fallback_booster_sha256": fallback_sha256,
        "learned_route_feature_columns": list(LEARNED_ROUTE_FEATURE_COLUMNS),
        "reproduces_bundle_cold_exactly": full_data,
        "full_data": full_data,
        "run_id": run_id_holder["run_id"],
        "feature_importance_gain": arm.importances,
        "seconds": {
            "training_set": round(build_seconds, 1),
            "fit": arm.fit_seconds,
            "rank": arm.rank_seconds,
        },
        # The ablation. Same frame, same groups, same labels, eight columns
        # instead of ten — so the difference between these two numbers is the
        # feature set alone, with no retrain asymmetry left in it.
        "control": {
            "arm": CONTROL_ARM,
            "feature_columns": len(FEATURE_COLUMNS),
            "booster_sha256": control.booster_sha256,
            "booster_dir": control.booster_dir,
            "metrics": {
                "warm_recall_at_k": control_result.warm.recall,
                "warm_ndcg_at_k": control_result.warm.ndcg,
                "cold_recall_at_k": control_result.cold.recall,
                "cold_ndcg_at_k": control_result.cold.ndcg,
                "overall_recall_at_k": control_result.overall.recall,
                "overall_ndcg_at_k": control_result.overall.ndcg,
            },
            "feature_importance_gain": control.importances,
            "seconds": {
                "fit": control.fit_seconds,
                "rank": control.rank_seconds,
            },
        },
    }
    return result, detail


def document(
    result: EvalResult, detail: dict[str, object], decision: GateDecision
) -> dict[str, object]:
    return {
        "arm": ARM,
        "run_id": detail["run_id"],
        "incumbent_run_id": INCUMBENT_RUN,
        "adr": "0018",
        "increment": 1,
        "metrics": {
            "warm_recall_at_k": result.warm.recall,
            "warm_ndcg_at_k": result.warm.ndcg,
            "cold_recall_at_k": result.cold.recall,
            "cold_ndcg_at_k": result.cold.ndcg,
            "overall_recall_at_k": result.overall.recall,
            "overall_ndcg_at_k": result.overall.ndcg,
        },
        "n_warm_users": result.n_warm_users,
        "n_cold_users": result.n_cold_users,
        "detail": detail,
        "gate": decision.to_dict(),
        "warm_primary_reading": warm_primary_reading(result, per_route_bundle_result()),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    shared = prepare_shared()
    result, detail = run(shared)

    incumbent = per_route_bundle_result()
    decision = promotion_decision(result, incumbent)
    logger.info("ADR 0001 gate, %s vs the O-9-fixed per-route bundle %s:", ARM, INCUMBENT_RUN)
    for line in decision.summary().splitlines():
        logger.info("  %s", line)

    warm_primary = warm_primary_reading(result, incumbent)
    logger.info(
        "Warm-primary reading (O-1 undecided): warm %+.2f%% against +3.00%%, "
        "cold %+.2f%% against a 5.00%% tolerance -> %s",
        100.0 * float(warm_primary["warm_relative_change"]),  # type: ignore[arg-type]
        100.0 * float(warm_primary["cold_relative_change"]),  # type: ignore[arg-type]
        "PROMOTE" if warm_primary["would_promote"] else "DO NOT PROMOTE",
    )

    control_metrics = detail["control"]["metrics"]  # type: ignore[index]
    control_warm = float(control_metrics["warm_ndcg_at_k"])
    logger.info(
        "Feature ablation on one training set — eight columns %.6f warm ndcg@10, ten columns "
        "%.6f, difference %+.2f%%. Same frame, same groups, same labels, retrained under "
        "identical conditions, so the gap is the feature set and nothing else.",
        control_warm,
        result.warm.ndcg,
        100.0 * (result.warm.ndcg - control_warm) / control_warm,
    )

    output = Path(shared.booster_root) / f"increment1-{ARM}-{detail['run_id']}.json"
    output.write_text(
        json.dumps(document(result, detail, decision), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote %s", output)


if __name__ == "__main__":
    main()
