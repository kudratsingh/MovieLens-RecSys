"""What `evaluate()` publishes per user, and whether a study can consume it.

The retrieval tolerance study measures the noise floor of a slice mean by
resampling *paired* per-user differences, so it needs the numbers behind the
means and it needs both runs' numbers to be about the same people. Everything
here guards one of the two ways that can quietly go wrong:

  1. The vector stops describing the run it is attached to. The study refuses a
     vector whose mean does not reproduce the published slice recall, so that
     reconstruction has to hold by construction rather than by luck — which
     means no rounding on the way out and no user counted twice.
  2. The user ids stop being the dataset's. A vector keyed by position, or by
     ids reindexed per run, would pair two runs' users at random and produce a
     bootstrap standard error that is not about anything.

The last test runs the whole path — evaluate, export, write a file, load it
back through the study's own loader, measure — on a fixture whose two noise
components are hand-set, because a format that satisfies the loader's types but
not its checks would look fine in every other test here.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from src.evaluation.manifest import PROTOCOL_SCHEMA_VERSION
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT_VERSION,
    RECALL_SLICES,
    EvalResult,
    UserMetrics,
    evaluate,
    per_user_recall_document,
)
from src.evaluation.retrieval_gate import ITEMITEM_MODEL_TYPE
from src.evaluation.tolerance_study import (
    MIN_BOOTSTRAP_REPLICATES,
    STUDY_SCHEMA_VERSION,
    StudyOptions,
    ToleranceStudyStatus,
    measure_retrieval_tolerance,
    study_inputs_from_json,
)

# Deliberately sparse and far from zero: an implementation that reindexed users
# to 0..n-1 would still pass every count-based assertion below.
WARM_USERS = tuple(range(9_001, 9_001 + 16 * 7, 7))
COLD_USERS = tuple(range(500_003, 500_003 + 24 * 11, 11))

# Ten holdout items per user makes each user's recall a tenth, so a run is
# described entirely by how many of them it retrieved.
HOLDOUT_ITEMS = 10

STUDY_MODEL = "sasrec"
GATE_CONFIG = "sasrec-fixture-v1"

FAST = StudyOptions(bootstrap_replicates=MIN_BOOTSTRAP_REPLICATES)


def _train_counts() -> dict[int, int]:
    counts = {user_id: COLD_START_THRESHOLD for user_id in WARM_USERS}
    counts.update({user_id: 1 for user_id in COLD_USERS})
    return counts


def _holdout(users: Sequence[int] = WARM_USERS + COLD_USERS) -> dict[int, set[int]]:
    return {user_id: {user_id * 100 + i for i in range(HOLDOUT_ITEMS)} for user_id in users}


def _recommendations(hits: Mapping[int, int]) -> dict[int, list[int]]:
    """Retrieve exactly ``hits[user]`` of each user's held-out items."""
    return {user_id: [user_id * 100 + i for i in range(n_hits)] for user_id, n_hits in hits.items()}


def _run(overrides: Mapping[int, int] | None = None) -> EvalResult:
    """Evaluate a run where every user recalls half their items but the named few."""
    hits = {user_id: 5 for user_id in WARM_USERS + COLD_USERS}
    hits.update(overrides or {})
    return evaluate(_recommendations(hits), _holdout(), _train_counts(), k=K_CANDIDATES)


def _protocol_payload() -> dict[str, object]:
    """A complete canonical protocol; the study rejects anything less."""
    return {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "raw_data_revision": "md5:fixture.dir",
        "derived_snapshot_hash": "sha256:split-fixture",
        "event_schema_version": "movielens-rating-v1",
        "train_cutoff": 1_000,
        "holdout_start": 1_000,
        "holdout_end": 2_000,
        "sealed_test_boundary": 3_000,
        "backtest_window_id": "fixed-holdout-v1",
        "timestamp_unit": "unix-seconds",
        "timezone": "UTC",
        "label_contract_version": "implicit-positive-v1",
        "relevance_definition": "every-rating-is-positive",
        "eligible_user_policy": "at-least-one-holdout-positive",
        "catalog_fingerprint": "sha256:catalog-fixture",
        "unknown_item_policy": "exclude",
        "cold_start_threshold": COLD_START_THRESHOLD,
        "learned_routing_policy": "history-count-gte-threshold",
        "fallback_policy": "training-popularity",
        "positive_history_filter": "strict-prior-equal-timestamp-excluded-v1",
        "seen_item_filter": "watched-strictly-prior-excluded-v1",
        "dismissal_filter": "dismissals-absent-from-dataset-v1",
        "target_filter": "target-retained-never-negative-v1",
        "candidate_filter": "unfiltered-retrieval-then-point-in-time-exclusions-v1",
        "feature_contract_version": "candidate-v1",
        "point_in_time_semantics": "strictly-earlier-event-time",
        "stage": "retrieval",
        "primary_metric": "recall",
        "metric_contract_version": "evaluation-v1",
        "metric_aggregation": "unweighted-user-mean",
        "k": K_CANDIDATES,
        "slice_definition": "warm-gte-10;cold-lt-10;overall=union",
    }


# --- what evaluate() now returns --------------------------------------------


def test_every_slice_vector_reproduces_the_mean_the_run_publishes() -> None:
    # The study's central sanity check on evidence, asserted here so it can
    # only fail for a real reason rather than because of how we serialize.
    result = _run({WARM_USERS[0]: 9, COLD_USERS[0]: 1, COLD_USERS[1]: 0})

    for slice_name in RECALL_SLICES:
        vector = result.per_user_recall[slice_name]
        published: float = getattr(result, slice_name).recall
        assert math.isclose(statistics.fmean(vector.values()), published, rel_tol=1e-12)


def test_the_vectors_carry_the_datasets_own_user_ids() -> None:
    result = _run()

    assert set(result.per_user_recall["warm"]) == set(WARM_USERS)
    assert set(result.per_user_recall["cold"]) == set(COLD_USERS)
    # The property that makes a paired bootstrap possible: two runs over one
    # holdout key their vectors identically, whatever each model scored.
    other = _run({WARM_USERS[3]: 10})
    assert set(other.per_user_recall["overall"]) == set(result.per_user_recall["overall"])


def test_warm_and_cold_membership_matches_the_slicing_the_run_used() -> None:
    # Including the boundary in both directions and a user the training counts
    # never saw — the ``.get(user_id, 0)`` case, which is cold.
    holdout = {1: {100}, 2: {200}, 3: {300}}
    train_counts = {1: COLD_START_THRESHOLD, 2: COLD_START_THRESHOLD - 1}
    result = evaluate({1: [100], 2: [200], 3: [300]}, holdout, train_counts)

    assert set(result.per_user_recall["warm"]) == {1}
    assert set(result.per_user_recall["cold"]) == {2, 3}
    assert len(result.per_user_recall["warm"]) == result.n_warm_users
    assert len(result.per_user_recall["cold"]) == result.n_cold_users


def test_overall_is_the_union_of_the_two_slices_and_counts_nobody_twice() -> None:
    result = _run()
    warm = result.per_user_recall["warm"]
    cold = result.per_user_recall["cold"]

    assert not set(warm) & set(cold)
    assert result.per_user_recall["overall"] == {**warm, **cold}
    assert len(result.per_user_recall["overall"]) == result.n_warm_users + result.n_cold_users


def test_the_published_means_are_unchanged_by_keeping_the_users() -> None:
    # Backward compatibility, stated as a number rather than as an intention:
    # 15 of the 16 warm users at 0.5 and one at 0.9 is a warm mean of 0.525.
    result = _run({WARM_USERS[0]: 9})

    assert result.warm.recall == pytest.approx(0.525)
    assert result.cold.recall == pytest.approx(0.5)
    assert result.overall.recall == pytest.approx(((15 * 0.5 + 0.9) + 24 * 0.5) / 40)
    assert result.k == K_CANDIDATES


def _hand_built_result() -> EvalResult:
    """A result nobody evaluated — the shape `mean_eval_result` returns."""
    return EvalResult(
        warm=UserMetrics(recall=0.5, ndcg=0.0),
        cold=UserMetrics(recall=0.5, ndcg=0.0),
        overall=UserMetrics(recall=0.5, ndcg=0.0),
        n_warm_users=1,
        n_cold_users=1,
    )


def test_a_result_that_did_not_come_from_evaluate_has_no_vectors() -> None:
    # A mean across seeds belongs to no run, so it must not look like one.
    assert _hand_built_result().per_user_recall == {}


# --- the exported document --------------------------------------------------


def _document(result: EvalResult, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "run_id": "fixture-run",
        "model_type": STUDY_MODEL,
        "seed": 101,
        "configuration_id": GATE_CONFIG,
    }
    arguments.update(overrides)
    return per_user_recall_document(result, **arguments)  # type: ignore[arg-type]


def test_the_document_carries_the_run_object_the_study_loader_reads() -> None:
    result = _run()
    document = _document(result, protocol=_protocol_payload())

    assert document["artifact_schema_version"] == PER_USER_RECALL_ARTIFACT_VERSION
    assert document["run_id"] == "fixture-run"
    assert document["seed"] == 101
    assert document["k"] == K_CANDIDATES
    assert document["metrics"] == {
        "warm_recall": result.warm.recall,
        "cold_recall": result.cold.recall,
        "overall_recall": result.overall.recall,
        "n_warm_users": result.n_warm_users,
        "n_cold_users": result.n_cold_users,
    }
    assert document["protocol"] == _protocol_payload()
    assert set(document["per_user_recall"]) == set(RECALL_SLICES)  # type: ignore[arg-type]


def test_user_ids_survive_json_as_the_ids_they_were() -> None:
    result = _run()
    vectors = _document(result)["per_user_recall"]

    # JSON has no integer keys; the ids are stringified, never renumbered.
    assert set(vectors["warm"]) == {str(user_id) for user_id in WARM_USERS}  # type: ignore[index]
    reloaded = json.loads(json.dumps(vectors))
    assert {int(key) for key in reloaded["cold"]} == set(COLD_USERS)


def test_recall_values_are_exported_unrounded() -> None:
    # A tenth is not exactly representable, so rounding to a "reasonable"
    # number of decimals would break the study's mean-reconstruction check on
    # a vector that is in fact the right one.
    result = _run({WARM_USERS[0]: 3})
    exported = json.loads(json.dumps(_document(result)["per_user_recall"]))

    assert exported["warm"][str(WARM_USERS[0])] == 3 / HOLDOUT_ITEMS
    assert statistics.fmean(exported["overall"].values()) == pytest.approx(
        result.overall.recall, rel=1e-12
    )


def test_the_protocol_is_omitted_rather_than_invented_when_absent() -> None:
    # The trainers now pass one, but this function must not manufacture a
    # protocol for a caller that has none — an absent key is a visible gap in
    # the evidence document, and a fabricated one is not.
    assert "protocol" not in _document(_run())


@pytest.mark.parametrize("blank", ["", "   ", " padded"])
def test_an_unusable_identifier_is_refused_at_export(blank: str) -> None:
    with pytest.raises(ValueError, match="configuration_id"):
        _document(_run(), configuration_id=blank)


def test_a_result_without_vectors_cannot_be_exported() -> None:
    with pytest.raises(ValueError, match="evaluate"):
        _document(_hand_built_result())


def test_a_vector_that_disagrees_with_the_run_is_refused_at_export() -> None:
    result = _run()
    del result.per_user_recall["cold"][COLD_USERS[0]]

    with pytest.raises(ValueError, match="cold vector holds"):
        _document(result)


# --- the whole path ---------------------------------------------------------


def test_evidence_written_from_evaluate_measures_a_tolerance(tmp_path: Path) -> None:
    """evaluate() → document → file → the study's loader → a proposal.

    The fixture sets both noise terms by hand. Every user recalls half their
    items under the incumbent, and each study run moves exactly one warm and
    one cold user by a single item, so the seed spread and the paired
    difference are both small enough to land under the study's 3% cap while
    being unmistakably nonzero — a degenerate study is refused, and rightly.
    """
    incumbent = _run()
    study = {
        101: _run({WARM_USERS[0]: 6, COLD_USERS[0]: 6}),
        202: _run({WARM_USERS[1]: 4, COLD_USERS[1]: 4}),
        303: _run({WARM_USERS[2]: 4, COLD_USERS[2]: 6}),
    }

    evidence = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "model_type": STUDY_MODEL,
        "gate_configuration_id": GATE_CONFIG,
        "incumbent": per_user_recall_document(
            incumbent,
            run_id="itemitem-incumbent",
            model_type=ITEMITEM_MODEL_TYPE,
            seed=None,
            configuration_id="itemitem-cosine-n200-threshold",
            protocol=_protocol_payload(),
        ),
        "study_runs": [
            per_user_recall_document(
                result,
                run_id=f"{STUDY_MODEL}-{seed}",
                model_type=STUDY_MODEL,
                seed=seed,
                configuration_id=GATE_CONFIG,
                protocol=_protocol_payload(),
            )
            for seed, result in study.items()
        ],
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")

    inputs = study_inputs_from_json(path.read_text(encoding="utf-8"))

    # Nothing was lost or renumbered on the way through the file.
    assert inputs.incumbent.per_user_recall["cold"] == incumbent.per_user_recall["cold"]
    assert inputs.study_runs[0].per_user_recall["overall"] == study[101].per_user_recall["overall"]

    report = measure_retrieval_tolerance(inputs, options=FAST)
    assert report.status is ToleranceStudyStatus.PROPOSED, report.summary()

    tolerance = report.as_tolerance()
    assert 0.005 <= tolerance.cold <= 0.03
    assert 0.005 <= tolerance.overall <= 0.03
    # Both terms measured on both gating slices: a study that silently lost the
    # vectors would still propose, from the seed term alone.
    for slice_name in ("cold", "overall"):
        measured = next(m for m in report.slices if m.slice_name == slice_name)
        assert measured.seed_half_width > 0
        assert measured.bootstrap_half_width > 0
