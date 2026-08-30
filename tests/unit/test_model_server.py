from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from fastapi import HTTPException, Request

from src.config import DEV_MODEL_SERVER_AUTH_TOKEN
from src.features import FEATURE_COLUMNS
from src.models.artifacts import ArtifactRef, CandidateIndex, ServingArtifactBundle, ServingManifest
from src.serving.model_server import (
    ChampionCoordinates,
    ChampionMismatchError,
    ModelRankingService,
    RankRequest,
    TenantArtifactMismatchError,
    rank,
)
from src.serving.policy import REASON_CHAMPION_MISMATCH


@dataclass
class _Ranker:
    observed_columns: list[str] | None = None

    def predict(self, features):  # type: ignore[no-untyped-def]
        self.observed_columns = list(features.columns)
        return np.asarray(features["user_genre_affinity"], dtype=np.float64)


class _OnlineResponse:
    def __init__(
        self,
        value: dict[str, list[Any]],
        timestamps: dict[str, list[Any]] | None = None,
    ) -> None:
        self._value = value
        self._timestamps = timestamps or {}

    def to_dict(self, include_event_timestamps: bool = False) -> dict[str, list[Any]]:
        if not include_event_timestamps:
            return self._value
        return {**self._value, **self._timestamps}


class _FeatureStore:
    def __init__(self, event_time: float = 1_760_000_000.0) -> None:
        self.entity_rows: list[dict[str, object]] = []
        self.call_count = 0
        self.event_time = event_time

    def get_online_features(self, *, features, entity_rows):  # type: ignore[no-untyped-def]
        self.call_count += 1
        self.entity_rows = entity_rows
        count = len(entity_rows)
        values = {column: [0.0] * count for column in FEATURE_COLUMNS}
        values["user_genre_affinity"] = [0.2, 0.9]
        timestamps = {f"{column}__ts": [self.event_time] * count for column in FEATURE_COLUMNS}
        return _OnlineResponse(values, timestamps)


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
        positive_history_movie_ids=[1],
        excluded_movie_ids=[],
        dismissed_movie_ids=[],
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
    assert result.items[0].features == {
        **{column: 0.0 for column in FEATURE_COLUMNS},
        "user_genre_affinity": 0.9,
    }


def test_ranking_reuses_version_scoped_online_feature_snapshot() -> None:
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
            candidates=CandidateIndex.build({1: {1, 2, 3}}),
            ranker=_Ranker(),  # type: ignore[arg-type]
        ),
        store,
        feature_cache_max_entries=1,
    )

    for _ in range(2):
        service.rank(
            tenant_id="demo",
            user_id=100,
            positive_history_movie_ids=[1],
            excluded_movie_ids=[],
            dismissed_movie_ids=[],
            limit=2,
            candidate_limit=2,
        )

    assert store.call_count == 1


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
            positive_history_movie_ids=[1],
            excluded_movie_ids=[],
            dismissed_movie_ids=[],
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
                positive_history_movie_ids=[1],
                excluded_movie_ids=[],
                limit=1,
                candidate_limit=1,
            ),
            cast(Request, object()),
            authorization=None,
        )

    assert error.value.status_code == 401


def test_watched_exclusions_do_not_stop_the_positive_history_from_seeding() -> None:
    """Mirrors the coordinator's real call: exclusions include the watched id.

    The sidecar has to keep seeding from positive history when the caller's
    exclusion set repeats it, or every warm user's item-item stage collapses
    into the index's popularity fill.
    """
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
            ranker=_Ranker(),  # type: ignore[arg-type]
        ),
        _FeatureStore(),
    )

    result = service.rank(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=[1],
        excluded_movie_ids=[1],
        dismissed_movie_ids=[],
        limit=2,
        candidate_limit=2,
    )

    assert result.seed_count == 1
    assert result.candidate_sources == {"item-item-cosine": 2}
    assert all(item.seed_movie_id == 1 for item in result.items)


def test_rank_request_defaults_dismissals_to_empty_for_an_older_caller() -> None:
    # An API that predates the split still gets seeded retrieval: its positive
    # history already has dismissals filtered out at the query.
    payload = RankRequest(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=[1],
        excluded_movie_ids=[1],
        limit=1,
        candidate_limit=1,
    )

    assert payload.dismissed_movie_ids == []


def _demo_service(store: _FeatureStore) -> ModelRankingService:
    """A service loaded with the bundle the demo tenant is registered on."""
    return ModelRankingService(
        ServingArtifactBundle(
            manifest=ServingManifest(
                tenant_id="demo",
                candidate=ArtifactRef("item-item-cosine", "candidate-v1", "c.json", "hash"),
                ranker=ArtifactRef("lightgbm-lambdarank", "ranker-v1", "r.txt", "hash"),
                feature_version="features-v1",
                trained_at="2026-08-15T00:00:00+00:00",
            ),
            candidates=CandidateIndex.build({1: {1, 2, 3}, 2: {1, 2}}),
            ranker=_Ranker(),  # type: ignore[arg-type]
        ),
        store,
    )


def test_a_matching_champion_is_served_normally() -> None:
    result = _demo_service(_FeatureStore()).rank(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=[1],
        excluded_movie_ids=[],
        dismissed_movie_ids=[],
        limit=2,
        candidate_limit=2,
        champion=ChampionCoordinates(
            candidate_version="candidate-v1",
            ranker_version="ranker-v1",
            feature_version="features-v1",
        ),
    )

    assert result.candidate_version == "candidate-v1"


@pytest.mark.parametrize("coordinate", ["candidate_version", "ranker_version", "feature_version"])
def test_a_champion_that_differs_in_any_coordinate_is_refused(coordinate: str) -> None:
    """Each coordinate is checked on its own.

    A bundle that swaps the ranker while keeping the candidate index is a real
    release, and a check that compared only one of the three would serve it
    under the previous version's name.
    """
    store = _FeatureStore()
    service = _demo_service(store)
    versions = {
        "candidate_version": "candidate-v1",
        "ranker_version": "ranker-v1",
        "feature_version": "features-v1",
    }
    versions[coordinate] = "something-else"

    with pytest.raises(ChampionMismatchError) as error:
        service.rank(
            tenant_id="demo",
            user_id=100,
            positive_history_movie_ids=[1],
            excluded_movie_ids=[],
            dismissed_movie_ids=[],
            limit=2,
            candidate_limit=2,
            champion=ChampionCoordinates(**versions),
        )

    # Refused before any work: a mismatched deployment must not pay a Redis
    # round trip per request to arrive at the same answer.
    assert store.entity_rows == []
    assert "something-else" in str(error.value)
    assert "candidate-v1/ranker-v1/features-v1" in str(error.value)


def test_a_caller_that_states_no_champion_is_still_served() -> None:
    """A rolling deploy has one build calling another for a few seconds.

    The tenant boundary still holds; only the version claim is unstated, and
    refusing every request for the length of a deploy would be worse than the
    skew it guards against.
    """
    result = _demo_service(_FeatureStore()).rank(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=[1],
        excluded_movie_ids=[],
        dismissed_movie_ids=[],
        limit=2,
        candidate_limit=2,
    )

    assert result.ranker_version == "ranker-v1"


@pytest.mark.asyncio
async def test_rank_endpoint_answers_a_champion_mismatch_with_a_coded_409() -> None:
    """The status alone cannot carry the meaning — the sidecar has two 409s.

    The cold-start decline is the other one, so the coordinator classifies on
    the code in the body, and this is where that code is put there.
    """
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ranking_service=_demo_service(_FeatureStore())))
    )

    with pytest.raises(HTTPException) as error:
        await rank(
            RankRequest(
                tenant_id="demo",
                user_id=100,
                positive_history_movie_ids=[1],
                excluded_movie_ids=[],
                dismissed_movie_ids=[],
                limit=1,
                candidate_limit=1,
                champion=ChampionCoordinates(
                    candidate_version="candidate-v9",
                    ranker_version="ranker-v1",
                    feature_version="features-v1",
                ),
            ),
            cast(Request, request),
            authorization=f"Bearer {DEV_MODEL_SERVER_AUTH_TOKEN}",
        )

    assert error.value.status_code == 409
    detail = error.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == REASON_CHAMPION_MISMATCH
    assert "candidate-v9" in detail["message"]


def test_rank_request_defaults_the_champion_to_absent_for_an_older_caller() -> None:
    payload = RankRequest(
        tenant_id="demo",
        user_id=100,
        positive_history_movie_ids=[1],
        excluded_movie_ids=[],
        limit=1,
        candidate_limit=1,
    )

    assert payload.champion is None
