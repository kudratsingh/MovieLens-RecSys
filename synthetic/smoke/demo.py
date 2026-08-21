"""Readiness and behavioral smoke checks for the local demo stack."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class DemoSmokeError(RuntimeError):
    """A named demo dependency or behavioral contract failed."""


@dataclass(frozen=True)
class SmokeSummary:
    persona_count: int
    action_history_count: int
    action_recommendation_count: int
    cold_history_count: int
    cold_recommendation_count: int


def wait_for_readiness(
    client: httpx.Client,
    *,
    api_url: str,
    web_url: str,
    keycloak_url: str,
    attempts: int = 60,
    interval_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    checks = {
        "FastAPI": f"{api_url.rstrip('/')}/healthz",
        "Next.js": web_url.rstrip("/"),
        "Keycloak demo realm": f"{keycloak_url.rstrip('/')}/realms/demo",
    }
    for name, url in checks.items():
        last_error = "no response"
        for attempt in range(attempts):
            try:
                response = client.get(url)
                if response.is_success:
                    break
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            if attempt < attempts - 1:
                sleep(interval_seconds)
        else:
            raise DemoSmokeError(f"{name} is not ready at {url}: {last_error}")


def run_behavior_smoke(
    client: httpx.Client,
    *,
    web_url: str,
    api_url: str | None = None,
    keycloak_url: str | None = None,
) -> SmokeSummary:
    """Check warm/cold behavior through the authenticated API.

    ``web_url`` remains the backwards-compatible fixture path for unit tests.
    The real Compose smoke supplies ``api_url`` plus ``keycloak_url`` and uses
    a short-lived confidential service token; browser-session behavior is
    covered separately by Playwright.
    """
    if (api_url and keycloak_url) or (not api_url and not keycloak_url):
        pass
    else:
        raise DemoSmokeError("api_url and keycloak_url must be supplied together")

    direct_api = api_url is not None
    base_url = (api_url or web_url).rstrip("/")
    headers = (
        {"Authorization": f"Bearer {service_access_token(client, keycloak_url)}"}
        if direct_api and keycloak_url
        else None
    )
    persona_path = "/personas" if direct_api else "/api/personas"
    personas_payload = _get_json(
        client,
        f"{base_url}{persona_path}",
        "persona API",
        headers=headers,
    )
    persona_items = _require_list(personas_payload, "items", "persona API")
    by_slug = {
        str(item["slug"]): item
        for item in persona_items
        if isinstance(item, dict) and "slug" in item
    }
    required_slugs = {"action-fan", "drama-fan", "eclectic-viewer", "cold-start"}
    missing = sorted(required_slugs - set(by_slug))
    if missing:
        raise DemoSmokeError(f"persona API is missing required personas: {missing}")

    action = _dashboard(
        client,
        base_url,
        int(by_slug["action-fan"]["user_id"]),
        direct_api=direct_api,
        headers=headers,
    )
    cold = _dashboard(
        client,
        base_url,
        int(by_slug["cold-start"]["user_id"]),
        direct_api=direct_api,
        headers=headers,
    )
    action_history = _dashboard_items(action, "history")
    action_recommendations = _dashboard_items(action, "recommendations")
    cold_history = _dashboard_items(cold, "history")
    cold_recommendations = _dashboard_items(cold, "recommendations")

    if not action_history:
        raise DemoSmokeError("Action Fan has no seeded history")
    if not action_recommendations:
        raise DemoSmokeError("Action Fan has no recommendations")
    if cold_history:
        raise DemoSmokeError("Cold Start unexpectedly has interaction history")
    if not cold_recommendations:
        raise DemoSmokeError("Cold Start has no popularity fallback recommendations")
    action_policy = _recommendation_policy(action)
    cold_policy = _recommendation_policy(cold)
    if action_policy != "item-item-cosine+lightgbm":
        raise DemoSmokeError(f"Action Fan did not use learned two-stage serving: {action_policy}")
    if cold_policy != "popularity":
        raise DemoSmokeError(f"Cold Start did not use popularity fallback: {cold_policy}")

    seen_ids = {int(item["movie_id"]) for item in action_history}
    recommended_ids = {int(item["movie_id"]) for item in action_recommendations}
    overlap = sorted(seen_ids & recommended_ids)
    if overlap:
        raise DemoSmokeError(f"Action Fan recommendations contain seen movie IDs: {overlap}")

    return SmokeSummary(
        persona_count=len(persona_items),
        action_history_count=len(action_history),
        action_recommendation_count=len(action_recommendations),
        cold_history_count=len(cold_history),
        cold_recommendation_count=len(cold_recommendations),
    )


def _dashboard(
    client: httpx.Client,
    base_url: str,
    user_id: int,
    *,
    direct_api: bool,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    if not direct_api:
        return _get_json(
            client,
            f"{base_url}/api/users/{user_id}",
            f"dashboard user {user_id}",
            headers=headers,
        )
    return {
        "recommendations": _get_json(
            client,
            f"{base_url}/users/{user_id}/recommendations?limit=8",
            f"recommendations user {user_id}",
            headers=headers,
        ),
        "history": _get_json(
            client,
            f"{base_url}/users/{user_id}/history?limit=8",
            f"history user {user_id}",
            headers=headers,
        ),
    }


def _dashboard_items(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    value = payload.get(section)
    if not isinstance(value, dict):
        raise DemoSmokeError(f"dashboard response has no {section!r} object")
    return _require_list(value, "items", f"dashboard {section}")


def _recommendation_policy(payload: dict[str, Any]) -> str:
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, dict):
        raise DemoSmokeError("dashboard response has no 'recommendations' object")
    policy = recommendations.get("policy")
    if not isinstance(policy, str):
        raise DemoSmokeError("dashboard recommendations have no policy")
    return policy


def _require_list(payload: dict[str, Any], key: str, source: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DemoSmokeError(f"{source} response has no valid {key!r} list")
    return value


def service_access_token(client: httpx.Client, keycloak_url: str) -> str:
    url = f"{keycloak_url.rstrip('/')}/realms/demo/protocol/openid-connect/token"
    try:
        response = client.post(
            url,
            data={
                "client_id": "movielens-api",
                "client_secret": "movielens-api-secret-dev-only",
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        token = response.json().get("access_token")
    except (httpx.HTTPError, ValueError) as exc:
        raise DemoSmokeError(f"Keycloak service token failed at {url}") from exc
    if not isinstance(token, str) or not token:
        raise DemoSmokeError("Keycloak service token response has no access_token")
    return token


def fetch_recent_audits(
    client: httpx.Client,
    *,
    api_url: str,
    keycloak_url: str,
    user_id: int = 900000101,
    limit: int = 3,
) -> dict[str, Any]:
    """Fetch recent demo audits with the same short-lived service identity."""
    token = service_access_token(client, keycloak_url)
    return _get_json(
        client,
        f"{api_url.rstrip('/')}/users/{user_id}/audits?limit={limit}",
        f"audits user {user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_json(
    client: httpx.Client,
    url: str,
    name: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DemoSmokeError(f"{name} failed at {url}: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise DemoSmokeError(f"{name} returned a non-object response")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the local MovieLens demo stack.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--web-url", default="http://localhost:3001")
    parser.add_argument("--keycloak-url", default="http://localhost:8080")
    parser.add_argument("--readiness-only", action="store_true")
    parser.add_argument("--audits-only", action="store_true")
    args = parser.parse_args()

    with httpx.Client(timeout=5.0) as client:
        wait_for_readiness(
            client,
            api_url=args.api_url,
            web_url=args.web_url,
            keycloak_url=args.keycloak_url,
        )
        if args.readiness_only:
            print("Demo dependencies are ready: FastAPI, Next.js, and Keycloak.")
            return
        if args.audits_only:
            audits = fetch_recent_audits(
                client,
                api_url=args.api_url,
                keycloak_url=args.keycloak_url,
            )
            print(json.dumps(audits, indent=2, sort_keys=True))
            return
        summary = run_behavior_smoke(
            client,
            web_url=args.web_url,
            api_url=args.api_url,
            keycloak_url=args.keycloak_url,
        )
    print(json.dumps(summary.__dict__, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
