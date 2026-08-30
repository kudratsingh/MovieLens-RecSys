from __future__ import annotations

import json

import httpx
import pytest

from src.features import FEATURE_COLUMNS
from src.serving.models import (
    ModelServerChampionMismatchError,
    ModelServerClient,
    ModelServerContractError,
)
from src.serving.policy import REASON_CHAMPION_MISMATCH
from src.serving.tenancy import TenantChampion


def _features(value: float = 0.0) -> dict[str, float]:
    return {column: value for column in FEATURE_COLUMNS}


@pytest.mark.asyncio
async def test_model_client_sends_tenant_history_and_service_token() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.read()))
        observed["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.2,
                "feature_latency_ms": 3.0,
                "ranker_latency_ms": 0.5,
                "latency_ms": 4.2,
                "items": [{"movie_id": 3, "score": 0.8, "features": _features()}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://models",
        headers={"Authorization": "Bearer secret"},
    )
    try:
        result = await client.rank(
            tenant_id="demo",
            user_id=10,
            positive_history_movie_ids=[1, 2],
            excluded_movie_ids=[1, 2, 7],
            dismissed_movie_ids=[7],
            limit=5,
        )
    finally:
        await client.aclose()

    assert observed["tenant_id"] == "demo"
    assert observed["positive_history_movie_ids"] == [1, 2]
    # The exclusion set carries the watched history; the dismissal set is the
    # only one the sidecar may subtract from the seeds, so they travel apart.
    assert observed["excluded_movie_ids"] == [1, 2, 7]
    assert observed["dismissed_movie_ids"] == [7]
    assert observed["authorization"] == "Bearer secret"
    assert result.items[0].movie_id == 3
    assert result.items[0].features == _features()


@pytest.mark.asyncio
async def test_model_client_rejects_seen_item_from_sidecar() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.1,
                "feature_latency_ms": 0.5,
                "ranker_latency_ms": 0.2,
                "latency_ms": 1.0,
                "items": [{"movie_id": 2, "score": 0.8, "features": _features()}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://models"
    )
    try:
        with pytest.raises(ModelServerContractError, match="seen movie"):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1, 2],
                limit=5,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_model_client_rejects_incomplete_feature_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.1,
                "feature_latency_ms": 0.5,
                "ranker_latency_ms": 0.2,
                "latency_ms": 1.0,
                "items": [{"movie_id": 3, "score": 0.8, "features": {}}],
            },
        )

    client = ModelServerClient(base_url="http://models", auth_token="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://models"
    )
    try:
        with pytest.raises(ModelServerContractError, match="incompatible features"):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1],
                limit=5,
            )
    finally:
        await client.aclose()


def _mock_client(handler) -> ModelServerClient:  # type: ignore[no-untyped-def]
    client = ModelServerClient(base_url="http://models", auth_token="secret")
    # Replace the real transport rather than the client, so the timeout and
    # header wiring under test stay the ones the constructor set up.
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://models",
        headers={"Authorization": "Bearer secret"},
    )
    return client


@pytest.mark.asyncio
async def test_the_champion_is_sent_as_three_named_coordinates() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.2,
                "feature_latency_ms": 3.0,
                "ranker_latency_ms": 0.5,
                "latency_ms": 4.2,
                "items": [{"movie_id": 3, "score": 0.8, "features": _features()}],
            },
        )

    client = _mock_client(handler)
    try:
        await client.rank(
            tenant_id="demo",
            user_id=10,
            positive_history_movie_ids=[1],
            limit=5,
            champion=TenantChampion(
                candidate_version="candidate-v1",
                ranker_version="ranker-v1",
                feature_version="features-v1",
            ),
        )
    finally:
        await client.aclose()

    assert observed["champion"] == {
        "candidate_version": "candidate-v1",
        "ranker_version": "ranker-v1",
        "feature_version": "features-v1",
    }


@pytest.mark.asyncio
async def test_no_champion_omits_the_field_rather_than_sending_null() -> None:
    """A sidecar reading an absent field and a null one must not disagree."""
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "tenant_id": "demo",
                "candidate_policy": "item-item-cosine",
                "candidate_version": "candidate-v1",
                "ranker_version": "ranker-v1",
                "feature_version": "features-v1",
                "candidate_latency_ms": 0.2,
                "feature_latency_ms": 3.0,
                "ranker_latency_ms": 0.5,
                "latency_ms": 4.2,
                "items": [{"movie_id": 3, "score": 0.8, "features": _features()}],
            },
        )

    client = _mock_client(handler)
    try:
        await client.rank(
            tenant_id="demo",
            user_id=10,
            positive_history_movie_ids=[1],
            limit=5,
        )
    finally:
        await client.aclose()

    assert "champion" not in observed


@pytest.mark.asyncio
async def test_a_coded_409_is_raised_as_a_champion_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": REASON_CHAMPION_MISMATCH,
                    "message": "tenant 'demo' is registered on candidate-v2/...",
                }
            },
        )

    client = _mock_client(handler)
    try:
        with pytest.raises(ModelServerChampionMismatchError, match="candidate-v2"):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1],
                limit=5,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_the_other_409_is_still_an_ordinary_transport_failure() -> None:
    """The sidecar's cold-start decline shares the status and not the meaning.

    Classifying on the status alone would audit a cold-start decline as a
    half-finished promotion, which is a different thing to go and fix.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"detail": "learned retrieval requires at least one interaction"}
        )

    client = _mock_client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1],
                limit=5,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_an_unreadable_409_body_is_not_guessed_at() -> None:
    """An honest "the sidecar failed" beats an invented reason in the audit."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, content=b"<html>gateway</html>")

    client = _mock_client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.rank(
                tenant_id="demo",
                user_id=10,
                positive_history_movie_ids=[1],
                limit=5,
            )
    finally:
        await client.aclose()
