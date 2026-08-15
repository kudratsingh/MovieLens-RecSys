from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pytest
from fastapi import HTTPException, Request

from src.features import FEATURE_COLUMNS
from src.models.artifacts import ArtifactRef, CandidateIndex, ServingArtifactBundle, ServingManifest
from src.serving.model_server import (
    ModelRankingService,
    RankRequest,
    TenantArtifactMismatchError,
    rank,
)


@dataclass
class _Ranker:
    observed_columns: list[str] | None = None

    def predict(self, features):  # type: ignore[no-untyped-def]
        self.observed_columns = list(features.columns)
        return np.asarray(features["user_genre_affinity"], dtype=np.float64)


class _OnlineResponse:
    def __init__(self, value: dict[str, list[Any]]) -> None:
        self._value = value

    def to_dict(self) -> dict[str, list[Any]]:
        return self._value


class _FeatureStore:
    def __init__(self) -> None:
        self.entity_rows: list[dict[str, object]] = []

    def get_online_features(self, *, features, entity_rows):  # type: ignore[no-untyped-def]
        self.entity_rows = entity_rows
        count = len(entity_rows)
        values = {column: [0.0] * count for column in FEATURE_COLUMNS}
        values["user_genre_affinity"] = [0.2, 0.9]
        return _OnlineResponse(values)


def test_ranking_uses_tenant_keyed_feast_rows_and_ranker_scores() -> None:
    ranker = _Ranker()
    store = _FeatureStore()
    service = ModelRankingService(
        ServingArtifactBundle(
            manifest=ServingManifest(
                tenant_id="demo",
                candidate=ArtifactRef("item-item-cosine", "candidate-v1", "c.json", "hash"),
                ranker=ArtifactRef("lightgbm-lambdarank", "ranker-v1", "r.txt", "hash"),
                feature_version="features-v1",
                trained_at="2026-08-15T00:00:00+00:00",
            ),
            candidates=CandidateIndex.build({1: {1, 2, 3}, 2: {1, 2}}),
            ranker=ranker,  # type: ignore[arg-type]
        ),
        store,
    )

    result = service.rank(
        tenant_id="demo",
        user_id=100,
        history_movie_ids=[1],
        limit=2,
        candidate_limit=2,
    )

    assert [item.movie_id for item in result.items] == [3, 2]
    assert ranker.observed_columns == FEATURE_COLUMNS
    assert store.entity_rows == [
        {"tenant_id": "demo", "user_id": 100, "item_id": 2},
        {"tenant_id": "demo", "user_id": 100, "item_id": 3},
    ]
    assert result.ranker_version == "ranker-v1"


def test_ranking_rejects_cross_tenant_artifact_use_before_feature_read() -> None:
    ranker = _Ranker()
    store = _FeatureStore()
    service = ModelRankingService(
        ServingArtifactBundle(
            manifest=ServingManifest(
                tenant_id="demo",
                candidate=ArtifactRef("item-item-cosine", "candidate-v1", "c.json", "hash"),
                ranker=ArtifactRef("lightgbm-lambdarank", "ranker-v1", "r.txt", "hash"),
                feature_version="features-v1",
                trained_at="2026-08-15T00:00:00+00:00",
            ),
            candidates=CandidateIndex.build({1: {1, 2}}),
            ranker=ranker,  # type: ignore[arg-type]
        ),
        store,
    )

    with pytest.raises(TenantArtifactMismatchError):
        service.rank(
            tenant_id="default",
            user_id=100,
            history_movie_ids=[1],
            limit=1,
            candidate_limit=1,
        )

    assert store.entity_rows == []


@pytest.mark.asyncio
async def test_rank_endpoint_rejects_missing_service_credentials() -> None:
    with pytest.raises(HTTPException) as error:
        await rank(
            RankRequest(
                tenant_id="demo",
                user_id=100,
                history_movie_ids=[1],
                limit=1,
                candidate_limit=1,
            ),
            cast(Request, object()),
            authorization=None,
        )

    assert error.value.status_code == 401
