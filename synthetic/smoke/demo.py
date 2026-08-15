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


def run_behavior_smoke(client: httpx.Client, *, web_url: str) -> SmokeSummary:
    base_url = web_url.rstrip("/")
    personas_payload = _get_json(client, f"{base_url}/api/personas", "persona API")
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

    action = _dashboard(client, base_url, int(by_slug["action-fan"]["user_id"]))
    cold = _dashboard(client, base_url, int(by_slug["cold-start"]["user_id"]))
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


def _dashboard(client: httpx.Client, base_url: str, user_id: int) -> dict[str, Any]:
    return _get_json(client, f"{base_url}/api/users/{user_id}", f"dashboard user {user_id}")


def _dashboard_items(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    value = payload.get(section)
    if not isinstance(value, dict):
        raise DemoSmokeError(f"dashboard response has no {section!r} object")
    return _require_list(value, "items", f"dashboard {section}")


def _require_list(payload: dict[str, Any], key: str, source: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DemoSmokeError(f"{source} response has no valid {key!r} list")
    return value


def _get_json(client: httpx.Client, url: str, name: str) -> dict[str, Any]:
    try:
        response = client.get(url)
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
        summary = run_behavior_smoke(client, web_url=args.web_url)
    print(json.dumps(summary.__dict__, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
