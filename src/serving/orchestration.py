"""Candidate → feature → ranker orchestration with explicit safe fallbacks.

The coordinator owns the split that ADR 0012 requires: positive watched
history, the full exclusion set, and dismissals are read as separate inputs
and stay separate all the way to the sidecar. The last two are not the same
list — exclusions contain the user's own watched titles and may only hide,
while a dismissal is the one signal that also stops a title from seeding
retrieval. Exclusions are re-applied at every stage that can introduce an id —
fallback, retrieval, hydration, and a final check on the outgoing list — so a
dismissed title has to survive four independent filters to leak, and the one
place that could still return it fails closed with an audited reason instead.

The policy this module reports is held to what actually ran: a two-stage call
whose retrieval no seed reached is reported as ``unseeded-retrieval`` with
``learned`` false, never as learned two-stage serving.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx
from sqlalchemy import Connection
from starlette.concurrency import run_in_threadpool

from src.evaluation.protocol import COLD_START_THRESHOLD
from src.serving.audit import PredictionAudit
from src.serving.models import (
    ModelRankingResult,
    ModelServerClient,
    ModelServerContractError,
)
from src.serving.policy import (
    CANDIDATE_SOURCE_POPULARITY_FALLBACK,
    CANDIDATE_SOURCE_POPULARITY_FILL,
    EXCLUSION_FILTER_POLICY,
    POLICY_POPULARITY,
    SCORE_SCALE_INTERACTION_COUNT,
    SCORE_SCALE_RANK,
    id_set_digest,
)
from src.serving.recommendations import (
    RecommendationService,
    RecommendedMovie,
    ServingInputState,
)

logger = logging.getLogger(__name__)

# Reason codes are the stable prefix of the audited reason string. The detail
# after the colon is for a human reading one row; the prefix is what tests and
# dashboards group on.
REASON_COLD_START = "cold-start"
REASON_MODEL_UNAVAILABLE = "model-server-unavailable"
REASON_EMPTY_LEARNED = "empty-learned-result"
REASON_EXCLUDED_BLOCKED = "excluded-id-blocked"
REASON_LEARNED = "learned-two-stage"
# A two-stage call whose retrieval stage no seed reached. The ranker still ran,
# so the response is not a popularity fallback — but it is not learned
# retrieval either, and it gets its own prefix rather than borrowing one.
REASON_UNSEEDED = "unseeded-retrieval"

# The honest policy name when retrieval degenerated to its fill order.
POLICY_UNSEEDED = f"{CANDIDATE_SOURCE_POPULARITY_FILL}+lightgbm"

LEARNED_REASON = "Similar to movies in this persona's watched history"


class ModelRanker(Protocol):
    async def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        positive_history_movie_ids: list[int],
        excluded_movie_ids: list[int] | None = None,
        dismissed_movie_ids: list[int] | None = None,
        limit: int,
        candidate_limit: int = 100,
    ) -> ModelRankingResult: ...


@dataclass(frozen=True)
class ServingPolicy:
    """What produced this response, in terms the UI is allowed to repeat."""

    name: str
    learned: bool
    positive_signal_count: int
    threshold: int
    reason: str
    score_scale: str
    filter_policy: str
    excluded_count: int


@dataclass(frozen=True)
class RecommendationDecision:
    policy: str
    serving_policy: ServingPolicy
    model_version: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    model_latency_ms: float
    fallback_reason: str | None
    items: list[RecommendedMovie]
    predictions: list[PredictionAudit]
    input_state_revision: int = 0
    input_state_hash: str = ""
    exclusion_hash: str = ""
    positive_signal_count: int = 0
    excluded_count: int = 0
    filter_policy: str = EXCLUSION_FILTER_POLICY
    feature_event_time: datetime | None = None
    candidate_sources: dict[str, int] = field(default_factory=dict)
    reason: str = ""


class RecommendationCoordinator:
    def __init__(
        self,
        recommendations: RecommendationService,
        models: ModelRanker | ModelServerClient,
    ) -> None:
        self._recommendations = recommendations
        self._models = models

    async def recommend(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        user_id: int,
        limit: int,
    ) -> RecommendationDecision:
        # SQLAlchemy uses a synchronous psycopg2 connection here. Keep each
        # operation serialized on the request's RLS transaction, but do not
        # block the worker event loop while Postgres is doing I/O.
        state = await run_in_threadpool(
            self._recommendations.serving_input_state,
            connection,
            user_id=user_id,
        )
        if state.positive_signal_count < COLD_START_THRESHOLD:
            return await self._popularity(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=limit,
                state=state,
                fallback_reason="cold-start",
                reason=(
                    f"{REASON_COLD_START}: {state.positive_signal_count} positive watched "
                    f"signals below threshold {COLD_START_THRESHOLD}"
                ),
            )
        try:
            learned = await self._models.rank(
                tenant_id=tenant_id,
                user_id=user_id,
                positive_history_movie_ids=state.positive_movie_ids,
                excluded_movie_ids=state.excluded_movie_ids,
                # Sent apart from the exclusion set, which contains this user's
                # own watched titles: only a dismissal may stop a title from
                # seeding retrieval (ADR 0012).
                dismissed_movie_ids=state.dismissed_movie_ids,
                limit=limit,
                candidate_limit=max(100, limit * 10),
            )
        except (httpx.HTTPError, ModelServerContractError) as exc:
            return await self._popularity(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=limit,
                state=state,
                fallback_reason="model-server-unavailable",
                reason=f"{REASON_MODEL_UNAVAILABLE}: {type(exc).__name__}",
            )
        items = await run_in_threadpool(
            self._recommendations.hydrate_ranked_movies,
            connection,
            user_id=user_id,
            ranked_items=[(item.movie_id, item.score) for item in learned.items],
            reason=LEARNED_REASON,
            excluded_movie_ids=state.excluded_movie_ids,
        )
        items, blocked = _enforce_exclusions(
            items,
            excluded=state.excluded_movie_ids,
            tenant_id=tenant_id,
            user_id=user_id,
            stage="learned-output",
        )
        if not items:
            return await self._popularity(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                limit=limit,
                state=state,
                fallback_reason=("excluded-id-blocked" if blocked else "empty-learned-result"),
                reason=(
                    f"{REASON_EXCLUDED_BLOCKED}: learned output held only excluded ids "
                    f"{sorted(blocked)}"
                    if blocked
                    else f"{REASON_EMPTY_LEARNED}: no learned candidate survived hydration"
                ),
            )
        served_ids = {movie.movie_id for movie in items}
        # The response may only claim learned two-stage serving when the
        # retrieval stage actually ran on this user's positive history. A
        # result no seed reached is the index's fill order with a ranker
        # applied to it, and calling that "learned" is the defect the Bundle 7
        # finish-gate review caught (N2). ``seed_count`` is the sidecar's count
        # of seeds that reached a candidate, so this is the seeds *used*.
        seeded = learned.seed_count > 0
        policy_name = f"{learned.candidate_policy}+lightgbm" if seeded else POLICY_UNSEEDED
        reason = (
            (
                f"{REASON_LEARNED}: {learned.candidate_policy} retrieval over "
                f"{learned.seed_count} positive seeds, ranked by {learned.ranker_version}"
            )
            if seeded
            else (
                f"{REASON_UNSEEDED}: none of {state.positive_signal_count} positive watched "
                f"signals seeded {learned.candidate_policy} retrieval; "
                f"{CANDIDATE_SOURCE_POPULARITY_FILL} candidates ranked by "
                f"{learned.ranker_version}"
            )
        )
        if blocked:
            reason = f"{reason}; {REASON_EXCLUDED_BLOCKED}: {sorted(blocked)}"
        return RecommendationDecision(
            policy=policy_name,
            serving_policy=ServingPolicy(
                name=policy_name,
                learned=seeded,
                positive_signal_count=state.positive_signal_count,
                threshold=COLD_START_THRESHOLD,
                reason=reason,
                score_scale=SCORE_SCALE_RANK,
                filter_policy=EXCLUSION_FILTER_POLICY,
                excluded_count=len(state.excluded_movie_ids),
            ),
            model_version=f"{learned.candidate_version}/{learned.ranker_version}",
            candidate_version=learned.candidate_version,
            ranker_version=learned.ranker_version,
            feature_version=learned.feature_version,
            candidate_latency_ms=learned.candidate_latency_ms,
            feature_latency_ms=learned.feature_latency_ms,
            ranker_latency_ms=learned.ranker_latency_ms,
            model_latency_ms=learned.latency_ms,
            # A degraded first stage is audited like any other degradation, so
            # `fallback_reason IS NOT NULL` still finds every request that did
            # not get the policy it was routed to.
            fallback_reason=None if seeded else REASON_UNSEEDED,
            items=items,
            predictions=[
                PredictionAudit(
                    movie_id=item.movie_id,
                    score=item.score,
                    features=item.features,
                    candidate_source=item.candidate_source,
                    seed_movie_id=item.seed_movie_id,
                )
                for item in learned.items
                if item.movie_id in served_ids
            ],
            input_state_revision=state.revision,
            input_state_hash=id_set_digest(state.positive_movie_ids),
            exclusion_hash=id_set_digest(state.excluded_movie_ids),
            positive_signal_count=state.positive_signal_count,
            excluded_count=len(state.excluded_movie_ids),
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=_event_time(learned.feature_event_time),
            candidate_sources=dict(learned.candidate_sources),
            reason=reason,
        )

    async def _popularity(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        user_id: int,
        limit: int,
        state: ServingInputState,
        fallback_reason: str,
        reason: str,
    ) -> RecommendationDecision:
        items = await run_in_threadpool(
            self._recommendations.popular_for_user,
            connection,
            user_id=user_id,
            limit=limit,
            excluded_movie_ids=state.excluded_movie_ids,
        )
        items, blocked = _enforce_exclusions(
            items,
            excluded=state.excluded_movie_ids,
            tenant_id=tenant_id,
            user_id=user_id,
            stage="popularity-output",
        )
        if blocked:
            reason = f"{reason}; {REASON_EXCLUDED_BLOCKED}: {sorted(blocked)}"
        return RecommendationDecision(
            policy=POLICY_POPULARITY,
            serving_policy=ServingPolicy(
                name=POLICY_POPULARITY,
                learned=False,
                positive_signal_count=state.positive_signal_count,
                threshold=COLD_START_THRESHOLD,
                reason=reason,
                score_scale=SCORE_SCALE_INTERACTION_COUNT,
                filter_policy=EXCLUSION_FILTER_POLICY,
                excluded_count=len(state.excluded_movie_ids),
            ),
            model_version="popularity-v1",
            candidate_version="popularity-v1",
            ranker_version="not-run",
            feature_version="not-read",
            candidate_latency_ms=0.0,
            feature_latency_ms=0.0,
            ranker_latency_ms=0.0,
            model_latency_ms=0.0,
            fallback_reason=fallback_reason,
            items=items,
            predictions=[
                PredictionAudit(
                    movie_id=item.movie_id,
                    score=item.score,
                    features={},
                    candidate_source=CANDIDATE_SOURCE_POPULARITY_FALLBACK,
                    seed_movie_id=None,
                )
                for item in items
            ],
            input_state_revision=state.revision,
            input_state_hash=id_set_digest(state.positive_movie_ids),
            exclusion_hash=id_set_digest(state.excluded_movie_ids),
            positive_signal_count=state.positive_signal_count,
            excluded_count=len(state.excluded_movie_ids),
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=None,
            candidate_sources={CANDIDATE_SOURCE_POPULARITY_FALLBACK: len(items)},
            reason=reason,
        )


def _enforce_exclusions(
    items: list[RecommendedMovie],
    *,
    excluded: list[int],
    tenant_id: str,
    user_id: int,
    stage: str,
) -> tuple[list[RecommendedMovie], list[int]]:
    """Drop any excluded id that reached the outgoing list.

    Every upstream stage already filters, so anything caught here means two
    views of the user's state disagreed. Returning it would be a durable,
    user-visible breach of an explicit "not for me"; dropping it costs one
    slot. The blocked ids travel back to the caller so the decision is audited
    rather than silently absorbed.
    """
    if not excluded:
        return items, []
    excluded_set = set(excluded)
    kept: list[RecommendedMovie] = []
    blocked: list[int] = []
    for item in items:
        if item.movie_id in excluded_set:
            blocked.append(item.movie_id)
            continue
        kept.append(item)
    if blocked:
        logger.warning(
            "excluded_ids_blocked tenant_id=%s user_id=%s stage=%s movie_ids=%s",
            tenant_id,
            user_id,
            stage,
            sorted(blocked),
        )
    return kept, blocked


def _event_time(value: float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC)
