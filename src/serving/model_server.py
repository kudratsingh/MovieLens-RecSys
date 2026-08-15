"""Private Feast + LightGBM sidecar for learned candidate ranking."""

from __future__ import annotations

import logging
import math
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import Settings
from src.features import FEATURE_COLUMNS
from src.features.online import RANKER_FEATURES, create_feature_store
from src.models.artifacts import ServingArtifactBundle, ServingManifest

logger = logging.getLogger(__name__)


class _OnlineResponse(Protocol):
    def to_dict(self) -> dict[str, list[Any]]: ...


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


@dataclass(frozen=True)
class RankingResult:
    items: list[RankedItem]
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    latency_ms: float


class ModelRankingService:
    """Own loaded artifacts and perform request-time feature lookup only."""

    def __init__(self, bundle: ServingArtifactBundle, feature_store: OnlineFeatureStore) -> None:
        self._bundle = bundle
        self._feature_store = feature_store

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
        history_movie_ids: list[int],
        limit: int,
        candidate_limit: int,
    ) -> RankingResult:
        started = time.perf_counter()
        manifest = self._bundle.manifest
        if tenant_id != manifest.tenant_id:
            raise TenantArtifactMismatchError(
                f"tenant {tenant_id!r} cannot use artifacts for {manifest.tenant_id!r}"
            )
        if not history_movie_ids:
            raise ColdStartError("learned retrieval requires at least one historical interaction")

        candidate_ids = self._bundle.candidates.retrieve(
            history_movie_ids,
            limit=max(limit, candidate_limit),
        )
        if not candidate_ids:
            raise ValueError("learned candidate index returned no unseen items")
        features = self._online_features(
            tenant_id=tenant_id,
            user_id=user_id,
            candidate_ids=candidate_ids,
        )
        scores = self._bundle.ranker.predict(features)
        order = np.argsort(-scores, kind="stable")[:limit]
        items = [
            RankedItem(movie_id=candidate_ids[int(index)], score=float(scores[int(index)]))
            for index in order
        ]
        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "learned_rank tenant_id=%s user_id=%s candidate_policy=%s "
            "candidate_version=%s ranker_version=%s feature_version=%s "
            "candidate_count=%s result_count=%s latency_ms=%.3f",
            tenant_id,
            user_id,
            manifest.candidate.artifact_type,
            manifest.candidate.version,
            manifest.ranker.version,
            manifest.feature_version,
            len(candidate_ids),
            len(items),
            latency_ms,
        )
        return RankingResult(
            items=items,
            candidate_policy=manifest.candidate.artifact_type,
            candidate_version=manifest.candidate.version,
            ranker_version=manifest.ranker.version,
            feature_version=manifest.feature_version,
            latency_ms=latency_ms,
        )

    def _online_features(
        self,
        *,
        tenant_id: str,
        user_id: int,
        candidate_ids: list[int],
    ) -> pd.DataFrame:
        response = self._feature_store.get_online_features(
            features=RANKER_FEATURES,
            entity_rows=[
                {"tenant_id": tenant_id, "user_id": user_id, "item_id": item_id}
                for item_id in candidate_ids
            ],
        ).to_dict()
        rows: dict[str, list[float]] = {}
        for column in FEATURE_COLUMNS:
            values = response.get(column)
            if values is None or len(values) != len(candidate_ids):
                raise ValueError(f"online feature response is missing candidate-aligned {column!r}")
            rows[column] = [_finite_float(value) for value in values]
        return pd.DataFrame(rows, columns=FEATURE_COLUMNS)


class RankRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: int
    history_movie_ids: list[int]
    limit: int = Field(default=10, ge=1, le=50)
    candidate_limit: int = Field(default=100, ge=1, le=500)


class RankItemResponse(BaseModel):
    movie_id: int
    score: float


class RankResponse(BaseModel):
    tenant_id: str
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    latency_ms: float
    items: list[RankItemResponse]


_settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manifest_path = _settings.model_artifact_dir / _settings.model_manifest_name
    bundle = ServingArtifactBundle.load(manifest_path)
    service = ModelRankingService(bundle, create_feature_store(_settings))
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
        result = service.rank(
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            history_movie_ids=payload.history_movie_ids,
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
        latency_ms=result.latency_ms,
        items=[RankItemResponse(movie_id=item.movie_id, score=item.score) for item in result.items],
    )


def _finite_float(value: Any) -> float:
    result = float(value) if value is not None else 0.0
    return result if math.isfinite(result) else 0.0
