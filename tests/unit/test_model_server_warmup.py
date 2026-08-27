"""Warm-up and per-worker readiness for the learned model sidecar.

The defect underneath these tests: a worker that reported healthy after paying
LightGBM's native initialization but before anything had touched Feast, whose
first real request then took 10.597 s against the API's 0.5 s client timeout and
came back as an honest HTTP 200 with ``learned=false``. Two properties have to
hold instead — the warm rank travels the same path ``/rank`` does, and a worker
that warms against an empty online store never becomes reachable.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from src.feature_contract import FEATURE_COLUMNS
from src.models.artifacts import ArtifactRef, CandidateIndex, ServingArtifactBundle, ServingManifest
from src.serving import model_server
from src.serving.model_server import (
    WARMUP_SEED_LIMIT,
    WARMUP_USER_ID,
    DegenerateWarmupError,
    ModelRankingService,
    healthz,
    lifespan,
)

_EVENT_TIME = 1_760_000_000

_MANIFEST = ServingManifest(
    tenant_id="demo",
    candidate=ArtifactRef("item-item-cosine", "candidate-v1", "c.json", "hash"),
    ranker=ArtifactRef("lightgbm-lambdarank", "ranker-v1", "r.txt", "hash"),
    feature_version="features-v1",
    trained_at="2026-08-15T00:00:00+00:00",
)

# Two users who watched the same twenty titles: every pair co-occurs, so all
# twenty ids carry neighbours and the eight lowest are the warm seeds.
_CATALOG_SIZE = 20
_HISTORIES = {1: set(range(1, _CATALOG_SIZE + 1)), 2: set(range(1, _CATALOG_SIZE + 1))}
_WARM_CANDIDATE_COUNT = _CATALOG_SIZE - WARMUP_SEED_LIMIT

_ITEM_COLUMNS = (
    "item_popularity_all_time",
    "item_popularity_30d",
    "item_popularity_7d",
    "item_age_days",
)


class _OnlineResponse:
    def __init__(self, values: dict[str, list[Any]], timestamps: dict[str, list[Any]]) -> None:
        self._values = values
        self._timestamps = timestamps

    def to_dict(self, include_event_timestamps: bool = False) -> dict[str, list[Any]]:
        if not include_event_timestamps:
            return dict(self._values)
        return {**self._values, **self._timestamps}


class _FeastLikeStore:
    """What Feast returns for the warm-up's entity rows.

    Feast answers a row it holds nothing for with ``None`` per feature and
    second 0 on the matching ``__ts`` column (``OnlineResponse.to_dict`` emits
    ``ts.seconds``), which is exactly what an emptied or evicted Redis looks
    like from inside the sidecar. The warm user id is one no tenant issues, so
    the user-scoped columns miss in every configuration below — the production
    shape the non-degeneracy check is written against.
    """

    def __init__(self, *, item_value: float | None = 3.0, event_time: int = _EVENT_TIME) -> None:
        self._item_value = item_value
        self._event_time = event_time
        self.entity_rows: list[dict[str, object]] = []
        self.requested_features: list[str] = []
        self.call_count = 0

    def get_online_features(
        self,
        *,
        features: list[str],
        entity_rows: list[dict[str, object]],
    ) -> _OnlineResponse:
        self.call_count += 1
        self.entity_rows = list(entity_rows)
        self.requested_features = list(features)
        count = len(entity_rows)
        values: dict[str, list[Any]] = {column: [None] * count for column in FEATURE_COLUMNS}
        timestamps: dict[str, list[Any]] = {
            f"{column}__ts": [0] * count for column in FEATURE_COLUMNS
        }
        if self._item_value is not None:
            for column in _ITEM_COLUMNS:
                values[column] = [self._item_value] * count
                timestamps[f"{column}__ts"] = [self._event_time] * count
        return _OnlineResponse(values, timestamps)


def _materialized_store() -> _FeastLikeStore:
    """Item rows written and timestamped; the user rows still miss."""
    return _FeastLikeStore()


def _empty_store() -> _FeastLikeStore:
    """Nothing in Redis: every value missing, every event timestamp second 0."""
    return _FeastLikeStore(item_value=None, event_time=0)


def _zeroed_store() -> _FeastLikeStore:
    """Rows exist and are timestamped, but every value in them is zero."""
    return _FeastLikeStore(item_value=0.0)


class _Ranker:
    def __init__(self) -> None:
        self.predict_calls = 0
        self.observed_columns: list[str] | None = None
        self.observed_rows = 0

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        self.predict_calls += 1
        self.observed_columns = list(features.columns)
        self.observed_rows = len(features)
        return np.asarray(features["item_popularity_all_time"], dtype=np.float64)


def _bundle(ranker: _Ranker | None = None) -> ServingArtifactBundle:
    return ServingArtifactBundle(
        manifest=_MANIFEST,
        candidates=CandidateIndex.build(_HISTORIES),
        ranker=ranker or _Ranker(),  # type: ignore[arg-type]
    )


def _service(store: _FeastLikeStore, ranker: _Ranker | None = None) -> ModelRankingService:
    return ModelRankingService(_bundle(ranker), store)


def _request(**state: Any) -> Request:
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))))


def _patch_boot(monkeypatch: pytest.MonkeyPatch, store: _FeastLikeStore) -> None:
    """Boot ``lifespan`` against stubs instead of a manifest on disk."""
    bundle = _bundle()
    monkeypatch.setattr(ServingArtifactBundle, "load", staticmethod(lambda path: bundle))
    monkeypatch.setattr(model_server, "create_feature_store", lambda settings: store)


def test_warmup_serves_a_representative_rank_over_item_features_alone() -> None:
    """The warm rank is a real one, and a missing warm user does not break it.

    ``WARMUP_USER_ID`` is deliberately a user no tenant has, so both user-scoped
    feature views miss and land in the frame as zeros. What is left is the item
    view, which is the only thing that can prove Redis holds anything.
    """
    store = _materialized_store()
    ranker = _Ranker()
    service = _service(store, ranker)

    report = service.warmup()

    assert report.seed_movie_ids == tuple(range(1, WARMUP_SEED_LIMIT + 1))
    assert report.candidate_count == _WARM_CANDIDATE_COUNT
    assert report.ranked_count == model_server.WARMUP_LIMIT
    assert report.seed_count == WARMUP_SEED_LIMIT
    assert report.feature_event_time == float(_EVENT_TIME)
    assert report.warmup_ms > 0
    # The booster scored the real candidate matrix, not a synthetic single row:
    # this is the work the first viewer used to pay for.
    assert ranker.predict_calls == 1
    assert ranker.observed_columns == FEATURE_COLUMNS
    assert ranker.observed_rows == _WARM_CANDIDATE_COUNT


def test_warmup_reads_the_online_store_once_for_the_rank_it_then_serves() -> None:
    """One Redis round trip: the assertion's read is the rank's read.

    The pre-read exists so the check can see the whole candidate matrix rather
    than the top-K the ranker returns. It shares the per-process feature cache
    with the rank that follows, so it must not cost a second lookup.
    """
    store = _materialized_store()
    service = _service(store)

    service.warmup()

    assert store.call_count == 1
    assert len(store.entity_rows) == _WARM_CANDIDATE_COUNT
    # Tenant-keyed and warm-user-keyed, the same entity shape `/rank` sends.
    assert store.entity_rows[0] == {
        "tenant_id": "demo",
        "user_id": WARMUP_USER_ID,
        "item_id": WARMUP_SEED_LIMIT + 1,
    }
    assert store.requested_features == model_server.RANKER_FEATURES


def test_warmup_seeds_come_from_the_bundle_and_are_stable_across_workers() -> None:
    """Sorted index ids, so two workers on one image warm on identical input."""
    histories = {user: {10, 25, 40, 55, 70, 85, 100, 115, 130} for user in (1, 2)}
    bundle = ServingArtifactBundle(
        manifest=_MANIFEST,
        candidates=CandidateIndex.build(histories),
        ranker=_Ranker(),  # type: ignore[arg-type]
    )

    reports = [ModelRankingService(bundle, _materialized_store()).warmup() for _ in range(2)]

    assert reports[0].seed_movie_ids == (10, 25, 40, 55, 70, 85, 100, 115)
    assert reports[1].seed_movie_ids == reports[0].seed_movie_ids


def test_warmup_falls_back_to_popularity_ids_when_the_index_has_no_neighbours() -> None:
    """A neighbourless index still warms the same read; it just cannot seed it."""
    service = ModelRankingService(
        ServingArtifactBundle(
            manifest=_MANIFEST,
            candidates=CandidateIndex(neighbors={}, popularity=tuple(range(1, 13))),
            ranker=_Ranker(),  # type: ignore[arg-type]
        ),
        _materialized_store(),
    )

    report = service.warmup()

    assert report.seed_movie_ids == tuple(range(1, WARMUP_SEED_LIMIT + 1))
    assert report.candidate_count == 4
    # Nothing was retrieved from a seed, and the report says so rather than
    # implying the similarity stage ran.
    assert report.seed_count == 0


def test_warmup_refuses_to_report_warm_against_an_empty_online_store() -> None:
    """The evicted-Redis case: no values and no event timestamps."""
    service = _service(_empty_store())

    with pytest.raises(DegenerateWarmupError) as error:
        service.warmup()

    assert isinstance(error.value, RuntimeError)
    assert "no Feast event timestamp" in str(error.value)
    assert "src.features.materialize" in str(error.value)


def test_warmup_refuses_to_report_warm_when_every_feature_value_is_zero() -> None:
    """Timestamped rows that carry nothing are still a constant feature matrix."""
    service = _service(_zeroed_store())

    with pytest.raises(DegenerateWarmupError) as error:
        service.warmup()

    assert "only zeros" in str(error.value)
    assert f"{_WARM_CANDIDATE_COUNT} candidates" in str(error.value)


def test_warmup_rejects_a_bundle_whose_index_holds_nothing() -> None:
    service = ModelRankingService(
        ServingArtifactBundle(
            manifest=_MANIFEST,
            candidates=CandidateIndex(neighbors={}, popularity=()),
            ranker=_Ranker(),  # type: ignore[arg-type]
        ),
        _materialized_store(),
    )

    with pytest.raises(DegenerateWarmupError):
        service.warmup()


async def test_healthz_answers_503_until_this_worker_has_warmed() -> None:
    response = Response()

    payload = await healthz(_request(), response)

    assert response.status_code == 503
    assert payload["status"] == "warming"
    assert payload["warm"] is False
    assert payload["warmup_ms"] is None


async def test_healthz_reports_the_warm_shape_and_the_thread_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_SERVER_WORKERS", "4")
    for name in model_server._NATIVE_THREAD_VARIABLES:
        monkeypatch.setenv(name, "1")
    service = _service(_materialized_store())
    report = service.warmup()
    response = Response()

    payload = await healthz(_request(ranking_service=service, warmup=report), response)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["warm"] is True
    assert payload["warmup_ms"] == pytest.approx(report.warmup_ms, abs=1e-3)
    assert payload["workers"] == 4
    assert payload["native_threads"] == {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
    }
    assert payload["tenant_id"] == "demo"
    assert payload["candidate_version"] == "candidate-v1"
    assert payload["ranker_version"] == "ranker-v1"
    assert payload["feature_version"] == "features-v1"


async def test_healthz_reports_an_unset_worker_count_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker cannot count its siblings, so it says so rather than guessing."""
    monkeypatch.delenv("MODEL_SERVER_WORKERS", raising=False)
    for name in model_server._NATIVE_THREAD_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    service = _service(_materialized_store())
    response = Response()

    payload = await healthz(_request(ranking_service=service, warmup=service.warmup()), response)

    assert response.status_code == 200
    assert payload["workers"] is None
    assert payload["native_threads"] == dict.fromkeys(model_server._NATIVE_THREAD_VARIABLES)


def test_healthz_serves_503_then_200_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through FastAPI itself, because the status code is the contract.

    The compose healthcheck reads the status line and nothing else, and every
    ``depends_on: service_healthy`` in the production stack waits on it, so an
    endpoint that returns a ``warm: false`` body with a 200 would advertise a
    cold worker as ready.
    """
    monkeypatch.setenv("MODEL_SERVER_WORKERS", "4")
    probe = FastAPI()
    probe.get("/healthz")(healthz)

    with TestClient(probe) as client:
        cold = client.get("/healthz")
        assert cold.status_code == 503
        assert cold.json()["warm"] is False

        service = _service(_materialized_store())
        probe.state.ranking_service = service
        probe.state.warmup = service.warmup()

        warm = client.get("/healthz")

    assert warm.status_code == 200
    body = warm.json()
    assert body["warm"] is True
    assert body["workers"] == 4
    assert body["warmup_ms"] > 0
    assert body["tenant_id"] == "demo"


async def test_lifespan_warms_before_it_publishes_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _materialized_store()
    _patch_boot(monkeypatch, store)
    app = FastAPI()

    async with lifespan(app):
        assert app.state.warmup.ranked_count == model_server.WARMUP_LIMIT
        assert app.state.ranking_service.manifest.tenant_id == "demo"
        # The worker paid the online read during startup, not on first request.
        assert store.call_count == 1


async def test_lifespan_refuses_to_start_against_an_empty_online_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup fails, so the container never reports healthy and never serves."""
    _patch_boot(monkeypatch, _empty_store())
    app = FastAPI()

    with pytest.raises(DegenerateWarmupError):
        async with lifespan(app):
            pass

    assert not hasattr(app.state, "ranking_service")
    assert not hasattr(app.state, "warmup")
