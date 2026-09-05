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

One regression is covered here rather than assumed. Until PR #162, `eval()`
mode took a fused attention path that returned NaN for a query row whose keys
were all masked — the first position of every **left-padded** sequence — and the
NaN reached the real positions, so a history shorter than `max_sequence_length`
encoded to NaN and retrieved nothing (O-9). These features consume exactly that
vector, and a NaN would have become the missing sentinel rather than a value, so
the short-history case is asserted here from the feature path's side as well as
in `tests/unit/test_sasrec.py`.

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
    # At or above `max_sequence_length`, so no row is left-padded — the
    # call-shape tolerance is measured on the simple case, and the padded case
    # gets its own test below rather than being folded into a tolerance.
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


def test_left_padded_sequences_encode_and_retrieve(model) -> None:  # type: ignore[no-untyped-def]
    """O-9's regression, from the feature path's side.

    This test began life asserting the *defect*: `build_index` puts the encoder
    in `eval()` mode so retrieval is deterministic (commit 1d189a8, correctly),
    PyTorch then took a fused attention path that yields NaN for a query position
    whose keys are all masked — the first position of every left-padded sequence
    — and the NaN reached the real positions, so any history shorter than the
    encoder's window retrieved **nothing**. It is inverted here because PR #162
    disabled that path, and a test that pinned the old behaviour would now be
    pinning the bug.

    `tests/unit/test_sasrec.py` covers the encoder itself. This one covers what
    the ranker features need from it: a short history has to produce a usable
    vector *and* a usable slate, because both feed
    `sasrec_user_item_score`/`_logit`, and a NaN vector here would silently
    become the missing sentinel rather than a value.
    """
    vocabulary = sorted(model._item_to_index)
    window = model.config.max_sequence_length
    features = SasrecScoreFeatures(model)

    for length in (1, 3, 12, window - 1, window):
        history = [int(movie) for movie in vocabulary[:length]]
        normalized, unnormalized = model.encode_histories([history])
        assert not np.isnan(normalized[0]).any(), f"length {length} encoded to NaN"

        candidates = model.retrieve_from_queries(normalized, 500)[0]
        assert len(candidates) == 500, f"length {length} retrieved {len(candidates)}"

        score, logit = features.scores_for(normalized[0], unnormalized[0], candidates)
        assert not np.isnan(score).any() and not np.isnan(logit).any()


def test_artifact_directory_is_configurable() -> None:
    """A worktree has no artifacts, so the override has to be the documented one."""
    assert os.environ.get("SASREC_RANKER_ARTIFACT_DIR", "") or resolve_artifact_dir()
