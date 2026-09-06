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
    "ARTIFACT_SHA256_NOT_PINNED",
    "CANDIDATE_SOURCE_POPULARITY_FALLBACK",
    "CANDIDATE_SOURCE_POPULARITY_FILL",
    "CANDIDATE_SOURCE_SIMILARITY",
    "EXCLUSION_FILTER_POLICY",
    "FILTER_POLICY_NOT_RUN",
    "POLICY_POPULARITY",
    "RANKER_ROUTES",
    "RANKER_ROUTE_FALLBACK",
    "RANKER_ROUTE_LEARNED",
    "RANKER_ROUTE_NOT_RUN",
    "REASON_CHAMPION_MISMATCH",
    "REASON_NO_CHAMPION",
    "RETRIEVER_FAMILY_NOT_RUN",
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

# The two ranking routes a manifest publishes a booster for. They live here
# rather than beside the manifest that validates them because the audit column
# that records *which one ran* is written by the slim API, which cannot import
# ``src.models``; ``src.models.artifacts`` re-exports these names so a manifest
# and an audit row can never disagree about how a route is spelled.
RANKER_ROUTE_LEARNED = "learned"
RANKER_ROUTE_FALLBACK = "fallback"
RANKER_ROUTES: tuple[str, ...] = (RANKER_ROUTE_LEARNED, RANKER_ROUTE_FALLBACK)

# What the audit records for a request that reached no ranker at all — a 4xx, or
# a failure before the coordinator returned. Deliberately outside
# ``RANKER_ROUTES``: those two name boosters a bundle actually publishes, and a
# request that ran neither must not be counted as either.
RANKER_ROUTE_NOT_RUN = "not-run"
RETRIEVER_FAMILY_NOT_RUN = "not-run"

# The audit's answer when no checksum-pinned artifact produced the candidates.
# The popularity fallback runs off a SQL query rather than a published index, so
# there is nothing to pin. Empty rather than a word: the column otherwise holds a
# 64-character hex digest, and an empty string cannot be mistaken for one.
ARTIFACT_SHA256_NOT_PINNED = ""


def id_set_digest(movie_ids: Iterable[int]) -> str:
    """Return a stable, order-independent digest of an input id set.

    Deduplicated and sorted first so two requests that carry the same logical
    state produce the same digest regardless of row order, and the count is
    part of the payload so an empty set is distinguishable from a missing one.
    """
    ordered = sorted(set(movie_ids))
    payload = f"{len(ordered)}:{','.join(str(movie_id) for movie_id in ordered)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
