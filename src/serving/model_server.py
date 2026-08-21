"""Private Feast + LightGBM sidecar for learned candidate ranking."""

from __future__ import annotations

import logging
import math
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import AliasChoices, BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import Settings
from src.feature_contract import FEATURE_COLUMNS
from src.features.online import RANKER_FEATURES, create_feature_store
from src.models.artifacts import ServingArtifactBundle, ServingManifest
from src.serving.policy import EXCLUSION_FILTER_POLICY

logger = logging.getLogger(__name__)

# Feast names its event-timestamp columns by appending this suffix when
# ``to_dict(include_event_timestamps=True)`` is used.
_FEAST_TIMESTAMP_SUFFIX = "__ts"


class _OnlineResponse(Protocol):
    def to_dict(self, include_event_timestamps: bool = False) -> dict[str, list[Any]]: ...


class OnlineFeatureStore(Protocol):
    def get_online_features(
        self,
        *,
        features: list[str],
        entity_rows: list[dict[str, object]],
    ) -> _OnlineResponse: ...


class ColdStartError(ValueError):
    """Learned retrieval intentionally declines users without history."""


class TenantArtifactMismatchError(ValueError):
    """The request tenant does not match the artifact isolation boundary."""


@dataclass(frozen=True)
class RankedItem:
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str
    seed_movie_id: int | None


@dataclass(frozen=True)
class RankingResult:
    items: list[RankedItem]
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    latency_ms: float
    candidate_sources: dict[str, int]
    # Positive seeds that actually reached a candidate, not the number offered.
    # The caller decides whether it may claim learned retrieval from this.
    seed_count: int
    excluded_count: int
    filter_policy: str
    feature_event_time: float | None


class ModelRankingService:
    """Own loaded artifacts and perform request-time feature lookup only."""

    def __init__(
        self,
        bundle: ServingArtifactBundle,
        feature_store: OnlineFeatureStore,
        *,
        feature_cache_max_entries: int = 256,
    ) -> None:
        self._bundle = bundle
        self._feature_store = feature_store
        self._feature_cache_max_entries = feature_cache_max_entries
        self._feature_cache: OrderedDict[
            tuple[str, int, tuple[int, ...]], tuple[pd.DataFrame, float | None]
        ] = OrderedDict()
        self._feature_cache_lock = Lock()

    @property
    def manifest(self) -> ServingManifest:
        return self._bundle.manifest

    def warmup(self) -> None:
        """Pay LightGBM's lazy native initialization before readiness."""
        self._bundle.ranker.predict(
            pd.DataFrame(
                [{column: 0.0 for column in FEATURE_COLUMNS}],
                columns=FEATURE_COLUMNS,
            )
        )

    def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        positive_history_movie_ids: list[int],
        excluded_movie_ids: list[int],
        dismissed_movie_ids: list[int],
        limit: int,
        candidate_limit: int,
    ) -> RankingResult:
        started = time.perf_counter()
        manifest = self._bundle.manifest
        if tenant_id != manifest.tenant_id:
            raise TenantArtifactMismatchError(
                f"tenant {tenant_id!r} cannot use artifacts for {manifest.tenant_id!r}"
            )
        if not positive_history_movie_ids:
            raise ColdStartError("learned retrieval requires at least one historical interaction")

        excluded = set(excluded_movie_ids)
        candidate_started = time.perf_counter()
        retrieval = self._bundle.candidates.retrieve(
            positive_history_movie_ids,
            limit=max(limit, candidate_limit),
            excluded_movie_ids=excluded,
            dismissed_movie_ids=dismissed_movie_ids,
        )
        candidate_ids = retrieval.movie_ids
        candidate_latency_ms = (time.perf_counter() - candidate_started) * 1000
        if not candidate_ids:
            raise ValueError("learned candidate index returned no unseen items")
        feature_started = time.perf_counter()
        features, feature_event_time = self._online_features(
            tenant_id=tenant_id,
            user_id=user_id,
            candidate_ids=candidate_ids,
        )
        feature_latency_ms = (time.perf_counter() - feature_started) * 1000
        ranker_started = time.perf_counter()
        scores = self._bundle.ranker.predict(features)
        order = np.argsort(-scores, kind="stable")
        items: list[RankedItem] = []
        for raw_index in order:
            if len(items) >= limit:
                break
            index = int(raw_index)
            contribution = retrieval.contributions[index]
            # Retrieval already dropped these, so a hit here means the index and
            # the live exclusion set disagreed. Drop it rather than rank it.
            if contribution.movie_id in excluded:
                logger.warning(
                    "excluded_candidate_blocked tenant_id=%s user_id=%s movie_id=%s stage=ranker",
                    tenant_id,
                    user_id,
                    contribution.movie_id,
                )
                continue
            items.append(
                RankedItem(
                    movie_id=contribution.movie_id,
                    score=float(scores[index]),
                    features={
                        column: float(features.iloc[index][column]) for column in FEATURE_COLUMNS
                    },
                    candidate_source=contribution.source,
                    seed_movie_id=contribution.seed_movie_id,
                )
            )
        ranker_latency_ms = (time.perf_counter() - ranker_started) * 1000
        latency_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "learned_rank tenant_id=%s user_id=%s candidate_policy=%s "
            "candidate_version=%s ranker_version=%s feature_version=%s "
            "candidate_count=%s result_count=%s positive_count=%s seed_count=%s "
            "excluded_count=%s candidate_latency_ms=%.3f feature_latency_ms=%.3f "
            "ranker_latency_ms=%.3f latency_ms=%.3f",
            tenant_id,
            user_id,
            manifest.candidate.artifact_type,
            manifest.candidate.version,
            manifest.ranker.version,
            manifest.feature_version,
            len(candidate_ids),
            len(items),
            # Offered next to used: a gap between the two is how an index that
            # has never scored this user's titles shows up in the logs.
            len(positive_history_movie_ids),
            retrieval.seed_count,
            retrieval.excluded_count,
            candidate_latency_ms,
            feature_latency_ms,
            ranker_latency_ms,
            latency_ms,
        )
        return RankingResult(
            items=items,
            candidate_policy=manifest.candidate.artifact_type,
            candidate_version=manifest.candidate.version,
            ranker_version=manifest.ranker.version,
            feature_version=manifest.feature_version,
            candidate_latency_ms=candidate_latency_ms,
            feature_latency_ms=feature_latency_ms,
            ranker_latency_ms=ranker_latency_ms,
            latency_ms=latency_ms,
            candidate_sources=retrieval.source_counts(),
            seed_count=retrieval.seed_count,
            excluded_count=retrieval.excluded_count,
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=feature_event_time,
        )

    def _online_features(
        self,
        *,
        tenant_id: str,
        user_id: int,
        candidate_ids: list[int],
    ) -> tuple[pd.DataFrame, float | None]:
        cache_key = (tenant_id, user_id, tuple(candidate_ids))
        # Artifacts and their Feast snapshot are versioned together. A new
        # manifest requires a sidecar restart, which also clears this bounded
        # cache. Candidate ids are part of the key, so immediate rating
        # feedback that changes the unseen set cannot reuse an old matrix.
        with self._feature_cache_lock:
            cached = self._feature_cache.get(cache_key)
            if cached is not None:
                self._feature_cache.move_to_end(cache_key)
                return cached

        response = self._feature_store.get_online_features(
            features=RANKER_FEATURES,
            entity_rows=[
                {"tenant_id": tenant_id, "user_id": user_id, "item_id": item_id}
                for item_id in candidate_ids
            ],
        ).to_dict(include_event_timestamps=True)
        rows: dict[str, list[float]] = {}
        for column in FEATURE_COLUMNS:
            values = response.get(column)
            if values is None or len(values) != len(candidate_ids):
                raise ValueError(f"online feature response is missing candidate-aligned {column!r}")
            rows[column] = [_finite_float(value) for value in values]
        result = (
            pd.DataFrame(rows, columns=FEATURE_COLUMNS),
            _oldest_event_time(response),
        )
        with self._feature_cache_lock:
            self._feature_cache[cache_key] = result
            self._feature_cache.move_to_end(cache_key)
            while len(self._feature_cache) > self._feature_cache_max_entries:
                self._feature_cache.popitem(last=False)
        return result


class RankRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: int
    # Positive history and exclusions are separate inputs on purpose: the first
    # seeds retrieval, the second only suppresses. ``history_movie_ids`` stays
    # accepted as an alias so a partially rolled deployment keeps serving.
    positive_history_movie_ids: list[int] = Field(
        validation_alias=AliasChoices("positive_history_movie_ids", "history_movie_ids"),
        serialization_alias="positive_history_movie_ids",
    )
    excluded_movie_ids: list[int] = Field(default_factory=list)
    # Dismissals are the only ids that also remove a seed. They arrive on their
    # own field because ``excluded_movie_ids`` is the caller's whole "never show
    # this" set and therefore contains the watched history — using it to filter
    # seeds is what silently emptied item-item retrieval. Defaulted so an API
    # that predates this field still gets seeded retrieval: the caller's
    # positive history already excludes dismissals at the query.
    dismissed_movie_ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)
    candidate_limit: int = Field(default=100, ge=1, le=500)


class RankItemResponse(BaseModel):
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str
    seed_movie_id: int | None


class RankResponse(BaseModel):
    tenant_id: str
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    latency_ms: float
    candidate_sources: dict[str, int]
    seed_count: int
    excluded_count: int
    filter_policy: str
    feature_event_time: float | None
    items: list[RankItemResponse]


_settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manifest_path = _settings.model_artifact_dir / _settings.model_manifest_name
    bundle = ServingArtifactBundle.load(manifest_path)
    service = ModelRankingService(
        bundle,
        create_feature_store(_settings),
        feature_cache_max_entries=_settings.model_feature_cache_max_entries,
    )
    service.warmup()
    app.state.ranking_service = service
    logger.info(
        "Model server loaded tenant=%s candidate=%s ranker=%s features=%s",
        bundle.manifest.tenant_id,
        bundle.manifest.candidate.version,
        bundle.manifest.ranker.version,
        bundle.manifest.feature_version,
    )
    yield


app = FastAPI(
    title="MovieLens Internal Model Server",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    service: ModelRankingService = request.app.state.ranking_service
    return {
        "status": "ok",
        "tenant_id": service.manifest.tenant_id,
        "candidate_version": service.manifest.candidate.version,
        "ranker_version": service.manifest.ranker.version,
        "feature_version": service.manifest.feature_version,
    }


@app.post("/rank", response_model=RankResponse)
async def rank(
    payload: RankRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RankResponse:
    expected = f"Bearer {_settings.model_server_auth_token.get_secret_value()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid model-server credentials")
    service: ModelRankingService = request.app.state.ranking_service
    try:
        result = await run_in_threadpool(
            service.rank,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            positive_history_movie_ids=payload.positive_history_movie_ids,
            excluded_movie_ids=payload.excluded_movie_ids,
            dismissed_movie_ids=payload.dismissed_movie_ids,
            limit=payload.limit,
            candidate_limit=payload.candidate_limit,
        )
    except TenantArtifactMismatchError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ColdStartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RankResponse(
        tenant_id=payload.tenant_id,
        candidate_policy=result.candidate_policy,
        candidate_version=result.candidate_version,
        ranker_version=result.ranker_version,
        feature_version=result.feature_version,
        candidate_latency_ms=result.candidate_latency_ms,
        feature_latency_ms=result.feature_latency_ms,
        ranker_latency_ms=result.ranker_latency_ms,
        latency_ms=result.latency_ms,
        candidate_sources=result.candidate_sources,
        seed_count=result.seed_count,
        excluded_count=result.excluded_count,
        filter_policy=result.filter_policy,
        feature_event_time=result.feature_event_time,
        items=[
            RankItemResponse(
                movie_id=item.movie_id,
                score=item.score,
                features=item.features,
                candidate_source=item.candidate_source,
                seed_movie_id=item.seed_movie_id,
            )
            for item in result.items
        ],
    )


def _finite_float(value: Any) -> float:
    result = float(value) if value is not None else 0.0
    return result if math.isfinite(result) else 0.0


def _oldest_event_time(response: dict[str, list[Any]]) -> float | None:
    """Return the oldest Feast event time backing this candidate matrix.

    Freshness is only honest if it reports the *stalest* feature the ranker
    saw, so the audit takes the minimum. Feast appends ``__ts`` columns only
    when the online store carries them; absence is reported as unknown rather
    than as "now".
    """
    oldest: float | None = None
    for column in FEATURE_COLUMNS:
        values = response.get(f"{column}{_FEAST_TIMESTAMP_SUFFIX}")
        if not values:
            continue
        for value in values:
            if value is None:
                continue
            seconds = float(value)
            if not math.isfinite(seconds) or seconds <= 0:
                continue
            if oldest is None or seconds < oldest:
                oldest = seconds
    return oldest
