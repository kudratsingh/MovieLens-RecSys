"""Rate-limiting contract per ADR 0014.

Three layers are covered separately on purpose. ``TokenBucketLimiter`` is
tested against a controlled clock, because the properties that matter — a burst
is absorbed once, refill is proportional to elapsed time, a full bucket is
indistinguishable from a new one — are statements about time and would be flaky
if they were statements about ``time.monotonic()``. ``SharedTokenBucket`` is
tested by executing its real Lua against a real Lua interpreter (``fakeredis``
is built on ``lupa``), because a token bucket written in a language nothing in
the suite runs is a token bucket nobody has read; the same fake's ``TIME``
command reads ``time.time``, so the clock is controllable there too. The
middleware is tested through a real ASGI app so the header contract, the 429
body, and the exemptions are asserted where a client sees them.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import fakeredis.aioredis
import pytest
import redis.exceptions
from fakeredis import FakeServer
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
    RateLimiter,
    RateLimitMiddleware,
    RateLimitPolicy,
    SharedTokenBucket,
    TokenBucketLimiter,
    bucket_key,
    build_rate_limiter,
    parse_redis_endpoint,
)
from src.serving.tenancy import NO_TENANT_OVERRIDES, TenantRateLimit

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


def test_the_limiter_reaches_for_redis_and_for_nothing_else() -> None:
    """The limiter sits in front of a path with a p99 SLO (non-negotiable #4).

    Asserted structurally against the module's own source rather than by timing
    a call. Redis is now a deliberate import — the shared bucket costs one
    round trip and ADR 0014's 2026-08-29 note is where that was decided — but
    everything else that could do I/O on this path still has to arrive as a
    decision rather than as an import nobody noticed. The database drivers in
    particular: the per-tenant quota (migration 0016) is the one thing here
    that reaches a database, and it arrives as an injected callable rather than
    as a driver — which is why the connection it forwards is typed as an opaque
    object. What this module takes from ``src.serving.tenancy`` is one frozen
    dataclass, the resolver answers from the tenant router's cache on the hot
    path, and a failed read falls open to the global policy in
    ``src.serving.app`` rather than failing the request.
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

    forbidden = {"socket", "httpx", "sqlalchemy", "psycopg2", "requests"}
    assert not (imported & forbidden), sorted(imported & forbidden)
    assert "redis" in imported

    signature = inspect.signature(module.RateLimitMiddleware.__init__)
    assert signature.parameters["tenant_limits"].default is None


# --- the shared bucket ------------------------------------------------------


@pytest.fixture
def redis_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClock]:
    """Control what Redis's own ``TIME`` command reports.

    The Lua script reads the clock from ``redis.call('TIME')`` and never from
    the caller, which is what stops four workers with four slightly different
    clocks from disagreeing about one bucket — and what stops a caller from
    refilling its own bucket by claiming time has passed. ``fakeredis``
    implements ``TIME`` on ``time.time``, so patching that is how a refill
    becomes a statement about arithmetic rather than a statement about
    ``sleep()``. ``asyncio`` schedules on ``time.monotonic``, which is
    untouched.
    """
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock)
    yield clock


def _shared(
    server: FakeServer,
    *,
    per_minute: int = 120,
    burst: int = 4,
    clock: FakeClock | None = None,
) -> SharedTokenBucket:
    """One worker: its own client and connection pool, the same Redis."""
    return SharedTokenBucket(
        redis_client=fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
        requests_per_minute=per_minute,
        burst=burst,
        clock=clock or time.monotonic,
    )


class _BrokenRedis:
    """A client whose every call fails, the way an unreachable Redis does."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or redis.exceptions.ConnectionError("redis is unreachable")
        self.calls = 0

    def register_script(self, script: str) -> Any:
        async def run(keys: Any = (), args: Any = ()) -> Any:
            self.calls += 1
            raise self.error

        return run

    async def ping(self) -> bool:
        raise self.error


async def test_the_shared_bucket_admits_one_burst_and_then_refuses(
    redis_clock: FakeClock,
) -> None:
    limiter = _shared(FakeServer(), burst=4)

    decisions = [await limiter.charge(KEY) for _ in range(5)]

    assert [decision.allowed for decision in decisions] == [True] * 4 + [False]
    assert [decision.remaining for decision in decisions] == [3, 2, 1, 0, 0]
    assert decisions[-1].limit == 4
    assert decisions[-1].retry_after_seconds == 1


async def test_the_shared_bucket_refills_at_the_configured_rate(
    redis_clock: FakeClock,
) -> None:
    """120/minute is two tokens a second, and the script has to agree."""
    limiter = _shared(FakeServer(), per_minute=120, burst=4)
    for _ in range(4):
        await limiter.charge(KEY)
    assert (await limiter.charge(KEY)).allowed is False

    redis_clock.advance(1.5)
    admitted = [(await limiter.charge(KEY)).allowed for _ in range(4)]

    assert admitted == [True, True, True, False]


async def test_the_shared_bucket_never_banks_more_than_the_burst(
    redis_clock: FakeClock,
) -> None:
    """An idle caller banks a burst, not an hour's worth of unspent quota."""
    limiter = _shared(FakeServer(), per_minute=120, burst=4)
    await limiter.charge(KEY)

    redis_clock.advance(3_600.0)
    admitted = [(await limiter.charge(KEY)).allowed for _ in range(5)]

    assert admitted == [True] * 4 + [False]


async def test_two_workers_charging_one_subject_meet_one_bucket(
    redis_clock: FakeClock,
) -> None:
    """This is the whole point of the shared bucket, so it is asserted directly.

    Two ``SharedTokenBucket`` instances with separate clients and separate
    connection pools stand in for two uvicorn workers. Under the in-process
    bucket each of them would have admitted a full burst of its own, which is
    the asymmetry ADR 0014 measured refusing 37.9% of one subject's requests
    while the service's nominal ceiling sat unused. Here the two of them
    together get exactly one burst.
    """
    server = FakeServer()
    worker_a = _shared(server, burst=4)
    worker_b = _shared(server, burst=4)

    admitted = []
    for index in range(6):
        worker = worker_a if index % 2 == 0 else worker_b
        admitted.append((await worker.charge(KEY)).allowed)

    assert admitted == [True] * 4 + [False, False]
    # And the refusal is the same refusal on either worker: a client that
    # reconnects onto the other process is not handed a fresh allowance.
    assert (await worker_a.charge(KEY)).allowed is False
    assert (await worker_b.charge(KEY)).allowed is False


async def test_shared_buckets_are_isolated_per_subject_and_per_tenant(
    redis_clock: FakeClock,
) -> None:
    """The highest-severity bug class has a rate-limiting shape too."""
    limiter = _shared(FakeServer(), burst=2)
    for _ in range(2):
        await limiter.charge(KEY)

    assert (await limiter.charge(KEY)).allowed is False
    assert (await limiter.charge(OTHER_KEY)).allowed is True
    assert (await limiter.charge(OTHER_TENANT)).allowed is True


def test_the_bucket_key_lives_under_the_tenant_router_prefix() -> None:
    """One spelling of ``tenant:<id>:``, asserted rather than remembered.

    ``ratelimit.py`` reaches for no database driver, so it cannot import the
    router that composes the prefix for the online feature store. This is what
    keeps the two copies honest: a tenant's rate-limit state has to live in the
    same namespace as the rest of its Redis state.
    """
    from src.serving.tenancy.router import TenantConfig

    tenant = TenantConfig(
        id="demo",
        display_name="Demo tenant",
        redis_prefix="tenant:demo:",
        ab_bucketing_seed=1,
    )

    assert bucket_key("demo", "subject-a").startswith(tenant.redis_prefix)
    assert bucket_key("demo", "subject-a") == "tenant:demo:ratelimit:subject-a"


async def test_an_idle_bucket_expires_on_its_own(redis_clock: FakeClock) -> None:
    """No eviction sweep here — a bucket idle past one full refill is a bucket
    indistinguishable from one that never existed, so Redis drops it."""
    server = FakeServer()
    limiter = _shared(server, per_minute=120, burst=4)
    await limiter.charge(KEY)

    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    ttl = await client.ttl(bucket_key(*KEY))

    policy = RateLimitPolicy(requests_per_minute=120, burst=4)
    assert ttl == policy.ttl_seconds
    # Long enough to outlive a full refill, so the key can only expire in a
    # state that changes no future decision.
    assert ttl > policy.capacity / policy.refill_per_second


async def test_the_script_reloads_itself_after_the_cache_is_flushed(
    redis_clock: FakeClock,
) -> None:
    """``EVALSHA`` with a ``SCRIPT LOAD`` fallback: a Redis restart, a failover
    onto a replica with a cold script cache, or an operator's ``SCRIPT FLUSH``
    must not turn every request into a 500."""
    server = FakeServer()
    limiter = _shared(server, burst=4)
    assert (await limiter.charge(KEY)).allowed is True

    await fakeredis.aioredis.FakeRedis(server=server).script_flush()

    assert (await limiter.charge(KEY)).allowed is True
    assert limiter.fail_open_total == 0


async def test_a_redis_failure_falls_back_to_the_in_process_bucket() -> None:
    """Fail open, but onto a real bucket rather than onto an unconditional yes.

    A limiter is backpressure, not an auth boundary: failing closed would turn
    a Redis blip into a total outage for a service that can otherwise still
    serve popularity fallbacks. What the fallback must not do is stop limiting
    — the guarantee weakens to per-worker, it does not disappear.
    """
    broken = _BrokenRedis()
    limiter = SharedTokenBucket(
        redis_client=broken,  # type: ignore[arg-type]
        requests_per_minute=120,
        burst=2,
    )

    decisions = [await limiter.charge(KEY) for _ in range(4)]

    assert [decision.allowed for decision in decisions] == [True, True, False, False]
    assert [decision.limit for decision in decisions] == [2] * 4
    assert decisions[-1].retry_after_seconds >= 1
    assert limiter.fail_open_total == 4
    assert broken.calls == 4


async def test_a_reply_the_client_cannot_read_fails_open_rather_than_500ing() -> None:
    """A limiter that takes down the thing it protects has stopped being a
    limiter. An unreadable reply means it is not working, and the answer to
    that is the same fail-open as an unreachable Redis — so the parsing lives
    inside the same guarded block as the call."""

    class _Garbled:
        def register_script(self, script: str) -> Any:
            async def run(keys: Any = (), args: Any = ()) -> Any:
                return ["not-a-number", "nor-this"]

            return run

        async def ping(self) -> bool:
            return True

    limiter = SharedTokenBucket(
        redis_client=_Garbled(),  # type: ignore[arg-type]
        requests_per_minute=120,
        burst=1,
    )

    assert (await limiter.charge(KEY)).allowed is True
    assert (await limiter.charge(KEY)).allowed is False
    assert limiter.fail_open_total == 2


async def test_a_timed_out_redis_fails_open_the_same_way() -> None:
    """Unreachable and unresponsive are the same answer to the only question
    the middleware is asking, and both have to be caught: a ``TimeoutError``
    escaping here would turn a slow Redis into a 500 on every request."""
    limiter = SharedTokenBucket(
        redis_client=_BrokenRedis(redis.exceptions.TimeoutError("timed out")),  # type: ignore[arg-type]
        requests_per_minute=120,
        burst=1,
    )

    assert (await limiter.charge(KEY)).allowed is True
    assert (await limiter.charge(KEY)).allowed is False
    assert limiter.fail_open_total == 2


async def test_the_fail_open_line_is_logged_once_per_window_with_its_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Redis outage under load must not write one warning per request: that
    buries the incident it is reporting. The line carries the count instead."""
    clock = FakeClock()
    limiter = SharedTokenBucket(
        redis_client=_BrokenRedis(),  # type: ignore[arg-type]
        requests_per_minute=120,
        burst=100,
        clock=clock,
    )

    with caplog.at_level("WARNING", logger="src.serving.ratelimit"):
        for _ in range(20):
            await limiter.charge(KEY)
        assert len(caplog.records) == 1

        clock.advance(31.0)
        await limiter.charge(KEY)

    assert len(caplog.records) == 2
    assert "requests=20" in caplog.records[1].getMessage()
    assert "total=21" in caplog.records[1].getMessage()
    assert limiter.fail_open_total == 21


async def test_the_backend_state_readiness_reports_follows_the_traffic(
    redis_clock: FakeClock,
) -> None:
    """``/readyz`` reports the bucket rather than the configuration.

    A recent fail-open is answered without asking Redis anything, because the
    traffic already answered the question. Once the window has passed with no
    further failure, the probe pings — so a worker that has taken no traffic
    since boot still reports the truth about its dependency.
    """
    healthy = _shared(FakeServer())
    assert await healthy.report() == "shared"

    clock = FakeClock()
    degraded = SharedTokenBucket(
        redis_client=_BrokenRedis(),  # type: ignore[arg-type]
        requests_per_minute=120,
        burst=4,
        clock=clock,
    )
    assert await degraded.report() == "degraded"  # nothing has failed; the ping does
    await degraded.charge(KEY)
    assert await degraded.report() == "degraded"  # the fail-open, without a ping

    assert await TokenBucketLimiter(requests_per_minute=120, burst=4).report() == "in-process"


async def test_both_backends_describe_the_same_bucket_state_identically(
    redis_clock: FakeClock,
) -> None:
    """A client must not be able to tell which bucket answered from the headers.

    The shared bucket fails open onto the in-process one mid-request, so a
    sequence of responses can be served by both; if the two rendered
    ``Reset`` or ``Retry-After`` differently, a client's backoff would see a
    discontinuity that reflects nothing about its own behaviour.
    """
    shared = _shared(FakeServer(), per_minute=120, burst=3)
    in_process = TokenBucketLimiter(requests_per_minute=120, burst=3, clock=FakeClock())

    for _ in range(4):
        from_redis = await shared.charge(KEY)
        from_memory = await in_process.charge(KEY)
        assert from_redis == from_memory


# --- backend selection ------------------------------------------------------


def test_the_backend_is_selected_from_settings(clean_env: None) -> None:
    redis_backed = build_rate_limiter(
        Settings(_env_file=None, environment="dev", rate_limit_backend="redis")
    )
    in_process = build_rate_limiter(
        Settings(_env_file=None, environment="dev", rate_limit_backend="memory")
    )

    assert isinstance(redis_backed, SharedTokenBucket)
    assert isinstance(in_process, TokenBucketLimiter)
    assert redis_backed.policy == in_process.policy


def test_the_default_backend_is_the_shared_one(clean_env: None) -> None:
    """Whichever way a deployment is configured, the default is the bucket that
    makes the configured limit mean what it says."""
    assert Settings(_env_file=None).rate_limit_backend == "redis"


@pytest.mark.parametrize(
    ("connection_string", "expected"),
    [
        ("localhost:6379", ("localhost", 6379, None, False)),
        ("redis:6379", ("redis", 6379, None, False)),
        ("redis:6379,password=s3cret", ("redis", 6379, "s3cret", False)),
        ("redis:6380,password=s3cret,ssl=true", ("redis", 6380, "s3cret", True)),
    ],
)
def test_the_deployed_connection_string_forms_are_read_the_same_way_everywhere(
    connection_string: str, expected: tuple[str, int, str | None, bool]
) -> None:
    """Feast's ``host:port,key=value`` form, parsed twice in this repository.

    The release preflight parses it to probe reachability and must stay
    importable with no Redis client installed; the limiter parses it to build
    one. Two parsers, one contract — asserted here so a deployment cannot end
    up with a preflight that reaches a Redis the limiter does not.
    """
    from src.release.bootstrap import parse_redis_connection_string

    endpoint = parse_redis_endpoint(connection_string)
    preflight_endpoint, preflight_options = parse_redis_connection_string(connection_string)

    assert (endpoint.host, endpoint.port, endpoint.password, endpoint.ssl) == expected
    assert (endpoint.host, endpoint.port) == (preflight_endpoint.host, preflight_endpoint.port)
    assert endpoint.password == (preflight_options.get("password") or None)


@pytest.mark.parametrize(
    "connection_string",
    ["", "redis", "redis:not-a-port", "redis:6379,password"],
)
def test_an_unreadable_connection_string_is_refused_rather_than_guessed(
    connection_string: str,
) -> None:
    with pytest.raises(ValueError):
        parse_redis_endpoint(connection_string)


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


# --- per-tenant quota overrides (migration 0016) -----------------------------


def _tenant_limited_client(
    *,
    overrides: dict[str, TenantRateLimit],
    principal: StubPrincipal = StubPrincipal("demo", "subject-a"),
    per_minute: int = 120,
    burst: int = 2,
    resolutions: list[str] | None = None,
    limiter: RateLimiter | None = None,
) -> TestClient:
    """The same harness, plus the registry lookup the app injects."""
    app = FastAPI()

    @app.get("/whoami")
    async def whoami() -> dict[str, str]:
        return {"tenant_id": principal.tenant_id}

    async def tenant_limits(tenant_id: str, connection: object | None) -> TenantRateLimit:
        if resolutions is not None:
            resolutions.append(tenant_id)
        return overrides.get(tenant_id, NO_TENANT_OVERRIDES)

    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter or TokenBucketLimiter(requests_per_minute=per_minute, burst=burst),
        tenant_limits=tenant_limits,
    )
    app.add_middleware(PrincipalMiddleware, principal=principal)
    return TestClient(app)


def test_a_tenant_without_overrides_charges_the_global_policy() -> None:
    """The unconfigured deployment behaves exactly as it did before 0016."""
    resolutions: list[str] = []
    with _tenant_limited_client(overrides={}, resolutions=resolutions, burst=2) as client:
        assert client.get("/whoami").headers[LIMIT_HEADER] == "2"
        assert client.get("/whoami").headers[LIMIT_HEADER] == "2"
        refused = client.get("/whoami")

    assert refused.status_code == 429
    assert resolutions == ["demo", "demo", "demo"]


def test_a_tenants_own_burst_replaces_the_global_one() -> None:
    with _tenant_limited_client(
        overrides={"demo": TenantRateLimit(requests_per_minute=600, burst=4)},
        burst=2,
    ) as client:
        statuses = [client.get("/whoami").status_code for _ in range(5)]
        headers = client.get("/whoami")

    # Four admitted on the tenant's own capacity, where the global policy would
    # have refused after two.
    assert statuses == [200, 200, 200, 200, 429]
    assert headers.status_code == 429
    assert headers.headers[LIMIT_HEADER] == "4"
    assert "600 requests/minute with a burst of 4" in headers.json()["detail"]


def test_one_null_column_falls_back_to_the_global_setting_on_its_own() -> None:
    """Each column coalesces independently.

    Lowering a tenant's sustained rate should not require restating a burst
    that was already right, and a half-specified override is a normal
    configuration rather than an error.
    """
    with _tenant_limited_client(
        overrides={"demo": TenantRateLimit(requests_per_minute=60, burst=None)},
        per_minute=120,
        burst=3,
    ) as client:
        refused = [client.get("/whoami") for _ in range(4)][-1]

    assert refused.status_code == 429
    assert refused.headers[LIMIT_HEADER] == "3"
    # And the 429 quotes the policy that actually refused it, not the default
    # the limiter was built with — an operator reading a client's log has to be
    # able to tell which knob produced the refusal.
    assert "60 requests/minute with a burst of 3" in refused.json()["detail"]


def test_each_tenant_gets_its_own_buckets() -> None:
    """One tenant emptying its bucket cannot throttle another.

    The keys were already per ``(tenant, subject)``; what this proves is that a
    tenant on its own quota is charged against its own capacity rather than
    spending a differently-configured tenant's allowance.
    """
    overrides = {"demo": TenantRateLimit(requests_per_minute=600, burst=1)}
    with _tenant_limited_client(overrides=overrides, burst=5) as demo:
        assert demo.get("/whoami").status_code == 200
        assert demo.get("/whoami").status_code == 429

    with _tenant_limited_client(
        overrides=overrides,
        principal=StubPrincipal("default", "subject-a"),
        burst=5,
    ) as other:
        assert [other.get("/whoami").status_code for _ in range(5)] == [200] * 5


def test_a_lowered_quota_takes_effect_on_the_next_request() -> None:
    """The registry is the control surface, so a change has to be readable
    without a restart. Nothing is cached per tenant: the numbers are arguments
    to the bucket, so the next request is charged against the new ones — and
    the surplus the caller had banked under the old capacity is clamped away
    rather than spent down.
    """
    overrides = {"demo": TenantRateLimit(requests_per_minute=60, burst=4)}
    with _tenant_limited_client(overrides=overrides, burst=2) as client:
        assert client.get("/whoami").status_code == 200  # three of four left

        overrides["demo"] = TenantRateLimit(requests_per_minute=60, burst=1)
        after = [client.get("/whoami") for _ in range(2)]

    # The banked three do not survive a capacity of one: exactly one more
    # request is admitted, and the refusal quotes the new number.
    assert [response.status_code for response in after] == [200, 429]
    assert after[-1].headers[LIMIT_HEADER] == "1"
    assert "60 requests/minute with a burst of 1" in after[-1].json()["detail"]


def test_raising_a_quota_is_not_a_way_to_buy_a_fresh_allowance(
    redis_clock: FakeClock,
) -> None:
    """A throttled caller does not get their bucket back when the number moves.

    The shared bucket holds the token count in Redis and the script clamps it
    to whatever capacity the request arrives with, so raising a quota lets the
    bucket refill *toward* the new capacity rather than filling it. That
    matters for the same reason the in-process map evicts fullest-first rather
    than least-recently-used: whatever a config change does, it must not be a
    route around the limit for the caller who was just refused.
    """
    limiter = _shared(FakeServer(), per_minute=120, burst=2)
    overrides = {"demo": TenantRateLimit(requests_per_minute=120, burst=2)}
    with _tenant_limited_client(overrides=overrides, limiter=limiter) as client:
        assert [client.get("/whoami").status_code for _ in range(2)] == [200, 200]
        assert client.get("/whoami").status_code == 429

        overrides["demo"] = TenantRateLimit(requests_per_minute=120, burst=10)
        raised = client.get("/whoami")
        assert raised.status_code == 429
        assert raised.headers[LIMIT_HEADER] == "10"

        # It is a refill, not a reset: two tokens a second buys two of the ten.
        redis_clock.advance(1.0)
        assert [client.get("/whoami").status_code for _ in range(2)] == [200, 200]
        assert client.get("/whoami").status_code == 429


def test_the_exempt_probes_never_consult_the_registry() -> None:
    """`/healthz` and `/readyz` carry no identity, so there is nothing to look up."""
    resolutions: list[str] = []
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def tenant_limits(tenant_id: str, connection: object | None) -> TenantRateLimit:
        resolutions.append(tenant_id)
        return NO_TENANT_OVERRIDES

    app.add_middleware(
        RateLimitMiddleware,
        limiter=TokenBucketLimiter(requests_per_minute=120, burst=2),
        tenant_limits=tenant_limits,
    )
    app.add_middleware(PrincipalMiddleware, principal=StubPrincipal("demo", "subject-a"))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200

    assert resolutions == []
    assert "/healthz" in UNAUTHENTICATED_PATHS


def test_the_request_transaction_is_what_the_registry_read_is_offered() -> None:
    """A registry read must not ask the pool for a second connection while this
    request still holds its own — that is how a pool deadlocks under load. The
    limiter forwards the handle without knowing what it is; ``src.serving.app``
    is what decides whether it is usable."""
    seen: list[object | None] = []
    sentinel = object()
    app = FastAPI()

    @app.get("/whoami")
    async def whoami() -> dict[str, str]:
        return {"tenant_id": "demo"}

    class _WithTransaction(PrincipalMiddleware):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                scope.setdefault("state", {})["db"] = sentinel
            await super().__call__(scope, receive, send)

    async def tenant_limits(tenant_id: str, connection: object | None) -> TenantRateLimit:
        seen.append(connection)
        return NO_TENANT_OVERRIDES

    app.add_middleware(
        RateLimitMiddleware,
        limiter=TokenBucketLimiter(requests_per_minute=120, burst=2),
        tenant_limits=tenant_limits,
    )
    app.add_middleware(_WithTransaction, principal=StubPrincipal("demo", "subject-a"))

    with TestClient(app) as client:
        assert client.get("/whoami").status_code == 200

    assert seen == [sentinel]


# --- settings ---------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ENVIRONMENT",
        "RATE_LIMIT_ENABLED",
        "RATE_LIMIT_REQUESTS_PER_MINUTE",
        "RATE_LIMIT_BURST",
        "RATE_LIMIT_BACKEND",
        "RATE_LIMIT_ALLOW_IN_PROCESS_BUCKET",
        "REDIS_CONNECTION_STRING",
        "MODEL_SERVER_AUTH_TOKEN",
        "PGBOUNCER_ADMIN_PASSWORD",
        "DEV_AUTH_BYPASS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_documented_defaults_are_the_measured_per_worker_numbers(clean_env: None) -> None:
    """The pair the deployment runs on, and the only place they are declared.

    Nothing in the Compose files or the env example sets `RATE_LIMIT_*`: the
    images bake `ENVIRONMENT=production`, so these defaults *are* the deployed
    policy. They describe one worker's allowance for one client, because
    keep-alive pins a client to one worker — ADR 0014 has the rehearsal
    measurement that made the first pair (120/30) refuse 37.9% of a 5 req/s
    canary.
    """
    settings = Settings(_env_file=None)
    assert settings.rate_limit_requests_per_minute == 600
    assert settings.rate_limit_burst == 120


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


def _deployed(**overrides: Any) -> Settings:
    """A Settings the way a deployed image builds one, minus the overrides."""
    return Settings(
        _env_file=None,
        environment="production",
        model_server_auth_token="not-the-dev-token",
        pgbouncer_admin_password="not-the-dev-password",
        **overrides,
    )


def test_production_refuses_the_per_worker_bucket_by_omission(clean_env: None) -> None:
    """ADR 0014's honesty rule. A limiter that is on while quietly admitting
    `workers × limit` advertises an `X-RateLimit-Limit` no client can plan
    against, and it is the state the rehearsal measured 37.9% of one subject's
    canary requests being refused in. Choosing it stays possible; choosing it
    by omission does not."""
    with pytest.raises(RuntimeError, match="RATE_LIMIT_BACKEND=memory"):
        _deployed(rate_limit_backend="memory")


def test_the_per_worker_bucket_is_available_to_a_deployment_that_says_so(
    clean_env: None,
) -> None:
    """An acknowledgement, not a lock. `rate_limit_enabled` is deliberately
    unguarded because an operator has to be able to turn the limiter off during
    an incident; the same reasoning says the backend has to be selectable, only
    not silently."""
    settings = _deployed(rate_limit_backend="memory", rate_limit_allow_in_process_bucket=True)

    assert settings.rate_limit_backend == "memory"
    assert settings.rate_limit_active is True


def test_a_dev_box_and_a_disabled_limiter_are_both_left_alone(clean_env: None) -> None:
    """The guard is about what a *running* limiter promises, so it has nothing
    to say about a stack where ADR 0014 turns the limiter off — the dev stack
    runs the in-process bucket in the unit tests, and the load rig disables the
    limiter outright."""
    assert (
        Settings(_env_file=None, environment="dev", rate_limit_backend="memory").rate_limit_backend
        == "memory"
    )
    assert _deployed(rate_limit_backend="memory", rate_limit_enabled=False).rate_limit_active is (
        False
    )


def test_the_deployed_default_needs_no_acknowledgement(clean_env: None) -> None:
    settings = _deployed()

    assert settings.rate_limit_backend == "redis"
    assert settings.rate_limit_allow_in_process_bucket is False
    assert settings.rate_limit_active is True
