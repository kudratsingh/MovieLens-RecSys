"""A trainer's protocol must survive the strict consumer that will read it.

The manifest was a contract with no producer: `retrieval_run_from_mlflow`
would have rejected every run that existed. These tests hold the producer to
the consumer's standard rather than to its own, so the round trip is the
centrepiece — build a manifest from a synthetic training context, put it
through the MLflow envelope, and read it back the way the gate does.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.data.split import TemporalSplit, temporal_split
from src.evaluation.manifest import (
    PROTOCOL_SCHEMA_VERSION,
    ProtocolManifest,
    ProtocolManifestError,
)
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, K
from src.evaluation.retrieval_gate import (
    MLFLOW_DETERMINISTIC_PARAM,
    MLFLOW_MODEL_TYPE_TAG,
    MLFLOW_SEED_PARAM,
    RetrievalRunNotUsableError,
    retrieval_run_from_mlflow,
)
from src.training import protocol_manifest
from src.training.protocol_manifest import (
    ProtocolDerivationError,
    build_protocol,
    catalog_fingerprint,
    derived_snapshot_hash,
    raw_data_revision,
    routing_policy_value,
    run_envelope,
)

# Enough users and days that the 80th-percentile cutoff lands inside the frame
# and leaves a populated holdout, which is all `build_protocol` needs from a
# split. The values are otherwise arbitrary.
_N_USERS = 20
_N_ITEMS = 12
_FIRST_TIMESTAMP = 1_500_000_000
_DAY = 86_400


def _ratings(*, items: int = _N_ITEMS, users: int = _N_USERS) -> pd.DataFrame:
    rows = []
    for user in range(1, users + 1):
        for offset in range(10):
            rows.append(
                {
                    "userId": user,
                    "movieId": 100 + (user + offset) % items,
                    "rating": 0.5 * (1 + (user + offset) % 9),
                    "timestamp": _FIRST_TIMESTAMP + (user * 10 + offset) * _DAY,
                }
            )
    frame = pd.DataFrame(rows)
    return frame.astype(
        {"userId": "int64", "movieId": "int64", "rating": "float64", "timestamp": "int64"}
    )


def _split(frame: pd.DataFrame | None = None) -> TemporalSplit:
    return temporal_split(frame if frame is not None else _ratings())


def _protocol(
    split: TemporalSplit | None = None,
    fitted: pd.DataFrame | None = None,
    **changes: object,
) -> ProtocolManifest:
    resolved = split or _split()
    arguments: dict[str, object] = {
        "split": resolved,
        "fitted_frame": resolved.train if fitted is None else fitted,
        "learned_routing_policy": routing_policy_value(COLD_START_THRESHOLD),
        "stage": "retrieval",
        "k": K_CANDIDATES,
    }
    arguments.update(changes)
    return build_protocol(**arguments)  # type: ignore[arg-type]


class _Info:
    def __init__(self, status: str = "FINISHED") -> None:
        self.run_id = "synthetic-run"
        self.status = status


class _Data:
    def __init__(self, params: dict[str, str], tags: dict[str, str]) -> None:
        self.params = params
        self.tags = tags
        self.metrics = {
            "warm_recall_at_k_candidates": 0.41,
            "cold_recall_at_k_candidates": 0.52,
            "overall_recall_at_k_candidates": 0.43,
            "n_warm_users": 14.0,
            "n_cold_users": 6.0,
        }


class _MlflowRun:
    """The shape `retrieval_run_from_mlflow` reads, and nothing more."""

    def __init__(self, params: dict[str, str], tags: dict[str, str]) -> None:
        self.info = _Info()
        self.data = _Data(params, tags)


def _run_from(protocol: ProtocolManifest, *, deterministic: bool, seed: int | None) -> _MlflowRun:
    envelope = run_envelope(protocol, deterministic=deterministic, seed=seed)
    return _MlflowRun(
        params=envelope.params,
        tags={**envelope.tags, MLFLOW_MODEL_TYPE_TAG: "itemitem_cosine"},
    )


# --- the round trip that matters --------------------------------------------


def test_an_emitted_protocol_is_readable_by_the_gate_that_will_judge_it() -> None:
    protocol = _protocol()

    run = retrieval_run_from_mlflow(_run_from(protocol, deterministic=True, seed=None))

    assert run.protocol == protocol
    assert run.protocol.semantic_hash == protocol.semantic_hash
    assert run.deterministic is True
    assert run.seed is None
    assert run.model_type == "itemitem_cosine"


def test_the_payload_the_run_carries_recomputes_to_the_hash_the_run_recorded() -> None:
    protocol = _protocol()
    envelope = run_envelope(protocol, deterministic=False, seed=7)

    rebuilt = ProtocolManifest.from_json(envelope.tags["evaluation_protocol"])

    assert rebuilt == protocol
    assert envelope.params["evaluation_protocol_hash"] == rebuilt.semantic_hash
    assert envelope.params["evaluation_protocol_schema_version"] == str(PROTOCOL_SCHEMA_VERSION)
    assert envelope.params[MLFLOW_SEED_PARAM] == "7"


def test_two_runs_over_identical_context_hash_equal() -> None:
    frame = _ratings()
    shuffled = frame.sample(frac=1.0, random_state=13).reset_index(drop=True)

    first = _protocol(_split(frame))
    second = _protocol(_split(shuffled))

    # Row order is a property of how the rows came out of Postgres, not of the
    # question being asked, so it must not be able to split one protocol in two.
    assert first == second
    assert first.semantic_hash == second.semantic_hash


# --- the two content-derived fields -----------------------------------------


def test_a_changed_catalog_changes_the_fingerprint() -> None:
    split = _split()
    smaller = split.train[split.train["movieId"] != split.train["movieId"].max()]

    assert catalog_fingerprint(smaller) != catalog_fingerprint(split.train)
    assert _protocol(split, smaller).semantic_hash != _protocol(split).semantic_hash


def test_the_catalog_fingerprint_is_over_the_item_set_and_not_the_rows() -> None:
    split = _split()
    duplicated = pd.concat([split.train, split.train], ignore_index=True)
    reordered = split.train.sort_values("movieId", ascending=False)

    # Same eligible items, three different frames: the catalog is what a model
    # may retrieve, not how many times each title was rated on the way in.
    assert catalog_fingerprint(duplicated) == catalog_fingerprint(split.train)
    assert catalog_fingerprint(reordered) == catalog_fingerprint(split.train)


def test_the_snapshot_hash_ignores_row_order_and_component_order() -> None:
    split = _split()
    shuffled = split.train.sample(frac=1.0, random_state=3).reset_index(drop=True)

    assert derived_snapshot_hash({"train": split.train, "holdout": split.holdout}) == (
        derived_snapshot_hash({"holdout": split.holdout, "train": shuffled})
    )


def test_the_snapshot_hash_sees_a_row_move_between_slices() -> None:
    split = _split()
    moved_row = split.holdout.iloc[[0]]
    grown_train = pd.concat([split.train, moved_row], ignore_index=True)

    # A flat union of the two frames would hash this as the same snapshot. It
    # is not: one of those rows was trained on rather than scored.
    assert derived_snapshot_hash({"train": grown_train, "holdout": split.holdout}) != (
        derived_snapshot_hash({"train": split.train, "holdout": split.holdout})
    )


def test_the_snapshot_hash_sees_a_changed_value_in_any_column() -> None:
    split = _split()
    baseline = derived_snapshot_hash({"train": split.train, "holdout": split.holdout})

    for column, replacement in (("rating", 4.75), ("timestamp", 1), ("movieId", 999_999)):
        edited = split.train.copy()
        row = edited.index[0]
        # The replacement has to actually differ, or this test passes by
        # comparing a frame with itself.
        assert edited.loc[row, column] != replacement
        edited.loc[row, column] = replacement
        assert derived_snapshot_hash({"train": edited, "holdout": split.holdout}) != baseline


def test_a_column_that_cannot_be_hashed_losslessly_is_refused() -> None:
    split = _split()
    lossy = split.train.astype({"userId": "float64"})

    with pytest.raises(ProtocolDerivationError, match="integer column"):
        derived_snapshot_hash({"train": lossy, "holdout": split.holdout})

    with pytest.raises(ProtocolDerivationError, match="missing rating"):
        derived_snapshot_hash({"train": split.train.drop(columns=["rating"])})


def test_a_snapshot_needs_at_least_one_named_component() -> None:
    with pytest.raises(ProtocolDerivationError, match="at least one named component"):
        derived_snapshot_hash({})


# --- the DVC revision -------------------------------------------------------


def test_the_committed_pointer_yields_an_algorithm_qualified_revision() -> None:
    revision = raw_data_revision()

    # The suffix is DVC's own marker for a tracked directory; the prefix is the
    # algorithm, without which two digests from different algorithms would be
    # unequal for a reason nobody could see.
    algorithm, _, digest = revision.partition(":")
    assert algorithm == "md5"
    assert digest.endswith(".dir")


def test_a_pointer_that_cannot_be_read_refuses_rather_than_guesses(tmp_path: Path) -> None:
    with pytest.raises(ProtocolDerivationError, match="cannot read the DVC pointer"):
        raw_data_revision(tmp_path / "absent.dvc")

    ambiguous = tmp_path / "two-outs.dvc"
    ambiguous.write_text("outs:\n- md5: aaa\n  path: a\n- md5: bbb\n  path: b\n")
    with pytest.raises(ProtocolDerivationError, match="exactly one tracked output"):
        raw_data_revision(ambiguous)

    digestless = tmp_path / "no-digest.dvc"
    digestless.write_text("outs:\n- path: a\n  hash: md5\n")
    with pytest.raises(ProtocolDerivationError, match="carries no 'md5' digest"):
        raw_data_revision(digestless)


def test_the_declared_algorithm_is_the_one_that_is_read(tmp_path: Path) -> None:
    pointer = tmp_path / "sha.dvc"
    pointer.write_text("outs:\n- sha256: deadbeef\n  hash: sha256\n  path: a\n")

    assert raw_data_revision(pointer) == "sha256:deadbeef"


# --- the fields that come from the split ------------------------------------


def test_the_boundaries_are_the_split_the_run_computed() -> None:
    split = _split()
    protocol = _protocol(split)

    assert protocol.train_cutoff == split.cutoff
    assert protocol.holdout_start == split.cutoff
    assert protocol.holdout_end == split.holdout_end
    assert str(split.cutoff) in protocol.backtest_window_id
    assert str(split.holdout_end) in protocol.backtest_window_id


def test_the_sealed_boundary_is_where_this_split_reserves_the_test_partition() -> None:
    split = _split()
    protocol = _protocol(split)

    # `temporal_split` sends every row at or after `holdout_end` to test, so the
    # seal opens exactly there. The run asserts it rather than leaving a reader
    # to infer it from the absence of a test metric.
    assert protocol.sealed_test_boundary == split.holdout_end
    assert (split.test["timestamp"] >= protocol.sealed_test_boundary).all()


def test_the_sealed_boundary_participates_in_the_hash() -> None:
    protocol = _protocol()
    moved = replace(protocol, sealed_test_boundary=protocol.sealed_test_boundary + _DAY)

    assert moved.semantic_hash != protocol.semantic_hash


def test_a_degenerate_split_cannot_produce_a_protocol() -> None:
    empty = _ratings().iloc[0:0]

    # `temporal_split` returns zero boundaries for an empty frame. There is no
    # evaluation question there, and the manifest is where that is caught.
    with pytest.raises(ProtocolManifestError):
        _protocol(_split(empty))


# --- stage, metric and routing ----------------------------------------------


def test_the_primary_metric_follows_the_stage_rather_than_the_caller() -> None:
    assert _protocol(stage="retrieval", k=K_CANDIDATES).primary_metric == "recall"
    assert _protocol(stage="ranking", k=K).primary_metric == "ndcg"


def test_a_top_ten_ranking_run_cannot_be_pooled_with_retrieval_evidence() -> None:
    ranking = _protocol(stage="ranking", k=K)
    retrieval = _protocol(stage="retrieval", k=K_CANDIDATES)

    # The separation the two-stage architecture needs: a recall@500 claim cannot
    # be met with a top-10 number, and the gate sees that as three differing
    # fields rather than having to be told.
    assert set(ranking.mismatches(retrieval)) == {"stage", "primary_metric", "k"}
    assert ranking.semantic_hash != retrieval.semantic_hash


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [
        (COLD_START_THRESHOLD, f"train-history-count-gte-{COLD_START_THRESHOLD}-v1"),
        (None, protocol_manifest.INDEX_MEMBERSHIP_ROUTING),
    ],
)
def test_the_routing_value_names_the_rule_the_model_was_given(
    threshold: int | None, expected: str
) -> None:
    assert routing_policy_value(threshold) == expected


def test_the_routing_rule_and_the_slice_boundary_are_not_the_same_field() -> None:
    opted_out = _protocol(learned_routing_policy=routing_policy_value(None))

    # `evaluate` slices warm from cold at ADR 0001's threshold whatever the
    # model routes on, so the opt-out changes one field and not the other.
    assert opted_out.cold_start_threshold == COLD_START_THRESHOLD
    assert opted_out.learned_routing_policy == protocol_manifest.INDEX_MEMBERSHIP_ROUTING
    assert opted_out.semantic_hash != _protocol().semantic_hash


# --- the determinism and seed contract --------------------------------------


def test_the_seed_contract_is_enforced_where_it_can_still_be_fixed() -> None:
    protocol = _protocol()

    with pytest.raises(ProtocolDerivationError, match="must not record a training seed"):
        run_envelope(protocol, deterministic=True, seed=42)
    with pytest.raises(ProtocolDerivationError, match="must record its integer training seed"):
        run_envelope(protocol, deterministic=False, seed=None)

    params = run_envelope(protocol, deterministic=True, seed=None).params
    assert params[MLFLOW_DETERMINISTIC_PARAM] == "true"
    assert MLFLOW_SEED_PARAM not in params


def test_a_stochastic_run_carries_the_seed_the_gate_needs_to_group_it() -> None:
    protocol = _protocol()

    run = retrieval_run_from_mlflow(_run_from(protocol, deterministic=False, seed=13))

    assert run.deterministic is False
    assert run.seed == 13


def test_a_tampered_payload_no_longer_matches_its_recorded_hash() -> None:
    protocol = _protocol()
    envelope = run_envelope(protocol, deterministic=True, seed=None)
    tampered = replace(protocol, sealed_test_boundary=protocol.sealed_test_boundary + _DAY)

    run = _MlflowRun(
        params=envelope.params,
        tags={**tampered.mlflow_tags(), MLFLOW_MODEL_TYPE_TAG: "itemitem_cosine"},
    )

    with pytest.raises(RetrievalRunNotUsableError, match="protocol hash mismatch"):
        retrieval_run_from_mlflow(run)


# --- what the vocabulary asserts --------------------------------------------


def test_the_filtering_vocabulary_matches_the_written_contract() -> None:
    protocol = _protocol()

    # These five values are quoted from
    # docs/model-planning/contracts/evaluation-protocol.md. Drifting from them
    # silently would make runs incomparable to the documented semantics while
    # still hashing consistently with each other, which is the worst outcome.
    assert protocol.positive_history_filter == "strict-prior-equal-timestamp-excluded-v1"
    assert protocol.seen_item_filter == "watched-strictly-prior-excluded-v1"
    assert protocol.dismissal_filter == "dismissals-absent-from-dataset-v1"
    assert protocol.target_filter == "target-retained-never-negative-v1"
    assert protocol.candidate_filter == "unfiltered-retrieval-then-point-in-time-exclusions-v1"


def test_the_event_schema_claim_is_checked_against_the_frame() -> None:
    split = _split()
    renamed = split.train.rename(columns={"movieId": "itemId"})

    # The version string is only worth carrying if a frame that does not match
    # it is refused; otherwise it is a label that travels with any data at all.
    with pytest.raises(ProtocolDerivationError, match="movieId"):
        _protocol(split, renamed)


def test_the_digest_is_independent_of_how_many_rows_a_pass_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split = _split()
    baseline = derived_snapshot_hash({"train": split.train, "holdout": split.holdout})

    # The chunk size bounds memory and nothing else. If it ever leaked into the
    # byte stream, a machine that hashed in smaller passes would silently
    # produce runs that could not be compared with anyone else's.
    monkeypatch.setattr(protocol_manifest, "_DIGEST_CHUNK_ROWS", 7)
    assert derived_snapshot_hash({"train": split.train, "holdout": split.holdout}) == baseline
