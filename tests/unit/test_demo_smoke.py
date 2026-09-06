from __future__ import annotations

import json

import httpx
import pytest

from synthetic.smoke.demo import (
    DemoSmokeError,
    assert_learned_retrieval,
    fetch_recent_audits,
    run_behavior_smoke,
    wait_for_readiness,
)


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
        "action_retriever_family": "item-item-cosine",
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


# --- the warm persona's policy assertion ------------------------------------
#
# The check used to require `item-item-cosine+lightgbm` literally, which made
# the runbook's smoke step unpassable under any other champion — a SASRec bundle
# answers `sasrec+lightgbm`. What it must keep refusing is a warm persona that
# quietly lost its retrieval stage, which is why the degraded names are asserted
# one by one rather than covered by "anything with a ranker passes".


def _warm_policy_transport(policy: str) -> httpx.MockTransport:
    """The fixture stack, with the warm persona answered by ``policy``."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users/101":
            return httpx.Response(
                200,
                json={
                    "history": {"items": [{"movie_id": 1}]},
                    "recommendations": {"policy": policy, "items": [{"movie_id": 2}]},
                },
            )
        return _response(request)

    return httpx.MockTransport(respond)


def test_behavior_smoke_accepts_a_sequence_champion() -> None:
    with httpx.Client(transport=_warm_policy_transport("sasrec+lightgbm")) as client:
        summary = run_behavior_smoke(client, web_url="http://web.test")

    assert summary.action_retriever_family == "sasrec"


@pytest.mark.parametrize(
    "policy",
    ["popularity", "popularity-fill+lightgbm", "popularity-fallback+lightgbm", "sasrec"],
)
def test_behavior_smoke_still_fails_a_warm_persona_that_lost_retrieval(policy: str) -> None:
    with httpx.Client(transport=_warm_policy_transport(policy)) as client:
        with pytest.raises(DemoSmokeError):
            run_behavior_smoke(client, web_url="http://web.test")


def test_an_expected_family_is_checked_exactly() -> None:
    with httpx.Client(transport=_warm_policy_transport("item-item-cosine+lightgbm")) as client:
        with pytest.raises(DemoSmokeError, match="expects 'sasrec'"):
            run_behavior_smoke(
                client, web_url="http://web.test", expected_retriever_family="sasrec"
            )


def test_assert_learned_retrieval_returns_the_family_that_served() -> None:
    assert assert_learned_retrieval("sasrec+lightgbm", subject="Action Fan") == "sasrec"
    assert (
        assert_learned_retrieval("item-item-cosine+lightgbm", subject="Action Fan")
        == "item-item-cosine"
    )


def test_direct_api_smoke_uses_a_short_lived_service_token() -> None:
    def direct_response(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "service-token"})
        assert request.headers.get("Authorization") == "Bearer service-token"
        if path == "/personas":
            return _response(httpx.Request("GET", "http://web.test/api/personas"))
        if path == "/users/101/history":
            return httpx.Response(200, json={"items": [{"movie_id": 1}]})
        if path == "/users/101/recommendations":
            return httpx.Response(
                200,
                json={
                    "policy": "item-item-cosine+lightgbm",
                    "items": [{"movie_id": 2}],
                },
            )
        if path == "/users/104/history":
            return httpx.Response(200, json={"items": []})
        if path == "/users/104/recommendations":
            return httpx.Response(
                200,
                json={"policy": "popularity", "items": [{"movie_id": 1}]},
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(direct_response)) as client:
        summary = run_behavior_smoke(
            client,
            web_url="http://web.test",
            api_url="http://api.test",
            keycloak_url="http://keycloak.test",
        )

    assert summary.action_history_count == 1
    assert summary.cold_history_count == 0


def test_recent_audits_use_a_short_lived_service_token() -> None:
    def audit_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "service-token"})
        assert request.headers.get("Authorization") == "Bearer service-token"
        assert request.url.path == "/users/900000101/audits"
        assert str(request.url.params) == "limit=3"
        return httpx.Response(200, json={"items": [{"request_id": "req-1"}]})

    with httpx.Client(transport=httpx.MockTransport(audit_response)) as client:
        payload = fetch_recent_audits(
            client,
            api_url="http://api.test",
            keycloak_url="http://keycloak.test",
        )

    assert payload == {"items": [{"request_id": "req-1"}]}
