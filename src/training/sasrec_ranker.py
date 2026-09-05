"""Retrain the LightGBM ranker on SASRec candidates and gate it against item-item.

SASRec retrieves better than item-item — warm recall@500 0.4652 against 0.3991 on
the same 1,931 warm / 710 cold holdout users — but handing those candidates to the
*existing* ranker bought almost nothing end to end (+1.67% warm NDCG@10, +0.43%
overall). That is the expected result and not a disappointment: the champion
booster was fitted on item-item's candidate distribution and has no sequence
feature, so it was being asked to order a slate it had never been trained to see.
This module answers the question that one could not: **what does the two-stage
system score when the ranker is trained on the candidates it will be served?**

`src/training/ranker.py` cannot answer it. Its construction, its type annotations
and its training-time retrieval are all `ItemItemModel` — including the
`filter_seen=False` argument, which only that class has. It stays untouched, and
this runner reproduces its construction behind a `CandidateSource` seam.

**Both arms come out of one process.** The ratings load, the temporal split, the
ADR 0011 cohort attachment, the point-in-time `FeatureIndex` and the 30-day
positive sample are computed once and shared, so the incumbent and the challenger
differ in the candidate stage and in nothing else — not in the positives, not in
the feature values, not in the seed. The comparison is what the run is for, so
the only way to be sure the two arms agree about everything else is to make them
literally the same objects.

Three properties are load-bearing.

**Point-in-time correctness of the SASRec query.** A user's history at training
time is the items they consumed *strictly before* the positive's timestamp; events
sharing a timestamp are never context for one another. That is
`src/models/candidates/sequence_data.py`'s rule for building training sequences,
and `tests/unit/test_sasrec_ranker.py` asserts the slice here agrees with that
builder row for row rather than merely claiming to.

**Exclusion matching (#126).** Retrieval is asked unfiltered and the user's
strictly-prior history is removed from the *negatives* pool afterwards — the rule
`ranker.py` applies and the one the protocol's own `candidate_filter` vocabulary
names. Both arms apply it; the pre-#126 toggle is deliberately not honoured here,
because a run that could silently be exclusion-mismatched is not the comparison
this was asked for.

**Routing symmetry.** Both arms decide learned-path-versus-popularity on the
user's whole training history, which is what `ranker.py` does and what both
models' holdout `recommend()` does. Deciding SASRec's routing on the *prefix*
instead would be stricter, but it would change which positives survive into the
training set and confound the retriever comparison with a population change. The
count of learned-path positives whose strict prefix was shorter than the threshold
is logged per arm, so the size of that compromise is on the record rather than
hidden (see `.coordination/DECISIONS.md` O-6).

Run with ``make train-sasrec-ranker``. Requires Postgres, a reachable MLflow
tracking store, and the pinned SASRec artifact directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import mlflow
import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine

from src.config import Settings
from src.data.load import load_ratings
from src.data.split import TemporalSplit, temporal_split
from src.evaluation.gate import GateDecision, promotion_decision
from src.evaluation.protocol import (
    COLD_START_THRESHOLD,
    K_CANDIDATES,
    PER_USER_RECALL_ARTIFACT,
    EvalResult,
    K,
    evaluate,
    per_user_recall_document,
)
from src.features import FeatureIndex
from src.models.candidates import routing
from src.models.candidates.itemitem import ItemItemModel
from src.models.candidates.popularity import PopularityModel
from src.models.candidates.sasrec import SASRecModel
from src.models.candidates.sasrec_artifact import (
    MANIFEST_FILENAME,
    SASRecArtifactManifest,
    load_sasrec,
)
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig
from src.training import protocol_manifest, sampling, seeds
from src.training.ranker import (
    NEGATIVES_PER_POSITIVE,
    RANKER_APPLY_SERVING_EXCLUSIONS,
    RANKER_POSITIVE_LIMIT,
    RANKER_POSITIVE_WINDOW_DAYS,
    RANKER_SEED,
    _sample_training_positives,
    _trailing_window,
)
from src.training.twotower import subsample_users
from synthetic.cold_start import harness as synth_cold
from synthetic.cold_start.load import SyntheticColdCohort

logger = logging.getLogger(__name__)

# The same experiment `ranker.py` writes to. These runs answer the same question
# at the same K against the same holdout, and splitting them across experiments
# would hide the comparison the whole exercise exists to make.
#
# `ranker.py`'s private sampling helpers are imported above rather than
# reimplemented, for the same reason: "both arms train from the identical 30-day
# positives" is the premise of the comparison, and the only way to be certain of
# it is to draw them with the code the incumbent trainer draws them with. A
# paraphrase that agreed today would be free to drift tomorrow.
PHASE_2_RANKER_EXPERIMENT = "phase-2-ranker"

# Where the pinned SASRec artifact lives. The default is repo-relative and matches
# the directory `src/training/sasrec.py` exports into; the env var exists because
# a worktree does not carry DVC-managed or run-scoped artifacts and has to point
# at the checkout that does.
ARTIFACT_DIR_ENV_VAR = "SASREC_RANKER_ARTIFACT_DIR"
DEFAULT_ARTIFACT_DIR = Path("artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9")

# Where each arm's booster is written, before it is evaluated. Create-only: a run
# never overwrites another run's weights, and a booster that exists on disk was
# saved before anybody knew what it scored.
BOOSTER_DIR_ENV_VAR = "SASREC_RANKER_BOOSTER_DIR"
DEFAULT_BOOSTER_DIR = Path("artifacts/sasrec-ranker-step1")
BOOSTER_FILENAME = "ranker.txt"

# Smoke-test knob. A fraction below 1.0 keeps every interaction of a seeded subset
# of users (`subsample_users`) so the runner can be proved end to end in minutes.
# It moves the split cutoff, so the ADR 0011 cohort — which is anchored to the
# full-data cutoff — is skipped, exactly as `src/training/sasrec.py` skips it.
SAMPLE_FRACTION_ENV_VAR = "SASREC_RANKER_USER_SAMPLE_FRACTION"
SUBSAMPLE_SEED = 42

# How many positives are encoded and searched per batch. Pinned and logged rather
# than tuned per run: rows never interact inside `retrieve_unfiltered`, but the
# floating-point path through a batched matmul is not bit-identical to a batch of
# one, so the batch size is part of what makes a run reproducible.
CANDIDATE_BATCH_SIZE = 1024

INCUMBENT_ARM = "itemitem"
CHALLENGER_ARM = "sasrec"


class ExclusionPolicyError(RuntimeError):
    """The run was asked for a comparison it cannot honestly make.

    `RANKER_APPLY_SERVING_EXCLUSIONS=false` reproduces the pre-#126 negative pool.
    That arm is a legitimate ablation in `ranker.py`, and it is not legitimate
    here: the incumbent this run gates against is the #126 item-item ranker, so an
    exclusion-mismatched pair would be two different experiments wearing one
    verdict.
    """


@dataclass(frozen=True)
class TrainingQuery:
    """One positive's point-in-time context, resolved once and shared.

    ``prior_movie_ids`` is the user's history strictly before ``as_of`` in
    ``(timestamp, movieId)`` order. It is both the sequence model's input and the
    #126 exclusion set, deliberately: two derivations of "what had this user seen
    by then" are two chances to disagree.
    """

    user_id: int
    as_of: int
    prior_movie_ids: np.ndarray


@dataclass
class RoutingCounts:
    """How a candidate source served the training positives it was asked about."""

    learned: int = 0
    fallback: int = 0
    empty_prefix: int = 0
    #: Learned-path positives whose strict prefix held fewer items than the
    #: cold-start threshold. Not an error — routing follows the full history by
    #: design — but the measure of how often it does, which Rung 3a will want.
    learned_with_short_prefix: int = 0

    def as_params(self, prefix: str) -> dict[str, int]:
        return {
            f"{prefix}_learned": self.learned,
            f"{prefix}_fallback": self.fallback,
            f"{prefix}_empty_prefix": self.empty_prefix,
            f"{prefix}_learned_short_prefix": self.learned_with_short_prefix,
        }


class CandidateSource(Protocol):
    """One arm's candidate stage in the two shapes the ranker needs.

    Training retrieval is unfiltered — the positive is drawn from the user's own
    train history, so a source that filtered its history would drop every positive
    it was asked about. Holdout retrieval is the serving shape, with the history
    excluded, because that is what the deployed path returns and what every
    recorded retrieval number was measured on.
    """

    name: str
    candidate_model_tag: str
    routing_counts: RoutingCounts

    def training_candidates(self, queries: Sequence[TrainingQuery], k: int) -> list[list[int]]: ...

    def holdout_candidates(self, user_id: int, k: int) -> list[int]: ...

    def served_by_learned_path(self, user_id: int) -> bool: ...

    def identity(self) -> dict[str, str]: ...


@dataclass
class ItemItemSource:
    """The incumbent: `ranker.py`'s candidate stage, called the way it calls it."""

    model: ItemItemModel
    name: str = INCUMBENT_ARM
    candidate_model_tag: str = "itemitem_cosine"
    routing_counts: RoutingCounts = field(default_factory=RoutingCounts)

    def training_candidates(self, queries: Sequence[TrainingQuery], k: int) -> list[list[int]]:
        out: list[list[int]] = []
        for query in queries:
            self._count(query)
            # `filter_seen=False` is the whole reason `ranker.py` is hard-coded to
            # this class, and the reason this seam exists at all.
            out.append(self.model.recommend(query.user_id, k, filter_seen=False))
        return out

    def holdout_candidates(self, user_id: int, k: int) -> list[int]:
        return self.model.recommend(user_id, k)

    def served_by_learned_path(self, user_id: int) -> bool:
        return self.model.was_served_by_itemitem(user_id)

    def identity(self) -> dict[str, str]:
        return {"candidate_k_neighbors": str(self.model.k_neighbors)}

    def _count(self, query: TrainingQuery) -> None:
        if self.model.was_served_by_itemitem(query.user_id):
            self.routing_counts.learned += 1
            if len(query.prior_movie_ids) < COLD_START_THRESHOLD:
                self.routing_counts.learned_with_short_prefix += 1
        else:
            self.routing_counts.fallback += 1


@dataclass
class SasrecSource:
    """The challenger: the pinned SASRec artifact, queried point-in-time.

    The artifact carries weights and a vocabulary and nothing else — user
    histories are request data, not model state — so the runtime history and the
    popularity fallback are rebuilt from the same frame the model was fitted on
    before this class is constructed.
    """

    model: SASRecModel
    popularity: PopularityModel
    manifest: SASRecArtifactManifest
    name: str = CHALLENGER_ARM
    candidate_model_tag: str = "sasrec_artifact"
    routing_counts: RoutingCounts = field(default_factory=RoutingCounts)

    def training_candidates(self, queries: Sequence[TrainingQuery], k: int) -> list[list[int]]:
        out: list[list[int]] = [[] for _ in queries]
        learned_rows: list[int] = []
        learned_histories: list[list[int]] = []
        max_length = self.model.config.max_sequence_length

        for row, query in enumerate(queries):
            if not self.model.was_served_by_sasrec(query.user_id):
                self.routing_counts.fallback += 1
                out[row] = self.popularity.recommend(query.user_id, k)
                continue
            if len(query.prior_movie_ids) == 0:
                # The encoder has no zero-length input: a fully padded sequence
                # masks every position and the attention softmax is undefined.
                # A user whose first events are all at one timestamp genuinely has
                # no causal context yet, and popularity is what serving would
                # return for them.
                self.routing_counts.empty_prefix += 1
                out[row] = self.popularity.recommend(query.user_id, k)
                continue
            self.routing_counts.learned += 1
            if len(query.prior_movie_ids) < COLD_START_THRESHOLD:
                self.routing_counts.learned_with_short_prefix += 1
            learned_rows.append(row)
            learned_histories.append(
                [int(movie_id) for movie_id in query.prior_movie_ids[-max_length:]]
            )

        if learned_histories:
            retrieved = self.model.retrieve_unfiltered(learned_histories, k)
            for row, candidates in zip(learned_rows, retrieved, strict=True):
                out[row] = candidates
        return out

    def holdout_candidates(self, user_id: int, k: int) -> list[int]:
        return self.model.recommend(user_id, k)

    def served_by_learned_path(self, user_id: int) -> bool:
        return self.model.was_served_by_sasrec(user_id)

    def identity(self) -> dict[str, str]:
        return {
            "sasrec_artifact_sha256": self.manifest.model_sha256,
            "sasrec_vocabulary_sha256": self.manifest.vocabulary_sha256,
            "sasrec_n_items": str(self.manifest.n_items),
            "sasrec_max_sequence_length": str(self.manifest.max_sequence_length),
        }


@dataclass(frozen=True)
class ArmOutcome:
    """Everything one arm produced, in the shape the gate and the report want."""

    name: str
    run_id: str
    result: EvalResult
    importances: dict[str, float]
    booster_path: Path
    booster_sha256: str
    n_groups: int
    n_rows: int
    n_dropped_positives: int
    routing_counts: RoutingCounts
    seconds: dict[str, float]


def resolve_artifact_dir() -> Path:
    raw = os.environ.get(ARTIFACT_DIR_ENV_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_ARTIFACT_DIR


def resolve_booster_dir() -> Path:
    raw = os.environ.get(BOOSTER_DIR_ENV_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_BOOSTER_DIR


def resolve_sample_fraction() -> float:
    raw = os.environ.get(SAMPLE_FRACTION_ENV_VAR, "").strip()
    return 1.0 if not raw else float(raw)


def strict_prefix(timestamps: np.ndarray, movie_ids: np.ndarray, as_of: int) -> np.ndarray:
    """The items a user had consumed strictly before ``as_of``.

    ``side="left"`` is what makes it strict: a run of equal timestamps is cut
    *before* the run, so events sharing a second are never context for one
    another. That is the same rule
    ``src/models/candidates/sequence_data.build_strict_prefix_examples`` applies
    when it builds SASRec's own training sequences, and it is the same
    ``searchsorted`` call `ranker.py` already uses to build its #126 exclusion
    set — which is why one slice serves both jobs here.

    ``timestamps`` and ``movie_ids`` must be the user's rows in
    ``(timestamp, movieId)`` order.
    """
    prior_end = int(np.searchsorted(timestamps, as_of, side="left"))
    return movie_ids[:prior_end]


def history_index(frame: pd.DataFrame) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Per-user ``(timestamps, movieIds)`` in the sequence builder's order."""
    ordered = frame.sort_values(["userId", "timestamp", "movieId"], kind="stable")
    index: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for user_id, rows in ordered.groupby("userId", sort=False):
        index[int(user_id)] = (
            rows["timestamp"].to_numpy(dtype=np.int64, copy=True),
            rows["movieId"].to_numpy(dtype=np.int64, copy=True),
        )
    return index


def build_ranker_training_set(
    positives: pd.DataFrame,
    source: CandidateSource,
    feature_index: FeatureIndex,
    history_by_user: dict[int, tuple[np.ndarray, np.ndarray]],
    n_negatives: int,
    rng: np.random.Generator,
    *,
    k_candidates: int = K_CANDIDATES,
    batch_size: int = CANDIDATE_BATCH_SIZE,
) -> tuple[pd.DataFrame, list[int], np.ndarray, int]:
    """Assemble the ``(features, group sizes, labels)`` triple LambdaRank consumes.

    Step for step this is `ranker.py`'s ``_build_ranker_training_set``: ask the
    candidate stage for ``k_candidates`` unfiltered, drop the positive if its own
    candidate stage missed it, filter the strictly-prior history out of the
    negatives pool, sample ``n_negatives`` from what is left, and compute features
    for the group as of the positive's timestamp. Two differences, both structural
    rather than behavioural:

    * retrieval goes through ``source``, and is asked in batches, because a
      sequence encoder called once per positive spends most of its time in
      dispatch overhead;
    * the point-in-time slice is computed before retrieval instead of after,
      because the sequence model needs it as *input* and the exclusion needs it as
      a *filter*, and they must be the same slice.

    The RNG is drawn from in positive order exactly as `ranker.py` draws from it,
    so an item-item arm here reproduces an item-item run there.
    """
    feature_rows: list[pd.DataFrame] = []
    labels: list[int] = []
    group_sizes: list[int] = []
    dropped_missing = 0
    empty = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))

    for start in range(0, len(positives), batch_size):
        chunk = positives.iloc[start : start + batch_size]
        rows = list(chunk.itertuples(index=False))
        queries = []
        for pos in rows:
            user_id = int(pos.userId)
            timestamps, movie_ids = history_by_user.get(user_id, empty)
            queries.append(
                TrainingQuery(
                    user_id=user_id,
                    as_of=int(pos.timestamp),
                    prior_movie_ids=strict_prefix(timestamps, movie_ids, int(pos.timestamp)),
                )
            )
        candidate_lists = source.training_candidates(queries, k_candidates)

        for pos, query, candidates in zip(rows, queries, candidate_lists, strict=True):
            pos_movie = int(pos.movieId)
            if pos_movie not in candidates:
                dropped_missing += 1
                continue

            serving_exclusions = {int(movie_id) for movie_id in query.prior_movie_ids}
            negatives_pool = [
                candidate
                for candidate in candidates
                if candidate != pos_movie and candidate not in serving_exclusions
            ]
            if len(negatives_pool) < n_negatives:
                sampled_negs = negatives_pool
            else:
                neg_idx = rng.choice(len(negatives_pool), size=n_negatives, replace=False)
                sampled_negs = [negatives_pool[int(i)] for i in neg_idx]

            group_items = [pos_movie, *sampled_negs]
            group_query = pd.DataFrame(
                {
                    "userId": [query.user_id] * len(group_items),
                    "movieId": group_items,
                    "as_of_timestamp": [query.as_of] * len(group_items),
                }
            )
            feature_rows.append(feature_index.features_for(group_query))
            labels.extend([1, *([0] * len(sampled_negs))])
            group_sizes.append(len(group_items))

    logger.info(
        "[%s] training set: %d groups, %d rows, %d positives dropped",
        source.name,
        len(group_sizes),
        sum(group_sizes),
        dropped_missing,
    )
    if not feature_rows:
        raise RuntimeError(
            f"[{source.name}] every positive was dropped; the candidate stage returned "
            "no list containing its own positive, which is a broken source rather than "
            "a trainable set"
        )
    features_df = pd.concat(feature_rows, ignore_index=True)
    return features_df, group_sizes, np.array(labels, dtype=np.float64), dropped_missing


def rank_for_holdout(
    ranker: LGBMRanker,
    source: CandidateSource,
    feature_index: FeatureIndex,
    user_ids: Sequence[int],
    as_of_timestamp: int,
    k_candidates: int,
    k_final: int,
) -> dict[int, list[int]]:
    """Candidates → features → ranker → top-K, in the shape ``evaluate`` wants."""
    candidates_by_user: dict[int, list[int]] = {}
    features_by_user: dict[int, pd.DataFrame] = {}
    for user_id in user_ids:
        candidates = source.holdout_candidates(user_id, k_candidates)
        if not candidates:
            candidates_by_user[user_id] = []
            continue
        candidates_by_user[user_id] = candidates
        features_by_user[user_id] = feature_index.features_for(
            pd.DataFrame(
                {
                    "userId": [user_id] * len(candidates),
                    "movieId": candidates,
                    "as_of_timestamp": [as_of_timestamp] * len(candidates),
                }
            )
        )
    return ranker.rank_candidates(candidates_by_user, features_by_user, k=k_final)


def load_pinned_sasrec(
    artifact_dir: Path, train_frame: pd.DataFrame, popularity: PopularityModel
) -> SasrecSource:
    """Load the checksum-pinned artifact and rebuild the state it does not carry.

    ``load_sasrec`` verifies the archive's SHA-256 against its manifest and
    rebuilds the exact retrieval index. What it cannot rebuild is runtime state:
    the archive deliberately holds no user histories, and its popularity fallback
    is unfitted. Both are derived from the same frame the model was fitted on,
    which is what makes the holdout numbers this run produces comparable with the
    recorded retrieval run.

    ``popularity`` is passed in rather than fitted here so that both arms fall
    back to the *same object*. Fitting a second copy of the same ranking on the
    same frame would cost about half a gigabyte to arrive at an answer that has to
    be identical anyway — and "has to be" is a weaker claim than "is".
    """
    manifest_path = artifact_dir / MANIFEST_FILENAME
    manifest = SASRecArtifactManifest.load(manifest_path)
    model = load_sasrec(manifest_path)
    model._popularity = popularity
    model._user_history, n_unknown = runtime_user_history(train_frame, model)
    logger.info(
        "Loaded pinned SASRec artifact %s (sha256=%s, %d items, %d runtime histories, "
        "%d rows outside the model vocabulary)",
        artifact_dir,
        manifest.model_sha256[:12],
        manifest.n_items,
        len(model._user_history),
        n_unknown,
    )
    return SasrecSource(model=model, popularity=popularity, manifest=manifest)


def runtime_user_history(
    train_frame: pd.DataFrame, model: SASRecModel
) -> tuple[dict[int, list[int]], int]:
    """Per-user chronological dense history, tolerating an out-of-vocabulary row.

    ``build_user_history`` is the canonical construction and this reproduces it
    exactly whenever every item in ``train_frame`` is in the model's vocabulary —
    which is the case for the full-data run, because the model was fitted on this
    frame. It cannot simply be reused, because it maps through the vocabulary and
    would raise on a frame that holds an item the model never saw: a subsampled
    smoke run splits at its own cutoff and can. An unknown item takes the model's
    explicit unknown token, the same convention ``recommend_from_history`` uses,
    so it can never alias a trained title. The count is returned rather than
    swallowed — for the full run it must be zero, and a non-zero one is worth
    reading in the log.
    """
    ordered = train_frame.sort_values(["userId", "timestamp"], kind="stable")
    mapped = ordered["movieId"].map(model._item_to_index)
    n_unknown = int(mapped.isna().sum())
    dense = mapped.fillna(model._unknown_index).astype("int64")
    grouped = ordered.assign(_dense=dense).groupby("userId")["_dense"].apply(list)
    return dict(grouped), n_unknown


def configuration_id(source: CandidateSource, config: LGBMRankerConfig) -> str:
    """Identify the arm independently of its training seed."""
    payload = {
        "arm": source.name,
        "candidate_model": source.candidate_model_tag,
        "candidate_identity": source.identity(),
        "k_candidates": K_CANDIDATES,
        "k_final": K,
        "negatives_per_positive": NEGATIVES_PER_POSITIVE,
        "positive_window_days": RANKER_POSITIVE_WINDOW_DAYS,
        "lgbm": {
            "num_leaves": config.num_leaves,
            "learning_rate": config.learning_rate,
            "min_data_in_leaf": config.min_data_in_leaf,
            "num_boost_round": config.num_boost_round,
            "feature_fraction": config.feature_fraction,
            "bagging_fraction": config.bagging_fraction,
            "bagging_freq": config.bagging_freq,
            "lambda_l2": config.lambda_l2,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sasrec-ranker-sha256:{hashlib.sha256(canonical).hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _save_booster_create_only(ranker: LGBMRanker, directory: Path) -> tuple[Path, str]:
    """Persist the booster before it is scored, refusing to replace one."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / BOOSTER_FILENAME
    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing booster at {path}")
    ranker.save_model(path)
    return path, _sha256(path)


def _load_movies(engine: Engine) -> pd.DataFrame:
    return pd.read_sql('SELECT "movieId", genres FROM movies', engine)


def run_arm(
    source: CandidateSource,
    *,
    split: TemporalSplit,
    train_frame: pd.DataFrame,
    cohort: SyntheticColdCohort | None,
    positives: pd.DataFrame,
    feature_index: FeatureIndex,
    history_by_user: dict[int, tuple[np.ndarray, np.ndarray]],
    movies_rows: int,
    seed: int,
    positive_limit: int,
    limit_binds: bool,
    window_rows: int,
    routing_policy: str,
    booster_root: Path,
    sample_fraction: float,
) -> ArmOutcome:
    """Train, save, evaluate and log one arm. Returns what the gate needs."""
    rng = np.random.default_rng(seed)
    config = LGBMRankerConfig(seed=seed)
    protocol = protocol_manifest.build_protocol(
        split=split,
        fitted_frame=train_frame,
        learned_routing_policy=protocol_manifest.routing_policy_value(
            routing.cold_start_threshold_for(routing_policy, COLD_START_THRESHOLD)
        ),
        stage="ranking",
        k=K,
    )
    envelope = protocol_manifest.run_envelope(protocol, deterministic=False, seed=seed)

    mlflow.set_experiment(PHASE_2_RANKER_EXPERIMENT)
    run_name = seeds.run_name_for(
        sampling.run_name_for(
            routing.run_name_for(f"lgbm-lambdarank-{source.name}-candidates", routing_policy),
            positive_limit,
        ),
        seed,
    )
    with mlflow.start_run(run_name=run_name) as run:
        run_id = run.info.run_id
        logger.info("[%s] MLflow run %s (%s)", source.name, run_id, run_name)

        started = time.perf_counter()
        features_df, group_sizes, labels, dropped = build_ranker_training_set(
            positives=positives,
            source=source,
            feature_index=feature_index,
            history_by_user=history_by_user,
            n_negatives=NEGATIVES_PER_POSITIVE,
            rng=rng,
        )
        build_seconds = time.perf_counter() - started

        started = time.perf_counter()
        ranker = LGBMRanker(config=config).fit(features_df, group_sizes, labels)
        fit_seconds = time.perf_counter() - started
        logger.info("[%s] booster fit in %.1fs", source.name, fit_seconds)

        # Saved before evaluation on purpose: the weights are then a fact
        # independent of whatever the holdout says about them, and a run that dies
        # during scoring still leaves something reloadable.
        booster_path, booster_sha256 = _save_booster_create_only(ranker, booster_root / run_id)
        logger.info("[%s] booster %s sha256=%s", source.name, booster_path, booster_sha256)

        started = time.perf_counter()
        holdout_user_ids = split.holdout["userId"].unique().tolist()
        cohort_user_ids = list(cohort.user_ids) if cohort is not None else []
        recommendations = rank_for_holdout(
            ranker=ranker,
            source=source,
            feature_index=feature_index,
            user_ids=holdout_user_ids + cohort_user_ids,
            as_of_timestamp=split.cutoff,
            k_candidates=K_CANDIDATES,
            k_final=K,
        )
        rank_seconds = time.perf_counter() - started

        holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
        train_counts = split.train.groupby("userId").size().to_dict()
        result = evaluate(
            recommendations,
            holdout,
            train_counts,
            k=K,
            synthetic_cold_users=(cohort.targets_by_bucket if cohort is not None else None),
            synthetic_cold_served_by=(
                source.served_by_learned_path if cohort is not None else None
            ),
        )
        for label, metrics, n_users in (
            ("Warm", result.warm, result.n_warm_users),
            ("Cold", result.cold, result.n_cold_users),
            ("Overall", result.overall, result.n_warm_users + result.n_cold_users),
        ):
            logger.info(
                "[%s] %s (n=%d): recall@%d=%.6f ndcg@%d=%.6f",
                source.name,
                label,
                n_users,
                K,
                metrics.recall,
                K,
                metrics.ndcg,
            )

        importances = ranker.feature_importances(importance_type="gain")
        logger.info("[%s] gain importances: %s", source.name, importances)

        mlflow.set_tags(
            {
                **envelope.tags,
                "model_family": "ranker",
                "model_type": "lgbm_lambdarank",
                "candidate_model": source.candidate_model_tag,
                "phase": "2",
                "stage": "ranker",
                "sasrec_ranker_arm": source.name,
                "cold_start_routing_policy": routing_policy,
                "train_seed": str(seed),
                "ranker_positive_limit_binding": str(limit_binds).lower(),
                "training_feature_source": "feature-index-point-in-time-v1",
                "serving_feature_source": "feast-postgres-redis-v1",
                # Both arms apply #126's rule; the module refuses to run otherwise,
                # so the pair can never be exclusion-mismatched.
                "ranker_serving_exclusions_applied": "true",
                # Inherited from `ranker.py` and shared by both arms: each
                # candidate model saw the whole training window, including the
                # 30 days the positives are drawn from.
                "candidate_leakage_compromise": "true",
                # Routing follows the full training history for both arms; only
                # the sequence model's *query* is point-in-time. See O-6.
                "sasrec_ranker_training_routing": "full-train-history-threshold",
                "sasrec_ranker_training_query": "strict-prior-equal-timestamp-excluded",
                **source.identity(),
            }
        )
        mlflow.log_params(
            {
                **envelope.params,
                "k_final": K,
                "k_candidates": K_CANDIDATES,
                "cold_start_threshold": COLD_START_THRESHOLD,
                "cold_start_routing_policy": routing_policy,
                "cutoff_timestamp": split.cutoff,
                "holdout_end_timestamp": split.holdout_end,
                "n_train_rows": len(split.train),
                "n_fitted_rows": len(train_frame),
                "n_holdout_rows": len(split.holdout),
                "n_holdout_users": len(holdout_user_ids),
                "n_movies": movies_rows,
                "user_sample_fraction": sample_fraction,
                "ranker_positive_window_days": RANKER_POSITIVE_WINDOW_DAYS,
                "ranker_positive_window_rows": window_rows,
                "ranker_positive_limit": positive_limit,
                "ranker_positive_limit_binding": limit_binds,
                "n_positives_sampled": len(positives),
                "n_ranker_positives_used": len(group_sizes),
                "n_ranker_positives_dropped": dropped,
                "negatives_per_positive": NEGATIVES_PER_POSITIVE,
                "n_ranker_training_rows": sum(group_sizes),
                "candidate_batch_size": CANDIDATE_BATCH_SIZE,
                "num_leaves": config.num_leaves,
                "learning_rate": config.learning_rate,
                "min_data_in_leaf": config.min_data_in_leaf,
                "num_boost_round": config.num_boost_round,
                "lambda_l2": config.lambda_l2,
                "seed": config.seed,
                "ranker_training_set_seconds": round(build_seconds, 1),
                "ranker_fit_seconds": round(fit_seconds, 1),
                "rank_seconds": round(rank_seconds, 1),
                "ranker_booster_sha256": booster_sha256,
                **source.routing_counts.as_params("training_positives"),
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
        mlflow.log_metrics({f"importance_{name}": value for name, value in importances.items()})
        mlflow.log_dict(
            per_user_recall_document(
                result,
                run_id=run_id,
                model_type=f"lgbm_lambdarank_{source.name}",
                seed=seed,
                configuration_id=configuration_id(source, config),
                protocol=protocol.to_dict(),
            ),
            PER_USER_RECALL_ARTIFACT,
        )
        if cohort is not None:
            synth_cold.log_summary(result, logger=logger, k=K)
            mlflow.log_params(synth_cold.params(cohort))
            mlflow.log_metrics(synth_cold.metrics(result, suffix=synth_cold.SUFFIX_AT_K))
            mlflow.set_tag(
                synth_cold.ROUTING_TAG, str(synth_cold.routing_is_correct(result)).lower()
            )

    return ArmOutcome(
        name=source.name,
        run_id=run_id,
        result=result,
        importances=importances,
        booster_path=booster_path,
        booster_sha256=booster_sha256,
        n_groups=len(group_sizes),
        n_rows=sum(group_sizes),
        n_dropped_positives=dropped,
        routing_counts=source.routing_counts,
        seconds={
            "training_set": round(build_seconds, 1),
            "fit": round(fit_seconds, 1),
            "rank": round(rank_seconds, 1),
        },
    )


def summary_document(
    incumbent: ArmOutcome, challenger: ArmOutcome, decision: GateDecision
) -> dict[str, object]:
    """One machine-readable record of the pair, independent of MLflow's store."""

    def arm(outcome: ArmOutcome) -> dict[str, object]:
        return {
            "arm": outcome.name,
            "run_id": outcome.run_id,
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
            "training_set": {
                "groups": outcome.n_groups,
                "rows": outcome.n_rows,
                "dropped_positives": outcome.n_dropped_positives,
            },
            "training_routing": outcome.routing_counts.as_params("training_positives"),
            "feature_importance_gain": outcome.importances,
            "booster": {
                "path": outcome.booster_path.name,
                "sha256": outcome.booster_sha256,
            },
            "seconds": outcome.seconds,
        }

    return {
        "schema_version": 1,
        "k": K,
        "incumbent": arm(incumbent),
        "challenger": arm(challenger),
        "gate": decision.to_dict(),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not RANKER_APPLY_SERVING_EXCLUSIONS:
        raise ExclusionPolicyError(
            "RANKER_APPLY_SERVING_EXCLUSIONS is false. This runner gates a challenger "
            "against the #126 item-item incumbent, and an exclusion-mismatched pair is "
            "two experiments wearing one verdict. Unset the variable and rerun."
        )

    settings = Settings()
    seed = seeds.resolve_seed(RANKER_SEED)
    positive_limit = sampling.resolve_positive_limit(RANKER_POSITIVE_LIMIT)
    sample_fraction = resolve_sample_fraction()
    booster_root = resolve_booster_dir()
    artifact_dir = resolve_artifact_dir()
    logger.info(
        "Seed %d, positive limit %s, user sample fraction %s",
        seed,
        f"{positive_limit:,}",
        sample_fraction,
    )

    engine = create_engine(settings.database_url)
    try:
        ratings = load_ratings(engine)
        movies = _load_movies(engine)
    finally:
        engine.dispose()
    logger.info("Loaded %s ratings, %s movies", f"{len(ratings):,}", f"{len(movies):,}")

    if sample_fraction != 1.0:
        ratings = subsample_users(ratings, sample_fraction, SUBSAMPLE_SEED)
        logger.info("Subsampled to %s ratings for a smoke run", f"{len(ratings):,}")

    split = temporal_split(ratings)
    logger.info(
        "Train=%s Holdout=%s Test=%s (cutoff=%d)",
        f"{len(split.train):,}",
        f"{len(split.holdout):,}",
        f"{len(split.test):,}",
        split.cutoff,
    )

    # The cohort is anchored to the full-data cutoff, so a subsample cannot carry
    # it — the same rule `src/training/sasrec.py` applies.
    train_frame, cohort = (
        synth_cold.prepare(split, logger=logger) if sample_fraction == 1.0 else (split.train, None)
    )

    routing_policy = routing.resolve_policy()
    logger.info("Cold-start routing policy: %s", routing_policy)
    threshold = routing.cold_start_threshold_for(routing_policy, COLD_START_THRESHOLD)

    started = time.perf_counter()
    feature_index = FeatureIndex.build(train_frame, movies)
    logger.info("Feature index in %.1fs", time.perf_counter() - started)

    window_rows = len(_trailing_window(split.train, RANKER_POSITIVE_WINDOW_DAYS))
    limit_binds = window_rows > positive_limit
    # Positives are drawn once, from the real train slice, with `ranker.py`'s own
    # sampler and its own RNG stream. Both arms then consume this identical frame.
    positives = _sample_training_positives(
        split.train,
        n_days=RANKER_POSITIVE_WINDOW_DAYS,
        limit=positive_limit,
        rng=np.random.default_rng(seed),
    )
    logger.info(
        "Sampled %s positives from a %s-row window (limit %s, %s)",
        f"{len(positives):,}",
        f"{window_rows:,}",
        f"{positive_limit:,}",
        "binding" if limit_binds else "not binding",
    )

    history_by_user = history_index(train_frame)

    started = time.perf_counter()
    itemitem = ItemItemSource(model=ItemItemModel(cold_start_threshold=threshold).fit(train_frame))
    logger.info("Item-item fit in %.1fs", time.perf_counter() - started)
    # The popularity fallback both arms share. `ItemItemModel.fit` builds it as a
    # side effect; handing the same object to the challenger is what makes "both
    # arms fall back identically" a fact rather than an inference.
    sasrec = load_pinned_sasrec(artifact_dir, train_frame, itemitem.model._popularity)

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    logger.info("MLflow tracking at %s", settings.mlflow_tracking_uri)

    def run(source: CandidateSource) -> ArmOutcome:
        return run_arm(
            source,
            split=split,
            train_frame=train_frame,
            cohort=cohort,
            positives=positives,
            feature_index=feature_index,
            history_by_user=history_by_user,
            movies_rows=len(movies),
            seed=seed,
            positive_limit=positive_limit,
            limit_binds=limit_binds,
            window_rows=window_rows,
            routing_policy=routing_policy,
            booster_root=booster_root,
            sample_fraction=sample_fraction,
        )

    incumbent = run(itemitem)
    challenger = run(sasrec)

    decision = promotion_decision(challenger.result, incumbent.result)
    logger.info("ADR 0001 gate, %s vs %s:", challenger.name, incumbent.name)
    for line in decision.summary().splitlines():
        logger.info("  %s", line)

    document = summary_document(incumbent, challenger, decision)
    document["incumbent_run_ids"] = [incumbent.run_id]
    document["candidate_run_ids"] = [challenger.run_id]
    output = booster_root / f"step1-{challenger.run_id}.json"
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("Wrote %s", output)


if __name__ == "__main__":
    main()
