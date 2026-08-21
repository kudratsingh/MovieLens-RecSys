"""Serving promises a percentile cannot express, checked against the warm load stack.

The k6 gates answer "is it fast" and "is the answer right". They cannot answer
the questions an operator asks at three in the morning: can I follow one request
from a log line to the row that recorded it, is the thing that answers
`/healthz` telling me anything, what does the service do when a caller hammers
it, and does a movie with no artwork take a page down. Those are pass/fail
facts, not distributions, so they are checked once against the same warm stack
the load gate just measured rather than sampled under load.

One of them is deliberately allowed to report "not implemented": there is no
rate limiting in `src/serving/` today, and this file records that as a measured
absence with the evidence behind it. Recording a gap honestly is the point —
inventing a limiter to have something to assert would be worse than the gap.

Writes a JSON report to stdout and a readable table to stderr, so a Make target
can capture the machine-readable half into the run artifact while the human
half lands in the job log. Exit status is 1 if any required check failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

# The audit row is written by middleware after the response is sent, so a read
# immediately afterwards can legitimately miss it. Bounded polling, not a sleep.
AUDIT_POLL_ATTEMPTS = 20
AUDIT_POLL_INTERVAL_S = 0.25
# Enough consecutive requests that any per-minute or per-second limiter worth
# the name would have engaged, and few enough to stay inside a CI job.
RATE_LIMIT_PROBE_REQUESTS = 60
RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after")

WARM_PERSONA = 900000101
CATALOG_MAX_LIMIT = 48
LIBRARY_MAX_LIMIT = 50


@dataclass
class Check:
    name: str
    passed: bool
    summary: str
    required: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "required": self.required,
            "summary": self.summary,
            "evidence": self.evidence,
        }


@dataclass
class Target:
    api_url: str
    keycloak_url: str
    realm: str
    client_id: str
    client_secret: str
    username: str
    password: str


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://api-load:8000")
    parser.add_argument("--keycloak-url", default="http://keycloak:8080")
    parser.add_argument("--realm", default="demo")
    parser.add_argument("--client-id", default="movielens-api")
    parser.add_argument("--client-secret", default="movielens-api-secret-dev-only")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo")
    args = parser.parse_args(argv)

    target = Target(
        api_url=str(args.api_url).rstrip("/"),
        keycloak_url=str(args.keycloak_url).rstrip("/"),
        realm=str(args.realm),
        client_id=str(args.client_id),
        client_secret=str(args.client_secret),
        username=str(args.username),
        password=str(args.password),
    )

    with httpx.Client(timeout=15.0) as client:
        token = _mint_token(client, target)
        checks = list(_run_checks(client, target, token))

    report = {
        "target": target.api_url,
        "checks": [check.as_dict() for check in checks],
        "required_failures": [
            check.name for check in checks if check.required and not check.passed
        ],
        "advisory_failures": [
            check.name for check in checks if not check.required and not check.passed
        ],
    }
    print(json.dumps(report, indent=2))
    _render(checks)
    return 1 if report["required_failures"] else 0


def _run_checks(client: httpx.Client, target: Target, token: str) -> Iterator[Check]:
    yield _check_readiness(client, target)
    yield _check_auth_boundary(client, target)
    request_id, echo_check = _check_request_id_echo(client, target, token)
    yield echo_check
    yield _check_request_id_persisted(client, target, token, request_id)
    yield _check_minted_request_id(client, target, token)
    yield _check_dependency_visibility(client, target, token)
    yield _check_rate_limiting(client, target, token)
    yield _check_degraded_metadata(client, target, token)
    yield _check_bounded_pages(client, target, token)
    yield _check_cursor_rejection(client, target, token)


# --- readiness and the auth boundary ----------------------------------------


def _check_readiness(client: httpx.Client, target: Target) -> Check:
    """`/healthz` answers without a token, and says exactly what it knows."""
    response = client.get(f"{target.api_url}/healthz")
    body = _json(response)
    # Recorded, not asserted: the endpoint deliberately reports no dependency
    # state, because a readiness probe that fails when Postgres blips takes the
    # whole deployment out during a rolling restart. Dependency visibility is
    # the audit row's job, and `_check_dependency_visibility` is where it is
    # actually proven. Naming the shape here keeps the gap from being silent.
    dependency_fields = sorted(key for key in body if key != "status")
    passed = response.status_code == 200 and body.get("status") == "ok"
    return Check(
        name="readiness",
        passed=passed,
        summary=(
            f"HTTP {response.status_code} without a token, body {body}; "
            f"dependency fields exposed: {dependency_fields or 'none (liveness only)'}"
        ),
        evidence={
            "status_code": response.status_code,
            "body": body,
            "dependency_fields": dependency_fields,
            "request_id_header": response.headers.get("x-request-id"),
        },
    )


def _check_auth_boundary(client: httpx.Client, target: Target) -> Check:
    """Every route except `/healthz` refuses an unauthenticated caller."""
    paths = [
        "/whoami",
        "/personas",
        f"/users/{WARM_PERSONA}/recommendations?limit=1",
        f"/users/{WARM_PERSONA}/history?limit=1",
        f"/users/{WARM_PERSONA}/catalog?limit=1",
        f"/users/{WARM_PERSONA}/library?tab=rated&limit=1",
        f"/users/{WARM_PERSONA}/taste-profile",
        f"/users/{WARM_PERSONA}/features",
        f"/users/{WARM_PERSONA}/audits?limit=1",
    ]
    statuses = {path: client.get(f"{target.api_url}{path}").status_code for path in paths}
    unprotected = sorted(path for path, status in statuses.items() if status != 401)
    return Check(
        name="auth_boundary",
        passed=not unprotected,
        summary=(
            f"{len(paths)} protected routes answered 401 without a token"
            if not unprotected
            else f"unprotected routes: {unprotected}"
        ),
        evidence={"statuses": statuses},
    )


# --- request identity -------------------------------------------------------


def _check_request_id_echo(client: httpx.Client, target: Target, token: str) -> tuple[str, Check]:
    """A caller-supplied correlation id comes back on the response."""
    request_id = f"reliability-{uuid.uuid4()}"
    response = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/recommendations?limit=3",
        headers={**_auth(token), "X-Request-ID": request_id},
    )
    echoed = response.headers.get("x-request-id")
    return request_id, Check(
        name="request_id_echoed",
        passed=response.status_code == 200 and echoed == request_id,
        summary=f"sent {request_id!r}, response echoed {echoed!r} (HTTP {response.status_code})",
        evidence={"sent": request_id, "echoed": echoed, "status_code": response.status_code},
    )


def _check_request_id_persisted(
    client: httpx.Client, target: Target, token: str, request_id: str
) -> Check:
    """The same id reaches the durable audit row, so a log line can be traced."""
    for attempt in range(AUDIT_POLL_ATTEMPTS):
        response = client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/audits?limit=20",
            headers=_auth(token),
        )
        items = _json(response).get("items")
        if isinstance(items, list):
            match = next((item for item in items if item.get("correlation_id") == request_id), None)
            if match is not None:
                return Check(
                    name="request_id_persisted",
                    passed=True,
                    summary=(
                        f"audit row {match.get('request_id')} carries correlation_id "
                        f"{request_id!r} after {attempt + 1} poll(s)"
                    ),
                    evidence={
                        "correlation_id": request_id,
                        "audit_request_id": match.get("request_id"),
                        "endpoint": match.get("endpoint"),
                        "http_status": match.get("http_status"),
                        "polls": attempt + 1,
                    },
                )
        time.sleep(AUDIT_POLL_INTERVAL_S)
    return Check(
        name="request_id_persisted",
        passed=False,
        summary=(
            f"no audit row carried correlation_id {request_id!r} within "
            f"{AUDIT_POLL_ATTEMPTS * AUDIT_POLL_INTERVAL_S:.0f}s — a request cannot be "
            "traced from its response header to its recorded prediction"
        ),
        evidence={"correlation_id": request_id, "polls": AUDIT_POLL_ATTEMPTS},
    )


def _check_minted_request_id(client: httpx.Client, target: Target, token: str) -> Check:
    """A caller that supplies no id still gets a usable one back."""
    response = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/recommendations?limit=3",
        headers=_auth(token),
    )
    minted = response.headers.get("x-request-id", "")
    try:
        uuid.UUID(minted)
        well_formed = True
    except ValueError:
        well_formed = False
    return Check(
        name="request_id_minted",
        passed=response.status_code == 200 and well_formed,
        summary=f"server minted {minted!r} for a request that supplied none",
        evidence={"minted": minted, "uuid": well_formed},
    )


def _check_dependency_visibility(client: httpx.Client, target: Target, token: str) -> Check:
    """Auth, model and database provenance are readable from the running service.

    Three separate claims, each proven by something the service actually
    returns: `/whoami` resolves the verified token to a tenant, realm and role
    set; the audit row names every model artifact version and the per-stage
    latencies behind one answer; and that row exists at all only because the
    request's transaction committed to Postgres.
    """
    actor = _json(client.get(f"{target.api_url}/whoami", headers=_auth(token)))
    audits = _json(
        client.get(f"{target.api_url}/users/{WARM_PERSONA}/audits?limit=1", headers=_auth(token))
    )
    items = audits.get("items")
    row: dict[str, Any] = items[0] if isinstance(items, list) and items else {}

    auth_fields = ["tenant_id", "realm", "roles", "authorized_party", "redis_prefix"]
    model_fields = ["model_version", "candidate_version", "ranker_version", "feature_version"]
    timing_fields = [
        "candidate_latency_ms",
        "feature_latency_ms",
        "ranker_latency_ms",
        "model_latency_ms",
        "latency_ms",
    ]
    database_fields = ["request_id", "actor_user_id", "created_at", "input_state_revision"]

    missing = (
        [name for name in auth_fields if actor.get(name) in (None, "")]
        + [name for name in model_fields if not row.get(name)]
        + [name for name in timing_fields if not isinstance(row.get(name), int | float)]
        + [name for name in database_fields if row.get(name) in (None, "")]
    )
    return Check(
        name="dependency_visibility",
        passed=not missing,
        summary=(
            f"auth resolved to tenant {actor.get('tenant_id')!r} realm {actor.get('realm')!r} "
            f"roles {actor.get('roles')}; model {row.get('model_version')!r} with per-stage "
            "latencies; audit row persisted"
            if not missing
            else f"missing provenance fields: {missing}"
        ),
        evidence={
            "auth": {name: actor.get(name) for name in auth_fields},
            "model": {name: row.get(name) for name in model_fields},
            "latency_ms": {name: row.get(name) for name in timing_fields},
            "database": {name: row.get(name) for name in database_fields},
            "missing": missing,
        },
    )


# --- rate limiting ----------------------------------------------------------


def _check_rate_limiting(client: httpx.Client, target: Target, token: str) -> Check:
    """Report what the service does under a rapid burst, including nothing.

    Advisory by design. `src/serving/` has no limiter, and `src/serving/tenancy/`
    says so; the per-tenant quota column exists but nothing reads it yet. This
    records the measured behaviour so the gap is evidence rather than folklore,
    and so the day a limiter lands, this check starts describing it without
    being rewritten.
    """
    statuses: dict[int, int] = {}
    limit_headers: dict[str, str] = {}
    for _ in range(RATE_LIMIT_PROBE_REQUESTS):
        response = client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=1", headers=_auth(token)
        )
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        for name, value in response.headers.items():
            if name.lower().startswith(RATE_LIMIT_HEADER_PREFIXES):
                limit_headers[name.lower()] = value
    throttled = statuses.get(429, 0)
    implemented = throttled > 0 or bool(limit_headers)
    return Check(
        name="rate_limiting",
        # Not a pass/fail contract: an absent limiter is a recorded gap, and a
        # present one only has to answer 429 rather than fail or hang.
        passed=set(statuses) <= {200, 429},
        required=False,
        summary=(
            f"{RATE_LIMIT_PROBE_REQUESTS} rapid authenticated requests -> statuses {statuses}; "
            + (
                f"rate limiting IS implemented ({throttled} throttled, headers "
                f"{sorted(limit_headers)})"
                if implemented
                else "rate limiting is NOT implemented (no 429, no X-RateLimit-* headers)"
            )
        ),
        evidence={
            "requests": RATE_LIMIT_PROBE_REQUESTS,
            "statuses": {str(code): count for code, count in sorted(statuses.items())},
            "implemented": implemented,
            "limit_headers": limit_headers,
        },
    )


# --- degraded metadata and bounded pages ------------------------------------


def _check_degraded_metadata(client: httpx.Client, target: Target, token: str) -> Check:
    """A movie with no artwork renders as a record, not as a failure.

    The reviewed demo catalog is mostly poster-less on purpose, which makes this
    the normal path rather than an edge case: if a missing poster could fail a
    page, most of Browse would be broken.
    """
    catalog = _json(
        client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit={CATALOG_MAX_LIMIT}&sort=title",
            headers=_auth(token),
        )
    )
    items = catalog.get("items")
    if not isinstance(items, list) or not items:
        return Check(
            name="degraded_metadata",
            passed=False,
            summary="catalog returned no items, so degraded metadata could not be exercised",
        )
    posterless = [item for item in items if not item.get("poster_url")]
    if not posterless:
        return Check(
            name="degraded_metadata",
            passed=False,
            summary=(
                "every catalog item on the first page has a poster, so this check proved "
                "nothing; reseed the reviewed fixture before trusting it"
            ),
        )

    subject = posterless[0]
    movie_id = subject.get("movie_id")
    detail_response = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/movies/{movie_id}", headers=_auth(token)
    )
    detail = _json(detail_response).get("item") or {}
    usable = (
        detail_response.status_code == 200
        and bool(detail.get("title"))
        and detail.get("source_status") in {"partial", "unavailable"}
        and bool(detail.get("metadata_source"))
    )
    # Every item on the page still has to be renderable, not just the one.
    all_titled = all(item.get("title") for item in items)
    return Check(
        name="degraded_metadata",
        passed=usable and all_titled,
        summary=(
            f"{len(posterless)}/{len(items)} first-page catalog items have no poster; "
            f"movie {movie_id} answers HTTP {detail_response.status_code} with "
            f"source_status={detail.get('source_status')!r} and a title"
        ),
        evidence={
            "posterless_on_first_page": len(posterless),
            "page_size": len(items),
            "sample_movie_id": movie_id,
            "detail_status": detail_response.status_code,
            "source_status": detail.get("source_status"),
            "metadata_source": detail.get("metadata_source"),
            "every_item_has_a_title": all_titled,
        },
    )


def _check_bounded_pages(client: httpx.Client, target: Target, token: str) -> Check:
    """Page size has a ceiling the caller cannot argue with."""
    probes = {
        f"catalog limit={CATALOG_MAX_LIMIT}": (
            f"/users/{WARM_PERSONA}/catalog?limit={CATALOG_MAX_LIMIT}",
            200,
        ),
        f"catalog limit={CATALOG_MAX_LIMIT + 1}": (
            f"/users/{WARM_PERSONA}/catalog?limit={CATALOG_MAX_LIMIT + 1}",
            422,
        ),
        f"library limit={LIBRARY_MAX_LIMIT}": (
            f"/users/{WARM_PERSONA}/library?tab=rated&limit={LIBRARY_MAX_LIMIT}",
            200,
        ),
        f"library limit={LIBRARY_MAX_LIMIT + 1}": (
            f"/users/{WARM_PERSONA}/library?tab=rated&limit={LIBRARY_MAX_LIMIT + 1}",
            422,
        ),
    }
    observed: dict[str, int] = {}
    oversized: list[str] = []
    for label, (path, expected) in probes.items():
        response = client.get(f"{target.api_url}{path}", headers=_auth(token))
        observed[label] = response.status_code
        if response.status_code != expected:
            oversized.append(f"{label} -> {response.status_code} (expected {expected})")
        if response.status_code == 200:
            items = _json(response).get("items")
            requested = int(path.rsplit("=", 1)[1])
            if isinstance(items, list) and len(items) > requested:
                oversized.append(f"{label} returned {len(items)} items")
    return Check(
        name="bounded_pages",
        passed=not oversized,
        summary=(
            "catalog and library reject an over-large page and never return more than asked"
            if not oversized
            else f"unbounded page behaviour: {oversized}"
        ),
        evidence={"statuses": observed, "problems": oversized},
    )


def _check_cursor_rejection(client: httpx.Client, target: Target, token: str) -> Check:
    """A cursor that no longer matches its query is refused, not silently re-run."""
    first = _json(
        client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=24&sort=title",
            headers=_auth(token),
        )
    )
    page = first.get("page") or {}
    cursor = page.get("next_cursor")
    if not isinstance(cursor, str):
        return Check(
            name="cursor_rejection",
            passed=False,
            summary="the first catalog page offered no continuation cursor to test",
        )
    # The cursor goes into the URL rather than into `params`: httpx replaces a
    # URL's query string when both are given, which would quietly drop the
    # filter this check exists to change and turn the assertion into a tautology.
    mismatched = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=24&sort=title&genre=Drama"
        f"&cursor={quote(cursor, safe='')}",
        headers=_auth(token),
    )
    garbage = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=24&sort=title&cursor=not-a-cursor",
        headers=_auth(token),
    )
    passed = mismatched.status_code == 400 and garbage.status_code == 400
    return Check(
        name="cursor_rejection",
        passed=passed,
        summary=(
            f"cursor reused under a different filter -> HTTP {mismatched.status_code}; "
            f"malformed cursor -> HTTP {garbage.status_code}"
        ),
        evidence={
            "mismatched_status": mismatched.status_code,
            "mismatched_detail": _json(mismatched).get("detail"),
            "garbage_status": garbage.status_code,
        },
    )


# --- plumbing ---------------------------------------------------------------


def _mint_token(client: httpx.Client, target: Target) -> str:
    response = client.post(
        f"{target.keycloak_url}/realms/{target.realm}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": target.client_id,
            "client_secret": target.client_secret,
            "username": target.username,
            "password": target.password,
        },
    )
    if response.status_code != 200:
        raise SystemExit(
            f"Keycloak token request failed with HTTP {response.status_code}: {response.text}"
        )
    token = _json(response).get("access_token")
    if not isinstance(token, str):
        raise SystemExit("Keycloak token response carried no access_token")
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _render(checks: Sequence[Check]) -> None:
    width = max(len(check.name) for check in checks)
    print("\n[reliability] serving promises checked against the warm load stack", file=sys.stderr)
    for check in checks:
        if check.passed:
            verdict = "PASS"
        else:
            verdict = "FAIL" if check.required else "NOTE"
        print(f"  {verdict}  {check.name:<{width}}  {check.summary}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
