from __future__ import annotations

import json

import httpx
import pytest

from synthetic.smoke.demo import DemoSmokeError, run_behavior_smoke, wait_for_readiness


def _response(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in {"/healthz", "/", "/realms/demo"}:
        return httpx.Response(200, json={"status": "ok"})
    if path == "/api/personas":
        return httpx.Response(
            200,
            json={
                "items": [
                    {"slug": "action-fan", "user_id": 101},
                    {"slug": "drama-fan", "user_id": 102},
                    {"slug": "eclectic-viewer", "user_id": 103},
                    {"slug": "cold-start", "user_id": 104},
                ]
            },
        )
    if path == "/api/users/101":
        return httpx.Response(
            200,
            json={
                "history": {"items": [{"movie_id": 1}]},
                "recommendations": {
                    "policy": "item-item-cosine+lightgbm",
                    "items": [{"movie_id": 2}],
                },
            },
        )
    if path == "/api/users/104":
        return httpx.Response(
            200,
            json={
                "history": {"items": []},
                "recommendations": {"policy": "popularity", "items": [{"movie_id": 1}]},
            },
        )
    return httpx.Response(404)


def test_readiness_names_each_required_dependency() -> None:
    with httpx.Client(transport=httpx.MockTransport(_response)) as client:
        wait_for_readiness(
            client,
            api_url="http://api.test",
            web_url="http://web.test",
            keycloak_url="http://keycloak.test",
            attempts=1,
        )


def test_readiness_failure_identifies_dependency_and_url() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(DemoSmokeError, match="FastAPI.*api.test/healthz.*HTTP 503"):
            wait_for_readiness(
                client,
                api_url="http://api.test",
                web_url="http://web.test",
                keycloak_url="http://keycloak.test",
                attempts=1,
            )


def test_behavior_smoke_covers_warm_and_cold_personas() -> None:
    with httpx.Client(transport=httpx.MockTransport(_response)) as client:
        summary = run_behavior_smoke(client, web_url="http://web.test")

    assert json.loads(json.dumps(summary.__dict__)) == {
        "persona_count": 4,
        "action_history_count": 1,
        "action_recommendation_count": 1,
        "cold_history_count": 0,
        "cold_recommendation_count": 1,
    }


def test_behavior_smoke_rejects_seen_recommendations() -> None:
    def leaking_response(request: httpx.Request) -> httpx.Response:
        response = _response(request)
        if request.url.path == "/api/users/101":
            return httpx.Response(
                200,
                json={
                    "history": {"items": [{"movie_id": 1}]},
                    "recommendations": {
                        "policy": "item-item-cosine+lightgbm",
                        "items": [{"movie_id": 1}],
                    },
                },
            )
        return response

    with httpx.Client(transport=httpx.MockTransport(leaking_response)) as client:
        with pytest.raises(DemoSmokeError, match="seen movie IDs"):
            run_behavior_smoke(client, web_url="http://web.test")
