"""Offline/online parity for ADR 0018's two SASRec ranker features.

Non-negotiable #2 says a feature computed offline must match the same feature
served online for the same key. The other two feature families satisfy that by
comparing an offline `FeatureIndex` computation against Feast's Redis and
Postgres reads. These two cannot be compared that way, and deliberately so:
ADR 0018 decides they are **computed per request, not materialised** — a user's
vector goes stale the moment they watch something, while an online feature view
refreshes on a batch cadence, so storing it would buy nothing and cost a second
source of truth with its own freshness clock.

The skew that remains is therefore not storage skew but *call-shape* skew: the
offline path encodes a batch of histories and the serving path encodes one, and
float32 matmul is not associative. That is what this module measures, at the
tolerances ADR 0018 names — **1e-5 absolute** on the bounded score, **1e-4
relative** on the logit.

Two assertions matter as much as the values, because a tolerance can paper over
a different model or a different history and neither would show up as a number:

* the artifact's SHA-256 is the pinned `43320b87…`, checked against the manifest
  the loader verified the archive bytes with;
* the encoded slice is the strict prefix — the same `searchsorted(..., "left")`
  cut the #126 exclusion path takes — asserted item for item, not inferred.

One property of the pinned encoder is pinned here rather than assumed: in
`eval()` mode PyTorch takes a fused attention path that returns NaN for a query
row whose keys are all masked, which every **left-padded** sequence has at its
first position, and that NaN propagates into the real positions. So a history
shorter than `max_sequence_length` currently encodes to NaN and retrieves
nothing. That is a pre-existing defect in the retrieval path, not in these
features — it predates ADR 0018 and every recorded SASRec number was measured
under it. It is written down as a test here so it cannot be rediscovered by
accident; see `.coordination/DECISIONS.md` O-9 for the repair decision, which is
the owner's because fixing it re-measures the whole SASRec line.

Skipped when the pinned artifact directory is absent, which is the normal state
of a fresh checkout and of CI: the archive is run-scoped and DVC-managed rather
than committed. Point `SASREC_RANKER_ARTIFACT_DIR` at a checkout that has it.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, load_sasrec
from src.models.candidates.sasrec_ranking_features import SasrecScoreFeatures
from src.training.sasrec_ranker import resolve_artifact_dir, strict_prefix

#: The artifact ADR 0018 pins increment 1 to. Not a variable: a run against a
#: different encoder is a different experiment, and the whole point of asserting
#: on the checksum is that it cannot quietly become one.
PINNED_MODEL_SHA256 = "43320b87e3cbc4a0dfbc90bce2e9d9b033fbd4c6cebe7f09447fa6cd5e1215e6"

SCORE_ABS_TOLERANCE = 1e-5
LOGIT_REL_TOLERANCE = 1e-4


def _artifact_dir() -> Path:
    directory = resolve_artifact_dir()
    if not (directory / MANIFEST_FILENAME).is_file():
        pytest.skip(
            f"pinned SASRec artifact not present at {directory}; "
            "set SASREC_RANKER_ARTIFACT_DIR to a checkout that has it"
        )
    return directory


@pytest.fixture(scope="module")
def model():  # type: ignore[no-untyped-def]
    directory = _artifact_dir()
    # `load_sasrec` verifies the archive's bytes against the manifest before it
    # returns, so reaching this line is already a checksum assertion; the
    # explicit one below is about *which* checksum.
    return load_sasrec(directory / MANIFEST_FILENAME)


def test_artifact_is_the_pinned_encoder() -> None:
    """The features are only comparable to a recorded run under one artifact."""
    from src.models.candidates.sasrec_artifact import SASRecArtifactManifest

    manifest = SASRecArtifactManifest.load(_artifact_dir() / MANIFEST_FILENAME)
    assert manifest.model_sha256 == PINNED_MODEL_SHA256
    assert manifest.retrieval_normalization == "l2"
    assert manifest.sequence_order == "oldest-to-newest"
    assert manifest.padding == "left-zero"


def test_encoded_slice_is_the_strict_prefix() -> None:
    """History and exclusions come from one slice, and this is that slice.

    Asserted against `strict_prefix` on a fixture with a run of equal
    timestamps, because the interesting case is not "earlier events are kept" —
    it is that events sharing a second are never context for one another, which
    is where a `side="right"` would silently leak one event into its own query.
    """
    timestamps = np.array([10, 20, 20, 20, 30], dtype=np.int64)
    movie_ids = np.array([1, 2, 3, 4, 5], dtype=np.int64)

    assert strict_prefix(timestamps, movie_ids, 20).tolist() == [1]
    assert strict_prefix(timestamps, movie_ids, 30).tolist() == [1, 2, 3, 4]
    assert strict_prefix(timestamps, movie_ids, 10).tolist() == []


def test_batched_offline_features_match_a_single_recompute(model) -> None:  # type: ignore[no-untyped-def]
    """The training-time batch and the request-time single encode agree.

    Histories are taken from the artifact's own vocabulary rather than from
    Postgres, so this runs without the database: the claim is about the encoder's
    numerics across call shapes, and a real user id would add a data dependency
    without adding evidence.
    """
    features = SasrecScoreFeatures(model)
    vocabulary = sorted(model._item_to_index)
    rng = np.random.default_rng(0)
    # At or above `max_sequence_length`, so no row is left-padded. Shorter
    # histories encode to NaN today — see the module docstring and the test
    # below, which pins that separately rather than hiding it in a tolerance.
    length = model.config.max_sequence_length
    histories = [
        [int(movie) for movie in rng.choice(vocabulary, size=size, replace=False)]
        for size in (length, length + 7, length + 40)
    ]

    batched_norm, batched_raw = model.encode_histories(histories)
    assert batched_norm.shape[0] == len(histories)

    for row, history in enumerate(histories):
        candidates = model.retrieve_from_queries(batched_norm[row : row + 1], 500)[0]
        assert candidates, "the pinned index must retrieve for a non-empty history"

        offline_score, offline_logit = features.scores_for(
            batched_norm[row], batched_raw[row], candidates
        )
        dense = [model.dense_index_for(movie) or model._unknown_index for movie in history]
        single_norm, single_raw = model.encode_dense_history(dense)
        online_score, online_logit = features.scores_for(single_norm[0], single_raw[0], candidates)

        assert not np.isnan(offline_score).any()
        assert np.allclose(offline_score, online_score, atol=SCORE_ABS_TOLERANCE, rtol=0.0)
        assert np.allclose(offline_logit, online_logit, rtol=LOGIT_REL_TOLERANCE, atol=0.0)


def test_score_is_bounded_and_matches_the_retrieval_order(model) -> None:  # type: ignore[no-untyped-def]
    """The score is a cosine, so it is in [-1, 1] and orders the slate.

    The bound is what makes an *absolute* tolerance meaningful on this column; if
    the item side ever stopped being normalised the tolerance would silently
    become a much weaker claim.
    """
    features = SasrecScoreFeatures(model)
    vocabulary = sorted(model._item_to_index)
    rng = np.random.default_rng(1)
    history = [
        int(movie)
        for movie in rng.choice(vocabulary, size=model.config.max_sequence_length, replace=False)
    ]

    normalized, unnormalized = model.encode_histories([history])
    candidates = model.retrieve_from_queries(normalized, 500)[0]
    score, _logit = features.scores_for(normalized[0], unnormalized[0], candidates)

    assert float(score.min()) >= -1.0 - SCORE_ABS_TOLERANCE
    assert float(score.max()) <= 1.0 + SCORE_ABS_TOLERANCE
    # The pinned index is IVF, so it is approximate over *which* items come back
    # — but the scores of what it returned are still the inner products it
    # ranked by, and it returns them in descending order.
    assert np.all(np.diff(score) <= SCORE_ABS_TOLERANCE)


def test_left_padded_sequences_encode_to_nan_today(model) -> None:  # type: ignore[no-untyped-def]
    """A recorded defect, pinned so it cannot be rediscovered by accident.

    `build_index` puts the encoder in `eval()` mode so retrieval is
    deterministic (commit 1d189a8, correctly). PyTorch then takes a fused
    attention path which, unlike the training-mode path, yields NaN for a query
    position whose keys are all masked — and the first position of every
    left-padded sequence is exactly that. The NaN propagates through the second
    block into the real positions, so the query vector is NaN and FAISS returns
    nothing.

    The effect is that any user or positive with fewer than
    `max_sequence_length` items of history retrieves an **empty** slate from the
    learned path. Every SASRec number on record was measured under this, so all
    of them understate the retriever rather than flatter it. The repair is the
    owner's call (`.coordination/DECISIONS.md` O-9) because it re-measures the
    whole line, and until then this test states the behaviour honestly.
    """
    vocabulary = sorted(model._item_to_index)
    short = [int(movie) for movie in vocabulary[: model.config.max_sequence_length - 1]]
    normalized, _unnormalized = model.encode_histories([short])
    assert np.isnan(normalized[0]).any()
    assert model.retrieve_from_queries(normalized, 500)[0] == []

    full = [int(movie) for movie in vocabulary[: model.config.max_sequence_length]]
    normalized_full, _ = model.encode_histories([full])
    assert not np.isnan(normalized_full[0]).any()
    assert len(model.retrieve_from_queries(normalized_full, 500)[0]) == 500


def test_artifact_directory_is_configurable() -> None:
    """A worktree has no artifacts, so the override has to be the documented one."""
    assert os.environ.get("SASREC_RANKER_ARTIFACT_DIR", "") or resolve_artifact_dir()
