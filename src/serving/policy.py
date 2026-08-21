"""Shared vocabulary for serving policy, exclusion filtering, and audit digests.

The API, the model sidecar, and the audit log all have to agree on what a
"policy" is, what was filtered out of a response, and how the request's input
state is identified. Keeping that vocabulary in one module is what lets a
recorded audit be replayed and compared against a later request instead of
being a free-text note.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from src.models.artifacts import (
    CANDIDATE_SOURCE_POPULARITY_FILL,
    CANDIDATE_SOURCE_SIMILARITY,
)

__all__ = [
    "CANDIDATE_SOURCE_POPULARITY_FALLBACK",
    "CANDIDATE_SOURCE_POPULARITY_FILL",
    "CANDIDATE_SOURCE_SIMILARITY",
    "EXCLUSION_FILTER_POLICY",
    "FILTER_POLICY_NOT_RUN",
    "POLICY_POPULARITY",
    "SCORE_SCALE_INTERACTION_COUNT",
    "SCORE_SCALE_NOT_RUN",
    "SCORE_SCALE_RANK",
    "id_set_digest",
]

# Bump the suffix whenever the *meaning* of the filter changes, so a stored
# audit can never be misread as having applied today's rules.
EXCLUSION_FILTER_POLICY = "watched-and-dismissed-excluded-v1"
FILTER_POLICY_NOT_RUN = "not-run"

POLICY_POPULARITY = "popularity"

CANDIDATE_SOURCE_POPULARITY_FALLBACK = "popularity-fallback"

# LightGBM LambdaRank emits an uncalibrated ordering score. Naming the scale on
# every response is what stops a client from rendering it as a match
# percentage or a probability (ADR 0012).
SCORE_SCALE_RANK = "lightgbm-rank-score"
SCORE_SCALE_INTERACTION_COUNT = "tenant-interaction-count"
SCORE_SCALE_NOT_RUN = "not-run"


def id_set_digest(movie_ids: Iterable[int]) -> str:
    """Return a stable, order-independent digest of an input id set.

    Deduplicated and sorted first so two requests that carry the same logical
    state produce the same digest regardless of row order, and the count is
    part of the payload so an empty set is distinguishable from a missing one.
    """
    ordered = sorted(set(movie_ids))
    payload = f"{len(ordered)}:{','.join(str(movie_id) for movie_id in ordered)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
