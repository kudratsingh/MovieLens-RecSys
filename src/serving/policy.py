"""Shared vocabulary for serving policy, exclusion filtering, and audit digests.

The API, the model sidecar, and the audit log all have to agree on what a
"policy" is, what was filtered out of a response, and how the request's input
state is identified. Keeping that vocabulary in one module is what lets a
recorded audit be replayed and compared against a later request instead of
being a free-text note.

Dependency-free on purpose, in the same spirit as ``src.feature_contract``.
The slim API image carries no numpy, pandas, LightGBM, or Feast (ADR 0008's
sidecar split), so a contract module the API imports must never reach into
``src.models`` — the constants live here and the model code imports *them*.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

__all__ = [
    "CANDIDATE_SOURCE_POPULARITY_FALLBACK",
    "CANDIDATE_SOURCE_POPULARITY_FILL",
    "CANDIDATE_SOURCE_SIMILARITY",
    "EXCLUSION_FILTER_POLICY",
    "FILTER_POLICY_NOT_RUN",
    "POLICY_POPULARITY",
    "REASON_CHAMPION_MISMATCH",
    "REASON_NO_CHAMPION",
    "SCORE_SCALE_INTERACTION_COUNT",
    "SCORE_SCALE_NOT_RUN",
    "SCORE_SCALE_RANK",
    "id_set_digest",
]

# Where a retrieved candidate came from. Defined here rather than beside the
# candidate index so both the slim API and the sidecar can name a source.
CANDIDATE_SOURCE_SIMILARITY = "item-item-cosine"
CANDIDATE_SOURCE_POPULARITY_FILL = "popularity-fill"

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

# The two reason prefixes about the tenant's *registered* champion (migration
# 0016). The rest of the vocabulary lives beside the gates that emit it in
# ``src.serving.orchestration``; these two are here because they are the one
# pair that crosses the sidecar boundary. ``champion-mismatch`` is the machine
# readable code the sidecar puts in its refusal body so the coordinator can tell
# "your bundle is not this tenant's champion" from every other way a rank call
# can fail, without reading prose, and ``no-champion`` is the same question
# answered before the call is made at all.
REASON_NO_CHAMPION = "no-champion"
REASON_CHAMPION_MISMATCH = "champion-mismatch"


def id_set_digest(movie_ids: Iterable[int]) -> str:
    """Return a stable, order-independent digest of an input id set.

    Deduplicated and sorted first so two requests that carry the same logical
    state produce the same digest regardless of row order, and the count is
    part of the payload so an empty set is distinguishable from a missing one.
    """
    ordered = sorted(set(movie_ids))
    payload = f"{len(ordered)}:{','.join(str(movie_id) for movie_id in ordered)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
