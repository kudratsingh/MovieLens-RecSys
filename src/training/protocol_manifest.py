"""Build a run's ``ProtocolManifest`` out of what the trainer actually knows.

`src/evaluation/manifest.py` defines the semantic contract and
`src/evaluation/retrieval_gate.py` refuses to compare runs that disagree about
it. Until this module existed nothing produced one, so every run in MLflow was
unusable as promotion evidence and the tolerance study had no artifacts to
assemble. This is the producer side of that contract.

Two rules shape everything below.

**Every field is derived from state the run genuinely has.** The boundaries come
from the `TemporalSplit` the trainer computed, the catalog from the frame it
fitted, the routing policy from the threshold the model was constructed with,
the raw revision from the committed DVC pointer. Where a value is a vocabulary
term rather than a measurement — the five filtering fields, the label and
relevance definitions — it is a constant here with the semantics it names spelled
out, and it is the same constant for every trainer that shares those semantics.
That is the point of a vocabulary: two runs that filter differently must record
different values, so the values cannot be per-run prose.

**A field that cannot be derived raises.** A manifest with one fabricated field
hashes equal to nothing real, and two runs carrying the same fabrication look
comparable when they are not. Refusing to produce a manifest costs a training
run; producing a wrong one costs the meaning of every comparison made against it.

The two content hashes are defined precisely because they are the fields most
easily faked into something that looks fine. Both are SHA-256 over canonically
ordered bytes, so they are order-independent by construction rather than by the
caller's care, and two runs over different data cannot collide.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd
import yaml

from src.data.split import TemporalSplit
from src.evaluation.manifest import (
    PROTOCOL_SCHEMA_VERSION,
    EvaluationStage,
    ProtocolManifest,
)
from src.evaluation.protocol import COLD_START_THRESHOLD
from src.evaluation.retrieval_gate import (
    MLFLOW_DETERMINISTIC_PARAM,
    MLFLOW_SEED_PARAM,
)

# `python -m src.training.*` runs from the project root, so the repository is
# two levels above this file. Resolved once rather than read from Settings: the
# DVC pointer is a committed artifact of *this* checkout, and an operator should
# not be able to point a run at a different dataset revision via env var.
REPO_ROOT: Final = Path(__file__).resolve().parents[2]
RAW_DATA_DVC_PATH: Final = REPO_ROOT / "data" / "raw" / "ml-25m.dvc"

# The event shape every trainer here loads. Asserted against the frame rather
# than assumed, so the version string is a checked claim and not a label.
EVENT_SCHEMA_COLUMNS: Final = ("userId", "movieId", "rating", "timestamp")
EVENT_SCHEMA_VERSION: Final = "movielens-ratings-uid-mid-rating-ts-v1"

# MovieLens timestamps are Unix epoch seconds, which are UTC by definition;
# `temporal_split` does its window arithmetic directly in them.
TIMESTAMP_UNIT: Final = "unix-seconds"
TIMEZONE: Final = "UTC"

# What counts as a label, and what counts as a hit. `evaluate` takes the holdout
# as a set of movie ids per user and ignores the rating value entirely, so these
# runs are implicit-feedback runs whatever the source column says. The day a
# rating threshold is introduced, these values change and every comparison
# against a pre-threshold run is correctly refused.
LABEL_CONTRACT_VERSION: Final = "holdout-interaction-is-positive-any-rating-v1"
RELEVANCE_DEFINITION: Final = "binary-held-out-interaction-v1"

# Who is scored: every user with at least one interaction in the holdout window.
# A run over a subsampled user population records the same policy and a
# different `derived_snapshot_hash`, which is the correct place for that
# difference — the rule did not change, the population did.
ELIGIBLE_USER_POLICY: Final = "users-with-holdout-interaction-v1"

# A holdout item the fitted catalog does not contain cannot be retrieved, and
# `recall_at_k` divides by the whole relevant set, so it counts against the
# score rather than being dropped from the denominator.
UNKNOWN_ITEM_POLICY: Final = "unretrievable-target-kept-in-recall-denominator-v1"

# Where every candidate model's learned path stops. All four trainers embed the
# same popularity ranking, counted over the training window.
FALLBACK_POLICY: Final = "popularity-train-window-count-v1"

# The routing vocabulary, in the two forms `routing.cold_start_threshold_for`
# can produce, plus the case of a model that has no learned path to route to.
INDEX_MEMBERSHIP_ROUTING: Final = "index-membership-v1"
POPULARITY_ONLY_ROUTING: Final = "no-learned-path-popularity-only-v1"

# The five filtering values are quoted verbatim from
# docs/model-planning/contracts/evaluation-protocol.md, which is where their
# semantics are written down. They are constants rather than parameters because
# all four trainers implement exactly these semantics: history is the training
# window (ties at the cutoff land in holdout, so an event at T is not context
# for itself), already-watched items are removed from the served list, MovieLens
# carries no dismissals, the target is never sampled as a negative, and
# retrieval is asked unfiltered with exclusions applied afterwards.
POSITIVE_HISTORY_FILTER: Final = "strict-prior-equal-timestamp-excluded-v1"
SEEN_ITEM_FILTER: Final = "watched-strictly-prior-excluded-v1"
DISMISSAL_FILTER: Final = "dismissals-absent-from-dataset-v1"
TARGET_FILTER: Final = "target-retained-never-negative-v1"
CANDIDATE_FILTER: Final = "unfiltered-retrieval-then-point-in-time-exclusions-v1"

# The candidate stage reads raw interactions and no engineered feature at all.
# Naming that is not the same as leaving the field blank: a run that later feeds
# `src/feature_contract.py` columns into retrieval records a different value and
# is not pooled with these.
FEATURE_CONTRACT_RAW_INTERACTIONS: Final = "none-raw-interactions-only-v1"

# One global cutoff, not a per-event as-of. Every user's context is their whole
# training window regardless of when in the holdout they are scored.
POINT_IN_TIME_SEMANTICS: Final = "single-global-train-cutoff-v1"

# `src/evaluation/metrics.py`: binary gains, recall over the whole relevant set,
# NDCG normalized by the ideal ordering of that set truncated at k.
METRIC_CONTRACT_VERSION: Final = "binary-recall-at-k-ndcg-at-k-v1"
METRIC_AGGREGATION: Final = "unweighted-mean-over-evaluated-users-v1"
SLICE_DEFINITION: Final = "warm-cold-by-train-interaction-count-overall-union-v1"

# ADR 0001's single fixed holdout. The interval is in the id even though the
# manifest carries the boundaries separately: an id that names only a scheme
# means a different interval the day the cutoff moves, and two runs would then
# look comparable because their window ids matched.
FIXED_HOLDOUT_WINDOW_SCHEMA: Final = "fixed-holdout-v1"

# Versioned prefixes so a change to what goes into a digest is visible as a
# changed digest rather than as a silent redefinition of an unchanged one.
_INTERACTION_DIGEST_SCHEMA: Final = "movielens-interactions-v1"
_CATALOG_DIGEST_SCHEMA: Final = "movielens-catalog-v1"
_SNAPSHOT_DIGEST_SCHEMA: Final = "movielens-derived-snapshot-v1"

# Rows per hashing pass. Purely a memory bound: the byte stream is one whole
# column at a time, so unlike the tolerance study's bootstrap block this size is
# not part of the result and can be changed without invalidating a digest.
_DIGEST_CHUNK_ROWS: Final = 1_000_000

# Little-endian fixed widths, pinned rather than inherited from the platform so
# a digest means the same thing on any machine that computes it.
_INT_DTYPE: Final = "<i8"
_FLOAT_DTYPE: Final = "<f8"


class ProtocolDerivationError(RuntimeError):
    """A protocol field could not be derived from this run's real state.

    Raised instead of substituting a plausible value. A manifest is only worth
    hashing if every field in it came from somewhere; one invented field makes
    the hash an assertion about nothing, and two runs sharing that invention
    compare as though they answered the same question.
    """


@dataclass(frozen=True)
class RunEnvelope:
    """The MLflow params and tags a gateable run must carry.

    Kept together because the pair is a single contract — the payload, the hash
    that verifies it, and the determinism/seed declaration the gate reads to
    decide whether one run or three are required.
    """

    params: dict[str, str]
    tags: dict[str, str]


def raw_data_revision(dvc_path: Path = RAW_DATA_DVC_PATH) -> str:
    """The immutable DVC object hash of the raw dataset behind this run.

    DVC records a tracked output's revision in the ``.dvc`` pointer file, which
    is the part git versions — for a directory, the hash of its file listing
    with a ``.dir`` suffix. Reading the pointer rather than shelling out to
    ``dvc`` keeps a training run independent of whether a remote is reachable
    or the cache is populated: the revision is a property of the committed
    pointer, not of the local cache's health.

    The algorithm is prefixed onto the value because DVC can be configured to
    hash with something other than md5, and two bare hex strings from different
    algorithms would compare as unequal without anyone being able to see why.
    """
    try:
        document = yaml.safe_load(dvc_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProtocolDerivationError(
            f"cannot read the DVC pointer at {dvc_path}; the raw data revision is not "
            "derivable and this run cannot record a protocol"
        ) from exc
    except yaml.YAMLError as exc:
        raise ProtocolDerivationError(f"{dvc_path} is not valid YAML: {exc}") from exc

    outs = document.get("outs") if isinstance(document, dict) else None
    if not isinstance(outs, list) or len(outs) != 1 or not isinstance(outs[0], dict):
        raise ProtocolDerivationError(
            f"{dvc_path} must declare exactly one tracked output; a pointer with several "
            "outputs has no single revision to record"
        )
    out = outs[0]
    algorithm = out.get("hash", "md5")
    digest = out.get(algorithm)
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise ProtocolDerivationError(f"{dvc_path} declares an unusable hash algorithm")
    if not isinstance(digest, str) or not digest.strip():
        raise ProtocolDerivationError(
            f"{dvc_path} carries no {algorithm!r} digest for its tracked output"
        )
    return f"{algorithm.strip()}:{digest.strip()}"


def catalog_fingerprint(fitted_frame: pd.DataFrame) -> str:
    """Content hash of the item ids a model fitted on this frame can retrieve.

    Every candidate model here ranks within the items present in its training
    frame — the popularity ranking, the item-item index and the transition
    counts all enumerate exactly those ids — so the eligible catalog is the
    distinct ``movieId`` set of the fitted frame.

    Order-independent because ``np.unique`` returns a sorted array, so the
    digest depends on which items are in the catalog and on nothing else.
    """
    _require_columns(fitted_frame, ("movieId",))
    items = np.unique(_integer_column(fitted_frame, "movieId"))
    digest = hashlib.sha256()
    digest.update(f"{_CATALOG_DIGEST_SCHEMA}\nmovieId\n{items.size}\n".encode())
    _update_in_chunks(digest, items)
    return f"sha256:{digest.hexdigest()}"


def derived_snapshot_hash(components: Mapping[str, pd.DataFrame]) -> str:
    """Content hash of the derived dataset this run's question is asked over.

    ``components`` are the named slices the run actually derived — in practice
    the frame the model was fitted on and the frame it was scored against. Each
    is digested as a multiset of events, then the named digests are combined in
    sorted order, so the result is independent of row order within a slice and
    of the order the slices were passed in.

    Naming the slices rather than digesting their union is deliberate: moving a
    row from train to holdout is a different evaluation question, and a flat
    union would hash it as the same one.
    """
    if not components:
        raise ProtocolDerivationError("a derived snapshot needs at least one named component")
    parts = []
    for name, frame in components.items():
        if not name.strip() or any(character in name for character in ":\n"):
            raise ProtocolDerivationError(
                f"snapshot component name {name!r} must be non-empty and free of ':' and newlines"
            )
        parts.append(f"{name}:{_interaction_digest(frame)}:{len(frame)}")
    payload = "\n".join([_SNAPSHOT_DIGEST_SCHEMA, *sorted(parts)])
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def routing_policy_value(cold_start_threshold: int | None) -> str:
    """Name the routing rule a model was actually constructed with.

    Derived from the value handed to the model rather than from the policy name
    the operator typed, so a run cannot record a rule its model did not apply.
    ``None`` is `routing.py`'s index-membership opt-out, not an absent setting.
    """
    if cold_start_threshold is None:
        return INDEX_MEMBERSHIP_ROUTING
    if type(cold_start_threshold) is not int or cold_start_threshold < 0:
        raise ProtocolDerivationError(
            f"cold_start_threshold must be a non-negative integer or None, "
            f"got {cold_start_threshold!r}"
        )
    return f"train-history-count-gte-{cold_start_threshold}-v1"


def build_protocol(
    *,
    split: TemporalSplit,
    fitted_frame: pd.DataFrame,
    learned_routing_policy: str,
    stage: EvaluationStage,
    k: int,
    feature_contract_version: str = FEATURE_CONTRACT_RAW_INTERACTIONS,
    dvc_path: Path = RAW_DATA_DVC_PATH,
) -> ProtocolManifest:
    """Assemble the complete semantic protocol for one training run.

    Args:
        split: the `TemporalSplit` this run computed. Supplies every boundary,
            including the sealed one — under ADR 0001's single fixed holdout the
            reserved partition begins exactly where the holdout ends, which is
            what ``temporal_split`` implements and therefore what the run can
            honestly assert.
        fitted_frame: the frame the model was fitted on — ``split.train`` with
            ADR 0011's cold-start cohort attached where the machine has it.
            Defines both the eligible catalog and the training half of the
            derived snapshot, so a run that fits on a different frame is
            correctly not comparable.
        learned_routing_policy: from `routing_policy_value`, or
            `POPULARITY_ONLY_ROUTING` for a model with no learned path.
        stage: the question being asked. The primary metric follows from it —
            retrieval is scored on recall, everything else on NDCG — so a
            trainer cannot claim a retrieval verdict for a top-10 ranking run.
        k: the cutoff the run's metrics were computed at.

    Raises:
        ProtocolDerivationError: a field could not be derived from real state.
        ProtocolManifestError: the derived values are not a valid protocol —
            most usefully when the split is degenerate or the stage and metric
            disagree.
    """
    return ProtocolManifest(
        schema_version=PROTOCOL_SCHEMA_VERSION,
        raw_data_revision=raw_data_revision(dvc_path),
        derived_snapshot_hash=derived_snapshot_hash(
            {"train": fitted_frame, "holdout": split.holdout}
        ),
        event_schema_version=EVENT_SCHEMA_VERSION,
        train_cutoff=split.cutoff,
        # Equal to the cutoff by construction: the holdout opens the instant
        # training closes. Two fields because they are two claims, and an
        # embargo between them would be a change of value, not of shape.
        holdout_start=split.cutoff,
        holdout_end=split.holdout_end,
        sealed_test_boundary=split.holdout_end,
        backtest_window_id=f"{FIXED_HOLDOUT_WINDOW_SCHEMA}:{split.cutoff}-{split.holdout_end}",
        timestamp_unit=TIMESTAMP_UNIT,
        timezone=TIMEZONE,
        label_contract_version=LABEL_CONTRACT_VERSION,
        relevance_definition=RELEVANCE_DEFINITION,
        eligible_user_policy=ELIGIBLE_USER_POLICY,
        catalog_fingerprint=catalog_fingerprint(fitted_frame),
        unknown_item_policy=UNKNOWN_ITEM_POLICY,
        # The warm/cold slice boundary, which `evaluate` applies unconditionally.
        # It is not the routing rule: a run under the index-membership opt-out
        # still slices here and says so in `learned_routing_policy`.
        cold_start_threshold=COLD_START_THRESHOLD,
        learned_routing_policy=learned_routing_policy,
        fallback_policy=FALLBACK_POLICY,
        positive_history_filter=POSITIVE_HISTORY_FILTER,
        seen_item_filter=SEEN_ITEM_FILTER,
        dismissal_filter=DISMISSAL_FILTER,
        target_filter=TARGET_FILTER,
        candidate_filter=CANDIDATE_FILTER,
        feature_contract_version=feature_contract_version,
        point_in_time_semantics=POINT_IN_TIME_SEMANTICS,
        stage=stage,
        primary_metric="recall" if stage == "retrieval" else "ndcg",
        metric_contract_version=METRIC_CONTRACT_VERSION,
        metric_aggregation=METRIC_AGGREGATION,
        k=k,
        slice_definition=SLICE_DEFINITION,
    )


def run_envelope(
    protocol: ProtocolManifest, *, deterministic: bool, seed: int | None
) -> RunEnvelope:
    """The MLflow fields `retrieval_run_from_mlflow` requires, or a refusal.

    The seed contract is enforced here rather than left to the reader: a
    deterministic run that records a seed, or a stochastic one that does not,
    is rejected by the gate at promotion time — which is a long way from where
    it could still be fixed. Failing in the trainer costs one run.
    """
    if type(deterministic) is not bool:
        raise ProtocolDerivationError("deterministic must be a boolean")
    if deterministic and seed is not None:
        raise ProtocolDerivationError(
            "a deterministic run must not record a training seed; a seed that changes "
            "nothing is evidence of nothing"
        )
    if not deterministic and type(seed) is not int:
        raise ProtocolDerivationError("a stochastic run must record its integer training seed")

    params = {
        **protocol.mlflow_params(),
        MLFLOW_DETERMINISTIC_PARAM: "true" if deterministic else "false",
    }
    if not deterministic:
        params[MLFLOW_SEED_PARAM] = str(seed)
    return RunEnvelope(params=params, tags=dict(protocol.mlflow_tags()))


def _interaction_digest(frame: pd.DataFrame) -> str:
    """SHA-256 over one frame's events, ordered canonically rather than as given.

    Sorting by the full event — every column, not just the identifying ones —
    makes the digest a function of the multiset of rows and nothing else, so two
    reads of the same table in different orders agree and two different datasets
    do not. Columns are streamed whole rather than interleaved per chunk, which
    keeps the chunk size out of the result.
    """
    _require_columns(frame, EVENT_SCHEMA_COLUMNS)
    digest = hashlib.sha256()
    digest.update(
        f"{_INTERACTION_DIGEST_SCHEMA}\n{','.join(EVENT_SCHEMA_COLUMNS)}\n{len(frame)}\n".encode()
    )
    if frame.empty:
        return digest.hexdigest()

    columns: dict[str, npt.NDArray[Any]] = {
        "userId": _integer_column(frame, "userId"),
        "movieId": _integer_column(frame, "movieId"),
        "rating": _rating_column(frame),
        "timestamp": _integer_column(frame, "timestamp"),
    }
    # np.lexsort takes its keys least-significant first, so this orders by
    # userId, then movieId, then timestamp, then rating. Including every column
    # leaves no tie for a stable sort to break by input order.
    order = np.lexsort(
        (columns["rating"], columns["timestamp"], columns["movieId"], columns["userId"])
    )
    for name in EVENT_SCHEMA_COLUMNS:
        _update_in_chunks(digest, columns[name], order)
    return digest.hexdigest()


def _update_in_chunks(
    digest: hashlib._Hash,
    values: npt.NDArray[Any],
    order: npt.NDArray[np.intp] | None = None,
) -> None:
    """Feed one column to the digest a bounded number of rows at a time."""
    for start in range(0, values.size, _DIGEST_CHUNK_ROWS):
        stop = start + _DIGEST_CHUNK_ROWS
        chunk = values[start:stop] if order is None else values[order[start:stop]]
        digest.update(np.ascontiguousarray(chunk).tobytes())


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [name for name in columns if name not in frame.columns]
    if missing:
        raise ProtocolDerivationError(
            f"frame is missing {', '.join(missing)}; expected the "
            f"{EVENT_SCHEMA_VERSION} event schema"
        )


def _integer_column(frame: pd.DataFrame, name: str) -> npt.NDArray[Any]:
    """One integer column as pinned little-endian int64, or a refusal to guess.

    A float column here would be silently truncated by the cast, and a truncated
    id hashes to something that is not the data. The columns come out of
    Postgres as integers; anything else is a change worth stopping for.
    """
    values = frame[name].to_numpy()
    if values.dtype.kind not in "iu":
        raise ProtocolDerivationError(
            f"{name} must be an integer column to be hashed losslessly, got dtype {values.dtype}"
        )
    return np.asarray(values, dtype=_INT_DTYPE)


def _rating_column(frame: pd.DataFrame) -> npt.NDArray[Any]:
    values = frame["rating"].to_numpy()
    if values.dtype.kind not in "iuf":
        raise ProtocolDerivationError(
            f"rating must be a numeric column to be hashed, got dtype {values.dtype}"
        )
    return np.asarray(values, dtype=_FLOAT_DTYPE)
