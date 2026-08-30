"""Serving promises a percentile cannot express, checked against the warm load stack.

The k6 gates answer "is it fast" and "is the answer right". They cannot answer
the questions an operator asks at three in the morning: can I follow one request
from a log line to the row that recorded it, is the thing that answers
`/healthz` telling me anything, what does the service do when a caller hammers
it, and does a movie with no artwork take a page down. Those are pass/fail
facts, not distributions, so they are checked once against the same warm stack
the load gate just measured rather than sampled under load.

Rate limiting used to be the one check allowed to report "not implemented".
ADR 0014's token bucket closed that gap, so the check is now required and
asserts the contract a client codes against: the `X-RateLimit-*` headers on an
admitted request, a 429 carrying `Retry-After` and a JSON detail once the
bucket is drained, and no third behaviour. A target that advertises a bucket it
never enforces fails, and so does one that refuses without saying when to come
back.

Since the shared bucket landed (ADR 0014's 2026-08-29 note) the check also
asserts that there is exactly *one* bucket behind the service rather than one
per worker, because that is the difference between a limit that means what it
says and a limit that means `workers x` it. Both halves are arithmetic against
numbers the service itself published: no more than one bucket's worth of
requests may be admitted before the first refusal, and once the bucket is
drained a burst of brand-new connections may be admitted only as fast as the
advertised refill. Neither number is hard-coded — the capacity and the refill
are read from the 429's own headers, so a deployment that changes its limits
does not have to change this file.

What the check still cannot assert is that a limiter is *configured* — against
a dev stack, where the synthetic-load harnesses deliberately drive one Keycloak
identity past any sane per-subject rate, ADR 0014 turns it off — so a target
with no limiter is recorded plainly rather than inferred over.

Writes a JSON report to stdout and a readable table to stderr, so a Make target
can capture the machine-readable half into the run artifact while the human
half lands in the job log. Exit status is 1 if any required check failed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

# The audit row is written by middleware after the response is sent, so a read
# immediately afterwards can legitimately miss it. Bounded polling, not a sleep.
AUDIT_POLL_ATTEMPTS = 20
AUDIT_POLL_INTERVAL_S = 0.25
# The documented defaults are a burst of 120 refilled at 600/minute (ADR 0014,
# src/config.py), so `burst + limit` is the window in which a drained bucket
# has to show itself. It is deliberately not `burst + 1`: the probe has to
# outrun the refill to drain anything at all, so it spends roughly a minute's
# worth of tokens on top of the burst while it works. With the shared bucket
# there is one bucket to drain however many workers answer, so 720 is generous
# for the documented numbers; a deployment with a larger burst, or one that
# deliberately runs the per-worker backend and therefore has N buckets to
# drain, fails with a message saying so and is fixed with
# --rate-limit-probe-requests rather than by editing this.
#
# The refill caveat is a property of a token bucket rather than a tuning
# problem: at ten tokens a second, a client that manages fewer than ten
# requests a second never reaches the floor however many it sends. That is why
# the check reports "advertises a bucket but admitted all N" in words instead
# of inferring a verdict from a window it could not close.
RATE_LIMIT_PROBE_REQUESTS = 720
# Once the bucket is drained, this many requests go out at once on brand-new
# connections. It is the direct test of the property the shared bucket exists
# for: a caller that opens a fresh connection — landing on whichever worker the
# accept queue hands it — must still meet the drained bucket. Under a
# per-worker bucket roughly (N-1)/N of them find a full one instead, which at
# the deployed two workers is half. Concurrent rather than sequential so the
# window they are measured over is one round trip and not ten: the allowance
# they are judged against is whatever the advertised refill produces during it,
# so a slow link widens the allowance rather than failing the check.
RATE_LIMIT_FRESH_CONNECTIONS = 10
# One token of slack on both arithmetic bounds, for the rounding in
# `X-RateLimit-Reset` and for a token that lands mid-flight.
RATE_LIMIT_ARITHMETIC_TOLERANCE = 1
RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after")
RATE_LIMIT_LIMIT_HEADER = "x-ratelimit-limit"
RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"
RATE_LIMIT_RESET_HEADER = "x-ratelimit-reset"
RETRY_AFTER_HEADER = "retry-after"

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
    parser.add_argument(
        "--rate-limit-probe-requests",
        type=int,
        default=RATE_LIMIT_PROBE_REQUESTS,
        help=(
            "how many rapid authenticated requests the rate-limit check may send "
            "before it gives up looking for a 429; raise it for a service running "
            "more workers or a larger burst than the documented defaults"
        ),
    )
    parser.add_argument(
        "--rate-limit-shared-probe-connections",
        type=int,
        default=RATE_LIMIT_FRESH_CONNECTIONS,
        help=(
            "how many brand-new connections the rate-limit check opens at once after the "
            "bucket drains, to prove a reconnect does not buy a fresh allowance; 0 skips "
            "that half of the check"
        ),
    )
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
        checks = list(
            _run_checks(
                client,
                target,
                token,
                int(args.rate_limit_probe_requests),
                int(args.rate_limit_shared_probe_connections),
            )
        )

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


def _run_checks(
    client: httpx.Client,
    target: Target,
    token: str,
    probe_requests: int,
    fresh_connections: int = RATE_LIMIT_FRESH_CONNECTIONS,
) -> Iterator[Check]:
    yield _check_readiness(client, target)
    yield _check_auth_boundary(client, target)
    request_id, echo_check = _check_request_id_echo(client, target, token)
    yield echo_check
    yield _check_request_id_persisted(client, target, token, request_id)
    yield _check_minted_request_id(client, target, token)
    yield _check_dependency_visibility(client, target, token)
    yield _check_degraded_metadata(client, target, token)
    yield _check_bounded_pages(client, target, token)
    yield _check_cursor_rejection(client, target, token)
    # Last, and it has to stay last: it drains this identity's rate-limit
    # bucket on purpose, and every check above authenticates as the same
    # subject, so running it earlier would throttle them into failing.
    yield _check_rate_limiting(client, target, token, probe_requests, fresh_connections)


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


def _check_rate_limiting(
    client: httpx.Client,
    target: Target,
    token: str,
    probe_requests: int,
    fresh_connections: int = RATE_LIMIT_FRESH_CONNECTIONS,
) -> Check:
    """A caller that hammers the API is refused, told when to come back, and
    cannot get a second allowance by reconnecting.

    The contract under test is ADR 0014's, from the client's side: an admitted
    request carries the three `X-RateLimit-*` headers, a drained bucket answers
    429 with `Retry-After` and an `ErrorResponse` body, and nothing else ever
    happens. Every failure mode below is one a client would have to work around
    at three in the morning — a 429 with no `Retry-After` leaves it guessing, a
    bucket advertised but never enforced makes its backoff code dead, and a
    third status code means the limiter is failing rather than limiting.

    On top of that it asserts there is one bucket and not one per worker, in
    the only terms an outside client can: how much it was admitted. Both bounds
    are computed from the capacity and refill the 429 itself advertised, so the
    check follows the deployment's configuration instead of restating it.

    The probe stops at the first refusal: what matters is that the bucket has a
    floor, not how far past it the service will keep counting.
    """
    statuses: dict[int, int] = {}
    limit_headers: dict[str, str] = {}
    admitted_headers: dict[str, str] = {}
    throttled: httpx.Response | None = None
    sent = 0
    admitted = 0
    started_at = time.monotonic()
    drained_after = 0.0

    for _ in range(probe_requests):
        response = client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=1", headers=_auth(token)
        )
        sent += 1
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        observed = {
            name.lower(): value
            for name, value in response.headers.items()
            if name.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)
        }
        limit_headers.update(observed)
        if response.status_code == 200:
            admitted += 1
            if observed:
                admitted_headers = observed
        if response.status_code == 429:
            drained_after = time.monotonic() - started_at
            throttled = response
            break

    enforced = throttled is not None
    advertised = bool(limit_headers)
    problems: list[str] = []
    shared: dict[str, Any] = {}

    unexpected = sorted(set(statuses) - {200, 429})
    if unexpected:
        problems.append(f"unexpected status codes under a burst: {unexpected}")

    if throttled is not None:
        headers = {name.lower(): value for name, value in throttled.headers.items()}
        if statuses.get(200):
            # Only when the probe saw one: an earlier check can legitimately
            # have drained the bucket, and a probe that opened on a 429 has
            # already proved everything a 200 would have.
            problems.extend(_rate_limit_header_problems(admitted_headers, "an admitted response"))
        problems.extend(_rate_limit_header_problems(headers, "the 429"))
        if _positive_int(headers.get(RETRY_AFTER_HEADER)) is None:
            problems.append("the 429 carried no usable Retry-After, so a client cannot back off")
        if headers.get(RATE_LIMIT_REMAINING_HEADER) != "0":
            problems.append(
                f"the 429 reported {RATE_LIMIT_REMAINING_HEADER}="
                f"{headers.get(RATE_LIMIT_REMAINING_HEADER)!r}, not 0"
            )
        if not str(_json(throttled).get("detail") or "").strip():
            problems.append("the 429 body carried no detail")
        shared = _shared_bucket_problems(
            target=target,
            token=token,
            throttled_headers=headers,
            admitted=admitted,
            drained_after=drained_after,
            fresh_connections=fresh_connections,
        )
        problems.extend(shared.pop("problems"))
    elif advertised:
        problems.append(
            f"the service advertises a bucket ({sorted(limit_headers)}) but admitted all "
            f"{sent} requests; either the limit is larger than this probe window or the "
            "headers describe a limiter that never engages — raise "
            "--rate-limit-probe-requests or check RATE_LIMIT_* on the target"
        )

    if enforced:
        reset = _positive_int(limit_headers.get(RATE_LIMIT_RESET_HEADER))
        outcome = (
            f"throttled after {sent} rapid requests with HTTP 429, "
            f"Retry-After={limit_headers.get(RETRY_AFTER_HEADER)}s"
            + (f", bucket full again in {reset}s" if reset else "")
            + (
                f"; {shared['fresh_connection_admissions']} of {fresh_connections} brand-new "
                "connections were admitted afterwards, so one bucket serves every worker"
                if shared.get("fresh_connections")
                else ""
            )
        )
    elif advertised:
        outcome = f"headers present but no 429 within {sent} requests"
    else:
        # Not a silent pass: the summary says which stacks are allowed to look
        # like this, so a deployed environment that lost its limiter reads as
        # wrong to a human even though the check cannot fail it from out here.
        outcome = (
            f"no limiter on this target — {sent} rapid requests, no 429, no X-RateLimit-* "
            "headers. Expected only where ADR 0014 turns it off (a dev stack, where the "
            "load harnesses drive one identity far past any per-subject rate); every "
            "deployed environment should show the enforced branch"
        )

    return Check(
        name="rate_limiting",
        passed=not problems,
        required=True,
        summary=outcome if not problems else "; ".join(problems),
        evidence={
            "requests_sent": sent,
            "requests_admitted": admitted,
            "probe_window": probe_requests,
            "probe_seconds": round(drained_after, 3) if enforced else None,
            "statuses": {str(code): count for code, count in sorted(statuses.items())},
            "enforced": enforced,
            "advertised": advertised,
            "limit_headers": limit_headers,
            "throttled_detail": _json(throttled).get("detail") if throttled is not None else None,
            "problems": problems,
            **shared,
        },
    )


def _shared_bucket_problems(
    *,
    target: Target,
    token: str,
    throttled_headers: dict[str, str],
    admitted: int,
    drained_after: float,
    fresh_connections: int,
) -> dict[str, Any]:
    """Assert one bucket stands behind the service, not one per worker.

    Two bounds, both in tokens, both derived from what the 429 advertised:

    * **How much was admitted before the refusal.** One bucket can give away
      its capacity plus whatever refilled while the probe was running, and no
      more. A per-worker bucket whose caller's connections spread across the
      workers gives away several times that — which is what a client sees as
      "the documented limit is not the limit".
    * **What a brand-new connection meets.** Once the bucket is drained, a
      fresh connection lands on whichever worker the accept queue hands it, and
      under one shared bucket it is refused like any other. Under a per-worker
      bucket roughly `(N-1)/N` of them find a full bucket instead. This is the
      half that catches the case the first bound cannot: a keep-alive client
      pinned to a single worker drains one bucket and sees exactly the numbers
      a shared bucket would have produced.

    The refill is read as `capacity / (reset - 1)` rather than
    `capacity / reset`, because `X-RateLimit-Reset` is whole seconds rounded
    up: taking it at face value would understate the refill and tighten the
    allowance, and a check whose tolerance leans towards false failure on a
    deploy gate is worse than one that leans the other way.
    """
    problems: list[str] = []
    evidence: dict[str, Any] = {"problems": problems}

    capacity = _positive_int(throttled_headers.get(RATE_LIMIT_LIMIT_HEADER))
    reset = _positive_int(throttled_headers.get(RATE_LIMIT_RESET_HEADER))
    if capacity is None or reset is None:
        # The header problems above already name this; there is nothing to do
        # arithmetic with, and inventing a capacity would be inventing a verdict.
        return evidence

    refill_per_second = capacity / max(1.0, reset - 1)
    evidence["capacity"] = capacity
    evidence["refill_per_second"] = round(refill_per_second, 3)

    one_bucket = capacity + math.ceil(refill_per_second * drained_after)
    allowance = one_bucket + RATE_LIMIT_ARITHMETIC_TOLERANCE
    evidence["one_bucket_allowance"] = allowance
    if admitted > allowance:
        problems.append(
            f"{admitted} requests were admitted before the first 429, but one bucket of "
            f"{capacity} tokens refilling at {refill_per_second:.1f}/s over the "
            f"{drained_after:.2f}s the probe took can only give away {allowance}. That is "
            f"about {admitted / max(1, one_bucket):.1f} buckets — the advertised limit is "
            "not the limit this service enforces, which is what a per-worker bucket looks "
            "like from out here (ADR 0014; check RATE_LIMIT_BACKEND on the target)"
        )

    if fresh_connections <= 0:
        return evidence

    opened_at = time.monotonic()
    statuses = _burst_on_fresh_connections(target, token, fresh_connections)
    window = time.monotonic() - opened_at
    reopened = statuses.count(200)
    refilled = math.floor(refill_per_second * window) + RATE_LIMIT_ARITHMETIC_TOLERANCE
    evidence["fresh_connections"] = fresh_connections
    evidence["fresh_connection_admissions"] = reopened
    evidence["fresh_connection_window_seconds"] = round(window, 3)
    evidence["fresh_connection_allowance"] = refilled
    unexpected = sorted(set(statuses) - {200, 429})
    if unexpected:
        problems.append(f"a fresh connection answered {unexpected} rather than 200 or 429")
    if reopened > refilled:
        problems.append(
            f"{reopened} of {fresh_connections} brand-new connections were admitted "
            f"immediately after the bucket drained, where a refill of "
            f"{refill_per_second:.1f}/s over {window:.2f}s allows at most {refilled}. A "
            "reconnect is buying a fresh allowance, which means the bucket is per worker "
            "rather than shared — or the shared bucket is failing open onto the in-process "
            "one, which /readyz reports as rate_limit=degraded (ADR 0014)"
        )
    return evidence


def _burst_on_fresh_connections(target: Target, token: str, count: int) -> list[int]:
    """Fire ``count`` requests at once, each on its own connection pool.

    Concurrent because the window matters: the allowance these are judged
    against is whatever the advertised refill produces while they are in
    flight, so sending them one after another would hand the bucket time to
    refill and turn the assertion into a measurement of the network.
    """

    def once(_: int) -> int:
        with httpx.Client(timeout=15.0) as fresh:
            return fresh.get(
                f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=1",
                headers=_auth(token),
            ).status_code

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(once, range(count)))


def _rate_limit_header_problems(headers: dict[str, str], where: str) -> list[str]:
    """The three headers a client plans its request rate from must be usable."""
    if not headers:
        return [f"{where} carried no X-RateLimit-* headers"]
    problems: list[str] = []
    for name in (RATE_LIMIT_LIMIT_HEADER, RATE_LIMIT_REMAINING_HEADER, RATE_LIMIT_RESET_HEADER):
        raw = headers.get(name)
        if raw is None:
            problems.append(f"{where} is missing {name}")
        elif not raw.isdigit():
            problems.append(f"{where} carried a non-numeric {name}={raw!r}")
    return problems


def _positive_int(raw: str | None) -> int | None:
    if raw is None or not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


# --- degraded metadata and bounded pages ------------------------------------


def _check_degraded_metadata(client: httpx.Client, target: Target, token: str) -> Check:
    """A movie with no artwork renders as a record, not as a failure.

    The reviewed catalog used to be mostly poster-less, which made this the
    normal path rather than an edge case. Since every title was enriched from
    TMDB the live stack may carry no degraded title at all; in that case the
    check walks the whole catalog to be sure, and then reports that it had no
    subject rather than failing — the degraded rendering itself is held by the
    web fixture matrix (web/e2e/poster-fallback.spec.ts) and the poster-card
    unit tests, which do not need a poster-less row to exist upstream.
    """
    catalog_response = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit={CATALOG_MAX_LIMIT}&sort=title",
        headers=_auth(token),
    )
    catalog = _json(catalog_response)
    items = catalog.get("items")
    if not isinstance(items, list) or not items:
        # Same reason as _no_cursor_summary: an empty item list after a 429 is a
        # limit, not a fixture, and the two get fixed in different places.
        reason = (
            f"HTTP {catalog_response.status_code}"
            if catalog_response.status_code != 200
            else "an empty item list"
        )
        return Check(
            name="degraded_metadata",
            passed=False,
            summary=(f"catalog answered {reason}, so degraded metadata could not be exercised"),
            evidence={"catalog_status": catalog_response.status_code},
        )
    posterless = [item for item in items if not item.get("poster_url")]
    scanned = len(items)
    pages = 1
    cursor = (catalog.get("page") or {}).get("next_cursor")
    # A complete first page proves nothing either way. Keep paging (bounded —
    # the fixture is 120 titles, three pages at this limit) until a poster-less
    # title turns up or the catalog runs out.
    while not posterless and cursor and pages < 10:
        page_response = client.get(
            f"{target.api_url}/users/{WARM_PERSONA}/catalog"
            f"?limit={CATALOG_MAX_LIMIT}&sort=title&cursor={quote(cursor, safe='')}",
            headers=_auth(token),
        )
        body = _json(page_response)
        page_items = body.get("items")
        if page_response.status_code != 200 or not isinstance(page_items, list):
            break
        pages += 1
        scanned += len(page_items)
        posterless = [item for item in page_items if not item.get("poster_url")]
        cursor = (body.get("page") or {}).get("next_cursor")
    if not posterless:
        # Not vacuous and not a failure: there is no degraded title to point at.
        # Say so, and say where the path is proved instead, so a reader does not
        # mistake this row for coverage it is not providing.
        return Check(
            name="degraded_metadata",
            passed=True,
            required=False,
            summary=(
                f"every one of the {scanned} catalog titles across {pages} pages has a "
                "poster, so the degraded path has no live subject; it is held by the "
                "web fixture matrix (web/e2e/poster-fallback.spec.ts) instead"
            ),
            evidence={"scanned": scanned, "pages": pages, "posterless": 0},
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


def _no_cursor_summary(response: httpx.Response) -> str:
    """Say why the first page carried no cursor, and name throttling as throttling.

    This whole suite runs as one subject inside a few seconds, so it competes
    with itself for that subject's token bucket (ADR 0014). A throttled catalog
    read returns a body with no ``page`` key, and reporting that as "the fixture
    has only one page" sends whoever reads it to look at the seed data instead
    of at the limit. Say which one it was.
    """
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after", "?")
        return (
            f"the first catalog page was rate limited (HTTP 429, Retry-After={retry_after}s), "
            "so no continuation cursor could be read. This suite and `verify --all` share one "
            "subject's bucket -- see ADR 0014"
        )
    if response.status_code != 200:
        return (
            f"the first catalog page answered HTTP {response.status_code}, "
            "so no continuation cursor could be read"
        )
    return "the first catalog page offered no continuation cursor to test"


def _check_cursor_rejection(client: httpx.Client, target: Target, token: str) -> Check:
    """A cursor that no longer matches its query is refused, not silently re-run."""
    first_response = client.get(
        f"{target.api_url}/users/{WARM_PERSONA}/catalog?limit=24&sort=title",
        headers=_auth(token),
    )
    first = _json(first_response)
    page = first.get("page") or {}
    cursor = page.get("next_cursor")
    if not isinstance(cursor, str):
        return Check(
            name="cursor_rejection",
            passed=False,
            summary=_no_cursor_summary(first_response),
            evidence={"first_page_status": first_response.status_code},
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
