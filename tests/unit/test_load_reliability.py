"""The rate-limit row of the reliability harness (ADR 0014).

That row runs in CI on every serving PR and again inside ``make prod-verify``,
where it can fail a deploy — and its interesting half is arithmetic against
headers a real service produced, which is exactly the kind of code that is
correct on the day it is written and wrong six months later. So the arithmetic
is exercised here against two services that differ only in the thing the check
exists to tell apart: one shared bucket, and one bucket per worker.

The services are ``httpx.MockTransport`` handlers rather than mocks of the
harness's own calls, so the check runs its real request loop, reads real
headers off real responses, and reaches its verdict the way it will against a
deployment.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest

from synthetic.load import reliability

TARGET = reliability.Target(
    api_url="http://api.test",
    keycloak_url="http://keycloak.test",
    realm="demo",
    client_id="movielens-api",
    client_secret="secret",
    username="verify",
    password="verify",
)


class _Buckets:
    """One or more token buckets in front of a catalog endpoint.

    ``workers`` is the whole point: at 1 this is the shared bucket every worker
    charges, and at 4 it is the per-worker bucket ADR 0014 shipped first, where
    a caller whose connections spread across the workers collects four
    allowances. ``refill_per_second`` shapes ``X-RateLimit-Reset`` — the number
    the check derives its allowance from — but no time passes here, so the
    buckets only ever drain. That is the point: it leaves the admitted count as
    a statement about how many buckets exist.
    """

    def __init__(self, *, capacity: int, refill_per_second: float, workers: int = 1) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = [float(capacity)] * workers
        self.next_worker = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index = self.next_worker % len(self.tokens)
        self.next_worker += 1
        tokens = self.tokens[index]
        reset = math.ceil((self.capacity - min(tokens, self.capacity)) / self.refill_per_second)
        headers = {
            "X-RateLimit-Limit": str(self.capacity),
            "X-RateLimit-Remaining": str(int(max(0.0, tokens - 1.0))),
            "X-RateLimit-Reset": str(max(1, reset)),
        }
        if tokens < 1.0:
            headers["X-RateLimit-Remaining"] = "0"
            headers["X-RateLimit-Reset"] = str(math.ceil(self.capacity / self.refill_per_second))
            headers["Retry-After"] = "1"
            return httpx.Response(
                429,
                headers=headers,
                json={"detail": "rate limit exceeded for this tenant and subject"},
            )
        self.tokens[index] = tokens - 1.0
        return httpx.Response(200, headers=headers, json={"items": []})


def _run(
    buckets: _Buckets,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fresh_connections: int = 4,
    fresh_admissions: int = 0,
) -> reliability.Check:
    """Run the real check against ``buckets``.

    The fresh-connection sweep is the one part that cannot go through the mock
    transport — it opens its own clients on purpose, because a brand-new
    connection is the thing under test — so its result is supplied here.
    """
    monkeypatch.setattr(
        reliability,
        "_burst_on_fresh_connections",
        lambda target, token, count: [200] * fresh_admissions + [429] * (count - fresh_admissions),
    )
    client = httpx.Client(transport=httpx.MockTransport(buckets))
    with client:
        return reliability._check_rate_limiting(
            client, TARGET, "token", 720, fresh_connections=fresh_connections
        )


def test_one_shared_bucket_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented deployment shape: a burst of 120 refilled at 600/minute
    behind however many workers, met by one client."""
    check = _run(_Buckets(capacity=120, refill_per_second=10.0), monkeypatch)

    assert check.passed, check.summary
    assert check.evidence["requests_admitted"] == 120
    assert check.evidence["capacity"] == 120
    assert check.evidence["fresh_connection_admissions"] == 0
    assert "one bucket serves every worker" in check.summary


def test_a_bucket_per_worker_is_caught_by_how_much_it_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asymmetry issue #70 exists to remove, seen from outside: four
    workers hand the same subject four allowances, and the number in
    `X-RateLimit-Limit` stops describing what the service enforces."""
    check = _run(_Buckets(capacity=120, refill_per_second=10.0, workers=4), monkeypatch)

    assert not check.passed
    assert check.evidence["requests_admitted"] == 480
    assert check.evidence["one_bucket_allowance"] < 480
    assert "buckets" in check.summary
    assert "RATE_LIMIT_BACKEND" in check.summary


def test_a_reconnect_that_buys_a_fresh_allowance_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half the admitted count cannot catch. A keep-alive client pinned to
    one worker drains one bucket and sees exactly the numbers a shared bucket
    would have produced — so the check reconnects and asks again."""
    check = _run(
        _Buckets(capacity=120, refill_per_second=10.0),
        monkeypatch,
        fresh_connections=10,
        fresh_admissions=5,
    )

    assert not check.passed
    assert "brand-new connections were admitted" in check.summary
    assert check.evidence["fresh_connection_admissions"] == 5


def test_a_slow_probe_widens_the_allowance_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refill of one token per second over the ten seconds the probe took is
    ten more tokens the service was entitled to hand out. The bound has to
    follow the clock or it fails honest deployments on slow links."""
    problems = reliability._shared_bucket_problems(
        target=TARGET,
        token="token",
        throttled_headers={
            "x-ratelimit-limit": "20",
            # reset-1 = 20 seconds for 20 tokens: one token a second.
            "x-ratelimit-reset": "21",
        },
        admitted=30,
        drained_after=10.0,
        fresh_connections=0,
    )

    assert problems["problems"] == []
    assert problems["one_bucket_allowance"] == 31


def test_headers_that_carry_no_arithmetic_produce_no_verdict() -> None:
    """A 429 with no usable capacity is already a failure — the header checks
    say so. Inventing a capacity here would be inventing a second verdict from
    the same defect."""
    problems = reliability._shared_bucket_problems(
        target=TARGET,
        token="token",
        throttled_headers={"retry-after": "1"},
        admitted=500,
        drained_after=0.1,
        fresh_connections=4,
    )

    assert problems == {"problems": []}


def test_the_shared_probe_can_be_turned_off_for_a_target_that_cannot_take_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loud, parameterised escape rather than an edit to this file — the same
    shape `--rate-limit-probe-requests` already has."""
    called: list[int] = []
    monkeypatch.setattr(
        reliability,
        "_burst_on_fresh_connections",
        lambda target, token, count: called.append(count) or [],
    )
    client = httpx.Client(
        transport=httpx.MockTransport(_Buckets(capacity=5, refill_per_second=10.0))
    )
    with client:
        check = reliability._check_rate_limiting(client, TARGET, "token", 720, fresh_connections=0)

    assert check.passed, check.summary
    assert called == []
    assert "fresh_connections" not in check.evidence


def test_the_rate_limit_row_still_runs_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """It drains this identity's bucket on purpose, and every check above
    authenticates as the same subject — so it has to be the last one. The
    reconnect sweep spends more of that same bucket, which makes the ordering
    matter more than it did, not less."""
    recorded: list[str] = []
    for name in dir(reliability):
        if name.startswith("_check_"):
            monkeypatch.setattr(
                reliability,
                name,
                _recorder(recorded, name),
            )

    checks = list(reliability._run_checks(None, TARGET, "token", 720, 4))  # type: ignore[arg-type]

    assert recorded[-1] == "_check_rate_limiting"
    assert len(checks) == len(recorded)


def _recorder(into: list[str], name: str) -> Any:
    def record(*args: Any, **kwargs: Any) -> Any:
        into.append(name)
        check = reliability.Check(name=name, passed=True, summary="stub")
        # `_check_request_id_echo` returns a (request_id, Check) pair; every
        # other check returns a Check. Match whichever the caller unpacks.
        return ("request-id", check) if name == "_check_request_id_echo" else check

    return record
