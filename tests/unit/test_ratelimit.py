"""Rate-limiting contract per ADR 0014.

Two layers are covered separately on purpose. ``TokenBucketLimiter`` is tested
against a controlled clock, because the properties that matter — a burst is
absorbed once, refill is proportional to elapsed time, a full bucket is
indistinguishable from a new one — are statements about time and would be
flaky if they were statements about ``time.monotonic()``. The middleware is
tested through a real ASGI app so the header contract, the 429 body, and the
exemptions are asserted where a client sees them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from src.auth.middleware import UNAUTHENTICATED_PATHS
from src.config import Settings
from src.serving.ratelimit import (
    LIMIT_HEADER,
    REMAINING_HEADER,
    RESET_HEADER,
    RETRY_AFTER_HEADER,
    RateLimitMiddleware,
    TokenBucketLimiter,
)

KEY = ("demo", "subject-a")
OTHER_KEY = ("demo", "subject-b")
OTHER_TENANT = ("default", "subject-a")


class FakeClock:
    """A clock the test advances explicitly, in seconds."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(
    clock: FakeClock, *, per_minute: int = 120, burst: int = 30, **kwargs: Any
) -> TokenBucketLimiter:
    return TokenBucketLimiter(requests_per_minute=per_minute, burst=burst, clock=clock, **kwargs)


# --- the bucket itself ------------------------------------------------------


def test_burst_is_admitted_once_and_then_refused() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)

    decisions = [limiter.acquire(KEY) for _ in range(31)]

    assert all(decision.allowed for decision in decisions[:30])
    assert decisions[-1].allowed is False
    assert decisions[0].remaining == 29
    assert decisions[29].remaining == 0
    assert decisions[-1].remaining == 0
    assert decisions[-1].limit == 30


def test_refill_is_proportional_to_elapsed_time() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(30):
        limiter.acquire(KEY)
    assert limiter.acquire(KEY).allowed is False

    # 120/minute is two tokens a second, so five seconds buys exactly ten.
    clock.advance(5.0)
    admitted = [limiter.acquire(KEY).allowed for _ in range(11)]

    assert admitted == [True] * 10 + [False]


def test_refill_never_exceeds_the_burst_capacity() -> None:
    """An idle caller banks a burst, not a minute's worth of unspent quota."""
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.acquire(KEY)

    clock.advance(3_600.0)
    admitted = [limiter.acquire(KEY).allowed for _ in range(31)]

    assert admitted == [True] * 30 + [False]


def test_buckets_are_isolated_per_subject_and_per_tenant() -> None:
    """The highest-severity bug class has a rate-limiting shape too: one
    tenant's traffic must not be able to throttle another's."""
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(30):
        limiter.acquire(KEY)

    assert limiter.acquire(KEY).allowed is False
    assert limiter.acquire(OTHER_KEY).allowed is True
    assert limiter.acquire(OTHER_TENANT).allowed is True


def test_reset_and_retry_after_describe_the_same_bucket() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    for _ in range(30):
        limiter.acquire(KEY)
    refused = limiter.acquire(KEY)

    # Empty bucket, two tokens a second: one token in a second (rounded up to a
    # whole second), the full 30 back in fifteen.
    assert refused.retry_after_seconds == 1
    assert refused.reset_seconds == 15

    clock.advance(15.0)
    replenished = limiter.acquire(KEY)
    assert replenished.allowed is True
    assert replenished.remaining == 29
    assert replenished.reset_seconds == 1


def test_retry_after_is_never_zero_seconds() -> None:
    """A client that reads Retry-After as 'sleep this long' must not spin."""
    clock = FakeClock()
    limiter = _limiter(clock, per_minute=6_000, burst=1)
    assert limiter.acquire(KEY).allowed is True

    refused = limiter.acquire(KEY)
    assert refused.allowed is False
    assert refused.retry_after_seconds == 1


def test_full_buckets_are_evicted_and_recovering_ones_are_kept() -> None:
    """Eviction may not hand a throttled caller a fresh allowance."""
    clock = FakeClock()
    limiter = _limiter(clock, max_buckets=4)
    for _ in range(30):
        limiter.acquire(KEY)
    assert limiter.acquire(KEY).allowed is False

    # Five untouched-since-full callers, enough to push past the ceiling.
    for index in range(5):
        limiter.acquire(("demo", f"filler-{index}"))

    assert limiter.bucket_count <= 4
    # The drained bucket is still drained. Eviction goes fullest-first, so the
    # five near-full fillers are what gets dropped; a least-recently-used policy
    # would have reached this bucket first and handed the throttled caller a
    # fresh allowance.
    assert limiter.acquire(KEY).allowed is False


def test_a_bucket_that_has_fully_refilled_can_be_dropped() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_buckets=2)
    limiter.acquire(KEY)
    limiter.acquire(OTHER_KEY)
    clock.advance(60.0)

    limiter.acquire(("demo", "third"))

    # Two of the three refilled to capacity in the meantime, so the map is back
    # under its ceiling without evicting anyone mid-recovery.
    assert limiter.bucket_count <= 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"requests_per_minute": 0, "burst": 30}, "requests_per_minute"),
        ({"requests_per_minute": 120, "burst": 0}, "burst"),
        ({"requests_per_minute": 120, "burst": 30, "max_buckets": 0}, "max_buckets"),
    ],
)
def test_nonsensical_configuration_is_refused_at_construction(
    kwargs: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        TokenBucketLimiter(**kwargs)


def test_the_limiter_module_imports_nothing_that_could_do_io() -> None:
    """The limiter sits in front of a path with a p99 SLO (non-negotiable #4).

    Asserted structurally against the module's own source rather than by timing
    a call: a future Redis-backed bucket (ADR 0014's named upgrade path) is a
    deliberate decision with a latency cost, and it should have to change this
    test rather than arrive as an import nobody noticed.
    """
    import ast
    import inspect

    import src.serving.ratelimit as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"redis", "socket", "httpx", "sqlalchemy", "psycopg2", "requests", "asyncio"}
    assert not (imported & forbidden), sorted(imported & forbidden)


# --- the middleware ---------------------------------------------------------


@dataclass(frozen=True)
class StubPrincipal:
    tenant_id: str
    user_id: str


class PrincipalMiddleware:
    """Stand in for AuthMiddleware: put a principal on the request state.

    Registered outside the limiter, exactly as ``AuthMiddleware`` is, so the
    ordering under test is the ordering ``src.serving.app`` wires up.
    """

    def __init__(self, app: ASGIApp, *, principal: StubPrincipal | None) -> None:
        self.app = app
        self._principal = principal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._principal is not None:
            scope.setdefault("state", {})["principal"] = self._principal
        await self.app(scope, receive, send)


def _client(
    *,
    principal: StubPrincipal | None = StubPrincipal("demo", "subject-a"),
    per_minute: int = 120,
    burst: int = 2,
) -> TestClient:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/whoami")
    async def whoami() -> dict[str, str]:
        return {"tenant_id": "demo"}

    limiter = TokenBucketLimiter(requests_per_minute=per_minute, burst=burst)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    app.add_middleware(PrincipalMiddleware, principal=principal)
    return TestClient(app)


def test_every_authenticated_response_carries_the_bucket_headers() -> None:
    with _client() as client:
        first = client.get("/whoami")
        second = client.get("/whoami")

    assert first.status_code == 200
    assert first.headers[LIMIT_HEADER] == "2"
    assert first.headers[REMAINING_HEADER] == "1"
    assert int(first.headers[RESET_HEADER]) >= 0
    assert second.headers[REMAINING_HEADER] == "0"


def test_an_exhausted_bucket_answers_429_with_retry_after_and_a_json_detail() -> None:
    with _client() as client:
        for _ in range(2):
            assert client.get("/whoami").status_code == 200
        throttled = client.get("/whoami")

    assert throttled.status_code == 429
    assert throttled.headers[RETRY_AFTER_HEADER] == "1"
    assert throttled.headers[LIMIT_HEADER] == "2"
    assert throttled.headers[REMAINING_HEADER] == "0"
    detail = throttled.json()["detail"]
    # The body names the policy, so an operator reading a client's log knows
    # which knob produced the refusal without opening the deployment panel.
    assert "120 requests/minute with a burst of 2" in detail


def test_the_probes_are_exempt_and_carry_no_bucket_headers() -> None:
    with _client(burst=1) as client:
        assert client.get("/whoami").status_code == 200
        assert client.get("/whoami").status_code == 429
        for path in sorted(UNAUTHENTICATED_PATHS):
            probe = client.get(path)
            assert probe.status_code == 200, path
            assert LIMIT_HEADER not in probe.headers, path


def test_a_request_with_no_resolved_principal_is_not_charged() -> None:
    """Fail open on a missing identity: a shared anonymous bucket would be an
    amplifier, not a limit. In the real stack auth has already answered 401."""
    with _client(principal=None, burst=1) as client:
        statuses = [client.get("/whoami").status_code for _ in range(5)]

    assert statuses == [200] * 5


def test_subjects_do_not_share_a_bucket_through_the_middleware() -> None:
    with _client(principal=StubPrincipal("demo", "subject-a"), burst=1) as client:
        assert client.get("/whoami").status_code == 200
        assert client.get("/whoami").status_code == 429
    with _client(principal=StubPrincipal("demo", "subject-b"), burst=1) as client:
        assert client.get("/whoami").status_code == 200


# --- settings ---------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ENVIRONMENT",
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_REQUESTS_PER_MINUTE",
        "RATE_LIMIT_BURST",
        "MODEL_SERVER_AUTH_TOKEN",
        "PGBOUNCER_ADMIN_PASSWORD",
        "DEV_AUTH_BYPASS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_documented_defaults_match_the_runbook(clean_env: None) -> None:
    settings = Settings(_env_file=None)
    assert settings.rate_limit_requests_per_minute == 120
    assert settings.rate_limit_burst == 30


def test_the_limiter_is_off_on_a_dev_box_and_on_everywhere_else(clean_env: None) -> None:
    assert Settings(_env_file=None, environment="dev").rate_limit_active is False
    for environment in ("staging", "production"):
        settings = Settings(
            _env_file=None,
            environment=environment,
            model_server_auth_token="not-the-dev-token",
            pgbouncer_admin_password="not-the-dev-password",
        )
        assert settings.rate_limit_active is True


def test_an_explicit_setting_wins_in_both_directions(clean_env: None) -> None:
    on_in_dev = Settings(_env_file=None, environment="dev", rate_limit_enabled=True)
    off_in_production = Settings(
        _env_file=None,
        environment="production",
        rate_limit_enabled=False,
        model_server_auth_token="not-the-dev-token",
        pgbouncer_admin_password="not-the-dev-password",
    )
    assert on_in_dev.rate_limit_active is True
    assert off_in_production.rate_limit_active is False


def test_a_zero_limit_is_refused_rather_than_read_as_disabled(clean_env: None) -> None:
    """`RATE_LIMIT_REQUESTS_PER_MINUTE=0` is a typo, not an off switch — the
    off switch is RATE_LIMIT_ENABLED. Reading it as 'unlimited' would be the
    dangerous interpretation; reading it as 'refuse everything' would be an
    outage. It is refused at construction instead."""
    with pytest.raises(Exception, match="rate_limit_requests_per_minute"):
        Settings(_env_file=None, rate_limit_requests_per_minute=0)
