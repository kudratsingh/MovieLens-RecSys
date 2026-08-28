"""Readiness and behavioral smoke checks for a deployed demo stack.

The checks themselves are GET-only and deployment-agnostic, so this is the most
valuable command to run immediately after a deploy. What used to stop it from
travelling was authentication: realm, client and grant were hardcoded to the
local dev realm. They are flags now, mirroring ``synthetic/load/reliability.py``,
so the same assertions can be pointed at a production realm with a password
grant. Every default is the local demo stack's value, so an argument-free run --
which is what CI and every ``make demo-*`` target does -- behaves exactly as it
did before the flags existed.

The full run also compares the catalog metadata the API serves with the reviewed
fixture the database is seeded from, because a snapshot that predates a fixture
refresh is invisible to every other check and very visible to a viewer: it is
posters that never load. ``--skip-catalog-coverage`` turns that off for a
restored dump that legitimately predates the fixture.

Against a deployment, where the confidential client issues no tokens and the
verification account carries the persona role instead::

    python -m synthetic.smoke.demo \\
        --api-url http://api.internal:8000 --web-url http://web.internal:3001 \\
        --keycloak-url http://keycloak.internal:8080 \\
        --realm demo --client-id movielens-verify --client-secret ... \\
        --grant-type password --username verify --password ...
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class CatalogCoverage:
    """How much of the reviewed fixture's metadata the stack is actually serving."""

    fixture_movie_count: int
    served_movie_count: int
    served_poster_count: int
    served_overview_count: int


# The fixture the demo database is seeded from. Read as plain JSON rather than
# through ``synthetic.personas.seed``: this module deliberately depends on
# nothing but httpx so it can be pointed at any deployment, and the seeder pulls
# in SQLAlchemy and application settings it has no use for here.
FIXTURE_CATALOG_PATH = Path(__file__).resolve().parents[1] / "personas" / "catalog.json"

# One page short of the endpoint's maximum, times enough pages to walk the whole
# reviewed fixture. A deployment with a real MovieLens ingest behind the same
# endpoint stops after these pages and reports what it checked, rather than
# paging tens of thousands of rows to answer a smoke-test question.
CATALOG_PAGE_LIMIT = 48
CATALOG_MAX_PAGES = 8
COVERAGE_PERSONA_USER_ID = 900000101


SUPPORTED_GRANT_TYPES = ("client_credentials", "password")


@dataclass(frozen=True)
class AuthConfig:
    """Which Keycloak identity the smoke authenticates as.

    The defaults are the local demo stack's confidential client. A deployment
    that has replaced those credentials -- which every non-local one must -- runs
    the same checks by passing its own realm, client and user. The password grant
    exists because a deployed realm need not expose a confidential client to a
    verification job; a purpose-built account with the persona role is a smaller
    thing to hand out than a client secret.
    """

    realm: str = "demo"
    client_id: str = "movielens-api"
    client_secret: str = "movielens-api-secret-dev-only"
    grant_type: str = "client_credentials"
    username: str | None = None
    password: str | None = None

    def __post_init__(self) -> None:
        if self.grant_type not in SUPPORTED_GRANT_TYPES:
            raise DemoSmokeError(
                f"unsupported grant type {self.grant_type!r}: "
                f"expected one of {', '.join(SUPPORTED_GRANT_TYPES)}"
            )
        if self.grant_type == "password" and not (self.username and self.password):
            raise DemoSmokeError("the password grant needs both --username and --password")

    def token_form(self) -> dict[str, str]:
        form = {"grant_type": self.grant_type, "client_id": self.client_id}
        # A public client has no secret, and Keycloak rejects an empty one
        # rather than ignoring it.
        if self.client_secret:
            form["client_secret"] = self.client_secret
        if self.grant_type == "password":
            form["username"] = self.username or ""
            form["password"] = self.password or ""
        return form


DEFAULT_AUTH = AuthConfig()


def wait_for_readiness(
    client: httpx.Client,
    *,
    api_url: str,
    web_url: str,
    keycloak_url: str,
    auth: AuthConfig | None = None,
    attempts: int = 60,
    interval_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    realm = (auth or DEFAULT_AUTH).realm
    checks = {
        "FastAPI": f"{api_url.rstrip('/')}/healthz",
        "Next.js": web_url.rstrip("/"),
        f"Keycloak {realm} realm": f"{keycloak_url.rstrip('/')}/realms/{realm}",
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
    auth: AuthConfig | None = None,
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
        {"Authorization": f"Bearer {service_access_token(client, keycloak_url, auth)}"}
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


def check_catalog_coverage(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    user_id: int = COVERAGE_PERSONA_USER_ID,
    catalog_path: Path = FIXTURE_CATALOG_PATH,
    page_limit: int = CATALOG_PAGE_LIMIT,
    max_pages: int = CATALOG_MAX_PAGES,
) -> CatalogCoverage:
    """Fail when the stack serves less metadata than the fixture it was seeded from.

    The database is the product's only poster source on the request path, so a
    snapshot taken before the fixture was enriched shows placeholders on every
    surface — which is how the demo spent a day serving 24 posters out of 120
    with every test still green. Comparing what the catalog endpoint returns
    against the committed fixture makes that a smoke failure instead of a
    viewer's first impression.

    Only titles the fixture actually has metadata for are counted, so a
    deployment whose catalog is larger than the fixture is measured on the rows
    the fixture owns rather than being asked to explain the rest.
    """
    fixture = _load_fixture_catalog(catalog_path)
    served = 0
    posters = 0
    overviews = 0
    poster_gaps: list[int] = []
    overview_gaps: list[int] = []

    cursor: str | None = None
    for _ in range(max_pages):
        # The cursor is unpadded base64url (src/serving/catalog.py:286), so it
        # carries nothing a query string would need escaped.
        query = f"limit={page_limit}" + (f"&cursor={cursor}" if cursor else "")
        payload = _get_json(
            client,
            f"{api_url.rstrip('/')}/users/{user_id}/catalog?{query}",
            f"catalog user {user_id}",
            headers=headers,
        )
        for item in _require_list(payload, "items", "catalog"):
            expected = fixture.get(int(item["movie_id"]))
            if expected is None:
                continue
            served += 1
            expected_poster, expected_overview = expected
            if item.get("poster_url"):
                posters += 1
            elif expected_poster:
                poster_gaps.append(int(item["movie_id"]))
            if item.get("overview"):
                overviews += 1
            elif expected_overview:
                overview_gaps.append(int(item["movie_id"]))
        page = payload.get("page")
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not isinstance(page, dict) or not page.get("has_more") or not cursor:
            break

    gaps = [
        f"{len(ids)} titles are served without {field} the fixture has ({_format_ids(ids)})"
        for field, ids in (("a poster", poster_gaps), ("an overview", overview_gaps))
        if ids
    ]
    if gaps:
        raise DemoSmokeError(
            "the served catalog is behind the reviewed fixture, so run "
            f"`make demo-seed`: {'; '.join(gaps)}"
        )

    return CatalogCoverage(
        fixture_movie_count=len(fixture),
        served_movie_count=served,
        served_poster_count=posters,
        served_overview_count=overviews,
    )


def _load_fixture_catalog(path: Path) -> dict[int, tuple[bool, bool]]:
    """Read which fixture titles claim a poster and an overview."""
    try:
        with path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
        movies = payload["movies"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DemoSmokeError(f"could not read the catalog fixture at {path}") from exc
    return {
        int(movie["movie_id"]): (
            bool(movie.get("poster_url")),
            bool(movie.get("overview")),
        )
        for movie in movies
    }


def _format_ids(movie_ids: Sequence[int], limit: int = 8) -> str:
    head = ", ".join(str(movie_id) for movie_id in movie_ids[:limit])
    return head if len(movie_ids) <= limit else f"{head}, … (+{len(movie_ids) - limit})"


def service_access_token(
    client: httpx.Client,
    keycloak_url: str,
    auth: AuthConfig | None = None,
) -> str:
    config = auth or DEFAULT_AUTH
    url = f"{keycloak_url.rstrip('/')}/realms/{config.realm}/protocol/openid-connect/token"
    try:
        response = client.post(url, data=config.token_form())
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
    auth: AuthConfig | None = None,
) -> dict[str, Any]:
    """Fetch recent demo audits with the same short-lived service identity."""
    token = service_access_token(client, keycloak_url, auth)
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check a MovieLens demo stack.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--web-url", default="http://localhost:3001")
    parser.add_argument("--keycloak-url", default="http://localhost:8080")
    parser.add_argument("--readiness-only", action="store_true")
    parser.add_argument("--audits-only", action="store_true")
    parser.add_argument(
        "--skip-catalog-coverage",
        action="store_true",
        help=(
            "do not compare served catalog metadata with the committed fixture "
            "(for a restored snapshot that legitimately predates it)"
        ),
    )
    parser.add_argument("--realm", default=DEFAULT_AUTH.realm)
    parser.add_argument("--client-id", default=DEFAULT_AUTH.client_id)
    parser.add_argument("--client-secret", default=DEFAULT_AUTH.client_secret)
    parser.add_argument(
        "--grant-type",
        default=DEFAULT_AUTH.grant_type,
        choices=SUPPORTED_GRANT_TYPES,
    )
    parser.add_argument("--username", default=DEFAULT_AUTH.username)
    parser.add_argument("--password", default=DEFAULT_AUTH.password)
    args = parser.parse_args(argv)

    auth = AuthConfig(
        realm=str(args.realm),
        client_id=str(args.client_id),
        client_secret=str(args.client_secret),
        grant_type=str(args.grant_type),
        username=args.username,
        password=args.password,
    )

    with httpx.Client(timeout=5.0) as client:
        wait_for_readiness(
            client,
            api_url=args.api_url,
            web_url=args.web_url,
            keycloak_url=args.keycloak_url,
            auth=auth,
        )
        if args.readiness_only:
            print("Demo dependencies are ready: FastAPI, Next.js, and Keycloak.")
            return
        if args.audits_only:
            audits = fetch_recent_audits(
                client,
                api_url=args.api_url,
                keycloak_url=args.keycloak_url,
                auth=auth,
            )
            print(json.dumps(audits, indent=2, sort_keys=True))
            return
        summary = run_behavior_smoke(
            client,
            web_url=args.web_url,
            api_url=args.api_url,
            keycloak_url=args.keycloak_url,
            auth=auth,
        )
        report: dict[str, Any] = dict(summary.__dict__)
        if not args.skip_catalog_coverage:
            coverage = check_catalog_coverage(
                client,
                api_url=args.api_url,
                headers={
                    "Authorization": (
                        f"Bearer {service_access_token(client, args.keycloak_url, auth)}"
                    )
                },
            )
            report.update(coverage.__dict__)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
